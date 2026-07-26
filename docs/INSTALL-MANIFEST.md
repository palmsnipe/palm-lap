# Reference installation manifest and rollback

Installed on 2026-07-15 on `palm-lap-host` by SSH as `operator` with passwordless sudo.

## Platform observed

- Debian 13.4 (`trixie`), arm64
- Linux `6.12.75+rpt-rpi-v8`
- BlueZ `5.82-1.1+rpt1`
- pppd `2.5.2-1+1`
- nftables `1.1.3-1`
- adapter `XX:XX:XX:XX:XX:XX` (host-specific value omitted)
- uplink `eth0`, Pi `192.168.1.50/24`, gateway/DNS `192.168.1.1`

## Package changes

Installed from Debian 13 repositories:

- `python3-dbus` `1.4.0-1`
- `python3-gi` `3.50.0-4+b1`
- `bluez-obexd` `5.82-1.1+rpt1`
- `dnsmasq` `2.91-1+deb13u1` (DNS only; DHCP disabled)
- `tcpdump` `4.99.5-2` (diagnostics)
- `python3-flask` `3.1.1-1`
- `python3-waitress` `3.0.2-1`
- their required GI/GLib dependencies; `libglib2.0` packages were updated from Debian's then-current `deb13u2` to `deb13u3` security/update revision.

No old BlueZ version, `bluez-tools`, iptables compatibility package, or HTTP
content proxy was installed. Current dnsmasq is installed solely as the
Palm-facing DNS forwarder; its DHCP functions are disabled.

## Installed runtime files

```text
/usr/local/libexec/palm-lapd
/usr/local/sbin/palm-lap-pair
/usr/local/sbin/palm-lap-device
/usr/local/bin/palm-send-prc
/usr/local/libexec/palm_web.py
/usr/local/libexec/palm-obex-inbox
/usr/local/sbin/palm-web-admin
/usr/local/sbin/palm-bluetooth-recover
/usr/local/sbin/palm-lap-controller-compat
/etc/palm-lap/config.json
/etc/dnsmasq.d/palm-lap.conf
/etc/palm-lap/web.json
/etc/systemd/system/palm-web.service
/etc/systemd/system/palm-bluetooth-recover.service
/etc/systemd/system/palm-lap-compat.service
/etc/systemd/user/palm-obex-inbox.service
/etc/sudoers.d/palm-web
/etc/bluetooth/main.conf
/etc/ppp/peers/palm-lap
/etc/nftables.d/palm-lap.nft
/etc/nftables.conf
/etc/sysctl.d/90-palm-lap-forwarding.conf
/etc/systemd/system/palm-lap.service
/etc/systemd/system/palm-lap-pairing-close.service
/etc/systemd/system/palm-lap-pairing-close.timer
```

Persistent state created by BlueZ for paired devices remains under `/var/lib/bluetooth` and is not managed by the allowlist helper.

Web runtime state and managed downloads are under `/var/lib/palm-web/`. The
dedicated system account is `palmweb` with nologin shell. Administration is
passwordless by user choice and restricted by client network plus CSRF.
The state directory is `0711` (not listable by other accounts); its public-file
child is `0755` with individual managed files `0644`, permitting the validated
OBEX sender path running as `operator` to read known files. Job state remains `0640`.
The separate Bluetooth inbox is a setgid `operator:palmweb` directory at
`/var/lib/palm-web/inbox`; completed payloads are `0640` and transfer metadata
is stored in hidden `0660` JSON sidecars.

## Home-folder project

```text
/home/operator/palm-lap/
  README.md
  ARCHITECTURE.md
  TROUBLESHOOTING.md
  INSTALL-MANIFEST.md
  CHANGELOG.md
  STRATEGY.md
  PRC-TRANSFER.md
  WEB-INTERFACE.md
  files -> /var/lib/palm-web/files
  LAST_BACKUP
  repository src/, config/, and systemd/ directories
  backups/20260715-205641/nftables.conf.original
  backups/20260715-205641/bluetooth-main.conf.original
```

The cloned repository is the human-editable implementation source. Installed
files are separate copies. `/etc/palm-lap/web.json` contains network and upload
policy but no credential or password hash.

## Service changes

- Enabled and started `nftables.service` (it was disabled/inactive before installation).
- Enabled and started new `palm-lap.service`.
- Left `palm-lap-pairing-close.timer` disabled normally; each pairing window starts it transiently.
- Enabled persistent `net.ipv4.ip_forward=1` (it was `0`).
- Replaced the original minimal `/etc/nftables.conf` with an equivalent base table plus `/etc/nftables.d/*.nft` include.
- Set BlueZ major/minor device class to LAN/Network Access Point (`0x000300`) in `/etc/bluetooth/main.conf`; pairing adds the missing Networking service hint for LAP dynamically.
- Enabled and started the per-user `obex.service` for modern Bluetooth Object Push.
- Installed and enabled the per-user `palm-obex-inbox.service`, with its
  `/usr/local/libexec/palm-obex-inbox` Agent1 receiver and separate
  `operator:palmweb` setgid inbox at `/var/lib/palm-web/inbox`.
- Enabled and started `dnsmasq.service` as DNS-only forwarding on loopback and
  dynamic `plap*` interfaces; IPCP advertises `10.77.0.1` as DNS.
- Created `palmweb`, enabled `palm-web.service`, and installed its single-command
  sudo policy after validation with `visudo`.

## Validation performed before physical Palm testing

- Python byte-code compilation for all scripts.
- JSON configuration parse and semantic validation.
- `pppd ... dryrun` of the peer options and pilot addresses.
- `nft -c -f /etc/nftables.conf` syntax validation.
- `systemd-analyze verify` of all new units. It reported only unrelated existing `resilio-sync.service` warnings.
- Live LAP registration; `bluetoothctl show` displays UUID `0x1102`.
- Pairing open/status/manual-close behavior and timer scheduling.
- Pairing state directory mode `0700`; state file is root-only while present.
- Adapter non-pairable/non-discoverable idle state.
- BlueZ restart propagation: `palm-lap.service` restarted with a new PID, re-registered LAP, and restored the closed adapter state.
- nftables tables loaded and IPv4 forwarding set to `1`.
- Full Pi reboot at 2026-07-15 21:02 EDT: all three services returned enabled/active, IPv4 forwarding returned as `1`, nftables tables loaded, LAP UUID returned, and the adapter remained non-pairable/non-discoverable.
- Discovery-failure diagnosis on 2026-07-15: confirmed inquiry was active but the on-air class was Telephony/Miscellaneous `0x400000`; corrected to Networking+Telephony/LAN Access `0x420300`, verified `PSCAN ISCAN` while pairing is open, and retained `PSCAN`/connectability after pairing closes.

At initial installation, all physical-device tests were pending.

Update after first Zire test: remote discovery, named SDP, legacy PIN, bond/trust, RFCOMM, LCP/IPCP, DNS IPCP options, live PPP, and Pi-to-Palm ping are now confirmed. Remaining evidence is Palm-initiated LAN/Internet traffic, DNS lookup from a Palm application, and application compatibility.

Final physical updates: WebPro was sent successfully over OBEX; Palm DNS and
plain-HTTP browsing work through the local DNS forwarder. The web service was
validated for catalog/attachment delivery, authentication, upload/hash
preservation, delete, pairing open/close, CSRF enforcement, LAN/Palm admin
separation, traversal rejection, and narrow-sudo denial of other commands.

## Rollback

The following returns this host to its observed pre-install networking behavior. Review before execution if other routing/firewall work has since been added.

```sh
sudo palm-lap-pair close
systemctl --user disable --now palm-obex-inbox.service
sudo systemctl disable --now palm-web.service
sudo systemctl disable --now palm-lap.service
sudo systemctl disable --now dnsmasq.service
sudo systemctl disable --now nftables.service
sudo rm -f /etc/systemd/system/palm-lap.service
sudo rm -f /etc/systemd/system/palm-lap-pairing-close.service
sudo rm -f /etc/systemd/system/palm-lap-pairing-close.timer
sudo rm -f /etc/systemd/system/palm-web.service
sudo rm -f /etc/systemd/system/palm-bluetooth-recover.service
sudo rm -f /etc/systemd/system/palm-lap-compat.service
sudo rm -f /etc/systemd/user/palm-obex-inbox.service
sudo rm -f /etc/sudoers.d/palm-web
sudo rm -f /usr/local/libexec/palm-lapd
sudo rm -f /usr/local/sbin/palm-lap-pair
sudo rm -f /usr/local/sbin/palm-lap-device
sudo rm -f /usr/local/sbin/palm-web-admin
sudo rm -f /usr/local/sbin/palm-bluetooth-recover
sudo rm -f /usr/local/sbin/palm-lap-controller-compat
sudo rm -f /usr/local/libexec/palm_web.py
sudo rm -f /usr/local/libexec/palm-obex-inbox
sudo rm -f /usr/local/bin/palm-send-prc
sudo rm -rf /etc/palm-lap
sudo rm -f /etc/dnsmasq.d/palm-lap.conf
sudo rm -f /etc/ppp/peers/palm-lap
sudo rm -f /etc/nftables.d/palm-lap.nft
sudo rm -f /etc/sysctl.d/90-palm-lap-forwarding.conf
sudo cp /home/operator/palm-lap/backups/20260715-205641/nftables.conf.original /etc/nftables.conf
sudo cp /home/operator/palm-lap/backups/20260715-205641/bluetooth-main.conf.original /etc/bluetooth/main.conf
sudo /usr/sbin/sysctl -w net.ipv4.ip_forward=0
sudo nft -f /etc/nftables.conf
sudo systemctl daemon-reload
sudo systemctl restart bluetooth.service
```

After confirming that no data is needed, `/var/lib/palm-web`, the `palmweb`
account/group, and `/home/operator/palm-lap/files` symlink may be removed. Package
removal is deliberately not automated: Python D-Bus/GI, Flask, Waitress,
dnsmasq, and tcpdump may gain other consumers. Bluetooth bonds must be removed
separately with `bluetoothctl remove MAC` if desired.
