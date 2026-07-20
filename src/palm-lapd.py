#!/usr/bin/python3
"""Palm OS Bluetooth LAP (PPP over RFCOMM) gateway for BlueZ 5."""

import argparse
import ipaddress
import json
import logging
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from xml.sax.saxutils import quoteattr

import dbus
import dbus.mainloop.glib
import dbus.service
from gi.repository import GLib

BLUEZ = "org.bluez"
PROFILE_MANAGER = "org.bluez.ProfileManager1"
AGENT_MANAGER = "org.bluez.AgentManager1"
PROPERTIES = "org.freedesktop.DBus.Properties"
OBJECT_MANAGER = "org.freedesktop.DBus.ObjectManager"
ADAPTER_IFACE = "org.bluez.Adapter1"
PROFILE_IFACE = "org.bluez.Profile1"
AGENT_IFACE = "org.bluez.Agent1"
LAP_UUID = "00001102-0000-1000-8000-00805f9b34fb"
PROFILE_PATH = "/org/palmlap/profile"
AGENT_PATH = "/org/palmlap/agent"
CONFIG_PATH = Path("/etc/palm-lap/config.json")
PAIRING_STATE = Path("/run/palm-lap/pairing.json")
MAC_RE = re.compile(r"^[0-9A-F]{2}(?::[0-9A-F]{2}){5}$")


class Rejected(dbus.DBusException):
    _dbus_error_name = "org.bluez.Error.Rejected"


def normalize_mac(value):
    value = str(value).strip().upper().replace("-", ":")
    if not MAC_RE.fullmatch(value):
        raise ValueError(f"invalid Bluetooth address: {value!r}")
    return value


def device_address(path):
    marker = "/dev_"
    text = str(path)
    if marker not in text:
        raise ValueError(f"unexpected BlueZ device path: {text}")
    return normalize_mac(text.rsplit(marker, 1)[1].replace("_", ":"))


def load_config(path=CONFIG_PATH):
    with path.open(encoding="utf-8") as handle:
        raw = json.load(handle)
    local_ip = ipaddress.IPv4Address(raw["local_ip"])
    subnet = ipaddress.IPv4Network(raw["subnet"])
    if local_ip not in subnet:
        raise ValueError("local_ip is outside subnet")
    channel = int(raw.get("rfcomm_channel", 4))
    if not 1 <= channel <= 30:
        raise ValueError("rfcomm_channel must be between 1 and 30")
    dns = [str(ipaddress.IPv4Address(item)) for item in raw.get("dns", [])]
    gateway_name = str(raw.get("gateway_name", "Palm LAP")).strip()
    if not 1 <= len(gateway_name) <= 80 or any(ord(c) < 32 for c in gateway_name):
        raise ValueError("gateway_name must contain 1-80 printable characters")
    devices = {}
    used_ips = set()
    for mac, details in raw.get("devices", {}).items():
        mac = normalize_mac(mac)
        if isinstance(details, str):
            details = {"ip": details, "name": ""}
        peer_ip = ipaddress.IPv4Address(details["ip"])
        if peer_ip not in subnet or peer_ip == local_ip:
            raise ValueError(f"peer IP for {mac} is invalid for {subnet}")
        if peer_ip in used_ips:
            raise ValueError(f"peer IP {peer_ip} is assigned more than once")
        used_ips.add(peer_ip)
        devices[mac] = {"ip": str(peer_ip), "name": str(details.get("name", ""))}
    return {
        "local_ip": str(local_ip),
        "subnet": str(subnet),
        "rfcomm_channel": channel,
        "dns": dns,
        "devices": devices,
        "gateway_name": gateway_name,
    }


def pairing_state():
    try:
        with PAIRING_STATE.open(encoding="utf-8") as handle:
            state = json.load(handle)
        if float(state["expires_at"]) <= time.time():
            return None
        pin = str(state["pin"])
        if not 1 <= len(pin) <= 16 or not pin.isalnum():
            return None
        return state
    except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def sdp_record(channel, gateway_name):
    # Matches the classic BlueZ sdptool "LAN" record: public browse group,
    # LAP 1.0 service/profile, and L2CAP -> RFCOMM on the selected channel.
    return f"""<?xml version="1.0" encoding="UTF-8" ?>
<record>
  <attribute id="0x0001">
    <sequence><uuid value="0x1102" /></sequence>
  </attribute>
  <attribute id="0x0004">
    <sequence>
      <sequence><uuid value="0x0100" /></sequence>
      <sequence><uuid value="0x0003" /><uint8 value="0x{channel:02x}" /></sequence>
    </sequence>
  </attribute>
  <attribute id="0x0005">
    <sequence><uuid value="0x1002" /></sequence>
  </attribute>
  <attribute id="0x0006">
    <sequence>
      <uint16 value="0x656e" />
      <uint16 value="0x006a" />
      <uint16 value="0x0100" />
    </sequence>
  </attribute>
  <attribute id="0x0009">
    <sequence>
      <sequence><uuid value="0x1102" /><uint16 value="0x0100" /></sequence>
    </sequence>
  </attribute>
  <attribute id="0x0100"><text value={quoteattr(gateway_name)} /></attribute>
</record>"""


def enforce_closed_pairing(bus):
    """Keep the controller closed unless palm-lap-pair opened a live window."""
    if pairing_state():
        return
    manager = dbus.Interface(bus.get_object(BLUEZ, "/"), OBJECT_MANAGER)
    for path, interfaces in manager.GetManagedObjects().items():
        if ADAPTER_IFACE not in interfaces:
            continue
        props = dbus.Interface(bus.get_object(BLUEZ, path), PROPERTIES)
        props.Set(ADAPTER_IFACE, "Discoverable", dbus.Boolean(False))
        props.Set(ADAPTER_IFACE, "Pairable", dbus.Boolean(False))
        props.Set(ADAPTER_IFACE, "Connectable", dbus.Boolean(True))
        logging.info("Bluetooth pairing/discovery are closed; bonded devices remain connectable")


class PalmAgent(dbus.service.Object):
    def __init__(self, bus, config):
        super().__init__(bus, AGENT_PATH)
        self.config = config

    def _require_window(self, device, action):
        state = pairing_state()
        if not state:
            logging.warning("Rejected %s for %s: pairing window is closed", action, device)
            raise Rejected("Palm LAP pairing window is closed")
        return state

    @dbus.service.method(AGENT_IFACE, in_signature="", out_signature="")
    def Release(self):
        logging.info("BlueZ released pairing agent")

    @dbus.service.method(AGENT_IFACE, in_signature="o", out_signature="s")
    def RequestPinCode(self, device):
        state = self._require_window(device, "legacy PIN request")
        logging.info("Supplying pairing-window PIN to %s", device)
        return str(state["pin"])

    @dbus.service.method(AGENT_IFACE, in_signature="o", out_signature="u")
    def RequestPasskey(self, device):
        state = self._require_window(device, "passkey request")
        pin = str(state["pin"])
        if not pin.isdigit() or int(pin) > 999999:
            raise Rejected("Pairing PIN is not a valid numeric passkey")
        logging.info("Supplying pairing-window passkey to %s", device)
        return dbus.UInt32(int(pin))

    @dbus.service.method(AGENT_IFACE, in_signature="os", out_signature="")
    def DisplayPinCode(self, device, pincode):
        self._require_window(device, "display PIN request")
        logging.info("BlueZ requested PIN display for %s; see the Palm", device)

    @dbus.service.method(AGENT_IFACE, in_signature="ouq", out_signature="")
    def DisplayPasskey(self, device, passkey, entered):
        self._require_window(device, "display passkey request")
        logging.info("BlueZ requested passkey display for %s; see the Palm", device)

    @dbus.service.method(AGENT_IFACE, in_signature="ou", out_signature="")
    def RequestConfirmation(self, device, passkey):
        self._require_window(device, "confirmation request")
        logging.info("Accepted pairing confirmation for %s during open window", device)

    @dbus.service.method(AGENT_IFACE, in_signature="o", out_signature="")
    def RequestAuthorization(self, device):
        self._require_window(device, "pairing authorization")
        logging.info("Authorized pairing for %s during open window", device)

    @dbus.service.method(AGENT_IFACE, in_signature="os", out_signature="")
    def AuthorizeService(self, device, uuid):
        mac = device_address(device)
        if str(uuid).lower() != LAP_UUID or mac not in self.config["devices"]:
            logging.warning("Rejected service %s from unauthorized device %s", uuid, mac)
            raise Rejected("Device is not authorized for Palm LAP")
        logging.info("Authorized LAP service connection from %s", mac)

    @dbus.service.method(AGENT_IFACE, in_signature="", out_signature="")
    def Cancel(self):
        logging.info("BlueZ canceled the current agent request")


class PalmProfile(dbus.service.Object):
    def __init__(self, bus, config, loop):
        super().__init__(bus, PROFILE_PATH)
        self.config = config
        self.loop = loop
        self.connections = {}
        GLib.timeout_add_seconds(2, self._reap_children)

    @dbus.service.method(PROFILE_IFACE, in_signature="", out_signature="")
    def Release(self):
        logging.info("BlueZ released LAP profile")
        self.stop_all()
        self.loop.quit()

    @dbus.service.method(PROFILE_IFACE, in_signature="oha{sv}", out_signature="")
    def NewConnection(self, device, fd, properties):
        path = str(device)
        mac = device_address(path)
        details = self.config["devices"].get(mac)
        if not details:
            logging.warning("Rejected RFCOMM connection from unauthorized device %s", mac)
            raise Rejected("Bluetooth device is not in the Palm LAP allowlist")
        if path in self.connections and self.connections[path].poll() is None:
            raise Rejected("A PPP session is already active for this device")

        peer_ip = details["ip"]
        suffix = int(ipaddress.IPv4Address(peer_ip)) & 0xFF
        interface = f"plap{suffix}"
        command = [
            "/usr/sbin/pppd", "call", "palm-lap",
            f"{self.config['local_ip']}:{peer_ip}",
            "ifname", interface,
        ]
        for resolver in self.config["dns"][:2]:
            command.extend(["ms-dns", resolver])

        raw_fd = fd.take()
        try:
            process = subprocess.Popen(
                command,
                stdin=raw_fd,
                stdout=raw_fd,
                stderr=None,
                close_fds=True,
                start_new_session=True,
            )
        except Exception:
            os.close(raw_fd)
            logging.exception("Could not start pppd for %s", mac)
            raise Rejected("Could not start PPP")
        os.close(raw_fd)
        self.connections[path] = process
        logging.info(
            "Started pppd pid=%d for %s (%s), peer=%s, interface=%s",
            process.pid, mac, details.get("name") or "unnamed", peer_ip, interface,
        )

    @dbus.service.method(PROFILE_IFACE, in_signature="o", out_signature="")
    def RequestDisconnection(self, device):
        self._stop(str(device), "BlueZ disconnection request")

    def _stop(self, path, reason):
        process = self.connections.pop(path, None)
        if not process or process.poll() is not None:
            return
        logging.info("Stopping pppd pid=%d: %s", process.pid, reason)
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

    def stop_all(self):
        for path in list(self.connections):
            self._stop(path, "service shutdown")

    def _reap_children(self):
        for path, process in list(self.connections.items()):
            status = process.poll()
            if status is not None:
                logging.info("pppd pid=%d exited with status %d", process.pid, status)
                self.connections.pop(path, None)
        return True


def configure_logging():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="validate configuration and exit")
    args = parser.parse_args()
    configure_logging()
    try:
        config = load_config()
    except Exception as error:
        logging.error("Invalid %s: %s", CONFIG_PATH, error)
        return 2
    if args.check:
        logging.info("Configuration is valid; %d authorized device(s)", len(config["devices"]))
        return 0

    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()
    loop = GLib.MainLoop()
    agent = PalmAgent(bus, config)
    profile = PalmProfile(bus, config, loop)
    agent_manager = dbus.Interface(bus.get_object(BLUEZ, "/org/bluez"), AGENT_MANAGER)
    profile_manager = dbus.Interface(bus.get_object(BLUEZ, "/org/bluez"), PROFILE_MANAGER)

    agent_manager.RegisterAgent(AGENT_PATH, "KeyboardDisplay")
    agent_manager.RequestDefaultAgent(AGENT_PATH)
    options = dbus.Dictionary({
        "Name": "Palm OS LAN Access",
        "Role": "server",
        "Channel": dbus.UInt16(config["rfcomm_channel"]),
        "RequireAuthentication": dbus.Boolean(True),
        # BlueZ 5.82 can stall legacy RFCOMM for ~25 seconds in its external
        # profile authorization path even after link-key authentication and
        # encryption succeed. NewConnection repeats the configured-MAC check
        # before it owns the fd or starts pppd, so this redundant prompt is not
        # part of the security boundary.
        "RequireAuthorization": dbus.Boolean(False),
        "AutoConnect": dbus.Boolean(False),
        "ServiceRecord": sdp_record(config["rfcomm_channel"], config["gateway_name"]),
        "Version": dbus.UInt16(0x0100),
    }, signature="sv")
    profile_manager.RegisterProfile(PROFILE_PATH, LAP_UUID, options)
    enforce_closed_pairing(bus)
    logging.info(
        "Palm LAP profile registered: UUID=%s RFCOMM=%d authorized_devices=%d",
        LAP_UUID, config["rfcomm_channel"], len(config["devices"]),
    )

    def shutdown(signum, _frame):
        logging.info("Received signal %d; shutting down", signum)
        profile.stop_all()
        loop.quit()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    try:
        loop.run()
    finally:
        profile.stop_all()
        try:
            profile_manager.UnregisterProfile(PROFILE_PATH)
        except dbus.DBusException:
            pass
        try:
            agent_manager.UnregisterAgent(AGENT_PATH)
        except dbus.DBusException:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
