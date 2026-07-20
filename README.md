# Palm OS Bluetooth LAP gateway

`palm-lap` turns a current Raspberry Pi OS/Debian system with a classic
Bluetooth adapter into a Palm OS 5 LAN Access Profile gateway. It provides:

- withdrawn Bluetooth LAP UUID `0x1102` on RFCOMM channel 4 using current
  BlueZ `Profile1`/`Agent1` APIs;
- one PPP interface and fixed peer address per authorized Palm;
- nftables forwarding and IPv4 NAT to a configurable LAN uplink;
- a Palm-compatible DNS forwarder;
- a legacy-PIN pairing window that is closed by default;
- a LAN-only web interface for pairing, authorization, multi-file drag/drop
  uploads, PRC/PDB downloads, OBEX sends, diagnostics, and Bluetooth recovery;
- Raspberry Pi Zero 2 W UART recovery for the observed BCM HCI timeout.

The implementation was physically validated with Palm OS 5 devices including
Zire 72, Tungsten T3/E2, and Palm TX. It intentionally uses current Debian 13,
BlueZ 5.82, pppd 2.5, nftables, dnsmasq, systemd, Flask, and Waitress rather
than retired BlueZ daemons or legacy iptables tooling.

## Supported target

The reference platform is a Raspberry Pi Zero 2 W running current Raspberry Pi
OS or Debian with its LAN uplink on Ethernet or a USB network adapter. The LAP,
PPP, web, DNS, and firewall components are generally reusable on other Linux
Bluetooth hosts. The deep UART-reset action validates Pi-specific
`serial0-0`/`hci_uart_bcm` paths and refuses to run elsewhere.

## Install

Review the installer and use a dedicated host. It backs up replaced host files
under `/var/backups/palm-lap/` before changing them.

```sh
git clone git@github.com:palmsnipe/palm-lap.git
cd palm-lap
sudo ./scripts/install.sh \
  --gateway-name "Palm LAP" \
  --operator-user "$USER" \
  --uplink eth0 \
  --upstream-dns 192.168.1.1 \
  --admin-network 192.168.1.0/24
```

The Palm PPP defaults are `10.77.0.1` for the Pi and `10.77.0.0/24` for peers.
Use `--help` to change them or let the installer infer the uplink, upstream DNS,
and admin network from the default route.

The installer leaves new-device pairing closed. Onboard a Palm with:

```sh
sudo palm-lap-pair open
# Pair from the Palm with the same temporary PIN, then find its address:
bluetoothctl devices Paired
sudo palm-lap-device add AA:BB:CC:DD:EE:FF 10.77.0.10 --name "Palm model"
sudo palm-lap-pair close
```

Configure the Palm connection as Bluetooth LAN/Local Network with automatic IP
and DNS. Username and password are normally empty.

## Verify

```sh
sudo ./scripts/verify.sh
```

The Palm file catalog is served at `http://10.77.0.1:8080/`. The passwordless
administration page is `http://PI_LAN_ADDRESS:8080/admin` and is available only
from the configured trusted LAN or loopback.

## Security model

- Bluetooth link-key authentication remains required.
- `NewConnection` checks every MAC against `/etc/palm-lap/config.json` before
  owning the RFCOMM descriptor or starting PPP.
- Palm interfaces can reach DNS, the file catalog, diagnostic ICMP, and routed
  LAN/Internet destinations, but not other services on the Pi.
- The web process runs as the unprivileged `palmweb` account and can invoke only
  one validating root helper through sudo.
- `/admin` deliberately has no password. Never expose TCP 8080 to the Internet
  or an untrusted/guest LAN.
- Bluetooth keys, temporary PIN state, device allowlists, uploaded Palm files,
  job history, captures, and backups are runtime data and are excluded here.

See [Architecture](docs/ARCHITECTURE.md), [Operations](docs/OPERATIONS.md),
[Troubleshooting](docs/TROUBLESHOOTING.md), and
[Web interface](docs/WEB-INTERFACE.md) for the complete design and runbook.

## Repository layout

```text
src/       gateway, pairing, web, OBEX, and recovery programs
systemd/   persistent and recovery units
config/    BlueZ, PPP, dnsmasq, nftables, sysctl, sudoers, and JSON examples
scripts/   installer, verification, and uninstall workflows
docs/      architecture, operations, troubleshooting, and implementation history
```

`scripts/uninstall.sh` removes runtime files while preserving configuration and
uploaded files. Pass `--purge` only when those should also be deleted. Host files
replaced during installation must be restored deliberately from the backup.

For an existing installation, use `sudo ./scripts/update.sh`. It updates code,
units, and documentation while preserving `/etc/palm-lap`, uploaded files,
bonds, and web job state. The installer refuses to overwrite an existing device
allowlist unless `--reconfigure` is supplied explicitly.
