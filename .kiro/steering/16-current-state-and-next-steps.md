---
title: Current state and next steps (hand-off snapshot)
inclusion: always
---

# Where the project stands (snapshot 2026-10-08 10:50, update when it changes)

Receive-only NMEA 2000 dashboard on an Arduino UNO Q. Bench work is finished; the
device is being taken to the boat. Read `docs/hardware.md` and
`docs/network-access.md` for detail; this file is the short version.

## State of the board right now (boat configuration)

- Production sketch flashed (ListenOnly, silent pin high, 40 MHz, polled). Health line
  `0,0,0,ListenOnly,true,true,0,<uptime_ms>`. `scripts/boat_preflight.sh` passes. The MCU recovery
  service `unoq-mcu-recover` must be active (the preflight checks it).
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

## The Bridge stall: mitigated by a host service, root cause still open

Under sustained CAN traffic the MCU's Bridge can wedge (bench: 53 stalls in 10.4 h, median 529 s).
Full evidence is in `docs/hardware.md`; the short version:

- Mechanism (read from a frozen board): the bridge thread spins in the msgpack parser at a higher
  priority than `loop()` and no Bridge request reaches the sketch again. The UART hardware is fine and a
  fresh Linux client fails too, so only an MCU reset clears it. Not the cause: batch size, loop delay,
  thread overlap, poll rate, bridge-thread stack size (2048 B still stalled).
- Fix in place: `deploy/unoq_mcu_recover.py`, a systemd service (`unoq-mcu-recover`, enabled on the
  board). It watches `receiver_ok_age_s` on `/api/v1/health` (seconds since the app's last successful
  Bridge call; a quiet bus does not move it) and resets the MCU when it exceeds 15 s. 3 of 3 natural
  stalls recovered in about 20 s with no app restart. The app logs `MCU restarted (...)` each time.
- **Do not make the sketch reset itself.** After any reset the base firmware's loader waits for the host
  to write a non-zero value to `0x40036400` before it starts the sketch; a sketch-side reset leaves the MCU
  dead until the host does that. A test forbids it.
- SPI rule (found 2026-10-08): the CAN controller's SPI bus is used ONLY from `loop()`. Bridge handlers
  run on the Bridge thread about two thirds of the time, and a handler that read the controller over SPI
  reported mode `NormalFD` (a read of zeros) 2 times in 40. `get_health()` now reports values `loop()`
  caches every 250 ms; a test forbids `can.`/`SPI.` inside any handler.
- Still unknown: why serial bytes are lost (the decoder buffer shows fragments with bytes missing).
  Lead: `ACAN2517` wraps SPI transfers in `noInterrupts()`; check whether that starves the UART FIFO.
- On the boat: note how often `MCU restarted` appears in the app log. Real traffic with real ACKing
  nodes may behave differently from the bench.

## Next steps, in order

1. **Boat day:** run `scripts/boat_preflight.sh arduino@<board>` (use
   `arduino@10.42.0.1` on BoatFi), connect to the bus, note whether and when the stall
   happens, save the first real capture (`scripts/promote_capture.py`). A real capture is
   the independent reference for checking the decoders against canboat.
2. Find why serial bytes are lost (see the stall section); the recovery service is the safety net,
   not the fix. Optionally measure the real-bus stall rate on the boat first.
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
