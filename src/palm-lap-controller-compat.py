#!/usr/bin/python3
"""Apply legacy Palm LAP discovery hints with btmgmt under systemd."""

import subprocess


BTMGMT = "/usr/bin/btmgmt"
COMMANDS = (
    [BTMGMT, "--index", "0", "class", "3", "0"],
    [
        BTMGMT, "--index", "0", "add-uuid",
        "00001102-0000-1000-8000-00805f9b34fb", "2",
    ],
)


def run_with_open_stdin(command):
    # BlueZ 5.82 btmgmt can stall under a systemd service when stdin is
    # /dev/null. Keep a pipe open while its bounded one-shot command runs.
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        returncode = process.wait(timeout=6)
    except subprocess.TimeoutExpired:
        process.terminate()
        process.wait(timeout=2)
        raise RuntimeError(f"timed out: {' '.join(command)}")
    finally:
        if process.stdin:
            process.stdin.close()
    output = process.stdout.read() if process.stdout else ""
    if output:
        print(output.strip())
    if returncode:
        raise RuntimeError(f"command failed ({returncode}): {' '.join(command)}")


def main():
    for command in COMMANDS:
        run_with_open_stdin(command)
    print("Legacy Palm LAP discovery hints restored")


if __name__ == "__main__":
    main()
