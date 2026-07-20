#!/usr/bin/python3
"""Send Palm OS database files over Bluetooth Object Push (OBEX)."""

import argparse
import os
import subprocess
import sys
import time

import dbus
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib


OBEX_SERVICE = "org.bluez.obex"
OBEX_ROOT = "/org/bluez/obex"
CLIENT_IFACE = "org.bluez.obex.Client1"
PUSH_IFACE = "org.bluez.obex.ObjectPush1"
TRANSFER_IFACE = "org.bluez.obex.Transfer1"
PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"


def parse_args():
    parser = argparse.ArgumentParser(
        description="Send one or more .prc/.pdb files to a paired Palm via Bluetooth."
    )
    parser.add_argument("files", nargs="+", help="Palm database file(s) to send")
    parser.add_argument(
        "--device", required=True,
        help="Bluetooth address of the paired Palm",
    )
    parser.add_argument(
        "--timeout", type=int, default=300,
        help="seconds to wait for each transfer (default: 300)",
    )
    return parser.parse_args()


def validate_files(paths):
    result = []
    for value in paths:
        path = os.path.abspath(value)
        if not os.path.isfile(path):
            raise ValueError(f"not a regular file: {value}")
        if os.path.splitext(path)[1].lower() not in (".prc", ".pdb"):
            raise ValueError(f"expected a .prc or .pdb file: {value}")
        result.append(path)
    return result


def start_obex_service():
    result = subprocess.run(
        ["systemctl", "--user", "start", "obex.service"],
        check=False, text=True, capture_output=True,
    )
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"could not start obex.service: {detail}")


def wait_for_service(bus, seconds=10):
    dbus_iface = dbus.Interface(
        bus.get_object("org.freedesktop.DBus", "/org/freedesktop/DBus"),
        "org.freedesktop.DBus",
    )
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if dbus_iface.NameHasOwner(OBEX_SERVICE):
            return
        time.sleep(0.2)
    raise RuntimeError("org.bluez.obex did not appear on the user D-Bus")


def scalar(value):
    if isinstance(value, (dbus.String, dbus.ObjectPath)):
        return str(value)
    if isinstance(value, (dbus.UInt16, dbus.UInt32, dbus.UInt64)):
        return int(value)
    return value


def transfer_one(bus, session_path, path, timeout):
    push = dbus.Interface(bus.get_object(OBEX_SERVICE, session_path), PUSH_IFACE)
    state = {
        "path": None,
        "props": {},
        "terminal": None,
        "removed": False,
        "timed_out": False,
        "last_report": None,
    }
    loop = GLib.MainLoop()

    def report():
        props = state["props"]
        status = props.get("Status", "unknown")
        done = props.get("Transferred", 0)
        size = props.get("Size", os.path.getsize(path))
        current = (status, done, size)
        if current != state["last_report"]:
            print(f"  {status}: {done}/{size} bytes", flush=True)
            state["last_report"] = current
        if status in ("complete", "error"):
            state["terminal"] = status
            if loop.is_running():
                loop.quit()

    def properties_changed(interface, changed, invalidated, object_path=None):
        if str(object_path) != state["path"] or str(interface) != TRANSFER_IFACE:
            return
        for key, value in changed.items():
            state["props"][str(key)] = scalar(value)
        for key in invalidated:
            state["props"].pop(str(key), None)
        report()

    def interfaces_removed(object_path, interfaces):
        if str(object_path) != state["path"]:
            return
        state["removed"] = True
        if loop.is_running():
            loop.quit()

    def timed_out():
        state["timed_out"] = True
        if loop.is_running():
            loop.quit()
        return GLib.SOURCE_REMOVE

    # Register before SendFile. Signals emitted while the blocking method call is
    # in progress remain queued and are matched once its returned path is known.
    property_match = bus.add_signal_receiver(
        properties_changed,
        signal_name="PropertiesChanged",
        dbus_interface=PROPERTIES_IFACE,
        path_keyword="object_path",
    )
    removed_match = bus.add_signal_receiver(
        interfaces_removed,
        signal_name="InterfacesRemoved",
        dbus_interface="org.freedesktop.DBus.ObjectManager",
    )
    print(f"Sending {os.path.basename(path)} ({os.path.getsize(path)} bytes)...")
    print("  Accept the incoming Bluetooth item on the Palm when prompted.")
    timeout_source = None
    try:
        transfer_path, initial = push.SendFile(path, timeout=60)
        state["path"] = str(transfer_path)
        state["props"] = {str(k): scalar(v) for k, v in initial.items()}
        props_iface = dbus.Interface(
            bus.get_object(OBEX_SERVICE, state["path"]), PROPERTIES_IFACE
        )
        try:
            state["props"].update({
                str(k): scalar(v)
                for k, v in props_iface.GetAll(TRANSFER_IFACE).items()
            })
        except dbus.DBusException:
            # Queued signals may already contain the terminal state/removal.
            pass
        report()
        if state["terminal"] is None:
            timeout_source = GLib.timeout_add_seconds(timeout, timed_out)
            loop.run()

        if state["terminal"] == "complete":
            print(f"Installed/received: {os.path.basename(path)}")
            return
        if state["terminal"] == "error":
            raise RuntimeError(
                f"the Palm rejected the transfer or the Bluetooth link failed: {path}"
            )
        if state["timed_out"]:
            try:
                dbus.Interface(
                    bus.get_object(OBEX_SERVICE, state["path"]), TRANSFER_IFACE
                ).Cancel()
            except dbus.DBusException:
                pass
            raise RuntimeError(f"timed out waiting for the Palm to accept {path}")
        if state["removed"]:
            raise RuntimeError("BlueZ removed the transfer without a final status")
        raise RuntimeError("transfer ended without a final status")
    finally:
        if timeout_source is not None:
            GLib.source_remove(timeout_source)
        property_match.remove()
        removed_match.remove()


def main():
    args = parse_args()
    try:
        DBusGMainLoop(set_as_default=True)
        files = validate_files(args.files)
        start_obex_service()
        bus = dbus.SessionBus()
        wait_for_service(bus)
        client = dbus.Interface(bus.get_object(OBEX_SERVICE, OBEX_ROOT), CLIENT_IFACE)
        print(f"Opening Bluetooth Object Push session to {args.device}...")
        session_path = client.CreateSession(
            args.device,
            dbus.Dictionary({"Target": "opp"}, signature="sv"),
            timeout=60,
        )
        try:
            for path in files:
                transfer_one(bus, str(session_path), path, args.timeout)
        finally:
            try:
                client.RemoveSession(session_path, timeout=30)
            except dbus.DBusException as exc:
                print(f"Warning: could not close OBEX session: {exc}", file=sys.stderr)
        return 0
    except (ValueError, RuntimeError, dbus.DBusException) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        print(
            "Check that Bluetooth is enabled on the Palm and that the device is paired.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
