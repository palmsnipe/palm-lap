#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
    echo "Run verification with sudo." >&2
    exit 1
fi

echo "Services"
for service in bluetooth palm-lap palm-web dnsmasq nftables; do
    printf '%-12s ' "$service"
    systemctl is-active "$service"
done

echo
echo "Configuration"
/usr/local/libexec/palm-lapd --check
dnsmasq --test
nft -c -f /etc/nftables.conf
printf 'ip_forward='
cat /proc/sys/net/ipv4/ip_forward

echo
echo "Bluetooth"
bluetoothctl show | grep -E 'Alias:|Powered:|Discoverable:|Pairable:|LAN Access'
btmgmt --index 0 info | grep -E 'class 0x|current settings'
palm-lap-pair status

echo
echo "Web"
curl -fsS http://127.0.0.1:8080/health
echo
