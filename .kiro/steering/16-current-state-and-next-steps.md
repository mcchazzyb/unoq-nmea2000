---
title: Current state and next steps (hand-off snapshot)
inclusion: always
---

# Where the project stands (snapshot 2026-10-11, update when it changes)

Receive-only NMEA 2000 dashboard on an Arduino UNO Q. It has now run against the real boat bus (dockside, 2026-10-10)
and decoded engine, tank and GPS data correctly. Read `docs/hardware.md`, `docs/network-access.md` and
`docs/diagnostics.md` for detail; this file is the short version.

## Git state

- `main` = origin/main = "Boat trip review: engine hours in hours, portable tank sizes, Dashboard/Setup tabs".
- Branch `shutdown-button` (cut from that main) adds the Setup-tab **Shut down the board** button, host units and
  docs. It was reviewed and committed but NOT merged to `main`, and as of 2026-10-11 it existed only on the laptop it
  was written on (NOT pushed). If you cannot see it with `git branch -a`, it has not been pushed yet. Agents never push
  `main`; the owner does.
- No git `user.*` is set on the laptop used so far. Commits were made with env vars
  (`GIT_AUTHOR_NAME="Charles Batchelor" GIT_AUTHOR_EMAIL=charles.batchelor@gmail.com`, same for COMMITTER), never by
  changing git config. Run git commands one at a time.
- Change workflow: `.kiro/skills/code-review-gate/SKILL.md`. The owner now wants **`gpt-5.6-terra`** as the reviewer
  (not sol). Block on Critical, advise on High. The reviews have found real bugs every time; fix them, add a test that
  fails without the fix, and re-review after any non-trivial fix. This repo has no `bd` tickets (skip that step).

## State of the board (as of the last connection, 2026-10-10 evening; the owner confirmed nothing changed on 2026-10-11)

- Hostname BoatLab. Address depends on the network: `192.168.2.22` on home WiFi `hoosie` (it was `192.168.201.129`
  earlier), `10.42.0.1` on its own BoatFi hotspot, mDNS `boatlab.local` when it resolves. Check the router if unsure.
- Production sketch (ListenOnly, silent pin high). App `unoq_nmea2000_capture` starts on boot, receiver `hardware`.
  Services: `unoq-mcu-recover`, `unoq-blackbox`, `unoq-ble-publisher`, `unoq-shutdown.path` (all real files in
  `/etc/systemd/system`, never symlinks into `/home`). `scripts/boat_preflight.sh` checks them.
- Deployed app code = `main` plus the FIRST version of the shutdown button. The board does NOT have the final
  review fixes (the host script's `.queued`/`.failed` markers, and the UI that says "Shutdown started" only on
  `queued`). Redeploy before relying on the button (see Deploy below).
- `settings.json` on the board: `{"0:4": 90.0}` (the real 90 L fuel tank, confirmed by the owner). It lives on the
  host (`~/ArduinoApps/unoq_nmea2000_capture/.cache/captures/`) so it survives restarts.
- The boat capture is on the board: `.cache/captures/capture-1791634279496924279.jsonl` (22,250 frames) plus
  `session.db`. A copy was taken to the laptop (`~/boat-capture-2026-10-10.jsonl`, not in the repo). Session-based
  retention is still not built, so do not let the capture dir fill the disk (`/` is 71% used by Docker, `/home` 7%).
- WiFi profiles (passwords are NOT in this repo): `hoosie` home, `iphone-hotspot`, `boatfi-ap` (SSID BoatFi).
  Phone access away from home is still untested with real clients.
- Pending physical steps for the owner: remove the D6 jumper, fit a fused, surge-rated 5 V / 3 A USB-C feed from a
  switched circuit (USB-C is 5 V only, VIN 7 to 24 V, no surge protection on the board).

## What the real bus gave us (dockside test 2026-10-10, 6.6 min, engine ran ~10 s, boat stationary)

- 22,250 frames, 0 malformed, 0 decode failures, persisted == received. Raw capture is the first independent reference.
- Devices: SA0 Suzuki engine gateway ("SUZUKI Engine" 2.3.0 RR31D), SA2 Garmin (mfr 229, GNSS source; it joined
  ~172 s in and gave its first valid position ~326 s in, the normal GPS lock delay), SA3 Suzuki LMF-200 gauge,
  SA4 "Tank Sender Adaptor" V2.00.
- Decoded and correct: rpm (0 to 3438), coolant temp, alternator V, **engine hours (183.87 h; the wire is seconds)**, fuel
  rate (peak 13.6 L/h), engine status, fuel tank level % (52.7 to 58.1), position, COG/SOG.
- Not sent by anything on the bus: oil pressure, heading, depth, wind, water temp, **speed through water**. The speed we
  have is **speed over ground** (PGN 129026), so range is over the ground. The tank sender reports capacity 0, so the
  owner's tank size is needed for litres, endurance and range. Range has never run with a real SOG.
- Unknown PGNs are ~70% of frames: Garmin 129540 satellites in view (27%, not needed), Garmin/Suzuki manufacturer
  proprietary (130830, 130832, 130817, 126720, 65280, ...: cannot be decoded without vendor docs), 129284/129283 route data
  (all N/A, no route active). **PGN 127497 (engine trip) trip-fuel-used reads a constant 544 with unclear units and its
  rate fields are N/A: do not use it.** Optional, not done: decode 129029 for satellites used (13), HDOP (0.95), altitude.
- The MCU counted 29,262 frames but the app got 22,250 (`mcu_dropped` 7,012, 24%) at 40 to 64 frames/s. `mcu_restarts`
  was 0. This is the known throughput ceiling, not a crash. Decide if it matters.
- Unexplained: SA4 sent 293 address claims in 8 bursts of ~37 identical frames (all other nodes <= 22). Hypothesis only:
  CAN auto-retransmission when nothing ACKs. Check on the next trip with every device powered.
- The bus was silent ~150 to 170 s (looks like a power cycle).

## Done in the 2026-10-10 session (all in `main` unless noted)

- Engine hours converted to hours in the decoder (`/3600`) and the UI unit is "h".
- Tank sizes: keys may be `<instance>:<sa>` (legacy), `name:<16hex NMEA NAME>:<instance>` (stable across address
  changes; saves use it when the NAME is known) or `inst:<N>` (set offline, before any bus connection). `inst:` only
  applies when exactly ONE fuel tank has that instance. Each live tank carries `setting_key` and `setting_source_key`
  so the Setup form shows one row per tank. A save rewrites an SA key to a NAME key only for a tank that is live.
- Page split into **Dashboard** (dials and data only) and **Setup** (bus status, sensor selection, tank sizes, device
  discovery, board power). The Setup tab shows "check bus" when the bus is not delivering frames.
- `shutdown-button` branch: `POST /api/v1/system/shutdown` (body `{"confirm":"shutdown"}`) writes a request file holding
  the kernel boot id; host path unit -> `deploy/unoq_shutdown.sh` runs `systemctl --no-block poweroff` only if the boot id
  matches THIS boot (stops a stale request from boot-looping the board) and writes `.queued` or `.failed`. The UI says
  "Shutdown started" only on `queued`. Installed by `scripts/install_shutdown.sh`. Dry-run tested on the board; a REAL
  poweroff has never been run and what the board does after poweroff is unverified.

## Known gaps and decisions (do not rediscover these)

- **Multi-boat is deferred.** The owner chose a surgical fix so this unit works; a marketable multi-boat design (setup
  flow by NAME/product, per-vessel profiles) comes later. Legacy `<instance>:<sa>` keys are address-bound, so moving the
  unit to another boat needs the settings cleared.
- `LiveStateCache` keeps the old `(instance, source_address)` tank when a sender re-appears at a new address, so
  `fuel_summary` can see a stale tank. Accepted, not fixed.
- Power loss is common on the boat. Evidence: ext4 journal recovery completed at the last boots; after the hard
  power-off on 2026-10-10 the system journal lost ~8 minutes of tail while the fsynced black box survived; there is no
  RTC. Gaps not yet closed: `settings.json` is written and renamed without fsync (a corrupt file loads as "no
  settings"); every captured frame is fsynced (eMMC wear; batching to ~1 s would bend the "persist every frame first"
  rule, owner's call); no pull-the-plug test yet (the first trip lost its app container and log at the next boot, cause
  unknown). A real shutdown sequence needs hardware: a supercap/UPS hold-up plus a power-fail signal on a GPIO
  that triggers `poweroff`; measure the board's current draw first. The GNSS time in PGN 129029 could set the clock when
  there is no internet.
- Security stays proportionate (steering 15): no auth, so anyone on the boat network can press Shut down. It needs an exact
  JSON body, so a link or a form post from another site cannot trigger it.

## The Bridge stall: mitigated by a host service, root cause still open

Under sustained CAN traffic the MCU's Bridge can wedge (bench: 53 stalls in 10.4 h, median 529 s; none seen at the
dock on 2026-10-10). Full evidence is in `docs/hardware.md`; the short version:

- Mechanism (read from a frozen board): the bridge thread spins in the msgpack parser at a higher priority than
  `loop()` and no Bridge request reaches the sketch again. Only an MCU reset clears it.
- Fix in place: `deploy/unoq_mcu_recover.py` (`unoq-mcu-recover`) watches `receiver_ok_age_s` on `/api/v1/health` and
  resets the MCU when it exceeds 15 s (about 20 s to recover, no app restart). The app logs `MCU restarted (...)`.
- **Do not make the sketch reset itself.** After any reset the base firmware's loader waits for the host to write a
  non-zero value to `0x40036400`; a sketch-side reset leaves the MCU dead. A test forbids it.
- SPI rule: the CAN controller's SPI bus is used ONLY from `loop()`; Bridge handlers must not call `can.`/`SPI.`
  (a test forbids it). `get_health()` reports values `loop()` caches every 250 ms.
- Still unknown: why serial bytes are lost. Lead: `ACAN2517` wraps SPI transfers in `noInterrupts()`.

## Next steps, in order

1. Owner: push/merge `shutdown-button`. Then redeploy to the board (below), re-run the dry-run check, and try one REAL
   shutdown with the board in reach (you cannot switch it back on remotely).
2. **Boat day (underway):** `scripts/boat_preflight.sh arduino@<board>`, connect to the bus, watch the Setup tab's Bus
   status, then run `scripts/blackbox_report.py --fetch`. First real SOG, fuel flow while running, endurance and range.
   Re-save the tank size once with the bus live (it migrates to the stable NAME key). Save the capture
   (`scripts/promote_capture.py`) and check decoders against canboat.
3. Power-loss hardening: fsync `settings.json`, pull-the-plug test at the bench, decide on frame-fsync batching, then
   the hold-up hardware.
4. Phone access: test BoatFi and the iPhone hotspot away from home; BLE `readings_text` was never tested over the air.
5. Find why serial bytes are lost (the recovery service is the safety net, not the fix).
6. Session-based data retention (clear previous session on start, size cap, explicit keep).
7. Small fixes: `nmea_name` loses precision in browsers (send as hex string); `message_rate` is a count, not a rate;
   `termination_off` in the sketch is a hard-coded `true`.
8. Stage 10 docs and sign-off in `.kiro/specs/nmea2000-diagnostic-platform/tasks.md`. Dashboard layout choice (A Helm,
   B Tile wall, C Night watch) is parked with the owner.

## Rules and gotchas learned (keep following these)

- Nothing in the production image may transmit. Bench ACK mode (`bench/ack_only/`, D6 jumper, `BENCH_ONLY_ACK`) is
  bench-only and must never reach the boat; tests enforce this.
- Read the manual first (steering 14). Validate decoders against canboat or real captures, not this repo's own generator.
- **Deploy** = copy the changed files into `~/ArduinoApps/unoq_nmea2000_capture/` then
  `arduino-app-cli app restart user:unoq_nmea2000_capture` (a sketch change reflashes). Do not assume only `web/`
  changed: on 2026-10-10 `protocol/decoders/engine.py` was never sent, so the board kept showing seconds. Compare
  checksums of every tracked file under `firmware/unoq_nmea2000_capture/` against the board before saying it is
  deployed. Host-side files (`deploy/*`, units) go to `~/unoq-nmea2000/deploy/` and are installed with the
  `scripts/install_*.sh` scripts (they COPY units into `/etc/systemd/system`; a symlink into `/home` fails at boot).
- Keep `firmware/unoq_nmea2000_capture/{web,service,protocol,...}` identical to the repo-root copies after edits.
- The app runs in container `unoq_nmea2000_capture-main-1`; `~/ArduinoApps/unoq_nmea2000_capture` is bind-mounted at
  `/app`. A Docker pool of 198.18.0.0/16 avoids the iPhone hotspot range. Pi rig (old address `192.168.201.119`, user
  `charlesb`, code `~/pi-nmea2000`, its own repo): `sudo ip link set can0 up type can bitrate 250000`.
- Never `pkill -f n2krig` over ssh (kills the ssh shell); find the PID and kill that.
- Agent shells do not keep an ssh-agent between calls. Use a key without a passphrase (the owner removed it from
  `~/.ssh/id_ed25519` on the main laptop) or ssh from your own terminal. "Permission denied (publickey)" on every host
  with "Server accepts key" in `ssh -vvv` means a locked macOS Keychain: unlock it, do not blame the hosts.
- Security is proportionate (steering 15): no auth, TLS or rate limiting unless asked.

## Picking this up on another computer

1. `git fetch`, check out `main` (and `shutdown-button` if it is not merged), `make install`, `make lint test`
   (374 tests pass on `shutdown-button`).
2. Accept the board's ssh host key once (`ssh arduino@<board>`), with your ssh key authorised on the board.
   Then `scripts/boat_preflight.sh arduino@<board>`.
3. Read this file, `docs/hardware.md`, `docs/network-access.md` and `docs/diagnostics.md`, then continue at "Next steps".
