# Implementation Plan

Converts the design into discrete, incremental coding tasks, grouped into **stages**.
Order follows the design's two non-negotiable priorities: capture-first/receive-only
safety is structural from Stage 1, and the host-testable stack (Protocol_Core ->
Ingestion -> transports -> UI) is built and proven on the Simulator before any hardware
work begins (Stage 8+).

**Testing policy for this plan:** build tasks within a stage are implementation-only.
Each stage ends in one **Stage Gate** task that writes and runs every unit test,
property test (Hypothesis, >=100 iterations, tagged `Feature: nmea2000-diagnostic-platform,
Property N: <text>`), and integration test for everything built in that stage, then runs
`make test lint`. A stage is not done until its gate passes. This keeps implementation
momentum within a stage while still proving each stage before the next one builds on it.
Per Requirement 19.6, every feature or fix added *after* this plan also needs its own
regression test — that ongoing rule belongs in `/.kiro/steering`, not as a repeated task
here.

Each task names the requirements it satisfies and, at the gate, the correctness
properties (Properties 1-39 in design.md) it proves.

---

## Stage 1 — Repository scaffolding and safety-critical types

- [x] 1.1 Repository scaffolding and steering rules
  - Create the directory layout: `/firmware`, `/protocol`, `/service`, `/ble`, `/web`,
    `/simulator`, `/fixtures`, `/fixtures/golden`, `/docs`, `/scripts`, `/.kiro/steering`
  - Add `pyproject.toml` pinning Python 3.11+, FastAPI, Uvicorn, Pydantic, pytest,
    pytest-cov, hypothesis
  - Add `Makefile` with targets `test`, `lint`, `run-sim`, `run-api`, `replay`
    (accepts `FILE=...`), `test-integration`, each exiting non-zero on failure
    (_Requirement 19.1, 23.2_)
  - Write the 12 steering rule files under `/.kiro/steering`, including the Requirement
    19.6 ongoing-regression-test rule and the Property-tagging convention
    (_Requirement 22.1-22.12_)
  - Add an import-linter config (or equivalent AST check) enforcing `/protocol` has zero
    imports from `/service`, `/ble`, `/web` (_Requirement 21.2_)
  - _Requirements: 21.1, 21.2, 22.1-22.12, 23.1_

- [x] 1.2 Hardware abstraction boundary and CanFrame/CanHealth types
  - Implement `protocol/receiver.py`: `CanFrame` (frozen dataclass with can_id, priority,
    pgn, source_address, dest_address, dlc, payload, capture_time, wall_clock_time,
    malformed), `CanHealth`, and the `ICanReceiver` Protocol with `frames()` and `health()`
    only — no `send()`/`transmit()` method anywhere in the type (_Requirement 1.1, 1.4, 20.5_)
  - _Requirements: 1.1, 1.4, 20.5_

- [x] 1.3 Domain_Model and Provenance types
  - Implement `protocol/domain.py`: the `UNSET` sentinel (distinct from `None` and `0`),
    `Provenance` (source_address, source_pgn, node_identity, raw_frame_ref, timestamp,
    decoder_version, quality, staleness_s, vendor_specific, value_origin, derivation),
    `TankState`, `EngineState`, `ElectricalState`, `NavigationState`, `NodeRecord`,
    `NetworkState`, `DeviceHealth`, `VesselState` exactly as specified in design.md
    (_Requirement 6.1-6.5, 11.1_)
  - Keep every entity free of transport-specific types or imports (_Requirement 21.2_)
  - _Requirements: 6.1-6.5, 11.1, 11.2, 21.2_

- [x] 1.4 Stage Gate — safety types and domain model
  - Structural test: `ICanReceiver` exposes no transmit-capable method (Property 28
    enforced at the type level)
  - Unit tests: `UNSET` distinguishable from `None` and from `0` for every optional field
    on every entity (Property 13)
  - Run `make test lint`
  - _Validates: Properties 13, 28_

## Stage 2 — Protocol_Core (parsing and reassembly)

- [x] 2.1 29-bit CAN identifier parser (pure, deterministic)
  - Implement `protocol/identifier.py`: `parse_identifier()` and `compose_identifier()`
    per the design's bit layout (priority, EDP/DP, PF, PS, source address), with PDU1
    (PF<240, destination derived) vs PDU2 (PF>=240, broadcast, no destination) branching
    (_Requirement 4.1, 4.2, 4.3, 4.7_)
  - _Requirements: 3.3, 4.1, 4.2, 4.3, 4.7_

- [x] 2.2 Malformed-frame handling in the parser
  - Add length/format validation that marks a frame `malformed=True` and returns without
    raising, so the caller can persist-then-continue (_Requirement 2.1, 20.1_)
  - _Requirements: 2.1, 20.1_

- [x] 2.3 Fast-packet reassembler
  - Implement the reassembly state machine keyed by `(pgn, source_address, sequence_id)`:
    first-frame opens a buffer recording expected length (data byte 1) and start time;
    subsequent frames require a contiguous frame counter; completion emits the message;
    discard on missing/out-of-order counter or a >5s gap since the first frame
    (_Requirement 4.4, 4.5, 4.6_)
  - Implement `is_fastpacket_pgn()` backed by a static known-fast-packet PGN table (e.g.
    126996) plus decoder-declared transport, so routing is decided from PGN metadata, not
    inferred from the frame (_Requirement 4.4_)
  - _Requirements: 4.4, 4.5, 4.6_

- [x] 2.4 Stage Gate — Protocol_Core
  - Unit tests: PDU1/PDU2 boundary values (PF=239, PF=240); malformed-stream processes
    every frame without raising; fast-packet in-order reassembly, missing-counter discard,
    out-of-order discard, >5s timeout discard, single-frame PGN bypass
  - Hypothesis property tests (>=100 iterations each): identifier round-trip over the
    valid identifier space (Property 6); destination-presence-iff-PF<240 (Property 7);
    fragmented-payload round-trip (Property 8); corruption-never-emits-wrong-message
    (Property 9); malformed input doesn't halt the parser (Property 4, parser half);
    fast-packet routing decided by PGN metadata not frame content (Property 34)
  - Run `make test lint`
  - _Validates: Properties 4 (parser half), 6, 7, 8, 9, 34_

## Stage 3 — Decoder registry and decoders

- [x] 3.1 Decoder_Registry and plugin model
  - Implement `protocol/registry.py`: `DecoderRegistry.register()` rejecting a second
    decoder for an already-mapped PGN (error names the conflicting PGN, existing decoder
    retained), `decode()` returning `DecodeResult.unknown()` for PGNs with no decoder, and
    threading `decoder.version` into produced Provenance (_Requirement 5.1-5.5_)
  - _Requirements: 5.1-5.5_

- [x] 3.2 Engine and tank decoders
  - Implement the PGN 127488/127489 Engine decoder populating `EngineState` fields only
    where present, instance-keyed by `(engine_instance, source_address)`
    (_Requirement 7.1-7.4_)
  - Implement the PGN 127505 Fluid Level decoder populating `TankState`, instance-keyed by
    `(tank_instance, source_address)`, validating `level_percent` is unset or within 0..100,
    and computing `quantity_remaining_litres` as a derived value (`level_percent *
    capacity_litres`) with `value_origin="derived"` when both inputs are present, else unset
    (_Requirement 8.1-8.7_)
  - _Requirements: 7.1-7.4, 8.1-8.7_

- [x] 3.3 Electrical, navigation, and identity decoders
  - Implement Electrical and Navigation decoders populating `ElectricalState` /
    `NavigationState` fields only where present (_Requirement 9.1-9.3_)
  - Implement the PGN 60928 ISO Address Claim decoder extracting NMEA_NAME, manufacturer
    code, device class, device function (_Requirement 10.5_)
  - Implement the PGN 126996 Product Information decoder (fast-packet, via the Stage 2
    reassembler), discarding and recording a reassembly-failure indication if it does not
    complete within 5s while leaving prior product info unchanged (_Requirement 10.6, 10.7_)
  - Every decoder leaves absent/invalid/reserved fields as `UNSET`, never a decoded zero
    (_Requirement 7.4, 8.5, 9.3_)
  - _Requirements: 9.1-9.3, 10.5, 10.6, 10.7_

- [x] 3.4 Provenance attachment and completeness
  - Ensure every code path that produces a decoded or derived value attaches a complete
    `Provenance`, recording any undeterminable field as `"unknown"` rather than omitting it
    (_Requirement 11.1, 11.2, 11.5_)
  - _Requirements: 11.1, 11.2, 11.5_

- [x] 3.5 Stage Gate — decoders
  - Unit tests: duplicate decoder registration rejected with existing decoder intact;
    unknown PGN never invokes a decoder; per-decoder present/absent/invalid/reserved field
    handling for engine, tank, electrical, navigation, identity, product-info decoders
  - Property tests: at-most-one-decoder-per-PGN (Property 10); every decoded value carries
    complete Provenance (Property 11); unset distinguishable from zero across all domain
    entities for decoder output (Property 13, decoder half); `level_percent` always
    unset-or-0..100 (Property 14); distinct engine/tank instances never collide
    (Property 15); derived `quantity_remaining_litres` always matches the derivation rule
    (Property 33)
  - Run `make test lint`
  - _Validates: Properties 10, 11, 13 (decoder half), 14, 15, 33_

## Stage 4 — Persistence and ingestion pipeline

- [x] 4.1 Raw_Capture_Log (append-only JSONL)
  - Implement `service/raw_log.py`: append-only writer with the documented JSONL record
    shape (`capture_time`, optional `wall_clock_time`, `can_id`, `priority`, `pgn`,
    `source`, `dest`, `dlc`, `payload_hex`, `malformed`, optional `decode_failure`)
    (_Requirement 12.3_)
  - Implement rotation at 100 MB or 24h (configurable), closing the current file and
    opening a timestamped successor, with rotated files remaining replayable
    (_Requirement 12.4_)
  - Implement write-failure handling: record a write-failure indication, retain prior data
    unchanged (_Requirement 12.8_)
  - _Requirements: 12.3, 12.4, 12.8_

- [x] 4.2 Session_Store (SQLite) and restart recovery
  - Implement `service/session_store.py`: SQLite schema for session metadata and decoded
    data, transactional commits (_Requirement 12.2_)
  - Implement recovery on restart: read the latest committed session, rebuild the
    Live_State_Cache to the state recorded at the last successful write
    (_Requirement 12.5_)
  - _Requirements: 12.2, 12.5_

- [x] 4.3 Ingestion_Service pipeline
  - Implement `service/ingestion.py` `ingest_batch()` in the exact stage order from
    design.md: persist raw before any decode/filter/classification, parse identifier,
    classify standard-vs-proprietary, route through the fast-packet reassembler only when
    `is_fastpacket_pgn()` says so, decode via the registry catching decoder exceptions into
    a decode-failure indication without stopping the batch, update Live_State_Cache, feed
    Discovery_Mode, persist decoded data (_Requirement 2.2, 2.3, 3.1, 3.2, 3.4-3.7, 12.1_)
  - Implement the `CAPTURE_HALTED_STORAGE_FULL` state: below 50 MB free, halt both
    persistence AND decoding of further incoming frames, continue serving the last
    persisted/live state marked stale, resume automatically once capacity is restored.
    Judgment call: an in-flight WebSocket/BLE subscriber keeps receiving the frozen last
    state (marked stale) rather than being disconnected — documented in
    `docs/architecture.md` since the requirement doesn't specify subscriber behavior during
    the transition itself (_Requirement 12.6, 12.7, 20.4_)
  - _Requirements: 2.2, 2.3, 3.1-3.7, 12.1, 12.6, 12.7, 20.4_

- [x] 4.4 Stage Gate — persistence and ingestion
  - Unit tests: raw-log rotation at size/age thresholds; append-only (no line ever
    rewritten); simulated write failure leaves prior records intact; SQLite kill-and-restart
    recovery
  - Property tests: frames written before an injected fault remain byte-identical
    afterward (Property 5); recovered state equals last successful write (Property 31);
    raw-before-decode ordering for every frame including malformed/unknown-PGN
    (Property 1); all valid frames retained regardless of manufacturer/address/PGN/
    frequency (Property 2); unknown PGNs never dropped, remain replayable (Property 3);
    malformed/decoder-raising frames don't halt the pipeline (Property 4, full); no value
    decoded or exposed while `CAPTURE_HALTED_STORAGE_FULL` is active (Property 35)
  - Run `make test lint`
  - _Validates: Properties 1, 2, 3, 4 (full), 5, 31, 35_

## Stage 5 — Discovery, Simulator, and golden fixtures

- [x] 5.1 Discovery_Mode
  - Implement `service/discovery.py`: `NodeRecord` keyed by `NodeKey` (NMEA_NAME-derived
    when known, provisional otherwise), reconciliation from provisional to NAME-based key
    on first Address Claim, mutable `current_source_address` updated in place when the
    same NAME reappears at a new address (_Requirement 10.1, 10.2_)
  - Implement the rolling 60-second message-rate window per node (_Requirement 10.3_)
  - Record identity fields (manufacturer, product info, device class/function, decoded
    device type) and retain raw identity when unresolved (_Requirement 10.4_)
  - Implement offline/online lifecycle: offline when silent beyond
    `max(3 x most-recent-interval, 30s)`, online again on next frame
    (_Requirement 10.9, 10.10_)
  - Implement address-conflict handling: two distinct NodeKeys claiming one source address
    both retained, conflict recorded on each (_Requirement 10.11_)
  - Implement exposure of known/unknown nodes, standard/proprietary PGNs, and per-node
    rates without manufacturer-based exclusion (_Requirement 10.8, 10.12_)
  - _Requirements: 10.1-10.4, 10.8-10.12_

- [x] 5.2 Simulator and fault injection
  - Implement `simulator/generator.py`: synthetic multi-node traffic generator covering
    engine, fuel tank, GPS/navigation, electrical, and unknown/proprietary node types
    (_Requirement 17.2_)
  - Implement `simulator/replay.py`: fixture replay preserving inter-frame timing within
    +/-5ms, implementing `ICanReceiver` so it drops into the boundary from Stage 1
    (_Requirement 17.1, 17.9_)
  - Implement fault injection: malformed frames, burst/high-rate traffic to a configurable
    max rate, dropped frames, CAN error conditions, surfaced through `CanHealth` without
    terminating replay (_Requirement 17.3, 17.8_)
  - _Requirements: 17.1, 17.2, 17.3, 17.8, 17.9_

- [x] 5.3 Golden fixture library
  - Author raw CAN fixtures (can_id, priority, PGN, source, dest, DLC, payload, timestamps)
    and canonical domain fixtures per entity, independent of CAN encoding
    (_Requirement 17.4, 17.5_)
  - Author the `/fixtures/golden` library: `engine_idle.jsonl`, `engine_rpm_change.jsonl`,
    `trim_change.jsonl`, `fuel_tank.jsonl`, `electrical.jsonl`, `navigation.jsonl`,
    `unknown_vendor.jsonl`, `malformed_frames.jsonl`, `mixed_multi_node.jsonl`, each with
    its documented expected assertions evaluated pass/fail (_Requirement 17.6, 17.7_)
  - _Requirements: 17.4, 17.5, 17.6, 17.7_

- [x] 5.4 Stage Gate — discovery and simulator
  - Property tests: message rate equals count in trailing 60s window (Property 17);
    offline/online round-trip threshold (Property 18); NodeKey retention under address
    competition and address moves (Property 19); NodeKey stability across a source address
    change (Property 39); replay reproduces inter-frame interval within +/-5ms (Property 30)
  - Run every golden fixture's documented assertions pass/fail
  - Run `make test lint run-sim`
  - _Validates: Properties 17, 18, 19, 30, 39_

## Stage 6 — Live cache, BLE, and extensibility stub

- [x] 6.1 Live_State_Cache and fan-out
  - Implement `service/cache.py`: in-memory store of latest decoded Domain_Model values,
    updated by the ingestion pipeline, read by every transport (_Requirement 6.6_)
  - Wire the fan-out step from `ingest_batch()` to BLE_Publisher, Web_API, and a
    DisplayAdapter stub, all consuming the same cache (_Requirement 6.6, 18.1_)
  - _Requirements: 6.6, 18.1_

- [x] 6.2 DisplayAdapter extensibility stub
  - Implement `protocol/display_adapter.py`: `DisplayAdapter` Protocol consuming
    live-state/domain events only, wired into the fan-out as a no-op reference
    implementation (_Requirement 18.1_)
  - Write `docs/future-ios.md` documenting the SwiftUI iPhone app contract (BLE scanning,
    connection, live notification subscription, gauge/status rendering, automatic
    reconnection, offline operation) with each operation's inputs/outputs
    (_Requirement 18.3_)
  - _Requirements: 18.1, 18.3_

- [x] 6.3 BLE wire format (versioned binary encoding)
  - Implement `ble/encoding.py`: versioned compact binary encoding (CBOR or a documented
    custom envelope) carrying multiple engine/tank instances without a single-instance
    assumption, an embedded schema/encoding version, and a per-value provenance summary
    (value_origin + staleness) (_Requirement 27.1, 27.2, 27.4, 27.5_)
  - Implement ATT-MTU fragmentation: ordered segments with sequence number and
    final-segment marker, reassembled by a client-side helper used in tests
    (_Requirement 27.3_)
  - _Requirements: 27.1-27.5_

- [x] 6.4 BLE_Publisher abstraction, mock implementation, and access security contract
  - Implement `ble/publisher.py`: the `BLEPublisher` Protocol (`publish()`,
    `schema_version()`) consuming only Domain_Model types, referencing neither Web_UI nor
    Web_API models (_Requirement 13.9, 18.4_)
  - Implement `MockBLEPublisher`: full GATT schema in memory, no radio required
    (_Requirement 13.10, 23.3, 23.4_)
  - Define the GATT characteristic schema (Schema Version, Device Info, Connection/Health,
    Live Engine/Fuel/Electrical/Navigation, Alerts/Status, Diagnostics) with every
    characteristic read/notify-only, no writable property anywhere
    (_Requirement 13.1, 13.2, 13.6_)
  - Implement notify-vs-read split: >1/s values via notification capped at one update per
    250ms per characteristic; <=1/s values via read (_Requirement 13.3, 13.4_)
  - Implement reconnect behavior (current values served on next read/notify, no client
    reconfiguration) and staleness (CAN disconnected -> `can_connected=false`; no frame
    for >=3s -> live data marked stale) (_Requirement 13.7, 13.8_)
  - Implement the no-raw-log-stream constraint (_Requirement 13.5_)
  - Implement the pairing-requirement contract: default-require-authenticated-pairing flag,
    reject reads/subscriptions from unpaired clients when required, explicit opt-in for
    open access, read-only guarantee holds under every pairing configuration
    (_Requirement 28.1-28.4_)
  - _Requirements: 13.1-13.10, 18.4, 28.1-28.4_

- [x] 6.5 Stage Gate — cache, BLE, extensibility
  - Integration test: one domain fixture flows through parser -> decoder -> cache and the
    same values are observable from a direct cache read and a mock BLE read
    (_Requirement 19.5_)
  - Property tests: adding/removing DisplayAdapter produces identical ingestion/decoding
    output (Property 29); fragmented BLE payloads reassemble correctly across ATT MTU
    (Property 37); BLE schema exposes no writable characteristic (Property 23); no BLE
    notification exceeds one per 250ms per characteristic (Property 22, BLE half); BLE
    live data marked stale after >=3s gap (Property 24, BLE half); unpaired-client
    rejection while required, read-only regardless of pairing config (Property 38)
  - Run `make test lint`
  - _Validates: Properties 16, 22 (BLE half), 23, 24 (BLE half), 29, 37, 38_

## Stage 7 — Web_API, Web_UI, Correlation_Mode, cross-cutting staleness

- [x] 7.1 Web_API (FastAPI)
  - Implement `web/api.py` with the seven REST endpoints (`/health`, `/live`, `/nodes`,
    `/pgns`, `/logs`, `/logs/{id}`, `POST /replay`) and the `/live/ws` WebSocket, all
    consuming Domain_Model/Pydantic views of the Live_State_Cache (_Requirement 14.1_)
  - Bind only to loopback and configured local interfaces by default (_Requirement 14.3_)
  - Implement Pydantic-based request validation rejecting missing/malformed/out-of-range
    parameters with an error naming the failed parameter, performing no operation on
    rejection (_Requirement 14.4, 14.5_)
  - Implement the concurrency/size limits: reject replay/log requests beyond 10 concurrent
    operations or a 100 MB file, with an error naming the exceeded limit
    (_Requirement 14.6_)
  - Implement safe path resolution for `/logs/{id}` and `/replay`: `Path.resolve()` +
    `is_relative_to(capture_dir)`, rejecting any path outside the capture directory
    without accessing it (_Requirement 14.7, 14.8_)
  - Implement the WebSocket/SSE push cap at one update per 250ms per client
    (_Requirement 14.2_)
  - Confirm no outbound internet request is issued anywhere in capture/decode/persist/
    serve (_Requirement 14.9_)
  - _Requirements: 14.1-14.9_

- [x] 7.2 Web_UI (single-page, no build step)
  - Implement `web/static/index.html` + vanilla JS: live dashboard for engine, tank,
    electrical, navigation values and network health, consuming the Domain_Model over
    WebSocket with REST fallback, updating within 1s of a cache change
    (_Requirement 15.1, 15.2, 15.4_)
  - Implement responsive layout with no horizontal scroll from 320px to 1920px
    (_Requirement 15.3_)
  - Implement the discovery view (per-node manufacturer, device class, product, observed
    PGNs, fps) with an empty-state indicator when no nodes are discovered
    (_Requirement 15.7, 15.9_)
  - Implement node drill-down: raw frames, decoded values, timestamps, frequency, payload
    variation, source Provenance (_Requirement 15.8_)
  - Implement the disconnected/degraded indicator while CAN is disconnected or no frame
    has arrived for >=3s (_Requirement 15.5_)
  - Confirm zero outbound requests after initial page load (_Requirement 15.6_)
  - _Requirements: 15.1-15.9_

- [x] 7.3 Correlation_Mode
  - Implement `service/correlation.py`: a recorded action with baseline-before/action/
    after windows; candidate fields are exactly those whose value changed within the
    action window, all other changes classified as unrelated traffic
    (_Requirement 16.1, 16.2, 16.3, 16.7_)
  - Implement correlation strength scoring in [0.0, 1.0] and the configurable confirmation
    threshold gate (below threshold -> candidate requiring confirmation, not confirmed)
    (_Requirement 16.1, 16.4, 16.6_)
  - Implement the zero-candidate report path (_Requirement 16.5_)
  - Implement confirmation-requires-repetition: >=2 repeated actions with a consistent
    candidate change before confirming; a single action or inconsistent repeats leave the
    field unconfirmed (_Requirement 16.8, 16.9_)
  - _Requirements: 16.1-16.9_

- [x] 7.4 Data-quality/staleness surfacing across transports
  - Ensure every served value (BLE characteristic, Web_API response, WebSocket push, UI
    render) carries its quality and staleness marker from Provenance, so a stale or
    spoofed value is distinguishable from current valid data (_Requirement 20.6_)
  - _Requirements: 20.6_

- [x] 7.5 Stage Gate — Web_API, Web_UI, Correlation_Mode
  - Unit/integration tests per Web_API endpoint including validation-rejection and
    path-traversal cases; browser-driven or DOM-level test for the UI degraded-state
    indicator and empty discovery state
  - Property tests: path resolution only within capture dir including traversal payloads
    (Property 26); invalid parameters rejected without side effects (Property 27); REST/WS
    push rate capped at 250ms per client (Property 22, full); same domain fixture yields
    identical values across BLE/REST/WS (Property 16, full); quality/staleness marker
    present on every served value across BLE/REST/WS (Property 25); UI staleness surfaced
    identically to BLE (Property 24, full); correlation candidates exactly in-window
    changers with bounded strength (Property 20); confirmation requires meeting the
    threshold (Property 21); confirmation requires >=2 consistent repeats (Property 36)
  - Run `make test lint run-api test-integration`
  - _Validates: Properties 16 (full), 20, 21, 22 (full), 24 (full), 25, 26, 27, 36_

- [ ] 7.6 User-configurable multi-instance dashboard (selectable sensors + gauges)
  - Add a selection panel to `web/static/index.html` listing every entity instance
    currently present in `/api/v1/live` (each engine/tank keyed by `instance:source`,
    plus the electrical/navigation singletons), with a checkbox per instance and a
    per-attribute checklist once an instance is selected (_Requirement 29.1, 29.3_)
  - Implement the client-only dashboard configuration model (`{schema_version, cards:
    [{entity_type, instance_key, attributes, order}]}`), persisted to `localStorage`
    on every change and read once on load — no new API endpoint, no server-side
    storage (_Requirement 29.2, 29.6, 29.7_)
  - Implement card rendering as a second view over the existing WebSocket/REST payload
    (reusing `renderLive`'s data, not a second poll loop), one card per selected
    instance showing exactly its selected attributes (_Requirement 29.2, 29.3, 29.8_)
  - Implement the gauge-vs-metric split: attributes in the static range table
    (`level_percent`, `rpm`, `coolant_temp`, `oil_pressure`, `fuel_rate`,
    `battery_voltage`, `charging_voltage`, `depth`) render as an inline-SVG circular
    dial scaled to their range (configurable max for `rpm`/`fuel_rate`/`depth`);
    attributes outside the table keep the existing numeric/textual metric row
    (_Requirement 29.4, 29.5_)
  - Implement drag-to-reorder (or equivalent up/down control for touch) on dashboard
    cards, persisting the new order immediately (_Requirement 29.6, 29.7_)
  - Implement the dashboard-area empty state (distinct from the discovery view's empty
    state) shown while zero instances are selected, pointing the user at the selection
    panel (_Requirement 29.9_)
  - Implement stale-instance handling: a card whose `instance_key` is no longer present
    in the latest payload keeps rendering its last-known values with the existing
    stale/quality-badge styling rather than disappearing (_Requirement 29.10_)
  - _Requirements: 29.1-29.10_

- [ ] 7.7 Stage Gate — configurable dashboard
  - DOM/unit tests: selecting/deselecting an instance adds/removes exactly its card;
    selecting a subset of attributes renders exactly that subset; reordering persists
    and survives a simulated reload (localStorage round-trip); an attribute in the range
    table renders a dial, one outside it renders a metric row; a card for an
    instance_key no longer in the live payload keeps its last values and stale styling
  - Property test: dashboard rendering equals exactly the persisted selection and survives
    a reload with no further user action (Property 40)
  - Run `make test lint run-api`
  - _Validates: Property 40; Requirements 29.1-29.10_

- [ ] 7.8 Mobile-first Web_UI layout
  - Rework `web/static/index.html`'s base (no-media-query) CSS to target a phone
    portrait viewport directly: single-column card stack, gauge/font sizing for
    arm's-length outdoor reading, with the existing desktop grid moved behind a
    `@media (min-width: 768px)` rule rather than being the default
    (_Requirement 30.1_)
  - Apply a minimum 44x44 CSS pixel touch target to every interactive control added for
    Requirement 29 (instance checkboxes, attribute checklist entries, reorder controls)
    (_Requirement 30.2_)
  - Extend the existing 320-1920px no-horizontal-scroll check with explicit assertions
    at 320px, 375px, and 430px covering the dashboard cards and gauges from Stage 7.6
    (_Requirement 30.3_)
  - _Requirements: 30.1-30.3_

- [ ] 7.9 LAN bind for phone testing, with an explicit never-wildcard guard
  - Extend `web/main.py`'s bind-host guard from its current `10.`/`192.168.` prefix
    check to the full RFC 1918 range (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16) via
    `ipaddress.ip_address(host).is_private and not is_loopback`, while explicitly
    keeping the existing refusal of `0.0.0.0`/`::` and any non-private address
    (_Requirement 30.4, 30.5_)
  - Add `docs/testing.md` (or `docs/api.md`) a "testing from your phone" section:
    discovering the Host_Platform's LAN IP on macOS/Linux/Windows, the
    `UNOQ_BIND_HOST=<ip> make run-api` invocation, and an explicit no-authentication/
    trusted-network-only warning (_Requirement 30.6_)
  - _Requirements: 30.4-30.6_

- [ ] 7.10 Stage Gate — mobile-first layout and LAN bind guard
  - Unit tests: bind guard accepts loopback, a 10.x address, a 172.16-31.x address, and
    a 192.168.x address (the common home/marina-router and Arduino-UNO-Q-advertised
    range); bind guard refuses `0.0.0.0`, `::`, and a public address, in every case
    with no change to the existing refusal behavior
  - Property test: the bind guard accepts iff loopback or private-non-unspecified, for
    arbitrary candidate host strings including malformed ones (Property 41)
  - DOM/viewport tests at 320px/375px/430px confirming no horizontal scroll and
    44x44px minimum touch targets on Requirement 29's controls
  - Run `make test lint run-api`
  - _Validates: Property 41; Requirements 14.3, 30.1-30.6_

## Stage 8 — Host-stack hardening and sign-off

- [x] 8.1 Capture promotion to golden fixtures
  - Implement `scripts/promote_capture.py`: documented procedure converting a recorded
    Raw_Capture_Log into a `/fixtures/golden` entry, requiring expected assertions,
    preserving raw frames and Provenance unchanged, and registering the new fixture in the
    fixture-driven test suite (_Requirement 25.1-25.4_)
  - _Requirements: 25.1-25.4_

- [x] 8.2 Security hardening pass
  - Verify and, where missing, add handling so corrupted replay files and unexpected
    payloads are quarantined/discarded with the process continuing
    (_Requirement 20.1, 20.3_)
  - Verify hostile Web_API/BLE requests are rejected with an error to the caller while
    valid clients keep being served; add any missing cases found (_Requirement 20.2_)
  - Add a secret-scan lint step (e.g. detect-secrets) to `make lint` enforcing no
    plaintext secrets/credentials/private keys in tracked source (_Requirement 20.7_)
  - _Requirements: 20.1, 20.2, 20.3, 20.7_

- [x] 8.3 Coverage gates and per-area test completeness audit
  - Configure `pytest-cov` thresholds in `make test`: fail if pure protocol/decoder
    coverage <90% or overall host-testable coverage <80% (_Requirement 19.2, 19.3_)
  - Audit that at least one automated test exists for each area: CAN/protocol parsing,
    fuel/tank decoding, BLE, Linux ingestion, persistence/replay, UI, integration flows;
    add any missing (_Requirement 19.4_)
  - _Requirements: 19.1-19.5_

- [x] 8.4 Documentation set (host-testable scope)
  - Write/update `README.md`, `docs/architecture.md` (incl. the Stage 4 storage-halt
    subscriber-behavior note), `docs/nmea-discovery.md`, `docs/tank-data.md`,
    `docs/ble-protocol.md` (incl. the security model), `docs/api.md` (OpenAPI-fed),
    `docs/log-format.md`, `docs/testing.md`, `docs/future-display.md`, `docs/security.md`
    (_Requirement 21.3, 21.4_)
  - _Requirements: 21.3, 21.4_

- [~] 8.5 Stage Gate — full host-testable sign-off (BLOCKED — see docs/architecture.md "Stage 8.5 sign-off status")
  - [x] Promote one real/simulated capture through `scripts/promote_capture.py` and confirm it
    runs as a regression test (_Requirement 25.4_) — demonstrated with a fresh 750 RPM
    idle capture; not kept as a fixture since `engine_idle.jsonl` already covers the
    identical scenario (no duplicate added to the golden library)
  - [x] Run `make test lint run-sim run-api replay test-integration` on macOS — all green
    (157/157 tests, lint clean, run-sim/run-api/replay all boot and behave correctly,
    run-api confirmed loopback-only)
  - [ ] Run the same sequence on a Windows or Linux host (CI matrix or local VM)
    (_Requirement 23.1, 23.2_) — Requirement 23.1 permits either Windows or Linux;
    WSL2 (a real Linux kernel) satisfies it. **Still open — needs the user to actually
    run `make test lint run-sim run-api replay test-integration` on their WSL box (or
    a CI matrix) and report the result**, since the requirement is "demonstrated",
    not "possible in principle".
  - [x] Audit for any OS-specific dependency outside the documented BlueZ (Linux-only)
    boundary; confirm it's isolated behind an abstraction with a mock substitute
    (_Requirement 23.3, 23.4_) — audited: zero OS-specific imports exist anywhere in the
    current codebase (BlueZ itself isn't even wired in yet — that's Stage 10); nothing to
    isolate because nothing OS-specific has been introduced
  - [x] Confirm coverage thresholds pass and every per-area test exists — 93.11% overall
    (≥80% required), 98% protocol/ (≥90% required), all 7 required test areas + both
    19.5 cross-cutting tests present (confirmed in Stage 8.3)
  - [x] **Spec decision resolved 2026-10-01:** `quantity_remaining_litres =
    (level_percent / 100) * capacity_litres` (Requirement 8.6, corrected from the
    original spec-literal formula). User confirmed real-world tank-sender convention:
    capacity is configured once in litres (e.g. 90 L, 120 L), level is % of that
    setting — matching NMEA 2000 PGN 127505's own field definitions. Updated
    requirements.md, design.md, `protocol/decoders/tank.py`, `protocol/domain.py`,
    and the Stage 3/6 pinning tests together. Property 33 now certified against the
    corrected formula.
  - **This gate is the pass/fail checkpoint for the entire host-testable MVP.** All of
    Requirements 1-23, 25, and 27-30 (host-testable scope) and Properties 1-31, 33-41
    must be demonstrated green here before Stage 9 (hardware) begins. The formula
    decision is resolved; the second-OS run (on the user's WSL box) and the new
    Stage 7.6-7.10 configurable-dashboard/mobile-first/LAN-bind work (Requirements 29-30,
    Properties 40-41) both remain before this box can be checked `[x]`.
  - _Validates: full regression suite; Requirements 19.1-19.5, 23.1-23.4, 25.4, 29.1-29.10, 30.1-30.6_

---

## Hardware Target phase (Arduino UNO Q) — begins only after Stage 8 Gate passes

## Stage 9 — Firmware and bench safety validation

- [ ] 9.1 MCP2518FDT firmware: SPI init, silent mode, termination off
  - Implement MCU firmware initializing SPI and the MCP2518FDT for classical CAN at
    250 kbit/s, 29-bit extended frames (_Requirement 24.2_)
  - Configure the CAN controller/transceiver into silent/listen-only mode before the
    Transmit_Path is electrically connected to the Backbone, reporting the configured mode
    as a verifiable health metric; if silent mode cannot be configured, keep Transmit_Path
    disabled, raise a startup fault, and continue receiving (_Requirement 1.2, 1.3, 24.3_)
  - Keep the switchable 120-ohm termination off, reporting termination state as a
    verifiable health metric (_Requirement 1.5, 24.4_)
  - Hold the Transmit_Path in a non-driving (high-impedance) state through startup,
    shutdown, crash, and reboot (_Requirement 1.6_)
  - Expose health metrics (total frames, fps, dropped frames, queue depth, controller
    state, CAN error counters, uptime) over USB or the MCU-to-Linux bridge/RPC
    (_Requirement 24.5_)
  - _Requirements: 1.2, 1.3, 1.5, 1.6, 24.2-24.5_

- [ ] 9.2 Stage Gate — bench validation of the silent-mode fail-safe (before any Backbone
      connection)
  - On the bench, measure and validate that the silent-mode control line (shield D4 with
    the P5 shunt fitted) holds the transceiver transmit channel non-driving throughout MCU
    reset, boot, and firmware startup (_Requirement 24.8_)
  - Where firmware-only control cannot guarantee the non-driving state during reset/boot,
    document the hardware fail-safe (e.g. a pull-up making silent mode the power-on
    default) in the Hardware_Setup_Guide (_Requirement 24.9_)
  - Confirm on the bench that no CAN Transmit_Path is enabled, consistent with
    Requirement 1 (_Requirement 24.7_)
  - **This gate must pass before the device is ever connected to a live Backbone.**
  - _Validates: 1.2, 1.3, 1.5, 1.6, 24.2-24.9_

## Stage 10 — Deployment, equivalence, real BLE, and final docs

- [ ] 10.1 docs/hardware.md and docs/boat-test.md
  - Write the Hardware_Setup_Guide: physical assembly of UNO Q + EVAL-ADM3055E-ARDZ
    shield, SPI/MCP2518FDT pin mapping with a wiring diagram or pin table, the
    120-ohm-termination-off setting with the authoritative-Backbone-termination note,
    CAN-H/CAN-L/GND2 terminal connections identifying the isolated (GND2) side of the
    ADM3055E, USB-C power with the explicit NMEA 2000 +12V-never-connected safety warning,
    verification steps for silent-mode and termination-off using the Stage 9 health
    metrics, and a step-ordered first-power-on checklist referencing Requirement 1
    (_Requirement 1.8, 26.1-26.7_)
  - Write `docs/boat-test.md`: on-boat connection and capture procedure including
    termination-off verification and non-disruption confirmation (_Requirement 26.8_)
  - _Requirements: 1.8, 26.1-26.8_

- [ ] 10.2 Deploy procedure and host/hardware equivalence verification
  - Write the documented `/scripts` deploy procedure for MCU firmware plus the Linux
    Ingestion_Service, transports, and Web_UI onto the Hardware_Target (_Requirement 24.1_)
  - Run the same Simulator/Golden_Fixture inputs from Stage 5 through the hardware capture
    path and confirm equivalent decoded Domain_Model values, allowing for real-time timing
    differences (_Requirement 24.6_)
  - _Requirements: 24.1, 24.6_

- [ ] 10.3 Real BLE: BlueZ GATT peripheral spike and implementation
  - Build the "BlueZ GATT peripheral on the actual UNO Q" proof-of-concept spike to lock
    the concrete library choice (`bluezero` vs `python-dbus` vs alternative) behind the
    existing `BLEPublisher` abstraction from Stage 6
  - Implement `BlueZPublisher` satisfying the same schema, notify/read split, reconnect,
    and staleness behavior already proven against `MockBLEPublisher` (_Requirement 13.1-13.10_)
  - Implement authenticated, bonded, encrypted pairing on the real BlueZ stack, defaulting
    to requiring pairing before vessel-data access, with open access as an explicit opt-in
    (_Requirement 28.1, 28.2, 28.3_)
  - _Requirements: 13.1-13.10, 28.1-28.3_

- [ ] 10.4 Final documentation pass (hardware scope)
  - Write/update `docs/can-safety.md` covering the full receive-only design and the four
    gates on any future transmit capability (_Requirement 21.3_)
  - Confirm every documentation file listed in Requirement 21.3 exists and reflects the
    as-built hardware behavior (_Requirement 21.3, 21.4_)
  - _Requirements: 21.3, 21.4_

- [ ] 10.5 Stage Gate — hardware sign-off
  - Property test (hardware-in-the-loop): host and hardware capture paths yield equivalent
    decoded values (Property 32)
  - Confirm no CAN Transmit_Path is enabled on the deployed hardware (Property 28,
    hardware re-check)
  - Confirm all documentation files exist and match as-built behavior
  - **This gate is the pass/fail checkpoint before the device goes on a real boat.**
  - _Validates: Properties 28 (hardware re-check), 32; Requirements 21.3, 21.4, 24.1, 24.6_
