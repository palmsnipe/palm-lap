# Palm LAP web interface

## Addresses

- Palm file catalog: <http://10.77.0.1:8080/>
- Mac/LAN file catalog: <http://192.168.1.50:8080/>
- Mac/LAN administration: <http://192.168.1.50:8080/admin> (no password)

The Palm catalog deliberately uses small HTML 4 markup without JavaScript so
WebPro and other Palm OS 5 browsers can render it. Selecting a `.prc` or `.pdb`
returns `application/vnd.palm` with an attachment filename, allowing the Palm's
Exchange Manager/browser to install or save it.

The administration interface is available without a password only when the
client address belongs to `192.168.1.0/24` or loopback. Requests from the Palm
PPP subnet receive a 404 for every `/admin` route. Per-process CSRF tokens
protect state-changing forms.

Administration uses plain HTTP because Palm browsers cannot use modern TLS and
the service shares one compatibility endpoint. Because there is deliberately no
password, every device on the trusted LAN can administer the gateway. Never
port-forward TCP 8080 or expose it to a guest/untrusted Wi-Fi network. A future
improvement can place the LAN admin UI behind a separate HTTPS reverse proxy
while retaining Palm HTTP on `10.77.0.1`.

## File workflow

1. Open `/admin` from the Mac.
2. Select one or more `.prc`/`.pdb` files, or drag them onto the upload area.
   The default limits are 50 files and 32 MiB for the complete request. The
   server stages and validates the whole batch before publishing any file, then
   stores it in `/var/lib/palm-web/files` with mode `0644`.

On iPhone and iPad, use the native file picker and select the package from the
Files app. The picker is intentionally not given an HTML `accept` filter because
iOS may hide uncommon, unregistered Palm extensions such as `.prc` and `.pdb`.
The server still enforces the extension, request limits, normalized filename,
and Palm database header for every file.
3. Either:
   - connect the Palm to `Palm LAP`, open `http://10.77.0.1:8080/`, and select the
     file; or
   - select a paired Palm and file under **Send by Bluetooth**.
4. Delete files no longer wanted from the managed-files table. Deletion is
   authenticated and CSRF-protected.

Bluetooth sending automatically disconnects the selected device's active base
connection because the tested Zire 72 does not expose Object Push while LAP is
connected. The Palm must be awake, Bluetooth must be on, and Object Push must be
available. The job page refreshes every three seconds and shows the exact
`palm-send-prc` progress/output. Jobs are serialized because there is one radio.
Job history survives web-service restarts; an in-flight job is marked
`interrupted` if the service itself restarts.

The home-folder convenience path `/home/operator/palm-lap/files` is a symlink to
the managed store. Do not replace it with an untrusted or network-mounted path.
The state directory is mode `0711` (traversable but not listable by other
accounts); its `files/` child is mode `0755` so the existing OBEX sender running
as `operator` can reach a validated known filename. Managed files are already
intentionally public through HTTP. Job metadata remains mode `0640` and is not
readable by `operator` or other accounts.

## Pairing and authorization workflow

1. Enter a temporary alphanumeric PIN (1–16 characters) and open the ten-minute
   pairing window from `/admin`.
2. On the Palm, discover `Palm LAP` and enter the same PIN.
3. Reload `/admin`; the bonded device appears in the paired-device table.
4. Select it under **Authorize bonded Palm for LAP**, assign a unique address in
   `10.77.0.0/24` (for example `10.77.0.11`), add a descriptive name, and submit.
5. Close pairing early when finished. It also closes automatically after ten
   minutes.

Authorization validates the bond, address, subnet, and uniqueness using the
same `palm-lap-device` helper as the command line. Runtime and editable source
configuration are deliberately separate. Web changes update only
`/etc/palm-lap/config.json`; they never alter the cloned repository.

## Bluetooth diagnostics and recovery

The top of `/admin` shows controller power, LAP UUID presence, discovery and
pairing flags, raw HCI state, management settings, Bluetooth device class, the
state of `bluetooth`, `palm-lap`, `dnsmasq`, and `nftables`, plus matching HCI
fault messages from the current boot.

Use the least disruptive recovery that matches the symptom:

1. **Restart LAP service** re-registers LAP without resetting the controller.
2. **Restart Bluetooth stack** restarts BlueZ, restores controller power and
   LAP class/UUID hints, and ensures LAP is running.
3. **Reset Bluetooth UART** is the deeper Pi-specific recovery for `Powered:
   no`, `hci0 DOWN`, HCI hardware errors, or reset-command timeouts. It unbinds
   and rebinds `serial0-0` from `hci_uart_bcm`, then restores Bluetooth and LAP.
   The request returns immediately; wait about 20 seconds before refreshing.

The latter two actions disconnect active Palm sessions. The UART button has an
additional browser confirmation and a server-side confirmation value. Bonds,
trust records, and LAP authorizations are not deleted by any recovery action.

UART recovery runs as the fixed `palm-bluetooth-recover.service` oneshot unit,
outside the hardened web service's mount/device namespace. The recovery script
first verifies the exact device and driver paths used by this Raspberry Pi and
refuses to proceed if they differ. New-device discovery class/UUID hints are
deliberately deferred while reset firmware settles; opening a pairing window
reasserts them before discovery begins.

## Security design

- `palm-web.service` runs as the dedicated unprivileged `palmweb` account.
- The account cannot use `operator`'s broad sudo privileges. Its sudo rule permits
  only `/usr/local/sbin/palm-web-admin`.
- That root helper accepts a small JSON request, validates action-specific
  values, restricts sends to files inside the managed store, and invokes only
  the existing pairing/device/sender commands or predetermined recovery unit.
- Bluetooth sending is executed as `operator` solely to access the existing BlueZ
  per-user OBEX D-Bus service.
- Uploaded filenames are normalized, extensions are restricted to `.prc` and
  `.pdb`, symlinks are not served, file size is limited, and a Palm database
  header is required.
- nftables permits Palm interfaces to reach only DNS, TCP 8080, and diagnostic
  ICMP on the Pi. The privileged admin routes add a second application-layer
  LAN-source check.

## Service operations

```sh
systemctl status palm-web.service
sudo journalctl -u palm-web.service -f
sudo systemctl restart palm-web.service
curl http://127.0.0.1:8080/health
```

Files:

```text
/usr/local/libexec/palm_web.py
/usr/local/sbin/palm-web-admin
/usr/local/sbin/palm-bluetooth-recover
/etc/palm-lap/web.json
/etc/systemd/system/palm-web.service
/etc/systemd/system/palm-bluetooth-recover.service
/etc/sudoers.d/palm-web
/var/lib/palm-web/files/
/var/lib/palm-web/jobs.json
```

Editable source is the cloned `palm-lap` repository. Installed runtime files
under `/usr/local` and `/etc` change only through an explicit install or
operator action.

## Troubleshooting

- **Palm cannot open the catalog:** verify LAP is connected, then try
  `http://10.77.0.1:8080/health`. Check the `palm-web` service and nftables input
  counters.
- **Bluetooth job says service record unavailable:** disconnect LAP, keep the
  Palm awake/discoverable, and retry. If the Zire refuses every channel, perform
  a soft reset as documented in `PRC-TRANSFER.md`.
- **Downloaded PRC does not install:** confirm sufficient free Palm storage. A
  Palm commonly needs additional working space beyond the PRC's file size.
- **Admin returns 404:** access it from the `192.168.1.0/24` LAN, not through
  the Palm PPP connection.
- **Pair/device action fails:** inspect both `palm-web.service` and
  `palm-lap.service` logs; the web page also displays validated-helper errors.
- **UART recovery fails:** inspect `sudo systemctl status
  palm-bluetooth-recover.service` and `sudo journalctl -u
  palm-bluetooth-recover.service -n 100`. The unit deliberately fails rather
  than writing to unexpected sysfs paths.
