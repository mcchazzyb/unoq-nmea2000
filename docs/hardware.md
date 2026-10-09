# Hardware: Arduino UNO Q + CAN shield

What is proven on the bench (Oct 2026) and what is not. Receive-only is
structural everywhere below (steering 01); nothing in the production image
transmits.

## Production build (the boat)

- Sketch: `firmware/unoq_nmea2000_capture/sketch/sketch.ino`.
- Controller: MCP2518FD on the CAN shield, **ListenOnly**, 250 kbit/s.
- **Oscillator is 40 MHz** (`OSC_40MHz`). The ACAN2517FD default of 20 MHz
  receives nothing on this board.
- **Polled, not interrupt-driven.** The INT line (D2) delivered zero frames
  on the real board; polling the same controller received them. The sketch
  uses `ACAN2517 can(MCP2518_CS, SPI, 255)`, `can.begin(settings, NULL)` and a
  bounded `isr_core()` loop (at most 4 passes). Do not use `can.poll()`: it
  disables interrupts and starves the Bridge.
- At most 4 frames per Bridge call (`FRAME_BATCH_MAX`). Tests enforce the
  oscillator, polling and batch limits.
- Health line from the MCU: `total,dropped,depth,mode,termination_off,silent_pin_high,errors`.
  Production expects mode `ListenOnly` and silent pin `true`.
  Read it with:
  `docker exec unoq_nmea2000_capture-main-1 python3 -c "from arduino.app_utils import Bridge; print(Bridge.call('get_health'))"`

### Silent pin (task 9.2, closed as an accepted deviation)

D4 is software-driven only. With no pull-up fitted, D4 reads about 0 V until
the sketch runs, so the transceiver is not silent in the first moments after
power-up. The controller is already listen-only by then. The owner accepted this
as a software-only silent mode; no pull-up is being fitted.

### Termination

Do not rely on the health line's `termination_off`: it is currently a
hard-coded `true`, not a measurement. Check the bus yourself: 60 ohm between
CANH and CANL with the board connected means two 120 ohm terminators, one at
each end. The board must not add a third.

## Bench testing

A Raspberry Pi with a CAN HAT (separate repo, `pi-nmea2000`) generates traffic
on a bench bus. Key facts:

- **A listen-only node never ACKs.** With only the Pi and the UNO Q on the bus,
  the Pi sees no ACK, goes error-passive and retries one frame at about
  2000 frames/s. The UNO Q then drops about 88%, the controller drops to
  RestrictedOperation and the Bridge stalls. A real NMEA 2000 bus always has
  other nodes that ACK; the bench does not.
- **Bench-only ACK mode** (approved by the owner, 2026-10-07):
  `bench/ack_only/sketch.ino.bench`. The ACK code sits in fenced lines behind
  `#define BENCH_ONLY_ACK 1` and only activates when D6 is jumpered to GND at
  boot. See `bench/ack_only/README.md`. `tests/test_bench_ack_sketch.py`
  proves the bench build differs from production only in the fenced lines.
  **It must never reach the boat image.**
- Do not validate the rig with this repo's own generator or fixtures: that is
  circular. Use an independent source (canboat, real logs) plus hostile frames.

### Switching back to the boat build

1. Copy the production sketch to the board:
   `scp firmware/unoq_nmea2000_capture/sketch/sketch.ino arduino@<board>:ArduinoApps/unoq_nmea2000_capture/sketch/sketch.ino`
2. `arduino-app-cli app restart user:unoq_nmea2000_capture`
3. Remove the D6 jumper. Expect health `ListenOnly,true,true`.

## The production image has not been validated on a live bus

Everything measured on the bench so far (stall rate, the 1.8 MB captures, the dashboard values) came from
the bench ACK build, or from the production sketch against a bus where the Pi's frames were never
acknowledged. The unmodified production image (ListenOnly, no ACK, silent pin high) has **never been
run against a bus with ACKing nodes**. The boat is therefore its first real test. Open questions that only
a bus with a second, ACKing node can answer:

- does the production build receive normally while other nodes acknowledge, and with what error counters;
- does the Bridge stall at the same rate without the bench ACK code;
- do the decoders cope with real devices (the golden fixtures are this repo's own).

To validate it before the next trip: the Pi sends, a third node ACKs (a second Pi/CAN HAT, a USB-CAN
adapter, or a real NMEA 2000 sensor on the bench bus), the UNO Q runs the production sketch with no D6
jumper. Then compare the black box (`docs/diagnostics.md`) with the Pi's own transmit counts.

## Known issue: the Bridge can wedge (mitigated, root cause not found)

Under sustained CAN traffic the RouterBridge on the MCU occasionally stops serving calls. Without
recovery that is a frozen dashboard until the app is restarted.

**Measured on the bench** (bench ACK sketch, Pi looping a synthetic scenario): 53 stalls in 10.4 h,
minimum 121 s, median 529 s, maximum 2612 s of running before a stall.

**What a stall is**, read from a frozen board over the debug port on 2026-10-08:
- The MCU is alive. No handler is stuck partway (entry and exit counters match) and none is being
  entered: no Bridge request reaches the sketch.
- The UART hardware is clean (no overrun, framing or noise flags, receive FIFO empty), and a brand-new
  Bridge client fails exactly like the old one. The Linux side is not the cause.
- The bridge thread (priority 5, above `loop()` at 14) spins at about 80% CPU inside the msgpack
  parser while holding `read_mutex`, with its stack pointer below the bottom of its stack.
  `loop()` drops from about 600 passes a second to about 8.
- The decoder's receive buffer holds request fragments with bytes missing from the middle
  (`get_frame|batch`, `get_h|ealth`), the signature of lost serial bytes. Why they are lost is not known.
  The library decoder cannot resynchronise from that state.
- Not the cause: batch size, loop delay, Python thread overlap, health-poll rate, and the bridge thread
  stack size (raised from 500 to 2048 bytes: it still stalled, after 590 s).
- The installed library does not honour `provide_safe()` as documented: the bridge thread's `update()`
  accepts requests of every tag, so handlers run on the 500-byte bridge thread about two thirds of the time.
  RouterBridge 0.4.3 is the newest release.

**Recovery (`deploy/unoq_mcu_recover.py`, a systemd service on the board).** The dashboard reports
`receiver_ok_age_s`, the seconds since its last successful Bridge call. It keeps advancing on a quiet
bus, so a silent CAN bus never triggers anything. If it exceeds 15 s the service resets the MCU the way
the platform's own flashing step does: hardware reset over the debug port, then write `0xCAFFEEEE` to
`0x40036400`. At most one reset a minute. The app reconnects by itself and logs
`MCU restarted (uptime A ms -> B ms)`.
- **The sketch cannot do this itself.** After any reset the base firmware's loader polls `0x40036400`
  once a second and only starts the sketch once something non-zero has been written there. A sketch that
  resets itself is never started again. Tests forbid it.
- Measured on natural stalls: 3 of 3 recovered with no app restart, about 20 s each
  (15 s detection plus reset and reconnect).
- A reset does not transmit. The MCP2518FD keeps its ListenOnly mode and D4 behaves as at power-up
  (the accepted software-only deviation, task 9.2).
- Install: copy `deploy/` to `~/unoq-nmea2000/deploy/` on the board, then
  `sudo cp ~/unoq-nmea2000/deploy/unoq-mcu-recover.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now unoq-mcu-recover`.
  Copy the unit; never `systemctl enable /home/...`: that symlink points into the separate `/home`
  partition, which is mounted after systemd reads `/etc/systemd/system`, so the unit failed to load on
  both boat boots (2026-10-09) and the safety net did not run. `scripts/boat_preflight.sh` checks that the
  service is active and that the unit is a real file.

**SPI rule.** The controller's SPI bus is used only from `loop()`. Bridge handlers run on the Bridge
thread about two thirds of the time, and two threads on one bus corrupt each other's transactions:
`get_health()` used to read the mode itself and reported `NormalFD` (enum value 0, a read of zeros) 2 times
in 40 on a quiet bus. It now reports values `loop()` caches every 250 ms (240 of 240 reads `ListenOnly`
afterwards), and a test forbids `can.` or `SPI.` inside any handler.

Still open: why serial bytes are lost. `ACAN2517` wraps its SPI transfers in `noInterrupts()`; whether
that starves the UART's 8-byte FIFO (about 0.7 ms at 115200 baud) has not been measured.
Real boat traffic, with real ACKing nodes, may behave differently from the bench.

## Disk use

The capture log and `session.db` grow without a cap. Floods filled the board's
disk during testing. Session-based retention (clear previous sessions on start,
size cap, explicit "keep this session") is proposed but not built.
