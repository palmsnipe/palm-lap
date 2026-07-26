#!/bin/sh
set -eu

PURGE=0
if [ "${1:-}" = "--purge" ]; then
    PURGE=1
elif [ "$#" -ne 0 ]; then
    echo "Usage: sudo ./scripts/uninstall.sh [--purge]" >&2
    exit 2
fi
if [ "$(id -u)" -ne 0 ]; then
    echo "Run this command with sudo." >&2
    exit 1
fi

OPERATOR_USER=""
if [ -s /etc/palm-lap/web.json ]; then
    OPERATOR_USER=$(python3 -c 'import json; print(json.load(open("/etc/palm-lap/web.json")).get("operator_user", ""))')
fi
if [ -n "$OPERATOR_USER" ] && id "$OPERATOR_USER" >/dev/null 2>&1; then
    OPERATOR_UID=$(id -u "$OPERATOR_USER")
    runuser -u "$OPERATOR_USER" -- env \
        XDG_RUNTIME_DIR="/run/user/$OPERATOR_UID" \
        DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$OPERATOR_UID/bus" \
        systemctl --user disable --now palm-obex-inbox.service 2>/dev/null || true
fi

systemctl disable --now palm-lap-compat.service palm-web.service palm-lap.service 2>/dev/null || true
palm-lap-pair close >/dev/null 2>&1 || true

rm -f \
    /etc/systemd/system/palm-lap.service \
    /etc/systemd/system/palm-lap-pairing-close.service \
    /etc/systemd/system/palm-lap-pairing-close.timer \
    /etc/systemd/system/palm-web.service \
    /etc/systemd/system/palm-bluetooth-recover.service \
    /etc/systemd/system/palm-lap-compat.service \
    /etc/systemd/user/palm-obex-inbox.service \
    /etc/systemd/user/obex.service.d/palm-lap.conf \
    /etc/sudoers.d/palm-web \
    /etc/sudoers.d/palm-obex \
    /etc/dnsmasq.d/palm-lap.conf \
    /etc/nftables.d/palm-lap.nft \
    /etc/ppp/peers/palm-lap \
    /etc/sysctl.d/90-palm-lap-forwarding.conf \
    /usr/local/libexec/palm-lapd \
    /usr/local/libexec/palm_web.py \
    /usr/local/libexec/palm-obex-inbox \
    /usr/local/sbin/palm-lap-pair \
    /usr/local/sbin/palm-lap-device \
    /usr/local/sbin/palm-web-admin \
    /usr/local/sbin/palm-bluetooth-recover \
    /usr/local/sbin/palm-lap-controller-compat \
    /usr/local/sbin/palm-obex-compat \
    /usr/local/bin/palm-send-prc
rmdir /etc/systemd/user/obex.service.d 2>/dev/null || true
rm -rf /usr/local/share/doc/palm-lap

if [ "$PURGE" -eq 1 ]; then
    rm -rf /etc/palm-lap /var/lib/palm-web
    deluser palmweb >/dev/null 2>&1 || true
    delgroup palmweb >/dev/null 2>&1 || true
else
    echo "Preserved /etc/palm-lap and /var/lib/palm-web; pass --purge to remove them."
fi

systemctl daemon-reload
systemctl restart nftables.service dnsmasq.service 2>/dev/null || true
echo "Palm LAP runtime files removed. Restore host backups from /var/backups/palm-lap if needed."
