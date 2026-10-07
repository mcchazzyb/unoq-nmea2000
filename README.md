# unoq-nmea2000

A passive, receive-only NMEA 2000 diagnostic and discovery platform. It
listens on a vessel's NMEA 2000 (CAN) backbone, decodes standard PGNs
(engine, tank, electrical, navigation, device identity), and serves the
decoded state locally over a web dashboard and BLE — without ever
transmitting a frame onto the bus.

## What this is for

Boat owners and installers who want to see what is actually on their NMEA
2000 network — live engine/tank/electrical/navigation values, every
discovered device with its manufacturer and product info, and raw frame
capture for diagnosing a misbehaving sender — without the cost or risk of a
transmit-capable tool.

## What this is not

Not a chartplotter, not an engine control tool, and not a way to inject
anything onto the backbone. See [docs/security.md](security.md) for why
receive-only is structural, not a configuration option.

## Status

The host-testable stack is complete and the Arduino UNO Q is now receiving real
frames on a bench CAN bus (a Raspberry Pi generating traffic), with engine, tank,
battery and navigation data on the web dashboard. See
[.kiro/specs/nmea2000-diagnostic-platform/tasks.md](.kiro/specs/nmea2000-diagnostic-platform/tasks.md)
for the open Stage 10 items.

Not yet done:

- **Boat trial.** Everything so far is bench-only.
- **Intermittent Bridge stall** under sustained traffic. The app must be
  restarted to recover; there is no self-recovery in the product yet. See
  [docs/hardware.md](docs/hardware.md).
- **Phone access away from home WiFi** is configured but untested
  ([docs/network-access.md](docs/network-access.md)).
- **Bluetooth** is read-only and binary; a human-readable readings
  characteristic is proposed, not built.

## The dashboard

The page is called **NMEA2000 Dashboard** and is designed for a phone first.

- Engine, tank, electrical and navigation cards; speed over ground and wind
  shown in knots, RPM and tank level as whole numbers, tank type by name.
- **Fuel endurance:** remaining fuel, endurance and range are derived values,
  marked as derived, and are null unless every input is known. Tank sizes are
  set in the Boat settings panel and kept in `settings.json` in the capture
  directory.
- A × button removes a stale card.

## Quick start (host-testable, no hardware required)

```bash
make install        # creates .venv, installs the package + dev dependencies
make test lint       # full test suite + coverage gates + lint
make run-sim          # in one terminal: synthetic multi-node CAN traffic generator
make run-api          # in another terminal: web dashboard at http://127.0.0.1:8000
```

Open `http://127.0.0.1:8000` for the live dashboard. With no simulator
running, the dashboard just shows no live data and a disconnected indicator
— it never fabricates a value.

To replay a captured or golden fixture file instead of the live simulator:

```bash
make replay FILE=fixtures/golden/engine_rpm_change.jsonl
```

## Documentation

- [docs/architecture.md](architecture.md) — judgment calls made where the
  spec left a detail unspecified, and why
- [docs/api.md](api.md) — Web_API REST + WebSocket reference
- [docs/ble-protocol.md](ble-protocol.md) — BLE GATT schema, wire format,
  and security model
- [docs/log-format.md](log-format.md) — Raw_Capture_Log JSONL record shape
- [docs/nmea-discovery.md](nmea-discovery.md) — node discovery, NodeKey
  identity, and the diagnostic PGN classifier
- [docs/tank-data.md](tank-data.md) — fuel/tank level decoding
- [docs/hardware.md](docs/hardware.md) — UNO Q build, bench testing, the
  bench-only ACK mode, known issues
- [docs/network-access.md](docs/network-access.md) — reaching the dashboard
  from a phone (WiFi profiles, hotspot fallback, Bluetooth)
- [docs/testing.md](testing.md) — test suite layout, coverage gates, running
  tests
- [docs/security.md](security.md) — receive-only enforcement, threat model,
  secret handling
- [docs/future-display.md](future-display.md) — contract for a future
  BLE-client display app (design reference, not built here)

## Project layout

```
protocol/    Layer 2 — pure Domain_Model, decoders, parsing. No hardware/
             UI import; runs and tests on a laptop (steering 05).
service/     Ingestion pipeline, persistence, discovery, correlation.
ble/         BLE wire format + MockBLEPublisher (Stage 10 adds the real
             BlueZ-backed publisher behind the same Protocol).
web/         FastAPI Web_API + the single-page Web_UI (fuel.py and
             settings.py hold the derived fuel values and tank sizes).
firmware/    The UNO Q App Lab app: MCU sketch, Bridge client, a synced
             copy of web/.
bench/       Bench-only test aids (ACK sketch). Never part of the boat image.
deploy/      systemd unit for the BLE publisher on the board.
simulator/   Synthetic traffic generator and fixture replay (ICanReceiver).
fixtures/    Golden fixture library (recorded/synthetic captures + their
             documented pass/fail assertions).
```

## Steering and specs

Project-specific build rules live in `.kiro/steering/`; the full
requirements/design/task breakdown lives in
`.kiro/specs/nmea2000-diagnostic-platform/`.
