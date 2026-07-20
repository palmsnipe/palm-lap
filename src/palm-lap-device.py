#!/usr/bin/python3
"""Manage the explicit Palm LAP Bluetooth-MAC/IP allowlist."""

import argparse
import ipaddress
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import dbus

CONFIG = Path("/etc/palm-lap/config.json")
BLUEZ = "org.bluez"
OBJECT_MANAGER = "org.freedesktop.DBus.ObjectManager"
PROPERTIES = "org.freedesktop.DBus.Properties"
DEVICE = "org.bluez.Device1"
MAC_RE = re.compile(r"^[0-9A-F]{2}(?::[0-9A-F]{2}){5}$")


def normalize_mac(value):
    value = value.strip().upper().replace("-", ":")
    if not MAC_RE.fullmatch(value):
        raise SystemExit(f"Invalid Bluetooth address: {value}")
    return value


def read_config():
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def write_config(config):
    temporary = CONFIG.with_suffix(".tmp")
    temporary.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(CONFIG)


def bluez_device(mac):
    bus = dbus.SystemBus()
    manager = dbus.Interface(bus.get_object(BLUEZ, "/"), OBJECT_MANAGER)
    for path, interfaces in manager.GetManagedObjects().items():
        details = interfaces.get(DEVICE)
        if details and str(details.get("Address", "")).upper() == mac:
            return dbus.Interface(bus.get_object(BLUEZ, path), PROPERTIES), details
    return None, None


def restart_and_check():
    subprocess.run(["/usr/local/libexec/palm-lapd", "--check"], check=True)
    subprocess.run(["systemctl", "restart", "palm-lap.service"], check=True)


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    add = sub.add_parser("add")
    add.add_argument("mac")
    add.add_argument("ip")
    add.add_argument("--name", default="")
    remove = sub.add_parser("remove")
    remove.add_argument("mac")
    args = parser.parse_args()

    config = read_config()
    if args.command == "list":
        if not config.get("devices"):
            print("No Palm devices are authorized.")
            return 0
        for mac, details in sorted(config["devices"].items()):
            print(f"{mac}\t{details['ip']}\t{details.get('name', '')}")
        return 0

    if os.geteuid() != 0:
        raise SystemExit("Run add/remove with sudo.")
    mac = normalize_mac(args.mac)
    config.setdefault("devices", {})
    if args.command == "remove":
        if mac not in config["devices"]:
            raise SystemExit(f"{mac} is not authorized.")
        del config["devices"][mac]
        write_config(config)
        restart_and_check()
        print(f"Removed {mac} from the Palm LAP allowlist. The Bluetooth bond remains.")
        return 0

    peer = ipaddress.IPv4Address(args.ip)
    subnet = ipaddress.IPv4Network(config["subnet"])
    local = ipaddress.IPv4Address(config["local_ip"])
    if peer not in subnet or peer == local or peer in (subnet.network_address, subnet.broadcast_address):
        raise SystemExit(f"Peer IP must be a usable address in {subnet}, excluding {local}.")
    for existing_mac, details in config["devices"].items():
        if details["ip"] == str(peer) and existing_mac != mac:
            raise SystemExit(f"{peer} is already assigned to {existing_mac}.")
    props, details = bluez_device(mac)
    if not details or not bool(details.get("Paired", False)):
        raise SystemExit(f"{mac} is not bonded in BlueZ; pair it first.")
    props.Set(DEVICE, "Trusted", dbus.Boolean(True))
    config["devices"][mac] = {"ip": str(peer), "name": args.name}
    write_config(config)
    restart_and_check()
    print(f"Authorized {mac} as {peer}; palm-lap.service restarted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
