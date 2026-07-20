#!/usr/bin/python3
"""Narrow root helper for the Palm gateway web interface."""

import argparse
import ipaddress
import json
import os
import pwd
import re
import subprocess
import sys
import time
from pathlib import Path


CONFIG = Path("/etc/palm-lap/config.json")
WEB_CONFIG = Path("/etc/palm-lap/web.json")
STORE = Path("/var/lib/palm-web/files")
PAIR = "/usr/local/sbin/palm-lap-pair"
DEVICE = "/usr/local/sbin/palm-lap-device"
SENDER = "/usr/local/bin/palm-send-prc"
RECOVERY_SERVICE = "palm-bluetooth-recover.service"
MAC_RE = re.compile(r"^[0-9A-F]{2}(?::[0-9A-F]{2}){5}$")


def fail(message):
    print(message, file=sys.stderr)
    raise SystemExit(1)


def request_data():
    raw = sys.stdin.buffer.read(8193)
    if len(raw) > 8192:
        fail("request is too large")
    try:
        value = json.loads(raw or b"{}")
    except json.JSONDecodeError:
        fail("invalid JSON request")
    if not isinstance(value, dict):
        fail("request must be a JSON object")
    return value


def mac(value):
    value = str(value).strip().upper().replace("-", ":")
    if not MAC_RE.fullmatch(value):
        fail("invalid Bluetooth address")
    return value


def run(command, **kwargs):
    return subprocess.run(command, check=True, text=True, **kwargs)


def probe(command):
    """Run a read-only diagnostic without making the whole status page fail."""
    try:
        return subprocess.run(
            command, check=False, text=True, capture_output=True, timeout=10
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(command, 1, "", str(exc))


def pairing_status():
    result = probe([PAIR, "status"])
    if result.returncode:
        return {"pairing window": "unavailable", "error": result.stderr.strip()}
    values = {}
    for line in result.stdout.splitlines():
        if ": " in line:
            key, value = line.split(": ", 1)
            values[key] = value
    return values


def paired_devices():
    result = probe(["bluetoothctl", "devices", "Paired"])
    if result.returncode:
        return []
    devices = []
    for line in result.stdout.splitlines():
        parts = line.split(maxsplit=2)
        if len(parts) < 2 or parts[0] != "Device":
            continue
        address = mac(parts[1])
        info = probe(["bluetoothctl", "info", address]).stdout
        details = {"mac": address, "name": parts[2] if len(parts) > 2 else address}
        for item in info.splitlines():
            if ": " in item:
                key, value = item.strip().split(": ", 1)
                if key in ("Name", "Alias", "Paired", "Bonded", "Trusted", "Connected"):
                    details[key.lower()] = value
        devices.append(details)
    return devices


def controller_status():
    show = probe(["bluetoothctl", "show"])
    adapter = {}
    for line in show.stdout.splitlines():
        item = line.strip()
        if ": " not in item:
            continue
        key, value = item.split(": ", 1)
        if key in ("Name", "Alias", "Powered", "Discoverable", "Pairable"):
            adapter[key.lower()] = value
    adapter["lap_uuid"] = (
        "yes" if "00001102-0000-1000-8000-00805f9b34fb" in show.stdout else "no"
    )
    if show.returncode and not adapter:
        adapter["error"] = (show.stderr or "Bluetooth controller unavailable").strip()

    hci = probe(["hciconfig", "hci0"])
    hci_lines = [line.strip() for line in hci.stdout.splitlines() if line.strip()]
    management = probe(["btmgmt", "--index", "0", "info"])
    settings = "unavailable"
    device_class = "unavailable"
    for line in management.stdout.splitlines():
        item = line.strip()
        if item.startswith("current settings:"):
            settings = item.split(":", 1)[1].strip()
        else:
            class_match = re.search(r"\bclass (0x[0-9a-fA-F]+)", item)
            if class_match:
                device_class = class_match.group(1)

    services = {}
    for service in ("bluetooth", "palm-lap", "dnsmasq", "nftables"):
        result = probe(["systemctl", "is-active", service])
        services[service] = result.stdout.strip() or "unknown"

    kernel = probe(["journalctl", "-k", "-b", "-n", "250", "--no-pager", "-o", "short-iso"])
    fault_terms = ("hardware error", "Opcode 0x0c03 failed", "hci0: command", "hci0: Opcode")
    faults = [line for line in kernel.stdout.splitlines() if any(term in line for term in fault_terms)]
    return {
        "adapter": adapter,
        "hci": hci_lines[:6],
        "settings": settings,
        "class": device_class,
        "services": services,
        "recent_faults": faults[-8:],
    }


def status():
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    print(json.dumps({
        "pairing": pairing_status(),
        "devices": paired_devices(),
        "authorized": config.get("devices", {}),
        "local_ip": config["local_ip"],
        "subnet": config["subnet"],
        "controller": controller_status(),
    }))


def restore_lap_hints():
    run(["bluetoothctl", "power", "on"], capture_output=True)
    run(["btmgmt", "--index", "0", "class", "3", "0"], capture_output=True)
    run([
        "btmgmt", "--index", "0", "add-uuid",
        "00001102-0000-1000-8000-00805f9b34fb", "2",
    ], capture_output=True)


def lap_restart():
    run(["systemctl", "restart", "palm-lap.service"], capture_output=True)
    print("LAP service restarted")


def bluetooth_restart():
    run(["systemctl", "restart", "bluetooth.service"], capture_output=True)
    time.sleep(2)
    restore_lap_hints()
    run(["systemctl", "start", "palm-lap.service"], capture_output=True)
    print("Bluetooth stack and LAP service restarted")


def bluetooth_uart_reset(data):
    if data.get("confirmation") != "RESET":
        fail("UART reset confirmation is missing")
    result = run(
        ["systemctl", "start", "--no-block", RECOVERY_SERVICE], capture_output=True
    )
    print(result.stdout, end="")
    print("Bluetooth UART reset scheduled")


def pair_open(data):
    pin = str(data.get("pin", ""))
    if not 1 <= len(pin) <= 16 or not pin.isalnum():
        fail("PIN must contain 1-16 letters or digits")
    result = run([PAIR, "open", "--pin-stdin"], input=pin + "\n", capture_output=True)
    print(result.stdout, end="")


def device_add(data):
    address = mac(data.get("mac", ""))
    name = str(data.get("name", "")).strip()
    if len(name) > 80 or any(ord(c) < 32 for c in name):
        fail("invalid device name")
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    try:
        peer = ipaddress.IPv4Address(str(data.get("ip", "")))
    except ipaddress.AddressValueError:
        fail("invalid peer address")
    subnet = ipaddress.IPv4Network(config["subnet"])
    if peer not in subnet:
        fail(f"peer must be inside {subnet}")
    command = [DEVICE, "add", address, str(peer)]
    if name:
        command += ["--name", name]
    result = run(command, capture_output=True)
    print(result.stdout, end="")


def device_remove(data):
    address = mac(data.get("mac", ""))
    result = run([DEVICE, "remove", address], capture_output=True)
    print(result.stdout, end="")


def send_file(data):
    address = mac(data.get("mac", ""))
    filename = os.path.basename(str(data.get("filename", "")))
    candidate = (STORE / filename).resolve()
    if candidate.parent != STORE.resolve() or not candidate.is_file():
        fail("file is not in the managed store")
    if candidate.suffix.lower() not in (".prc", ".pdb"):
        fail("only .prc and .pdb files may be sent")
    # Object Push is not exposed by the tested Zire while LAP is connected.
    subprocess.run(["bluetoothctl", "disconnect", address], check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    web_config = json.loads(WEB_CONFIG.read_text(encoding="utf-8"))
    operator = str(web_config.get("operator_user", "")).strip()
    try:
        operator_record = pwd.getpwnam(operator)
    except KeyError:
        fail("configured operator_user does not exist")
    env = os.environ.copy()
    env["XDG_RUNTIME_DIR"] = f"/run/user/{operator_record.pw_uid}"
    env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path=/run/user/{operator_record.pw_uid}/bus"
    os.execvpe(
        "runuser",
        ["runuser", "-u", operator, "--", SENDER, "--device", address,
         "--timeout", "600", str(candidate)],
        env,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action",
        choices=(
            "status", "pair-open", "pair-close", "device-add", "device-remove",
            "send", "lap-restart", "bluetooth-restart", "bluetooth-uart-reset",
        ),
    )
    args = parser.parse_args()
    if os.geteuid() != 0:
        fail("this helper must run through sudo")
    data = request_data() if args.action != "status" else {}
    if args.action == "status":
        status()
    elif args.action == "pair-open":
        pair_open(data)
    elif args.action == "pair-close":
        run([PAIR, "close"])
    elif args.action == "device-add":
        device_add(data)
    elif args.action == "device-remove":
        device_remove(data)
    elif args.action == "send":
        send_file(data)
    elif args.action == "lap-restart":
        lap_restart()
    elif args.action == "bluetooth-restart":
        bluetooth_restart()
    elif args.action == "bluetooth-uart-reset":
        bluetooth_uart_reset(data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
