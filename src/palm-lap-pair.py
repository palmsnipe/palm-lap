#!/usr/bin/python3
"""Open, close, or inspect the deliberately short Palm pairing window."""

import argparse
import getpass
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import dbus

BLUEZ = "org.bluez"
OBJECT_MANAGER = "org.freedesktop.DBus.ObjectManager"
PROPERTIES = "org.freedesktop.DBus.Properties"
ADAPTER = "org.bluez.Adapter1"
STATE = Path("/run/palm-lap/pairing.json")
DURATION = 600
BTMGMT = "/usr/bin/btmgmt"


def require_root():
    if os.geteuid() != 0:
        raise SystemExit("Run this command with sudo.")


def adapter(bus):
    manager = dbus.Interface(bus.get_object(BLUEZ, "/"), OBJECT_MANAGER)
    for path, interfaces in manager.GetManagedObjects().items():
        if ADAPTER in interfaces:
            index = int(str(path).rsplit("hci", 1)[1])
            return dbus.Interface(bus.get_object(BLUEZ, path), PROPERTIES), index
    raise SystemExit("No BlueZ adapter is available.")


def close_window(props, index):
    props.Set(ADAPTER, "Discoverable", dbus.Boolean(False))
    props.Set(ADAPTER, "Pairable", dbus.Boolean(False))
    try:
        STATE.unlink()
    except FileNotFoundError:
        pass
    # BlueZ disables page scanning when both flags are cleared. Keep the
    # controller connectable so already bonded/authorized Palms can reconnect.
    props.Set(ADAPTER, "Connectable", dbus.Boolean(True))


def apply_controller_compatibility(index):
    commands = [
        [BTMGMT, "--index", str(index), "class", "3", "0"],
        [BTMGMT, "--index", str(index), "add-uuid", "00001102-0000-1000-8000-00805f9b34fb", "2"],
    ]
    for command in commands:
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL)


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    opened = sub.add_parser("open", help="open a ten-minute pairing window")
    pin_source = opened.add_mutually_exclusive_group()
    pin_source.add_argument("--pin", help="1-16 character PIN; prompt if omitted")
    pin_source.add_argument(
        "--pin-stdin", action="store_true",
        help="read the PIN from standard input (for the validated web helper)",
    )
    sub.add_parser("close", help="close the pairing window immediately")
    sub.add_parser("status", help="show pairing-window status")
    args = parser.parse_args()
    require_root()
    bus = dbus.SystemBus()
    props, index = adapter(bus)

    if args.command == "status":
        try:
            state = json.loads(STATE.read_text(encoding="utf-8"))
            remaining = max(0, round(float(state["expires_at"]) - time.time()))
        except (FileNotFoundError, ValueError, KeyError, json.JSONDecodeError):
            remaining = 0
        print(f"pairing window: {'open' if remaining else 'closed'}")
        print(f"seconds remaining: {remaining}")
        print(f"adapter pairable: {bool(props.Get(ADAPTER, 'Pairable'))}")
        print(f"adapter discoverable: {bool(props.Get(ADAPTER, 'Discoverable'))}")
        return 0

    if args.command == "close":
        close_window(props, index)
        subprocess.run(["systemctl", "stop", "palm-lap-pairing-close.timer"], check=False)
        print("Palm LAP pairing window is closed.")
        return 0

    if args.pin_stdin:
        pin = sys.stdin.readline().strip()
    else:
        pin = args.pin if args.pin is not None else getpass.getpass("Palm pairing PIN: ")
    if not 1 <= len(pin) <= 16 or not pin.isalnum():
        raise SystemExit("PIN must contain 1-16 letters or digits.")
    STATE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = STATE.with_suffix(".tmp")
    temporary.write_text(json.dumps({"pin": pin, "expires_at": time.time() + DURATION}) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(STATE)
    apply_controller_compatibility(index)
    props.Set(ADAPTER, "PairableTimeout", dbus.UInt32(DURATION))
    props.Set(ADAPTER, "DiscoverableTimeout", dbus.UInt32(DURATION))
    props.Set(ADAPTER, "Pairable", dbus.Boolean(True))
    props.Set(ADAPTER, "Discoverable", dbus.Boolean(True))
    subprocess.run(["systemctl", "restart", "palm-lap-pairing-close.timer"], check=True)
    print(f"Palm LAP pairing is open for {DURATION} seconds.")
    print("Use the same PIN on the Palm. The window closes automatically.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
