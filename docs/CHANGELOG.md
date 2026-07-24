# Changelog

## 0.6.1 — 2026-07-24

- Removed the browser-side file-extension filter that could prevent iOS Safari
  from selecting unregistered `.prc` and `.pdb` file types.
- Retained all server-side extension, filename, size, count, and Palm database
  validation.

## 0.6.0 — 2026-07-19

- Added multi-file `.prc`/`.pdb` uploads to the LAN administration interface.
- Added a drag-and-drop upload area with a selected-file summary while keeping
  the Palm-facing catalog free of JavaScript and compatible with old browsers.
- Made the per-batch file-count limit configurable and added validation of every
  staged file before publishing any member of the batch.

## 0.5.0 — 2026-07-19

- Published the validated implementation as the private
  `palmsnipe/palm-lap` GitHub repository.
- Reorganized runtime code, systemd units, configuration templates, scripts,
  and documentation into a reusable installation source tree.
- Removed real Palm/controller addresses, operator paths, device allowlists,
  uploaded packages, captures, bonds, PIN state, jobs, and backups.
- Made gateway name, operator account, admin network, uplink, upstream DNS,
  Palm subnet, and local PPP address installation inputs.
- Added backed-up first-install, preserve-configuration update, verification,
  uninstall, repository hygiene, and GitHub Actions validation workflows.
- Made the installer refuse to replace an existing allowlist unless the
  operator supplies `--reconfigure` explicitly.

## 0.4.2 — 2026-07-19

- Diagnosed Palm TX timeouts where BlueZ showed an ACL connection but never
  handed RFCOMM to the LAP daemon: SDP found UUID `0x1102` and channel 4, link-
  key authentication and E0 encryption succeeded, then BlueZ 5.82 stalled for
  about 25 seconds in external-profile service authorization.
- Disabled only BlueZ's redundant `RequireAuthorization` profile prompt.
  Cryptographic `RequireAuthentication` remains enabled, and `NewConnection`
  still rejects every MAC outside the four-device configuration before taking
  ownership of the descriptor or starting PPP.
- Removed the temporary BlueZ debug override after validation.
- Confirmed TX RFCOMM, LCP, IPCP, peer `10.77.0.13`, DNS `10.77.0.1`, live
  `plap13`, and bidirectional ICMP with zero packet loss.

## 0.4.1 — 2026-07-19

- Reboot-validated persistence of Bluetooth, LAP, web, DNS, nftables, IPv4
  forwarding, four bonds/authorizations, LAP advertisement, and closed-but-
  connectable idle state.
- Found that BlueZ initially returned with class `0x400300`, omitting the
  withdrawn LAP Networking service bit even though the profile was registered.
- Added an enabled delayed boot oneshot that reapplies the LAN/AP device class
  and LAP Networking UUID hint after the controller and profile settle.
- Wrapped `btmgmt` with bounded commands and an open input pipe to accommodate
  BlueZ 5.82 stalling when launched by systemd with `/dev/null` as stdin.

## 0.4.0 — 2026-07-19

- Added live Bluetooth controller, HCI, service, LAP UUID, device-class, and
  recent kernel-fault diagnostics to the LAN-only web administration page.
- Added web controls to restart only LAP, restart the BlueZ/LAP stack, or run
  the deeper Raspberry Pi Bluetooth UART recovery.
- Implemented UART recovery as a fixed root-only oneshot systemd unit with
  platform/path validation, service restoration, controller power-up, and LAP
  profile restoration; the web helper can start only this predetermined unit.
- Made deep recovery asynchronous so its initiating HTTP response completes
  before the shared wireless chipset can briefly interrupt connectivity.
- Kept discovery-only class/UUID management hints out of the fresh-firmware
  critical path after end-to-end testing exposed a management-command stall;
  `palm-lap-pair` reasserts both before every new-device pairing window.
- Kept every recovery route behind the existing LAN-source and CSRF checks and
  added confirmation plus active-session disruption warnings.

## 0.3.2 — 2026-07-19

- Diagnosed a spontaneous Bluetooth controller failure after a successful Palm
  TX session: the kernel reported HCI hardware error `0x00` and reset command
  timeouts (`Opcode 0x0c03 failed: -110`), leaving `hci0` down and BlueZ showing
  `Powered: no` despite no RF kill.
- Recovered without rebooting by unbinding and rebinding the Pi's
  `serial0-0` device from `hci_uart_bcm`, then restored controller power, the
  LAN/NAP class hint, the LAP UUID hint, and `palm-lap.service`.
- Verified the controller is again powered, connectable, and page-scanning,
  with LAP registered and all four Palm bonds and authorizations preserved.
- Added a repeatable controller-level diagnosis and recovery procedure to the
  troubleshooting guide and corrected its DNS and packet-capture notes.

## 0.3.1 — 2026-07-15

- Removed web-admin password authentication at the user's request.
- Retained LAN-source enforcement, Palm-subnet 404 responses, CSRF protection,
  the dedicated service account, and the narrow validated privilege helper.
- Removed the generated password hash and credentials handoff file.

## 0.3.0 — 2026-07-15

- Added a Palm-compatible HTTP file catalog and LAN-only authenticated admin UI.
- Added validated uploads, direct Palm downloads, serialized Bluetooth send
  jobs with progress/history, pairing controls, and bonded-device authorization.
- Isolated the web process under a dedicated `palmweb` system account and a
  single narrow sudo helper; privileged admin routes are unavailable from PPP.
- Added TCP 8080 to the small Palm-facing firewall allowlist and retained the
  default block on all other Pi services.
- Added complete web-interface operations, security, recovery, and development
  documentation.

## 0.2.1 — 2026-07-15

- Diagnosed WebPro's DNS timeout: PPP and NAT worked, IPCP supplied the LAN
  router as DNS, and a query/reply crossed the gateway before Palm disconnected.
- Added current Debian dnsmasq as a DNS-only cache/forwarder on Palm PPP
  interfaces and changed IPCP DNS to the directly connected peer `10.77.0.1`.
- Allowed only UDP/TCP DNS plus diagnostic ICMP to the Pi from Palm interfaces.
- Corrected the editable configuration source to include the authorized Zire 72.

## 0.2.0 — 2026-07-15

- Installed BlueZ 5.82 `bluez-obexd` and enabled its per-user OBEX service.
- Added `palm-send-prc`, a D-Bus Object Push sender for `.prc` and `.pdb` files.
- Archived and extracted PalmDB's NetFront 3.1 package under `packages/`.
- Documented UDMH as NetFront 3.1's required memory-heap prerequisite.
- Archived PalmDB's one-file NetFront installer and documented that it embeds
  NetFront 3.1 plus MaxX, avoiding a separate UDMH installation.
- Physically validated outbound Object Push to the Zire 72 after disconnecting
  LAP; documented the single-profile limitation.
- Changed transfer completion tracking from polling to D-Bus signals so BlueZ's
  short-lived final `complete` state cannot be missed.
- Archived the official Zire 72 WebPro 3.5 manual-install PRC with its checksum
  and made it the preferred browser bootstrap package.
- Confirmed the Zire 72 advertises Object Push UUID `0x1105` on RFCOMM channel 1
  after a soft reset, then completed an end-to-end WebProV transfer with final
  BlueZ status `complete`.

## 0.1.0 — 2026-07-15

- Inspected Debian, kernel, BlueZ, PPP, routing, firewall, and adapter state.
- Chose current BlueZ Profile1/Agent1 APIs instead of deprecated BlueZ utilities.
- Added explicit LAP 1.0 SDP record on RFCOMM channel 4.
- Added MAC/IP allowlist and one pppd process per connected device.
- Added three-minute, root-only legacy-PIN pairing window with automatic closure.
- Added pppd peer policy overriding physical-modem defaults.
- Added isolated `10.77.0.0/24` Palm subnet, IPv4 forwarding, nftables filtering, and NAT through `eth0`.
- Added systemd lifecycle/hardening and BlueZ restart propagation.
- Corrected initial BlueZ behavior that made the adapter pairable after registering the default agent; daemon startup now explicitly closes pairing/discovery.
- Added source tree, backup, operator runbook, architecture, troubleshooting, manifest, and rollback documentation under `/home/operator/palm-lap`.
- Completed all tests possible without a physical Palm. End-to-end interoperability remains the next milestone.
- Reboot-tested service, LAP advertisement, firewall, forwarding, and closed pairing state.

## 0.1.1 — 2026-07-15

- Diagnosed Palm discovery failure while the Pi was correctly inquiry-visible.
- Found the controller advertised Telephony/Miscellaneous class `0x400000`; current BlueZ does not attach the Networking service-class hint to withdrawn LAP UUID `0x1102`.
- Set the device major class to LAN/Network Access Point and made `palm-lap-pair open` add the Networking hint, producing on-air class `0x420300`.
- Kept the controller connectable after closing pairing so bonded Palms can reconnect while discovery and new pairing remain disabled.
- Verified open state `connectable discoverable bondable`, `PSCAN ISCAN`, LAP UUID advertisement, and closed state `connectable`, `PSCAN`.
- Confirmed the Zire 72 discovers the Pi from LAN Setup.
- Added the SDP language-base descriptor and named the service `palm-lap-host Palm LAN` to replace Palm's “Unnamed Network Access Point” label.
- Set the persistent BlueZ adapter alias to the same `palm-lap-host Palm LAN` label.
- Renamed both labels to the user-selected `Palm LAP`.
- Extended the controlled pairing/discovery window from three to ten minutes so Zire LAN Setup retries do not race the timeout.
- Bonded and authorized Zire 72 `AA:BB:CC:DD:EE:10` as peer `10.77.0.10`.
- Confirmed successful RFCOMM, LCP, IPCP, DNS negotiation, live `plap10`, and bidirectional ping with zero packet loss.
- Closed new-device pairing after validation while retaining connectability for the bonded Zire.
