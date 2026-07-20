# Sending Palm applications from Palm LAP

The Raspberry Pi can install `.prc` applications and send `.pdb` databases to
the paired Palm with Bluetooth Object Push (OBEX). On the tested Zire 72, first
disconnect the `Palm LAP` LAP/PPP network session: its older Bluetooth stack does
not expose Object Push while LAP is connected.

## Normal use

Run the sender as the regular `operator` user, **not with sudo**:

```sh
palm-send-prc --device AA:BB:CC:DD:EE:FF /path/to/application.prc
```

The Palm should display an incoming-item confirmation. Accept it and leave the
Palm awake until the script reports `complete`. Several files may be supplied;
they are sent sequentially:

```sh
palm-send-prc --device AA:BB:CC:DD:EE:FF first.prc second.prc
```

Always select the paired Palm explicitly:

```sh
palm-send-prc --device AA:BB:CC:DD:EE:FF application.prc
```

## NetFront 3.1

PalmDB's current NetFront page says that UDMH must be installed and enabled.
Install `UDMH_5_3.prc` first, open UDMH on the Palm, configure/enable it for
NetFront, and only then install `NetFront3.prc`.

### One-file installer without UDMH

PalmDB also lists `netfrontInstallerV2.prc` under version 3.0. Binary inspection
shows that it actually contains NetFront 3.1 and embeds `MaxX`, so it does not
need UDMH as a separate installation. It is the preferred first test when UDMH
is not wanted:

```sh
palm-send-prc --device AA:BB:CC:DD:EE:FF /path/to/netfrontInstallerV2.prc
```

The installer may install/activate its bundled MaxX memory extension. This is
different from avoiding memory extensions altogether. Do not later enable UDMH
alongside MaxX without first checking/removing the older extension.

The downloaded NetFront ZIP also contains `MaxX.prc`, an older memory extension.
It is retained in the archive but is not installed by the documented procedure.
Do not enable MaxX and UDMH together without device-specific research.

Files retained on the Pi:

```text
/home/operator/palm-lap/packages/netfront31/netfront31.zip
/home/operator/palm-lap/packages/netfront31/extracted/NetFront3.prc
/home/operator/palm-lap/packages/netfront31/extracted/MaxX.prc
/home/operator/palm-lap/packages/netfront30/netfrontInstallerV2.prc
```

Suggested commands:

```sh
palm-send-prc --device AA:BB:CC:DD:EE:FF /path/to/UDMH_5_3.prc
# Enable UDMH for NetFront on the Palm, then:
palm-send-prc --device AA:BB:CC:DD:EE:FF /path/to/NetFront3.prc
```

Recorded SHA-256 checksums:

```text
f710050ec06666adf26b3a1f66d3cc09d141d974a85ed0158a81a3bed9740f31  netfrontInstallerV2.prc
c0ed3a13e20c6b982a3b28c569ef4c4e6ba85d90a919ea05da3670cc3cf63d4e  netfront31.zip
```

## Zire 72 WebPro 3.5

The official Zire 72 manual-install package supplied by the device media was
copied from the Mac and preserved on the Pi:

```sh
palm-send-prc --device AA:BB:CC:DD:EE:FF /path/to/WebProV.prc
```

```text
e61a6e792a3b0a5f6c8192da210e6604c38274218eb98efe9ea12670d82f3f58  WebProV.prc
1610259 bytes
```

Prefer this device-specific browser over the modified NetFront package for the
first Internet test. It does not add NetFront's MaxX/UDMH heap-manager question.

Sources:

- <https://palmdb.net/app/netfront>
- <https://palmdb.net/app/udmh>
- BlueZ 5.82 D-Bus APIs: `org.bluez.obex.Client1`,
  `org.bluez.obex.ObjectPush1`, and `org.bluez.obex.Transfer1`

## Troubleshooting

- Keep Bluetooth on and the Palm awake. Disconnect the Zire 72's LAP connection
  before sending; physical testing confirmed that Object Push appears only after
  LAP is disconnected.
- `Error: ... Connection refused` usually means the Palm is not accepting Object
  Push. Open the Palm's Bluetooth preferences, ensure Bluetooth is on, and retry.
- `Unable to find service record` means the Palm did not advertise Object Push.
  Disconnect LAP, enable Bluetooth discovery, leave the Palm awake, and retry.
  The Zire's standalone SDP listing can be empty even when the subsequent OBEX
  connection succeeds, so the sender itself is the authoritative test.
- If Object Push remains unavailable, toggle the Zire's Bluetooth radio Off and
  On, set Discoverable to Yes, and check that Power Preferences → Beam Receive
  is On before retrying.
- If every RFCOMM channel is refused, perform a non-destructive soft reset. On
  the tested Zire 72 this restored `OBEX Object Push` on RFCOMM channel 1.

## Physical validation

On 2026-07-15, `WebProV.prc` was transferred end to end to the Zire 72. BlueZ
reported final status `complete`, and the sender exited successfully. The
working sequence was: disconnect LAP, soft-reset the stuck Palm Bluetooth
service, enable Bluetooth/discovery, refresh discovery from the Pi, then send.
- `timed out waiting` means the confirmation was not accepted within five minutes.
- The sender runs in the user's D-Bus session. Do not invoke it with `sudo`.
- Service diagnostics:

  ```sh
  systemctl --user status obex.service
  journalctl --user -u obex.service --since '10 minutes ago'
  ```
