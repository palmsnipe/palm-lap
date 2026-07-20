#!/bin/sh
set -eu

REPO_ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
if [ "$(id -u)" -ne 0 ]; then
    echo "Run this updater with sudo." >&2
    exit 1
fi
if [ ! -s /etc/palm-lap/config.json ] || [ ! -s /etc/palm-lap/web.json ]; then
    echo "No existing Palm LAP configuration; run scripts/install.sh instead." >&2
    exit 1
fi

STAMP=$(date +%Y%m%d-%H%M%S)
BACKUP_DIR="/var/backups/palm-lap/$STAMP-update"
install -d -m 0700 "$BACKUP_DIR"
cp -a /etc/palm-lap/config.json /etc/palm-lap/web.json "$BACKUP_DIR/"

install -o root -g root -m 0755 "$REPO_ROOT/src/palm-lapd.py" /usr/local/libexec/palm-lapd
install -o root -g root -m 0755 "$REPO_ROOT/src/palm_web.py" /usr/local/libexec/palm_web.py
install -o root -g root -m 0755 "$REPO_ROOT/src/palm-lap-pair.py" /usr/local/sbin/palm-lap-pair
install -o root -g root -m 0755 "$REPO_ROOT/src/palm-lap-device.py" /usr/local/sbin/palm-lap-device
install -o root -g root -m 0755 "$REPO_ROOT/src/palm-web-admin.py" /usr/local/sbin/palm-web-admin
install -o root -g root -m 0755 "$REPO_ROOT/src/palm-bluetooth-recover.py" /usr/local/sbin/palm-bluetooth-recover
install -o root -g root -m 0755 "$REPO_ROOT/src/palm-lap-controller-compat.py" /usr/local/sbin/palm-lap-controller-compat
install -o root -g root -m 0755 "$REPO_ROOT/src/palm-send-prc.py" /usr/local/bin/palm-send-prc

install -d -m 0755 /usr/local/share/doc/palm-lap
install -o root -g root -m 0644 "$REPO_ROOT"/docs/*.md /usr/local/share/doc/palm-lap/
for unit in "$REPO_ROOT"/systemd/*; do
    install -o root -g root -m 0644 "$unit" "/etc/systemd/system/$(basename "$unit")"
done

/usr/local/libexec/palm-lapd --check
systemd-analyze verify \
    /etc/systemd/system/palm-lap.service \
    /etc/systemd/system/palm-web.service \
    /etc/systemd/system/palm-bluetooth-recover.service \
    /etc/systemd/system/palm-lap-compat.service
systemctl daemon-reload
systemctl restart palm-lap.service palm-web.service

echo "Code and units updated. Device configuration and web files were preserved."
echo "Safety copy: $BACKUP_DIR"
