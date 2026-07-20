# Palm LAP gateway operator runbook

This Raspberry Pi Zero 2 W (`palm-lap-host`, `192.168.1.50`) provides Bluetooth LAN Access Profile (LAP) for Palm OS devices. It advertises Bluetooth UUID `0x1102`, carries PPP over RFCOMM channel 4, and routes authorized Palm traffic through `eth0` using nftables.

Last updated: 2026-07-19
Implementation version: 0.4.2 (passwordless LAN admin by user choice)

## Current status

- `palm-lap.service`, `bluetooth.service`, and `nftables.service` are enabled and running.
- IPv4 forwarding is enabled persistently.
- LAP is advertised as `LAN Access Using PPP`.
- The Bluetooth alias and LAP SDP service name are both `Palm LAP` so legacy discovery screens have a usable label.
- During pairing, the controller advertises Bluetooth class `0x420300`: Networking + Telephony services, LAN/Network Access Point device. This explicit compatibility hint is needed because current BlueZ does not map withdrawn LAP UUID `0x1102` to the Networking class bit itself.
- `palm-lap-compat.service` reapplies that legacy Networking hint after every
  boot, once BlueZ and the LAP profile have settled.
- Pairing and discovery are closed by default.
- Four Palms are bonded, trusted, and authorized: Zire 72 at `10.77.0.10`, E2
  at `10.77.0.11`, T3 at `10.77.0.12`, and TX at `10.77.0.13`; the Pi side and
  Palm-facing DNS server are `10.77.0.1`.
- Palm traffic can initiate connections to the LAN and Internet. NAT and firewall rules prevent unsolicited LAN-to-Palm traffic and prevent Palm access to services on the Pi itself, except gateway ICMP diagnostics.

Physical Palm testing has confirmed discovery, named SDP service, legacy PIN
pairing, bonding, authorization, RFCOMM, LCP, IPCP, DNS, LAN forwarding, public
Internet access, and browser access through the local DNS forwarder.

Post-reboot Palm TX testing also confirmed encrypted link-key authentication,
RFCOMM channel 4, live `plap13`, peer/DNS negotiation, and bidirectional IP
reachability after working around a BlueZ 5.82 external-profile authorization
stall. The configured-MAC check remains enforced before PPP starts.

## Onboard another Palm

### 1. Watch the service log

Open one SSH session:

```sh
sudo journalctl -fu palm-lap.service
```

For additional Bluetooth detail in another session:

```sh
sudo journalctl -fu bluetooth.service
```

### 2. Open a ten-minute pairing window

In another SSH session:

```sh
sudo palm-lap-pair open
```

Enter a temporary alphanumeric PIN when prompted. Use the same PIN on the Palm. The PIN is held only in `/run/palm-lap/pairing.json`, mode `0600`, and is deleted when the window closes. The adapter automatically becomes non-pairable and non-discoverable after ten minutes.

The command also applies classic-Palm discovery compatibility. While open, `btmgmt info` should show class `0x420300` and settings `connectable discoverable bondable`; `hciconfig hci0` should show `PSCAN ISCAN`.

Check or close the window manually:

```sh
sudo palm-lap-pair status
sudo palm-lap-pair close
```

### 3. Pair from the Palm

On the Palm, search for nearby Bluetooth devices and select `Palm LAP`. Exact menu labels differ by Palm model. If the Palm tries LAP immediately after bonding, the first attempt may be rejected because the new MAC is not yet in the allowlist; that is expected.

Find the bonded address on the Pi:

```sh
bluetoothctl devices Paired
bluetoothctl devices Bonded
```

You can also inspect a device:

```sh
bluetoothctl info AA:BB:CC:DD:EE:FF
```

### 4. Authorize the bonded Palm

Assign the first Palm `10.77.0.10`:

```sh
sudo palm-lap-device add AA:BB:CC:DD:EE:FF 10.77.0.10 --name "Palm model"
```

The command refuses unbonded devices, validates that the IP is unique and in the Palm subnet, marks the device trusted in BlueZ, validates the complete configuration, and restarts `palm-lap.service`.

List or remove authorizations:

```sh
sudo palm-lap-device list
sudo palm-lap-device remove AA:BB:CC:DD:EE:FF
```

Removal leaves the Bluetooth bond in place. To remove the bond too:

```sh
bluetoothctl remove AA:BB:CC:DD:EE:FF
```

### 5. Configure the Palm network connection

The intended Palm settings are:

- connection method: Bluetooth;
- target/service: `Palm LAP`;
- service/type: LAN, Local Network, or LAN Access Point—not phone/modem DUN;
- network protocol: PPP;
- IP address: automatic;
- DNS: query/automatic;
- username/password: empty unless that Palm build insists on values.

Retry the Palm connection after authorization. A successful connection creates `plap10` with local address `10.77.0.1` and peer address `10.77.0.10`.

The Pi supplies `10.77.0.1` as both IPCP DNS addresses. A current dnsmasq
instance provides DNS caching/forwarding only on loopback and `plap*`; DHCP is
disabled. It forwards upstream to the LAN router at `192.168.1.1`. This keeps
legacy Palm resolvers on their directly connected PPP subnet.

## Verify a live connection

On the Pi:

```sh
ip -brief address show plap10
ip route show dev plap10
sudo journalctl -u palm-lap.service -n 100 --no-pager
sudo nft list table inet palm_lap_filter
sudo nft list table ip palm_lap_nat
```

From the Palm, test in this order:

1. `10.77.0.1` — PPP gateway.
2. `192.168.1.1` — LAN router by IPv4 address.
3. Another known LAN IPv4 service.
4. A known public IPv4 address.
5. A DNS hostname.
6. A controlled plain-HTTP page.

This order distinguishes Bluetooth/PPP, forwarding, Internet routing, DNS, and application-protocol failures.

## Routine status

```sh
systemctl is-active palm-lap bluetooth nftables
systemctl is-enabled palm-lap bluetooth nftables
bluetoothctl show
sudo palm-lap-pair status
sudo palm-lap-device list
cat /proc/sys/net/ipv4/ip_forward
sudo nft list ruleset
```

Expected idle security state:

```text
Discoverable: no
Pairable: no
UUID: LAN Access Using PPP (00001102-0000-1000-8000-00805f9b34fb)
```

`sudo btmgmt info` should still include `connectable` while idle. This permits an already bonded/authorized Palm to reconnect without making the Pi discoverable or pairable.

## Configuration changes

The authoritative runtime configuration is `/etc/palm-lap/config.json`. Prefer `palm-lap-device` for device changes. For subnet, DNS, or RFCOMM-channel changes:

1. Back up the file.
2. Edit it as root.
3. Update `/etc/nftables.d/palm-lap.nft` if the subnet, local IP, interface, or uplink changes.
4. Validate and reload:

```sh
sudo /usr/local/libexec/palm-lapd --check
sudo nft -c -f /etc/nftables.conf
sudo systemctl restart nftables palm-lap
```

Never assign the same peer IP to two devices. Interface names are `plap` plus the last IP octet and must fit Linux's 15-character interface-name limit.

## Multiple Palms

Authorize each bonded device with a unique address. The current assignments are
`.10` Zire 72, `.11` E2, `.12` T3, and `.13` TX. The daemon supports simultaneous
sessions and launches one `pppd` per Bluetooth device; simultaneous multi-Palm
operation should still be validated before relying on it.

## Internet versus modern web access

Successful public-IP and DNS tests mean the Palm has Internet connectivity. Modern HTTPS sites may still fail because Palm OS browsers lack current TLS versions, certificate roots, ciphers, JavaScript, and HTML support. Do not change Bluetooth or PPP settings to solve an HTTPS compatibility problem.

If content translation is later added, make it a separate LAN service bound only to `10.77.0.0/24`, restrict its destinations, and never use it for credentials or sensitive sessions.

## Installing Palm applications over Bluetooth

The Pi also has a modern BlueZ Object Push sender for `.prc` and `.pdb` files.
See `PRC-TRANSFER.md` for commands, NetFront package notes, checksums, and
troubleshooting. Run `palm-send-prc` as `operator`, without `sudo`.

## Web file catalog and administration

The gateway provides a Palm-compatible catalog at `http://10.77.0.1:8080/` and
a LAN-only, passwordless admin interface at `http://192.168.1.50:8080/admin`.
See `WEB-INTERFACE.md` for uploads, browser installation, Bluetooth send jobs,
pairing/authorization, live Bluetooth diagnostics, tiered recovery controls,
security boundaries, and troubleshooting.

## Documentation map

- `README.md` — daily operation and first-device onboarding.
- `ARCHITECTURE.md` — design, trust boundaries, and code behavior.
- `TROUBLESHOOTING.md` — symptom-driven diagnosis.
- `INSTALL-MANIFEST.md` — installed files, packages, validation, and rollback.
- `CHANGELOG.md` — implementation history.
- `STRATEGY.md` — the original researched strategy.
- `src/`, `config/`, and `systemd/` in the cloned repository — reusable source
  and templates; runtime device configuration remains under `/etc/palm-lap`.
- `backups/` — pre-install configuration backups.
