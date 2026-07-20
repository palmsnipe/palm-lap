#!/bin/sh
set -eu

REPO_ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
GATEWAY_NAME="Palm LAP"
PALM_SUBNET="10.77.0.0/24"
LOCAL_IP="10.77.0.1"
UPLINK=""
UPSTREAM_DNS=""
ADMIN_NETWORK=""
OPERATOR_USER="${SUDO_USER:-}"
INSTALL_PACKAGES=1
RECONFIGURE=0

usage() {
    cat <<'EOF'
Usage: sudo ./scripts/install.sh [options]

Options:
  --gateway-name NAME       Bluetooth/SDP/web name (default: Palm LAP)
  --operator-user USER      Account used for OBEX sends (default: invoking user)
  --uplink INTERFACE        LAN/Internet interface (default: interface with default route)
  --upstream-dns ADDRESS    DNS forwarder (default: default-route gateway)
  --admin-network CIDR      LAN allowed to use /admin (default: uplink IPv4 network)
  --palm-subnet CIDR        PPP network (default: 10.77.0.0/24)
  --local-ip ADDRESS        Pi PPP/DNS address (default: 10.77.0.1)
  --no-packages             Do not run apt-get
  --reconfigure             Replace existing /etc/palm-lap configuration
  -h, --help                Show this help
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --gateway-name) GATEWAY_NAME=$2; shift 2 ;;
        --operator-user) OPERATOR_USER=$2; shift 2 ;;
        --uplink) UPLINK=$2; shift 2 ;;
        --upstream-dns) UPSTREAM_DNS=$2; shift 2 ;;
        --admin-network) ADMIN_NETWORK=$2; shift 2 ;;
        --palm-subnet) PALM_SUBNET=$2; shift 2 ;;
        --local-ip) LOCAL_IP=$2; shift 2 ;;
        --no-packages) INSTALL_PACKAGES=0; shift ;;
        --reconfigure) RECONFIGURE=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this installer with sudo." >&2
    exit 1
fi

if [ -e /etc/palm-lap/config.json ] && [ "$RECONFIGURE" -ne 1 ]; then
    echo "Existing /etc/palm-lap/config.json found; refusing to replace the device allowlist." >&2
    echo "Use scripts/update.sh to update code, or pass --reconfigure intentionally." >&2
    exit 1
fi

if [ -z "$OPERATOR_USER" ]; then
    OPERATOR_USER=$(getent passwd 1000 | cut -d: -f1 || true)
fi
if [ -z "$OPERATOR_USER" ] || ! id "$OPERATOR_USER" >/dev/null 2>&1; then
    echo "A valid --operator-user is required." >&2
    exit 1
fi

if [ -z "$UPLINK" ]; then
    UPLINK=$(ip route show default | awk 'NR == 1 {for (i=1; i<=NF; i++) if ($i == "dev") {print $(i+1); exit}}')
fi
case "$UPLINK" in *[!A-Za-z0-9_.:-]*|'') echo "Invalid uplink interface." >&2; exit 1 ;; esac
ip link show "$UPLINK" >/dev/null 2>&1 || { echo "Uplink $UPLINK does not exist." >&2; exit 1; }

if [ -z "$UPSTREAM_DNS" ]; then
    UPSTREAM_DNS=$(ip route show default | awk 'NR == 1 {for (i=1; i<=NF; i++) if ($i == "via") {print $(i+1); exit}}')
fi
if [ -z "$UPSTREAM_DNS" ]; then
    echo "A valid --upstream-dns is required." >&2
    exit 1
fi

if [ -z "$ADMIN_NETWORK" ]; then
    UPLINK_CIDR=$(ip -o -4 address show dev "$UPLINK" scope global | awk 'NR == 1 {print $4}')
    if [ -z "$UPLINK_CIDR" ]; then
        echo "Cannot infer --admin-network from $UPLINK." >&2
        exit 1
    fi
    ADMIN_NETWORK=$(python3 -c 'import ipaddress,sys; print(ipaddress.ip_interface(sys.argv[1]).network)' "$UPLINK_CIDR")
fi

python3 - "$PALM_SUBNET" "$LOCAL_IP" "$UPSTREAM_DNS" "$ADMIN_NETWORK" "$GATEWAY_NAME" <<'PY'
import ipaddress
import sys

subnet = ipaddress.ip_network(sys.argv[1])
local = ipaddress.ip_address(sys.argv[2])
ipaddress.ip_address(sys.argv[3])
ipaddress.ip_network(sys.argv[4])
if local not in subnet:
    raise SystemExit("local IP must belong to Palm subnet")
name = sys.argv[5]
if not 1 <= len(name) <= 80 or any(ord(c) < 32 for c in name):
    raise SystemExit("gateway name must contain 1-80 printable characters")
PY

if [ "$INSTALL_PACKAGES" -eq 1 ]; then
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y \
        bluez bluez-obexd ppp dnsmasq nftables rfkill tcpdump \
        python3-dbus python3-gi python3-flask python3-waitress
fi

STAMP=$(date +%Y%m%d-%H%M%S)
BACKUP_DIR="/var/backups/palm-lap/$STAMP"
install -d -m 0700 "$BACKUP_DIR"
for path in \
    /etc/bluetooth/main.conf \
    /etc/nftables.conf \
    /etc/dnsmasq.d/palm-lap.conf \
    /etc/nftables.d/palm-lap.nft \
    /etc/palm-lap/config.json \
    /etc/palm-lap/web.json; do
    if [ -e "$path" ]; then
        cp -a "$path" "$BACKUP_DIR/$(echo "$path" | sed 's#^/##; s#/#__#g')"
    fi
done

install -d -m 0755 /usr/local/libexec /usr/local/sbin /usr/local/bin
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

install -d -m 0755 /etc/palm-lap /etc/dnsmasq.d /etc/nftables.d /etc/ppp/peers
install -o root -g root -m 0644 "$REPO_ROOT/config/main.conf" /etc/bluetooth/main.conf
install -o root -g root -m 0644 "$REPO_ROOT/config/palm-lap.ppp" /etc/ppp/peers/palm-lap
install -o root -g root -m 0644 "$REPO_ROOT/config/90-palm-lap-forwarding.conf" /etc/sysctl.d/90-palm-lap-forwarding.conf

python3 - "$GATEWAY_NAME" "$PALM_SUBNET" "$LOCAL_IP" > /etc/palm-lap/config.json <<'PY'
import json
import sys
print(json.dumps({
    "devices": {},
    "dns": [sys.argv[3]],
    "gateway_name": sys.argv[1],
    "local_ip": sys.argv[3],
    "rfcomm_channel": 4,
    "subnet": sys.argv[2],
}, indent=2, sort_keys=True))
PY
chmod 0600 /etc/palm-lap/config.json

python3 - "$GATEWAY_NAME" "$LOCAL_IP" "$OPERATOR_USER" "$ADMIN_NETWORK" > /etc/palm-lap/web.json <<'PY'
import json
import sys
print(json.dumps({
    "admin_networks": [sys.argv[4], "127.0.0.0/8"],
    "gateway_ip": sys.argv[2],
    "gateway_name": sys.argv[1],
    "max_upload_bytes": 32 * 1024 * 1024,
    "operator_user": sys.argv[3],
}, indent=2, sort_keys=True))
PY

sed \
    -e "s|@UPSTREAM_DNS@|$UPSTREAM_DNS|g" \
    "$REPO_ROOT/config/palm-lap.dnsmasq.in" > /etc/dnsmasq.d/palm-lap.conf
sed \
    -e "s|@SUBNET@|$PALM_SUBNET|g" \
    -e "s|@LOCAL_IP@|$LOCAL_IP|g" \
    -e "s|@UPLINK@|$UPLINK|g" \
    "$REPO_ROOT/config/palm-lap.nft.in" > /etc/nftables.d/palm-lap.nft
chmod 0644 /etc/dnsmasq.d/palm-lap.conf /etc/nftables.d/palm-lap.nft

if ! grep -Fq 'include "/etc/nftables.d/*.nft"' /etc/nftables.conf; then
    printf '\ninclude "/etc/nftables.d/*.nft"\n' >> /etc/nftables.conf
fi

if ! getent group palmweb >/dev/null; then
    addgroup --system palmweb
fi
if ! id palmweb >/dev/null 2>&1; then
    adduser --system --ingroup palmweb --home /var/lib/palm-web --no-create-home \
        --shell /usr/sbin/nologin palmweb
fi
install -d -o palmweb -g palmweb -m 0711 /var/lib/palm-web
install -d -o palmweb -g palmweb -m 0755 /var/lib/palm-web/files
chown root:palmweb /etc/palm-lap/web.json
chmod 0640 /etc/palm-lap/web.json

install -o root -g root -m 0440 "$REPO_ROOT/config/palm-web.sudoers" /etc/sudoers.d/palm-web
visudo -cf /etc/sudoers.d/palm-web >/dev/null

for unit in "$REPO_ROOT"/systemd/*; do
    install -o root -g root -m 0644 "$unit" "/etc/systemd/system/$(basename "$unit")"
done

sysctl --system >/dev/null
dnsmasq --test
/usr/local/libexec/palm-lapd --check
nft -c -f /etc/nftables.conf
systemd-analyze verify \
    /etc/systemd/system/palm-lap.service \
    /etc/systemd/system/palm-web.service \
    /etc/systemd/system/palm-bluetooth-recover.service \
    /etc/systemd/system/palm-lap-compat.service

loginctl enable-linger "$OPERATOR_USER" >/dev/null 2>&1 || true
OPERATOR_UID=$(id -u "$OPERATOR_USER")
systemctl start "user@$OPERATOR_UID.service" >/dev/null 2>&1 || true
runuser -u "$OPERATOR_USER" -- env \
    XDG_RUNTIME_DIR="/run/user/$OPERATOR_UID" \
    DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$OPERATOR_UID/bus" \
    systemctl --user start obex.service >/dev/null 2>&1 || true

systemctl daemon-reload
systemctl enable bluetooth.service nftables.service dnsmasq.service \
    palm-lap.service palm-web.service palm-lap-compat.service
systemctl restart bluetooth.service
systemctl restart nftables.service dnsmasq.service
systemctl start palm-lap.service palm-web.service palm-lap-compat.service

"$REPO_ROOT/scripts/verify.sh"

echo
echo "Palm LAP installed. Backup: $BACKUP_DIR"
echo "Admin network: $ADMIN_NETWORK"
echo "Open pairing with: sudo palm-lap-pair open"
echo "Then authorize a bond with: sudo palm-lap-device add MAC PEER_IP --name NAME"
