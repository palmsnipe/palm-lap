#!/usr/bin/python3
"""Recover the Raspberry Pi Zero 2 W Bluetooth UART after an HCI timeout."""

import subprocess
import time
from pathlib import Path


DEVICE = "serial0-0"
DRIVER_DIR = Path("/sys/bus/serial/drivers/hci_uart_bcm")
DEVICE_DRIVER = Path(f"/sys/bus/serial/devices/{DEVICE}/driver")
LAP_UUID = "00001102-0000-1000-8000-00805f9b34fb"


def run(command, check=True, timeout=10):
    return subprocess.run(
        command, check=check, text=True, capture_output=True, timeout=timeout
    )


def validate_platform():
    if not DEVICE_DRIVER.exists():
        raise RuntimeError(f"Bluetooth UART device {DEVICE} is absent")
    if DEVICE_DRIVER.resolve() != DRIVER_DIR.resolve():
        raise RuntimeError(f"{DEVICE} is not bound to hci_uart_bcm")
    for control in ("unbind", "bind"):
        if not (DRIVER_DIR / control).exists():
            raise RuntimeError(f"missing sysfs control: {DRIVER_DIR / control}")


def restore_services():
    run(["systemctl", "start", "bluetooth.service"], check=False)
    for _ in range(10):
        if Path("/sys/class/bluetooth/hci0").exists():
            break
        time.sleep(1)
    run(["bluetoothctl", "power", "on"])
    # palm-lapd restores classic page scanning/connectability and registers the
    # actual LAP profile. Discovery-only management class/UUID hints are not
    # issued while the freshly reset firmware is settling: they can stall here,
    # and palm-lap-pair safely reasserts them before every pairing window.
    run(["systemctl", "start", "palm-lap.service"])
    time.sleep(2)
    show = run(["bluetoothctl", "show"]).stdout
    if "Powered: yes" not in show or LAP_UUID not in show:
        raise RuntimeError("controller power or registered LAP profile did not recover")


def main():
    validate_platform()
    run(["systemctl", "stop", "palm-lap.service", "bluetooth.service"], timeout=15)
    try:
        (DRIVER_DIR / "unbind").write_text(DEVICE, encoding="ascii")
        time.sleep(2)
        (DRIVER_DIR / "bind").write_text(DEVICE, encoding="ascii")
        time.sleep(4)
        restore_services()
    except Exception:
        # Make a best effort to leave normal services running even if recovery
        # fails; systemd records the original exception and non-zero result.
        run(["systemctl", "start", "bluetooth.service"], check=False)
        run(["systemctl", "start", "palm-lap.service"], check=False)
        raise
    print("Bluetooth UART rebound; controller powered; LAP service restored")


if __name__ == "__main__":
    main()
