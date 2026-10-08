---
title: Current state and next steps (hand-off snapshot)
inclusion: always
---

# Where the project stands (snapshot 2026-10-08, update when it changes)

Receive-only NMEA 2000 dashboard on an Arduino UNO Q. Bench work is finished; the
device is being taken to the boat. Read `docs/hardware.md` and
`docs/network-access.md` for detail; this file is the short version.

## State of the board right now (boat configuration)

- Production sketch flashed (ListenOnly, silent pin high, 40 MHz, polled). Health line
  `0,0,0,ListenOnly,true,true,0`. `scripts/boat_preflight.sh` passes.
- App `unoq_nmea2000_capture` is the App Lab default (starts on boot), receiver =
  `hardware`. Old captures and `session.db` cleared; `settings.json` kept (holds a bench
  tank size for key `0:42`, harmless; set real tank sizes in the dashboard).
- BLE publisher service `unoq-ble-publisher` enabled. Dashboard is called
  **NMEA2000 Dashboard**.
- WiFi profiles in NetworkManager (passwords are NOT in this repo, the owner has them):
  `hoosie` home (0), `iphone-hotspot` (-10), `boatfi-ap` own hotspot SSID BoatFi (-100,
  board is 10.42.0.1). **None of the phone/hotspot paths has been tested away from home.**
- Pending physical steps for the owner: remove the D6 jumper (inert on the production
  sketch, but remove it), fit a fused, surge-rated 5 V / 3 A USB-C feed from a switched
  circuit (datasheet: USB-C is 5 V only, VIN 7 to 24 V, no surge protection on the board).

## The main open problem: the Bridge stall (NOT fixed, no self-recovery)

Under sustained CAN traffic the Bridge stops answering and the dashboard goes stale until
the app is restarted. Overnight bench soak (bench ACK sketch, Pi looping a synthetic
scenario): **53 stalls in 10.4 h, time-to-stall min 121 s, median 529 s, max 2612 s.**
An external watchdog restarted the app each time (about 100 s to recover).

- The MCU itself keeps running. Ruled out as sole cause: batch size, loop delay, Python
  thread overlap, health-poll rate. The "empty batch" test (4 of 4 survived 130 s) is NOT
  evidence: 130 s is shorter than the median time to stall.
- The container has `restart=no`, so an exit inside it will not bring the app back.
  Recovery must come from outside it (a host systemd unit running
  `arduino-app-cli app restart user:unoq_nmea2000_capture`) or from the MCU side.
- Detect the stall from the Bridge itself (health call failing or timing out), never from
  engine data age: a quiet bus is legitimate and must not trigger a restart.
- Real boat traffic and real ACKing nodes may behave differently from the bench. Record
  the first boat session honestly either way.
- Candidate fixes, untested: compact frame encoding, `provide()` instead of
  `provide_safe()`, newer RouterBridge (installed: 0.4.3), MCU self-reset watchdog.

## Next steps, in order

1. **Boat day:** run `scripts/boat_preflight.sh arduino@<board>` (use
   `arduino@10.42.0.1` on BoatFi), connect to the bus, note whether and when the stall
   happens, save the first real capture (`scripts/promote_capture.py`). A real capture is
   the independent reference for checking the decoders against canboat.
2. Build stall self-recovery (host-side watchdog keyed on Bridge failure) and re-soak.
3. Phone access: test BoatFi and the iPhone hotspot away from home; check the Docker
   subnet 172.20.0.0/16 against the iPhone hotspot range. Then a BLE plain-text readings
   characteristic (LightBlue can show it); the live BLE characteristics are binary and
   were never tested with real traffic.
4. Session-based data retention (clear previous session on start, size cap, explicit keep)
   as a requirement change; the capture log filled the disk during bench floods.
5. Small fixes: `nmea_name` loses precision in browsers (send as hex string);
   `message_rate` is a count, not a rate; `termination_off` in the sketch is a hard-coded
   `true`, not measured.
6. Stage 10 docs and sign-off in `.kiro/specs/nmea2000-diagnostic-platform/tasks.md`
   (10.1 boat-test.md, 10.2 deploy script, 10.4 can-safety.md, 10.5). Layout choice for the
   dashboard (A Helm, B Tile wall, C Night watch mockups) is parked with the owner.

## Rules and gotchas learned (keep following these)

- Nothing in the production image may transmit. Bench ACK mode
  (`bench/ack_only/`, D6 jumper, `BENCH_ONLY_ACK`) is approved for the bench only and must
  never reach the boat; tests enforce this.
- Read the manual first (steering 14). Do not validate the Pi rig with this repo's own
  generator (circular); use canboat, real logs, an independent decoder plus hostile frames.
- Boards: UNO Q `arduino@192.168.201.129` (hostname BoatLab, mDNS `boatlab.local`), app dir
  `~/ArduinoApps/unoq_nmea2000_capture` is bind-mounted to `/app` in container
  `unoq_nmea2000_capture-main-1`. Deploy = `scp` then
  `arduino-app-cli app restart user:unoq_nmea2000_capture` (a sketch change reflashes).
  Pi rig `charlesb@192.168.201.119` (user is `charlesb`), code in `~/pi-nmea2000`, its own
  repo. Bring up CAN: `sudo ip link set can0 up type can bitrate 250000`.
- Never `pkill -f n2krig` over ssh (kills the ssh shell); find the PID and kill that.
- Keep `firmware/unoq_nmea2000_capture/web/*` identical to `web/*` after edits.
- Run git commands one at a time (a parallel pair once collided on `.git/index.lock`).
  Agents never push to `main`; the owner pushes.
- If every ssh host fails with "Permission denied (publickey)" while `ssh -vvv` shows
  "Server accepts key", the macOS Keychain is locked: unlock the Mac, do not blame the hosts.
- Security is proportionate (steering 15): no auth, TLS or rate limiting unless asked.

## Picking this up on another computer

1. `git pull`, then `make install` and `make lint test`.
2. Accept the board's ssh host key once (`ssh arduino@<board>`), with your ssh key
   authorised on the board and the Pi. Then `scripts/boat_preflight.sh arduino@<board>`.
3. Read this file, `docs/hardware.md` and `docs/network-access.md`, then continue at
   "Next steps".
