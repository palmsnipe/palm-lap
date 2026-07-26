#!/usr/bin/python3
"""Receive Bluetooth Object Push files into a separate, audited inbox."""

import argparse
import json
import os
import re
import secrets
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib


OBEX_SERVICE = "org.bluez.obex"
OBEX_ROOT = "/org/bluez/obex"
AGENT_PATH = "/org/palmlap/obex_inbox_agent"
AGENT_MANAGER_IFACE = "org.bluez.obex.AgentManager1"
AGENT_IFACE = "org.bluez.obex.Agent1"
TRANSFER_IFACE = "org.bluez.obex.Transfer1"
SESSION_IFACE = "org.bluez.obex.Session1"
PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"
OBJECT_MANAGER_IFACE = "org.freedesktop.DBus.ObjectManager"
DEVICE_IFACE = "org.bluez.Device1"
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def scalar(value):
    if isinstance(value, (dbus.String, dbus.ObjectPath)):
        return str(value)
    if isinstance(value, (dbus.Byte, dbus.UInt16, dbus.UInt32, dbus.UInt64)):
        return int(value)
    if isinstance(value, dbus.Boolean):
        return bool(value)
    return value


def safe_filename(value):
    """Return a portable basename without discarding an uncommon extension."""
    name = str(value or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = CONTROL_RE.sub("_", name).strip()
    if name in ("", ".", ".."):
        name = "received-file"
    if name.startswith("."):
        name = "received-" + name.lstrip(".")
    encoded = name.encode("utf-8")
    if len(encoded) > 180:
        suffix = Path(name).suffix
        budget = max(1, 180 - len(suffix.encode("utf-8")))
        stem = Path(name).stem
        while len(stem.encode("utf-8")) > budget:
            stem = stem[:-1]
        name = (stem or "received-file") + suffix
    return name


def unique_destination(inbox, name, reserved=()):
    inbox = Path(inbox)
    base_name = safe_filename(name)
    suffix = Path(base_name).suffix
    stem = base_name[:-len(suffix)] if suffix else base_name
    candidate = inbox / base_name
    reserved_paths = {str(Path(item)) for item in reserved}
    counter = 2
    while candidate.exists() or str(candidate) in reserved_paths:
        candidate = inbox / f"{stem}-{counter}{suffix}"
        counter += 1
    return candidate


def metadata_path(inbox, transfer_id):
    return Path(inbox) / f".transfer-{transfer_id}.json"


def write_metadata(inbox, record):
    target = metadata_path(inbox, record["id"])
    temporary = target.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.chmod(temporary, 0o660)
    temporary.replace(target)


def recover_interrupted(inbox):
    """Discard incomplete payloads left by a stopped receiver and retain audit data."""
    for path in Path(inbox).glob(".transfer-*.json"):
        if path.is_symlink():
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if record.get("status") != "receiving":
            continue
        payload = Path(inbox) / os.path.basename(record.get("stored_name", ""))
        if payload.parent == Path(inbox) and payload.is_file() and not payload.is_symlink():
            payload.unlink()
        record["status"] = "interrupted"
        record["finished_at"] = utc_now()
        write_metadata(inbox, record)


class InboxAgent(dbus.service.Object):
    def __init__(self, session_bus, system_bus, inbox, max_bytes, loop):
        super().__init__(session_bus, AGENT_PATH)
        self.session_bus = session_bus
        self.system_bus = system_bus
        self.inbox = Path(inbox).resolve()
        self.max_bytes = max_bytes
        self.loop = loop
        self.pending = {}
        self.started = {}

    def transfer_properties(self, transfer_path):
        interface = dbus.Interface(
            self.session_bus.get_object(OBEX_SERVICE, transfer_path),
            PROPERTIES_IFACE,
        )
        return {
            str(key): scalar(value)
            for key, value in interface.GetAll(TRANSFER_IFACE).items()
        }

    def session_properties(self, session_path):
        interface = dbus.Interface(
            self.session_bus.get_object(OBEX_SERVICE, session_path),
            PROPERTIES_IFACE,
        )
        return {
            str(key): scalar(value)
            for key, value in interface.GetAll(SESSION_IFACE).items()
        }

    def trusted_device(self, address):
        manager = dbus.Interface(
            self.system_bus.get_object("org.bluez", "/"), OBJECT_MANAGER_IFACE
        )
        normalized = str(address).upper()
        for _path, interfaces in manager.GetManagedObjects().items():
            properties = interfaces.get(DEVICE_IFACE)
            if not properties or str(properties.get("Address", "")).upper() != normalized:
                continue
            paired = bool(properties.get("Paired", False))
            trusted = bool(properties.get("Trusted", False))
            if not paired or not trusted:
                return None
            return {
                "address": normalized,
                "name": str(
                    properties.get("Alias")
                    or properties.get("Name")
                    or normalized
                ),
            }
        return None

    @dbus.service.method(AGENT_IFACE, in_signature="o", out_signature="s")
    def AuthorizePush(self, transfer_path):
        transfer_path = str(transfer_path)
        properties = self.transfer_properties(transfer_path)
        session_path = str(properties["Session"])
        session = self.session_properties(session_path)
        sender = self.trusted_device(session.get("Destination", ""))
        if sender is None:
            raise dbus.DBusException(
                "Incoming files are accepted only from paired and trusted devices",
                name="org.bluez.obex.Error.Rejected",
            )

        expected_size = int(properties.get("Size", 0))
        if expected_size and expected_size > self.max_bytes:
            raise dbus.DBusException(
                f"Incoming file exceeds the {self.max_bytes}-byte limit",
                name="org.bluez.obex.Error.Rejected",
            )

        original_name = (
            properties.get("Name")
            or os.path.basename(str(properties.get("Filename", "")))
            or "received-file"
        )
        destination = unique_destination(
            self.inbox,
            original_name,
            (item["destination"] for item in self.pending.values()),
        )
        transfer_id = secrets.token_hex(8)
        record = {
            "id": transfer_id,
            "status": "receiving",
            "original_name": str(original_name),
            "stored_name": destination.name,
            "sender_address": sender["address"],
            "sender_name": sender["name"],
            "local_address": str(session.get("Source", "")),
            "expected_size": expected_size or None,
            "started_at": utc_now(),
            "received_at": None,
            "duration_seconds": None,
            "size": None,
        }
        self.pending[transfer_path] = {
            "destination": str(destination),
            "record": record,
        }
        self.started[transfer_path] = time.monotonic()
        write_metadata(self.inbox, record)
        print(
            f"Accepting {original_name!r} from {sender['name']} "
            f"({sender['address']}) as {destination.name!r}",
            flush=True,
        )
        return str(destination)

    @dbus.service.method(AGENT_IFACE, in_signature="", out_signature="")
    def Cancel(self):
        print("BlueZ canceled a pending inbox authorization", flush=True)

    @dbus.service.method(AGENT_IFACE, in_signature="", out_signature="")
    def Release(self):
        print("BlueZ released the inbox agent", flush=True)
        self.loop.quit()

    def finish(self, transfer_path, status):
        pending = self.pending.pop(transfer_path, None)
        started = self.started.pop(transfer_path, None)
        if pending is None:
            return
        destination = Path(pending["destination"])
        record = pending["record"]
        record["finished_at"] = utc_now()
        if started is not None:
            record["duration_seconds"] = round(max(0.0, time.monotonic() - started), 3)

        if status == "complete" and destination.is_file() and not destination.is_symlink():
            actual_size = destination.stat().st_size
            if actual_size <= self.max_bytes:
                os.chmod(destination, 0o640)
                record["status"] = "complete"
                record["received_at"] = record["finished_at"]
                record["size"] = actual_size
                print(
                    f"Received {destination.name!r} from "
                    f"{record['sender_name']} ({record['sender_address']}), "
                    f"{actual_size} bytes in {record['duration_seconds']} seconds",
                    flush=True,
                )
            else:
                destination.unlink()
                record["status"] = "rejected-too-large"
        else:
            if destination.is_file() and not destination.is_symlink():
                destination.unlink()
            record["status"] = status
        write_metadata(self.inbox, record)

    def properties_changed(
        self, interface, changed, _invalidated, object_path=None
    ):
        transfer_path = str(object_path)
        if str(interface) != TRANSFER_IFACE or transfer_path not in self.pending:
            return
        values = {str(key): scalar(value) for key, value in changed.items()}
        status = values.get("Status")
        if status in ("complete", "error"):
            self.finish(transfer_path, status)

    def interfaces_removed(self, object_path, _interfaces):
        transfer_path = str(object_path)
        if transfer_path not in self.pending:
            return
        self.finish(transfer_path, "removed")


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--inbox", default="/var/lib/palm-web/inbox",
        help="directory used only for received Bluetooth files",
    )
    parser.add_argument(
        "--max-bytes", type=int, default=64 * 1024 * 1024,
        help="maximum accepted incoming file size",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    inbox = Path(args.inbox)
    if args.max_bytes < 1:
        print("max-bytes must be positive", file=sys.stderr)
        return 2
    if inbox.is_symlink():
        print("inbox must not be a symlink", file=sys.stderr)
        return 2
    inbox.mkdir(parents=True, exist_ok=True)
    recover_interrupted(inbox)

    DBusGMainLoop(set_as_default=True)
    session_bus = dbus.SessionBus()
    system_bus = dbus.SystemBus()
    loop = GLib.MainLoop()
    agent = InboxAgent(session_bus, system_bus, inbox, args.max_bytes, loop)
    session_bus.add_signal_receiver(
        agent.properties_changed,
        signal_name="PropertiesChanged",
        dbus_interface=PROPERTIES_IFACE,
        path_keyword="object_path",
    )
    session_bus.add_signal_receiver(
        agent.interfaces_removed,
        signal_name="InterfacesRemoved",
        dbus_interface=OBJECT_MANAGER_IFACE,
    )
    manager = dbus.Interface(
        session_bus.get_object(OBEX_SERVICE, OBEX_ROOT), AGENT_MANAGER_IFACE
    )
    manager.RegisterAgent(AGENT_PATH)
    print(f"Bluetooth inbox agent registered; storing files in {inbox}", flush=True)

    def stop(_signum, _frame):
        loop.quit()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        loop.run()
    finally:
        try:
            manager.UnregisterAgent(AGENT_PATH)
        except dbus.DBusException:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
