# Troubleshooting

Always diagnose from Bluetooth discovery toward the application layer. A later-layer failure does not imply LAP is broken.

## Baseline snapshot

```sh
date
systemctl --no-pager --full status palm-lap bluetooth nftables
bluetoothctl show
sudo palm-lap-pair status
sudo palm-lap-device list
ip -brief address
ip route
cat /proc/sys/net/ipv4/ip_forward
sudo nft list ruleset
sudo journalctl -u palm-lap.service -b --no-pager
sudo journalctl -u bluetooth.service -b --no-pager
```

## Bluetooth controller is powered off or `hci0` is down

This is below the LAP/PPP layer. Typical evidence is `Powered: no`, `hci0:
DOWN`, and kernel messages such as `hardware error 0x00` or `Opcode 0x0c03
failed: -110`.

```sh
bluetoothctl show
sudo hciconfig hci0
sudo rfkill list bluetooth
sudo journalctl -k -b --no-pager | grep -E 'Bluetooth|hci0'
```

If Bluetooth is not RF-killed, first try the ordinary recovery path:

```sh
bluetoothctl power on
sudo systemctl restart bluetooth.service
```

If the controller remains down after a hardware timeout, reset only the Pi's
Bluetooth UART. First verify that the device and driver paths match this Pi:

```sh
readlink -f /sys/class/bluetooth/hci0/device/driver
ls /sys/bus/serial/drivers/hci_uart_bcm/
```

On `palm-lap-host` the device is `serial0-0` and the driver is `hci_uart_bcm`:

```sh
sudo systemctl stop palm-lap.service bluetooth.service
printf serial0-0 | sudo tee /sys/bus/serial/drivers/hci_uart_bcm/unbind
sleep 2
printf serial0-0 | sudo tee /sys/bus/serial/drivers/hci_uart_bcm/bind
sleep 4
sudo systemctl start bluetooth.service
bluetoothctl power on
sudo btmgmt --index 0 class 3 0
sudo btmgmt --index 0 add-uuid 00001102-0000-1000-8000-00805f9b34fb 2
sudo systemctl start palm-lap.service
```

Do not copy those unbind/bind paths to a different Pi without checking them.
Validate recovery with:

```sh
bluetoothctl show | grep -E 'Powered:|LAN Access|Discoverable:|Pairable:'
sudo hciconfig hci0 | grep -E 'UP|PSCAN|ISCAN'
systemctl is-active bluetooth palm-lap
```

An idle, healthy gateway is powered and connectable (`UP RUNNING PSCAN`) but
not discoverable or pairable. Existing bonds and device authorizations survive
this reset, so a previously configured Palm should not need re-pairing.

## Palm cannot find `Palm LAP`

- Confirm a pairing window is open: `sudo palm-lap-pair status`.
- Confirm `Powered: yes`, `Discoverable: yes`, and `Pairable: yes` in `bluetoothctl show`.
- Confirm the classic discovery class and scan flags:

```sh
sudo btmgmt info | grep -E 'class 0x|current settings'
sudo hciconfig hci0 | grep -E 'UP|PSCAN|ISCAN'
```

While the window is open, expect class `0x420300`, `connectable discoverable bondable`, and `PSCAN ISCAN`. If the class is `0x400000` or the device is `Miscellaneous`, the Palm may filter it from LAN access-point searches; close and reopen the window with the current helper.
- The window lasts ten minutes; reopen it if necessary.
- Keep the Palm close to the Pi during initial pairing.
- If BlueZ is unhealthy, restart it; `palm-lap.service` is configured as `PartOf=bluetooth.service` and will restart with it:

```sh
sudo systemctl restart bluetooth.service
```

Afterward, verify LAP returned and the controller closed:

```sh
bluetoothctl show | grep -E 'LAN Access|Discoverable:|Pairable:'
```

Then run `sudo palm-lap-pair open` again; the Networking class hint is intentionally applied when a pairing window opens.

## Palm pairs but reports that the service/connection failed

The Palm may have tried LAP immediately after bonding, before its MAC was authorized. This is expected on first onboarding.

```sh
bluetoothctl devices Paired
sudo palm-lap-device add AA:BB:CC:DD:EE:FF 10.77.0.10 --name "Palm model"
```

Then retry from the Palm. Check for `Authorized LAP service connection` and `Started pppd` in the journal.

If there is no LAP request, verify that the Palm connection is configured for LAN/Local Network rather than phone or modem. Some devices may support only DUN or PAN; capture the exact UI and service-discovery behavior before adding another profile.

## LAP UUID is absent

```sh
systemctl is-active palm-lap.service
sudo journalctl -u palm-lap.service -n 100 --no-pager
bluetoothctl show | grep 'LAN Access'
```

Validate and restart:

```sh
sudo /usr/local/libexec/palm-lapd --check
sudo systemctl restart palm-lap.service
```

The old `sdptool browse local` diagnostic is not useful on this BlueZ 5 installation because the legacy local SDP socket is absent. `bluetoothctl show` and remote discovery are the relevant checks.

## RFCOMM connects but no PPP interface appears

Look for the pppd start and exit status:

```sh
sudo journalctl -u palm-lap.service -n 150 --no-pager
```

Check PPP prerequisites and option syntax:

```sh
ls -l /dev/ppp
lsmod | grep -E 'ppp|rfcomm'
sudo /usr/sbin/pppd call palm-lap 10.77.0.1:10.77.0.10 ifname plap10 dryrun
```

Repeated LCP requests with no reply generally indicate that the Palm did not start PPP, selected a modem/DUN workflow, or expects a pre-PPP script. LCP succeeds but IPCP fails when IP/DNS negotiation is incompatible. Preserve the journal before changing options.

If BlueZ shows the Palm as connected but there is no `NewConnection`, no pppd,
and it logs `ext_io_disconnected ... Transport endpoint is not connected`,
capture with `btmon`. The Palm TX case showed correct SDP/RFCOMM channel 4 and
successful link-key authentication/encryption, followed by a BlueZ 5.82 stall
in external-profile service authorization. Version 0.4.2 leaves authentication
enabled but relies on the daemon's mandatory `NewConnection` MAC allowlist
instead of that redundant BlueZ prompt.

## PPP is up but the LAN is unreachable

```sh
ip -brief address show plap10
ip route show dev plap10
cat /proc/sys/net/ipv4/ip_forward
sudo nft list table inet palm_lap_filter
```

Rule counters should increase. Confirm the uplink is still `eth0`; if it changes, update the nftables file and documentation.

Validate before reloading:

```sh
sudo nft -c -f /etc/nftables.conf
sudo systemctl restart nftables.service
```

## LAN works but public IPv4 does not

Inspect NAT counters:

```sh
sudo nft list table ip palm_lap_nat
ip route show default
```

The masquerade counter should increase for Palm traffic leaving `eth0`. Confirm the Pi itself can reach the Internet before investigating Palm-specific behavior.

## Public IPv4 works but hostnames do not

IPCP advertises the directly connected resolver `10.77.0.1`. A DNS-only
dnsmasq instance listens on loopback and `plap*`, then forwards requests to the
LAN router at `192.168.1.1`.

```sh
systemctl is-active dnsmasq
sudo ss -lntup | grep ':53'
sudo journalctl -u dnsmasq.service -b --no-pager
sudo nft list table inet palm_lap_filter
```

If DNS settings change, update `/etc/palm-lap/config.json` and the dnsmasq
configuration together, validate both services, then restart them.

## DNS works but websites fail

Test a controlled plain-HTTP endpoint. Modern HTTPS failures are expected with Palm OS TLS/certificate limitations and are outside Bluetooth, PPP, routing, and DNS. Do not weaken LAN-wide security or transparently downgrade arbitrary HTTPS.

## Capture guidance

`btmon` is included with BlueZ and can capture low-level Bluetooth negotiation:

```sh
sudo btmon
```

Run captures only during a short controlled test. Bluetooth and packet captures can contain device identifiers or application data; store and share them accordingly. `tcpdump` is installed; scope it to `plap10` (or the active `plap*` interface) or the Palm addresses rather than capturing the whole LAN.
