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

## Known issue: intermittent Bridge stall

Under sustained CAN traffic the Bridge occasionally stops answering
(`get_health` times out) after roughly 30 to 110 s, in about a third of 130 s
bench trials. The MCU is not dead; restarting the app recovers it.

- **Overnight soak (2026-10-07/08, bench ACK sketch, Pi looping a synthetic
  scenario): 53 stalls in 10.4 h.** Time running before a stall: minimum 121 s,
  median 529 s, mean 681 s, maximum 2612 s (16 under 5 min, 21 between 5 and
  15 min, 16 over 15 min). Recovery by restarting the app took about 100 s.
- Ruled out as the sole cause: batch size, loop yielding, Python thread
  overlap, health-poll rate.
- Four of four 130 s trials with empty batches (frames received, none returned
  over the Bridge) survived. That is **not** evidence about the frame-return
  path: 130 s is shorter than the median time to a stall, so a third of runs
  would survive by chance. A longer empty-batch soak would be needed.
- Untested candidate fixes: compact frame encoding, `provide()` instead of
  `provide_safe()`, a newer RouterBridge (installed 0.4.3), an MCU-side
  self-reset.
- **The production image has no self-recovery yet.** The container is started
  with `restart=no`, so recovery has to come from outside it. A recovery
  watchdog should key on the Bridge call failing, not on engine data age: a
  quiet bus is legitimate and must not cause a restart. The bench watchdog
  (`soak_watch.sh`, since removed from the board) keyed on engine age and was a
  test aid only.
- Real boat traffic, with real ACKing nodes, may behave differently. Record the
  first boat session either way.

## Disk use

The capture log and `session.db` grow without a cap. Floods filled the board's
disk during testing. Session-based retention (clear previous sessions on start,
size cap, explicit "keep this session") is proposed but not built.
