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

Host-testable MVP in progress. See
[.kiro/specs/nmea2000-diagnostic-platform/tasks.md](.kiro/specs/nmea2000-diagnostic-platform/tasks.md)
for the current stage. Hardware (Arduino UNO Q + CAN shield) stages begin
only after the full host-testable Stage 8 gate passes — everything up to
that point runs on a laptop with no CAN hardware, using the built-in
simulator and golden fixture replay.

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
web/         FastAPI Web_API + the single-page Web_UI.
simulator/   Synthetic traffic generator and fixture replay (ICanReceiver).
fixtures/    Golden fixture library (recorded/synthetic captures + their
             documented pass/fail assertions).
```

## Steering and specs

Project-specific build rules live in `.kiro/steering/`; the full
requirements/design/task breakdown lives in
`.kiro/specs/nmea2000-diagnostic-platform/`.
