# Future iOS app contract (Requirement 18.3)

This document specifies the contract a future SwiftUI iPhone app would
implement against the platform's BLE_Service (Stage 6/7's `BLEPublisher` and
the `/ble/encoding.py` wire format). It is a design reference, not a build
plan — no Swift code ships as part of this spec's host-testable stages.

The app is a **read-only BLE client**. It never writes to any GATT
characteristic (steering 01, 12: the platform itself never transmits on the
CAN bus, and the BLE schema exposes no writable characteristic — Property 23).

## Operations

### 1. BLE scanning

- **Input:** none (user taps "Scan" or the app auto-scans on foreground).
- **Output:** a list of discovered peripherals advertising the platform's
  GATT service UUID, each with a human-readable name (from the platform's
  advertised local name) and signal strength (RSSI).
- **Behavior:** standard `CBCentralManager.scanForPeripherals` with the
  service UUID filter from `ble/publisher.py`'s advertised schema. No
  authentication token is exchanged at this stage — Requirement 14's
  local-only/no-secrets posture applies to BLE the same as to the Web_API.

### 2. Connection

- **Input:** the user selects a discovered peripheral.
- **Output:** a connected `CBPeripheral`, or a typed connection-failure
  reason (timeout, out of range, peripheral busy).
- **Behavior:** `CBCentralManager.connect`, then service/characteristic
  discovery against the schema `ble/encoding.py` defines. The app reads the
  schema-version characteristic FIRST and refuses to proceed if the
  platform's `schema_version()` is incompatible with what the app knows how
  to decode (Requirement 27.2 — the embedded version exists for exactly
  this check).

### 3. Live notification subscription

- **Input:** the characteristic(s) to subscribe to (engine, tank, electrical,
  navigation — one characteristic per Domain_Model entity family, per the
  BLE wire format).
- **Output:** a stream of decoded Domain_Model values, each carrying its
  Provenance summary (`value_origin`, `staleness_s`) per Requirement 27.5.
- **Behavior:** `CBPeripheral.setNotifyValue(true, for:)`. The platform caps
  notification frequency at **250ms per characteristic** (Property 22) — the
  app must not assume a higher update rate and should visually indicate
  staleness itself if no notification arrives within a few multiples of that
  interval (a client-side staleness heuristic, independent of the
  platform's own `quality`/`staleness_s` fields).
- **Fragmentation:** a value exceeding the negotiated ATT MTU arrives as
  ordered segments with a sequence number and a final-segment marker
  (Requirement 27.3). The app's BLE layer must reassemble these before
  handing a complete record to the UI layer — `ble/encoding.py`'s
  reference reassembly helper (used in the platform's own tests) is the
  model to port.

### 4. Gauge/status rendering

- **Input:** a reassembled, decoded Domain_Model value (e.g. `EngineState`
  for one `(instance, source_address)` key).
- **Output:** a rendered gauge/status view.
- **Behavior:** every rendered value MUST show its Provenance — at minimum
  `quality` and `staleness_s` (Requirement 11.3: every transport surfaces
  Provenance). A value whose `quality` is `"stale"` or `"invalid"` must be
  visually distinguished (e.g. greyed out), never presented identically to a
  fresh valid reading.

### 5. Automatic reconnection

- **Input:** a dropped BLE connection (out of range, peripheral restarted).
- **Output:** a background reconnection attempt using
  `CBCentralManager.connect` with the previously known peripheral identifier,
  retried with backoff.
- **Behavior:** on reconnection, the app re-reads the schema-version
  characteristic (the platform may have restarted with a different build)
  and re-subscribes to its prior characteristics. All displayed values are
  marked stale from the moment of disconnection until a fresh notification
  arrives — the app's own responsibility, independent of the platform's
  `CAPTURE_HALTED_STORAGE_FULL` staleness marking.

### 6. Offline operation

- **Input:** no BLE connection available (platform out of range, powered
  off, or app launched with no prior pairing).
- **Output:** the app's last-known cached state (if any), clearly marked as
  offline/stale, with no fabricated or extrapolated values.
- **Behavior:** the app may cache the last-received Domain_Model snapshot
  locally (on-device only — this is explicitly NOT a network sync; the
  platform has no cloud component per Requirement 14.9) and show it with an
  "offline since <timestamp>" indicator rather than a blank screen.

## Non-goals

- No write path. The app never sends a command, never requests the platform
  change any value, and the GATT schema offers no characteristic that would
  allow it (Property 23).
- No pairing/bonding beyond what iOS's standard BLE connection flow provides
  — no app-level account, no cloud identity, no telemetry leaving the device
  (Requirement 14.9, 15.6).
- No control of the vessel. This is a diagnostic viewer, consistent with the
  platform's own receive-only posture (steering 01).
