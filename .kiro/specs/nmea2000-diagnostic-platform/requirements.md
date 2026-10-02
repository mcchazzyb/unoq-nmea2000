# Requirements Document

## Introduction

The NMEA 2000 Diagnostic Platform is a passive, receive-only marine network diagnostic and discovery tool. It captures raw NMEA 2000 (CAN) traffic, decodes standard and vendor-specific messages where decoders exist, preserves unknown traffic, and exposes a shared domain model over multiple transports (BLE, REST, WebSocket/SSE) and a local web UI.

The Minimum Viable Product (MVP) is fully host-testable in Python on a development machine with no physical CAN hardware, no Bluetooth radio, and no vessel. A simulator and golden fixtures provide synthetic and replayable traffic that drive the entire Linux service, API, BLE domain, and web UI stack.

Safety is the highest priority: the MVP is strictly passive receive-only, never transmits on the CAN bus, defaults termination off, and must never disrupt the physical NMEA 2000 backbone. Capture-first is the second priority: raw bus data is preserved before any decoding, filtering, or classification, and unknown or proprietary traffic is never discarded.

This document defines requirements only (the "what"). Architecture and implementation choices (the "how") are recorded in the design document.

## Glossary

- **Platform**: The complete NMEA 2000 Diagnostic Platform software system covering protocol parsing, ingestion, persistence, transports, and UI.
- **Host_Platform**: A development machine (macOS or Windows/Linux PC) on which the host-testable stack runs without physical CAN or Bluetooth hardware.
- **Hardware_Target**: The Arduino UNO Q (STM32U585 MCU plus Qualcomm Linux MPU) with the EVAL-ADM3055E-ARDZ CAN shield, to which validated software is deployed for on-device validation.
- **Hardware_Setup_Guide**: The wiring and physical assembly documentation (docs/hardware.md) covering shield mounting, SPI/CAN pin mapping, jumper and termination settings, power, and NMEA 2000 backbone connection for the Hardware_Target.
- **Protocol_Core**: The platform-independent Layer 2 component that parses 29-bit CAN identifiers and reassembles fast-packet messages. No hardware or UI dependencies.
- **CAN_Receiver**: The abstraction (`ICanReceiver`) that supplies `CanFrame` batches, implemented by real hardware sources, the Simulator, or fixture replay.
- **CanFrame**: A single captured 29-bit extended CAN frame with CAN ID, priority, PGN, source address, destination (where applicable), DLC, payload bytes, a monotonic Capture_Time, and an optional UTC/Wall_Clock_Time.
- **CanHealth**: The reported health state of a CAN source, including connected state, frames per second, dropped frames, and error count.
- **PGN**: Parameter Group Number, derived from the CAN identifier.
- **Source_Address**: The 8-bit address of the node that transmitted a frame. Source_Address is an attribute of a node that may change over time, not the node's identity key.
- **NodeKey**: A stable identifier for a discovered node derived from the NMEA_NAME (from PGN 60928 ISO Address Claim) when available, and a provisional key otherwise.
- **NMEA_NAME**: The 64-bit ISO NAME carried in a PGN 60928 Address Claim, uniquely identifying a device independent of its current Source_Address.
- **Fast_Packet**: The NMEA 2000 multi-frame transport used for messages exceeding 8 bytes.
- **Decoder**: A registered component that converts a decoded PGN payload into domain values.
- **Decoder_Registry**: The plugin registry mapping PGNs to Decoders.
- **Domain_Model**: The generic, transport-independent set of entities (VesselState, EngineState, TankState, ElectricalState, NavigationState, NetworkState, DeviceHealth). NetworkState nodes are keyed by NodeKey.
- **Live_State_Cache**: The in-memory store of the most recent decoded Domain_Model values.
- **Ingestion_Service**: The Layer 3 Linux component that receives frame batches, normalizes events, persists data, decodes known PGNs, maintains the Live_State_Cache, and serves transports.
- **Raw_Capture_Log**: The append-only persistent log of raw captured frames.
- **Session_Store**: The SQLite database holding session metadata and decoded data.
- **Discovery_Mode**: The component that maintains the inventory of observed nodes and their identity, PGNs, and rates.
- **Correlation_Mode**: The guided workflow that helps confirm decoded values correspond to real physical behavior.
- **Provenance**: The origin metadata retained for every decoded value.
- **Value_Origin**: A provenance attribute marking whether a value was directly decoded from a frame ("decoded") or computed from other decoded values ("derived").
- **Capture_Time**: The monotonic timestamp at which a frame was captured.
- **Wall_Clock_Time**: The optional UTC/wall-clock timestamp associated with a captured frame where a real-time clock is available.
- **BLE_Service**: The read-only GATT service exposing Domain_Model values over Bluetooth Low Energy.
- **BLE_Publisher**: The transport-agnostic interface publishing Domain_Model values to BLE, with a real (BlueZ/D-Bus) implementation and a mock in-memory implementation.
- **Web_API**: The REST and WebSocket/SSE HTTP interface.
- **Web_UI**: The single self-contained responsive HTML/JS page served by the Web_API.
- **Simulator**: The host-based synthetic and replay CAN/NMEA traffic generator.
- **Golden_Fixture**: A version-controlled capture fixture with expected assertions.
- **Display_Adapter**: The abstraction (`DisplayAdapter`) that consumes live-state/domain events for a future screen.
- **Transmit_Path**: Any capability that would send frames onto the CAN bus.
- **Backbone**: The physical NMEA 2000 network cabling and its authoritative termination.

## Requirements

### Requirement 1: Passive Receive-Only Safety

**User Story:** As a boat owner, I want the platform to be strictly passive on the NMEA 2000 network, so that connecting my diagnostic tool cannot disrupt navigation or engine instruments.

#### Acceptance Criteria

1. THE Platform SHALL operate in receive-only mode in the MVP, consuming CanFrame data and transmitting zero frames on the Backbone during any continuous operating window of 24 hours or longer.
2. WHERE the CAN controller or transceiver supports a silent or listen-only mode, THE Platform SHALL configure the CAN source to operate in that mode before the CAN Transmit_Path is electrically connected to the Backbone.
3. IF the CAN source cannot be configured into silent or listen-only mode, THEN THE Platform SHALL keep the CAN Transmit_Path disabled, indicate a startup fault to the user, and continue consuming CanFrame data without transmitting.
4. THE Platform SHALL keep the Transmit_Path disabled by default, requiring all four of the following before any frame is transmitted: a separate approved feature spec, compile-time enablement, runtime enablement, and an explicit user confirmation action.
5. THE Platform SHALL default CAN Backbone termination to off, treating the device as a drop node and treating Backbone termination as authoritative.
6. WHILE the Platform starts up, shuts down, crashes, or reboots, THE Platform SHALL hold the CAN Transmit_Path in a non-driving (high-impedance) state so that the NMEA Backbone continues operating without added bus load.
7. THE Platform SHALL treat all engine, trim, throttle, steering, ignition, and control-related values as informational only, exposing no control functions.
8. THE Platform SHALL draw operating power from USB-C in the MVP, and the NMEA 2000 NET-S (+12V) conductor SHALL be electrically unconnected to the Hardware_Target and shield in the USB-powered MVP.

### Requirement 2: Robustness Against Malformed Traffic

**User Story:** As a diagnostic user, I want the software to keep running when it encounters bad or unexpected data, so that a single corrupt frame never takes the tool offline.

#### Acceptance Criteria

1. WHEN the Protocol_Core receives a malformed frame, THE Protocol_Core SHALL record the frame in the Raw_Capture_Log with a malformed-frame indication and continue processing the next received frame without terminating.
2. WHEN the Ingestion_Service receives a frame carrying an unknown or vendor-specific PGN, THE Ingestion_Service SHALL retain the raw frame in the Raw_Capture_Log and continue processing the next received frame.
3. IF a Decoder raises an error while decoding a frame, THEN THE Ingestion_Service SHALL preserve the raw frame in the Raw_Capture_Log, record a decode-failure indication associated with that frame, and continue processing the next received frame.
4. WHEN the Simulator injects malformed frames, burst traffic at up to the CAN bus maximum sustained frame rate, dropped frames, or CAN error conditions, THE Platform SHALL continue operating without terminating and without losing any frame already persisted to the Raw_Capture_Log.
5. IF the Platform receives frames faster than it can process them, THEN THE Platform SHALL buffer or persist received frames up to available storage capacity and SHALL continue operating without terminating.

### Requirement 3: Capture First, Classify Second

**User Story:** As a reverse-engineer, I want raw bus data preserved before any decoding or filtering, so that I never lose traffic I cannot yet interpret.

#### Acceptance Criteria

1. WHEN a CanFrame is received, THE Ingestion_Service SHALL persist the raw frame to the Raw_Capture_Log before performing any decoding, filtering, or classification of that frame.
2. WHEN a valid extended 29-bit NMEA 2000 frame is received, THE Platform SHALL persist it to the Raw_Capture_Log without pre-filtering by manufacturer, source address, known PGN, node type, or expected message frequency.
3. WHEN a captured frame is processed, THE Platform SHALL identify the Source_Address, derive the PGN, and classify the frame as standard or proprietary.
4. WHERE a Decoder exists for a captured frame's PGN, THE Platform SHALL decode the frame.
5. WHERE no Decoder exists for a captured frame's PGN, THE Platform SHALL retain the raw frame in the Raw_Capture_Log and SHALL keep the frame visible, loggable, exportable, and replayable.
6. THE Platform SHALL retain every captured frame regardless of its manufacturer of origin and SHALL NOT discard a frame on the basis of its source manufacturer.
7. IF a Decoder fails or is absent for a captured frame, THEN THE Raw_Capture_Log SHALL retain the raw frame.

### Requirement 4: 29-bit CAN Identifier and Fast-Packet Parsing

**User Story:** As a developer, I want deterministic protocol parsing isolated from device assumptions, so that identifier decoding is fully testable and correct.

#### Acceptance Criteria

1. WHEN the Protocol_Core parses a 29-bit CAN identifier, THE Protocol_Core SHALL derive priority, PGN, and Source_Address from the identifier.
2. IF the parsed PDU format value is below 240, THEN THE Protocol_Core SHALL apply PDU1 addressing logic and SHALL derive a destination address from the identifier.
3. IF the parsed PDU format value is 240 or above, THEN THE Protocol_Core SHALL apply PDU2 broadcast logic and SHALL NOT derive a destination address.
4. WHEN Fast_Packet frames belonging to one message are received in order with a contiguous sequence of frame counters, THE Protocol_Core SHALL reassemble the frames into a single message using the sequence identifier and frame counters.
5. IF a Fast_Packet reassembly has a missing frame counter, a frame arriving out of order beyond one frame position, or does not complete within 5 seconds of the first frame, THEN THE Protocol_Core SHALL discard the incomplete reassembly buffer for that sequence identifier and SHALL continue processing subsequent frames.
6. THE Protocol_Core SHALL preserve the raw payload bytes for every parsed frame.
7. THE Protocol_Core SHALL perform identifier parsing without applying higher-level assumptions about device meaning.

### Requirement 5: Decoder Registry and Plugin Model

**User Story:** As a developer, I want decoders registered through a plugin model, so that new PGN support can be added without changing capture or ingestion code.

#### Acceptance Criteria

1. THE Decoder_Registry SHALL map each PGN to at most one registered Decoder and SHALL allow registration of additional Decoders for PGNs that have no existing registered Decoder.
2. IF a Decoder is registered for a PGN that already has a registered Decoder, THEN THE Decoder_Registry SHALL reject the registration, SHALL retain the existing Decoder, and SHALL return an error indication reporting the conflicting PGN.
3. WHEN a frame with a PGN that has a registered Decoder is processed, THE Ingestion_Service SHALL invoke the registered Decoder for that PGN.
4. WHEN a frame with a PGN that has no registered Decoder is processed, THE Ingestion_Service SHALL retain the frame in an unknown state and SHALL NOT invoke a Decoder.
5. WHEN a Decoder produces a decoded value, THE Decoder_Registry SHALL record the Decoder version used to produce that decoded value.

### Requirement 6: Generic Shared Domain Model

**User Story:** As an architect, I want a generic domain model shared across all transports, so that BLE, REST, WebSocket, and future clients consume one consistent representation.

#### Acceptance Criteria

1. THE Domain_Model SHALL define the entities VesselState, EngineState, TankState, ElectricalState, NavigationState, NetworkState, and DeviceHealth.
2. THE Platform SHALL avoid defining a single monolithic manufacturer-specific data class such as a SuzukiData class.
3. THE TankState entity SHALL define the fields tank_instance, fluid_type, level_percent, capacity_litres, quantity_remaining_litres, source_address, source_pgn, and updated_at.
4. WHERE the NMEA source does not provide a TankState field value, THE TankState entity SHALL represent that field as optional and unset, distinguishable from a decoded value of zero.
5. THE Domain_Model SHALL represent updated_at as a timestamp indicating the time at which the entity's field values were most recently decoded.
6. THE BLE_Service and Web_API SHALL consume the Domain_Model entities rather than transport-specific data structures, such that the same field values are observable across every transport for a given decoded frame.

### Requirement 7: Engine Data Decoding

**User Story:** As a diagnostic user, I want engine parameters decoded where the traffic supports it, so that I can observe engine behavior for informational purposes.

#### Acceptance Criteria

1. WHERE frames for candidate engine PGNs 127488 and 127489 are observed, THE Platform SHALL decode available engine values including RPM, coolant temperature, oil pressure, alternator voltage, engine hours, fuel rate, trim, and status into EngineState, populating each field only where present in the frame and leaving all other fields unset.
2. THE Platform SHALL support multiple engine instances concurrently, associating each decoded EngineState with a key formed by its engine instance and Source_Address, such that no two distinct engine instances share one EngineState.
3. THE Platform SHALL expose decoded engine values as read-only informational data, performing no control, actuation, or write-back action toward any NMEA source based on those values.
4. IF an engine field value is absent, invalid, or reserved, THEN THE Platform SHALL represent that field as unset in EngineState, distinguishable from a decoded value of zero.

### Requirement 8: Fuel and Tank Level Decoding

**User Story:** As a diagnostic user, I want tank levels decoded from PGN 127505, so that I can see fuel and other fluid levels with their source information.

#### Acceptance Criteria

1. WHEN a PGN 127505 Fluid Level frame is decoded, THE Platform SHALL populate TankState with tank_instance, fluid_type, level_percent, and capacity_litres where present, leaving any field absent from the frame unset.
2. THE Platform SHALL support multiple tank instances and multiple fluid types concurrently, associating each decoded TankState with a key formed by its tank_instance and Source_Address, such that no two distinct tank instances share one TankState.
3. IF capacity_litres is missing from a PGN 127505 frame, THEN THE Platform SHALL leave capacity_litres and quantity_remaining_litres unset while retaining level_percent.
4. WHEN a level_percent value within the range 0 through 100 inclusive is decoded, THE Platform SHALL represent that value unchanged in TankState.
5. IF a PGN 127505 field carries an invalid or reserved value, or IF a decoded level_percent value falls outside the range 0 through 100 inclusive, THEN THE Platform SHALL leave the corresponding TankState field unset, distinguishable from a decoded value of zero.
6. WHEN both level_percent and capacity_litres are available, THE Platform SHALL compute quantity_remaining_litres as a derived value and SHALL mark its Value_Origin as "derived" recording the derivation ((level_percent / 100) x capacity_litres); IF either input is unset, THEN quantity_remaining_litres SHALL be unset.
7. THE Platform SHALL mark directly-decoded TankState fields with Value_Origin "decoded".
8. THE Platform SHALL treat capacity_litres (PGN 127505) and fuel_rate (PGN 127489) as already expressed in litres and litres-per-hour on the wire per the NMEA 2000 standard, regardless of whether the transmitting sender/gateway's own installer configuration uses gallons internally; THE Platform SHALL perform no unit conversion at decode time, since any gallon-to-litre conversion is the responsibility of the transmitting device before the value reaches the bus.

### Requirement 9: Electrical and Navigation Decoding

**User Story:** As a diagnostic user, I want electrical and navigation values decoded, so that I can observe battery, charging, and position data.

#### Acceptance Criteria

1. WHERE frames carrying electrical parameters are observed, THE Platform SHALL decode available values including battery voltage, charging voltage, current, battery state, and DC source into ElectricalState, populating each field only where present and leaving all other fields unset.
2. WHERE frames carrying navigation parameters are observed, THE Platform SHALL decode available values including GPS position, course over ground, speed over ground, heading, depth, water temperature, and wind into NavigationState, populating each field only where present and leaving all other fields unset.
3. IF an electrical or navigation field value is absent, invalid, or reserved, THEN THE Platform SHALL represent that field as unset, distinguishable from a decoded value of zero.

### Requirement 10: Node and PGN Discovery

**User Story:** As a diagnostic user, I want an inventory of every node and PGN observed on the network, so that I can understand the network without hard-coded assumptions.

#### Acceptance Criteria

1. THE Discovery_Mode SHALL maintain an inventory of observed nodes keyed by a stable NodeKey, where the NodeKey is derived from the NMEA_NAME when a PGN 60928 Address Claim has been observed, and a provisional NodeKey otherwise.
2. THE Discovery_Mode SHALL treat Source_Address as a mutable attribute of a node rather than its identity, and WHEN a node's NMEA_NAME appears at a new Source_Address, THE Discovery_Mode SHALL retain the same NodeKey and update the Source_Address attribute.
3. WHEN a frame is observed from a node, THE Discovery_Mode SHALL record, per NodeKey, the current Source_Address, the set of observed PGNs, a first-seen timestamp, a last-seen timestamp, and a message rate computed as the count of messages observed within a rolling 60-second window.
4. WHERE identity traffic is observed, THE Discovery_Mode SHALL record, per NodeKey, any manufacturer, product information, device class, device function, and decoded device type available, and SHALL retain the raw identity data when the identity cannot be resolved to a decoded device type.
5. WHEN a PGN 60928 ISO Address Claim frame is observed, THE Discovery_Mode SHALL extract and record the NMEA_NAME, manufacturer code, device class, and device function.
6. WHEN a complete PGN 126996 Product Information message is observed, THE Discovery_Mode SHALL reassemble and record the product information against the corresponding NodeKey.
7. IF a multi-frame PGN 126996 message cannot be reassembled within 5 seconds of its first frame, THEN THE Discovery_Mode SHALL discard the incomplete fragments, record a reassembly-failure indication for the affected NodeKey, and retain prior recorded product information for that NodeKey unchanged.
8. THE Discovery_Mode SHALL determine device identity from observed network identity data and corroborating traffic rather than from fixed numeric Source_Address values.
9. WHEN a NodeKey has not been observed transmitting for a duration exceeding 3 times its most recent recorded message interval or exceeding 30 seconds, whichever is greater, THE Discovery_Mode SHALL mark the NodeKey offline.
10. WHEN a NodeKey previously marked offline is observed transmitting a frame, THE Discovery_Mode SHALL mark the NodeKey online.
11. IF two distinct NMEA_NAMEs (distinct NodeKeys) are observed claiming the same Source_Address, THEN THE Discovery_Mode SHALL retain both NodeKeys and their observations without discarding either, and SHALL record the address conflict.
12. THE Discovery_Mode SHALL expose known nodes, unknown nodes, standard PGNs, proprietary PGNs, and message rates for every observed node without excluding any node based on its manufacturer.

### Requirement 11: Data Provenance

**User Story:** As a diagnostic user, I want every value to carry its origin, so that no number is shown without knowing where it came from.

#### Acceptance Criteria

1. WHEN a value is decoded, THE Platform SHALL retain Provenance including Source_Address, source PGN, source node identity, a reference to the originating raw frame, timestamp, Decoder version, data quality state, a flag indicating whether the value is standard-decoded or vendor-specific, and the Value_Origin indicating whether the value is decoded or derived, and WHERE the Value_Origin is derived, THE Platform SHALL record the derivation inputs among the retained Provenance.
2. IF a Provenance field cannot be determined at decode time, THEN THE Platform SHALL record that field as unknown rather than omitting the Provenance.
3. WHEN a request to view a decoded value is received, THE BLE_Service, Web_API, and Web_UI SHALL each surface the Provenance associated with that value.
4. WHEN decoded data is persisted and later replayed, THE Platform SHALL preserve the Provenance unchanged across persistence and replay.
5. THE Platform SHALL avoid exposing a decoded value without its associated Provenance.

### Requirement 12: Ingestion, Persistence, and Recovery

**User Story:** As a diagnostic user, I want captured data reliably persisted and recoverable, so that a session survives restarts and adverse conditions.

#### Acceptance Criteria

1. WHEN the Ingestion_Service receives a frame batch, THE Ingestion_Service SHALL normalize the frames into events, persist raw frames to the Raw_Capture_Log, decode known PGNs, and update the Live_State_Cache.
2. THE Session_Store SHALL persist session metadata and decoded data using SQLite.
3. THE Raw_Capture_Log SHALL be append-only.
4. THE Ingestion_Service SHALL rotate the Raw_Capture_Log when the log reaches a configured maximum size or a configured maximum age, defaulting to 100 MB or 24 hours where no value is configured.
5. WHEN the Ingestion_Service restarts after a stop, THE Ingestion_Service SHALL recover prior session data from the Session_Store and Raw_Capture_Log and restore the session to the state recorded at the last successful write.
6. IF available storage falls below 50 MB free, THEN THE Ingestion_Service SHALL halt the capture pipeline, set a CAPTURE_HALTED_STORAGE_FULL state, stop decoding further incoming frames, and continue serving the last persisted and live state marked as stale, until persistence capacity is restored.
7. WHILE in the CAPTURE_HALTED_STORAGE_FULL state, THE Platform SHALL NOT decode or expose any decoded value that lacks a persisted raw source frame.
8. IF a write to the Session_Store or Raw_Capture_Log fails, THEN THE Ingestion_Service SHALL record a write-failure indication and retain previously persisted data unchanged.

### Requirement 13: BLE Read-Only Transport

**User Story:** As a mobile user, I want to read live diagnostic data over Bluetooth, so that I can monitor the vessel from a phone without a control channel.

#### Acceptance Criteria

1. THE BLE_Service SHALL expose a GATT service providing device information (product name, software version, firmware version, device ID, uptime), connection and health values (CAN connected, frames per second, dropped frames, error count, discovered node count), live vessel data (generic engine, fuel, electrical, and navigation values, alerts, and status), and diagnostics (observed PGN count, unknown PGN count, capture state).
2. THE BLE_Service SHALL expose no GATT characteristic with a write, write-without-response, or otherwise controllable property.
3. WHEN a value updates more frequently than once per second, THE BLE_Service SHALL deliver that value through GATT notifications or indications at a maximum rate of one update per 250 milliseconds per characteristic.
4. THE BLE_Service SHALL provide values that update once per second or less frequently through characteristic reads.
5. THE BLE_Service SHALL expose no characteristic that streams the full Raw_Capture_Log.
6. THE BLE_Service SHALL expose a schema version identifier characteristic whose value changes whenever the BLE characteristic set or value formats change.
7. WHEN a previously connected BLE client reconnects, THE BLE_Service SHALL serve current values on the next read or notification without requiring client-side reconfiguration.
8. WHILE the CAN source is disconnected, THE BLE_Service SHALL report the CAN connected value as disconnected and SHALL mark live vessel data as stale when no CAN frame has been received for 3 seconds or more.
9. THE BLE_Publisher SHALL consume the Domain_Model and SHALL reference neither the Web_UI nor the Web_API model.
10. THE BLE_Publisher SHALL provide a mock in-memory implementation that serves the full BLE schema without a Bluetooth radio.

### Requirement 14: Web API

**User Story:** As a client developer, I want a documented local HTTP and WebSocket API, so that I can consume live and historical data safely.

#### Acceptance Criteria

1. THE Web_API SHALL provide the endpoints GET /api/v1/health, GET /api/v1/live, GET /api/v1/nodes, GET /api/v1/pgns, GET /api/v1/logs, GET /api/v1/logs/{id}, and POST /api/v1/replay.
2. WHEN the Domain_Model updates, THE Web_API SHALL push the updated live data to connected WebSocket or SSE clients at a maximum rate of one update per 250 milliseconds per client.
3. THE Web_API SHALL bind only to loopback and configured local network interfaces and SHALL bind to no publicly routable interface by default.
4. WHEN the Web_API receives a request, THE Web_API SHALL validate that each parameter matches its declared type and permitted value range.
5. IF a request parameter is missing, malformed, or outside its permitted range, THEN THE Web_API SHALL reject the request without performing the operation and SHALL return an error response indicating which parameter failed validation.
6. THE Web_API SHALL reject any replay or log request that exceeds 10 concurrent operations or references a file larger than 100 megabytes, returning an error response indicating the exceeded limit.
7. WHEN a log or replay request references a file path, THE Web_API SHALL resolve the path within the configured capture directory.
8. IF a resolved log or replay file path lies outside the configured capture directory, THEN THE Web_API SHALL reject the request without accessing the file and SHALL return an error response indicating the path is not permitted.
9. THE Platform SHALL complete capture, decode, persistence, and serving operations without issuing any outbound internet request.
10. THE Platform SHALL provide a Live_Ingestion_Runtime that continuously pulls frames from a configured ICanReceiver (Simulator or real hardware, selected by configuration, never by code change) through one running Ingestion_Service, keeping the Live_State_Cache the Web_API and Web_UI read updated in real time while `make run-api` / `web.main` is serving, so that GET /api/v1/live and the WebSocket feed reflect genuinely live decoded data rather than only data from a separately-run replay or test.
11. THE Live_Ingestion_Runtime SHALL run the ICanReceiver consumption loop without blocking the Web_API's own request handling, and SHALL leave the Web_API's existing bind-host and local-only guarantees (Requirement 14.3) unchanged.

### Requirement 15: Web UI

**User Story:** As a diagnostic user, I want a responsive local web page, so that I can view live data on a phone or laptop without installing an app.

#### Acceptance Criteria

1. THE Web_UI SHALL be a single self-contained HTML and JavaScript page served by the Web_API with no build step.
2. THE Web_UI SHALL display engine, tank, electrical, and navigation values and network health, consuming the Domain_Model over WebSocket or REST.
3. THE Web_UI SHALL render all primary content without horizontal scrolling at viewport widths from 320 pixels to 1920 pixels.
4. WHEN the Domain_Model updates, THE Web_UI SHALL reflect the updated values within 1 second.
5. WHILE the CAN source reports disconnected, or WHILE no CAN frame has been received for 3 seconds or more, THE Web_UI SHALL display a disconnected or degraded state indicator.
6. THE Web_UI SHALL load and display served content without any outbound internet request after the initial page load.
7. THE Web_UI SHALL provide a discovery view listing each node with manufacturer, device class, product, observed PGNs, and frames per second.
8. WHEN a user selects a node in the discovery view, THE Web_UI SHALL display that node's raw frames, decoded values, timestamps, message frequency, payload variation, and source Provenance.
9. WHILE no nodes have been discovered, THE Web_UI SHALL display an empty-state indicator in the discovery view.

### Requirement 16: Correlation and Validation Mode

**User Story:** As a reverse-engineer, I want a guided workflow to confirm decoded values match physical behavior, so that I do not mislabel fields based on plausible-looking numbers.

#### Acceptance Criteria

1. WHEN a physical action is recorded, THE Correlation_Mode SHALL record the action performed, a time window with explicit start and end timestamps, the set of fields whose values changed within that window, candidate PGNs, candidate fields, and a correlation strength expressed as a numeric score from 0.0 to 1.0.
2. WHEN a physical action is recorded over a time window, THE Correlation_Mode SHALL identify candidate fields whose values changed at least once between the window start and end timestamps and assign each a correlation strength score from 0.0 to 1.0.
3. WHEN evaluating candidate fields, THE Correlation_Mode SHALL classify a field as action-correlated only if its value change falls within the action time window, and SHALL classify all other value changes as unrelated traffic.
4. WHEN a field's value falls within a plausible range for a known unit but no recorded action correlates with its change, THE Correlation_Mode SHALL mark the field as unconfirmed rather than assigning a confirmed label.
5. IF no candidate field changes within the action time window, THEN THE Correlation_Mode SHALL report zero candidates and indicate that no correlation was found without recording a confirmed label.
6. WHERE a candidate field's correlation strength is below a configurable confirmation threshold, THE Correlation_Mode SHALL present the field as a candidate requiring confirmation rather than as a confirmed label.
7. THE Correlation_Mode SHALL require a baseline-before window, an action window, and an after window for each recorded physical action.
8. THE Correlation_Mode SHALL require at least two repeated physical actions that consistently produce the same candidate field change before moving that field from candidate to confirmed.
9. IF a single physical action is recorded, or IF repeated physical actions produce inconsistent candidate field changes, THEN THE Correlation_Mode SHALL leave the field as a candidate that is unconfirmed.

### Requirement 17: Simulator and Golden Fixtures

**User Story:** As a developer, I want a simulator and version-controlled fixtures, so that the whole stack is testable on a laptop without hardware.

#### Acceptance Criteria

1. THE Simulator SHALL generate synthetic CAN and NMEA frames and SHALL replay recorded captures, preserving the recorded inter-frame timing within a tolerance of plus or minus 5 milliseconds per frame.
2. THE Simulator SHALL emit traffic from multiple simulated node types including engine, fuel tank, GPS and navigation, electrical, and unknown or proprietary nodes.
3. WHEN a fault-injection option is enabled, THE Simulator SHALL emit malformed frames, burst traffic, high-rate traffic, dropped frames, and CAN error conditions, where burst and high-rate traffic reach a configurable maximum frame rate.
4. THE Platform SHALL provide raw CAN fixtures containing CAN ID, priority, PGN, Source_Address, destination where applicable, DLC, payload bytes, and timestamps.
5. THE Platform SHALL provide canonical domain fixtures for EngineState, TankState, ElectricalState, NavigationState, NetworkState, and DeviceHealth that are independent of CAN encoding.
6. THE Platform SHALL provide a /fixtures/golden library including engine_idle.jsonl, engine_rpm_change.jsonl, trim_change.jsonl, fuel_tank.jsonl, electrical.jsonl, navigation.jsonl, unknown_vendor.jsonl, malformed_frames.jsonl, and mixed_multi_node.jsonl.
7. THE Platform SHALL associate each Golden_Fixture with a set of expected assertions, where each assertion is evaluated as pass or fail against the decoded output of that fixture.
8. IF a fixture or replayed frame is malformed, THEN THE CAN_Receiver abstraction SHALL surface the malformed frame through the CanHealth interface without terminating replay of subsequent frames.
9. THE CAN_Receiver abstraction SHALL allow the Simulator or fixture replay to replace a real hardware source using the ICanReceiver, CanFrame, and CanHealth interfaces.
10. THE Platform SHALL make the Linux service, Web_API, BLE domain, and Web_UI executable and verifiable using the Simulator or fixture replay without any physical hardware connected.

### Requirement 18: Extensibility Interfaces

**User Story:** As an architect, I want future clients and displays to attach without changing capture or decoding, so that the platform grows without regressions.

#### Acceptance Criteria

1. THE Display_Adapter abstraction SHALL consume live-state and domain events.
2. WHEN a Display_Adapter is added, THE Platform SHALL produce identical CAN ingestion output and identical NMEA decoding output for the same input frames as produced before the Display_Adapter was added.
3. THE Platform SHALL provide an interface contract for a future native Swift or SwiftUI iPhone application covering BLE scanning, connection, live notification subscription, gauge and status rendering, automatic reconnection, and offline operation, where the contract defines each operation's inputs and outputs.
4. THE Platform SHALL expose the BLE transport through an interface that contains no UI model type, such that a change to any UI model requires no change to the BLE transport interface.

### Requirement 19: Build, Test, and Coverage

**User Story:** As a developer, I want standard make targets and coverage gates, so that verification is consistent and enforced.

#### Acceptance Criteria

1. THE Platform SHALL provide the invocable make targets test, lint, run-sim, run-api, replay (accepting FILE=...), and test-integration, each of which SHALL exit with a zero status on success and a non-zero status on failure.
2. WHEN the test target is run, THE Platform SHALL fail with a non-zero exit status IF measured test coverage for pure protocol and decoder modules is below 90 percent of executable lines.
3. WHEN the test target is run, THE Platform SHALL fail with a non-zero exit status IF measured test coverage across all host-testable code is below 80 percent of executable lines.
4. THE Platform SHALL include at least one automated test for each of the following areas: CAN and protocol parsing, fuel and tank decoding, BLE, Linux ingestion, persistence and replay, UI, and integration flows.
5. THE Platform SHALL include an automated test verifying that a raw fixture flows through parser, Decoder, and canonical Domain_Model state, and a separate automated test verifying that a single domain fixture yields identical values across the BLE, Web_API, and Web_UI transports.
6. WHEN a feature is added or a bug is fixed, THE Platform SHALL include at least one automated test that fails without the change and passes with it.

### Requirement 20: Security and Threat Handling

**User Story:** As a security-conscious owner, I want the platform to resist hostile and adverse conditions, so that the diagnostic tool does not become an attack surface.

#### Acceptance Criteria

1. WHEN malformed CAN traffic, a corrupted replay file, or an unexpected payload is encountered, THE Platform SHALL treat it as untrusted input, discard or quarantine the offending item, and continue processing subsequent valid input without terminating the process.
2. IF a hostile or malformed request arrives at the Web_API or BLE_Service, THEN THE Platform SHALL reject that request with an error indication to the caller and SHALL continue serving valid clients without interruption.
3. IF a replay file is corrupted, THEN THE Platform SHALL reject the file, surface an error indication identifying the failure to the caller, and continue running without terminating the process.
4. IF storage is exhausted through capture growth, THEN THE Platform SHALL apply the storage-full handling defined in Requirement 12.
5. THE Platform SHALL avoid enabling an accidental Transmit_Path as defined in Requirement 1.
6. WHILE serving live data, THE Platform SHALL expose, for each served value, its data quality and staleness state such that a stale or spoofed value is distinguishable from a current valid value at the transport interface.
7. THE Platform SHALL contain no plaintext secrets, credentials, or private keys in tracked source files.

### Requirement 21: Repository Structure and Documentation

**User Story:** As a maintainer, I want a defined repository layout and complete documentation, so that transport and domain logic stay separate and the definition of done is clear.

#### Acceptance Criteria

1. THE Platform SHALL organize code into the directories /firmware, /protocol, /service, /ble, /web, /simulator, /fixtures, /docs, /scripts, and /.kiro/steering.
2. THE Platform SHALL keep transport logic separate from domain logic such that domain modules contain no dependency on transport modules.
3. THE Platform SHALL provide the documentation files README.md, docs/architecture.md, docs/hardware.md, docs/can-safety.md, docs/boat-test.md, docs/nmea-discovery.md, docs/tank-data.md, docs/ble-protocol.md, docs/api.md, docs/log-format.md, docs/testing.md, docs/future-display.md, docs/future-ios.md, and docs/security.md.
4. WHEN a feature is added or a bug is fixed, THE Platform SHALL update each documentation file whose described behavior is changed by that feature or fix before the change is considered done.

### Requirement 22: Kiro Steering Rules

**User Story:** As a project lead, I want the safety and architecture principles encoded as steering rules, so that all future work respects them.

#### Acceptance Criteria

1. THE Platform SHALL record in /.kiro/steering a discoverable rule that CAN transmission is never enabled without approval and a separate spec.
2. THE Platform SHALL record in /.kiro/steering a discoverable rule that all valid NMEA frames are captured before filtering or classification.
3. THE Platform SHALL record in /.kiro/steering a discoverable rule that traffic is never assumed to be from a single manufacturer such as Suzuki.
4. THE Platform SHALL record in /.kiro/steering a discoverable rule that unknown and proprietary PGNs are preserved.
5. THE Platform SHALL record in /.kiro/steering a discoverable rule that BLE is a transport and not part of the NMEA decoder.
6. THE Platform SHALL record in /.kiro/steering a discoverable rule that REST, WebSocket, BLE, and display transports share a common Domain_Model.
7. THE Platform SHALL record in /.kiro/steering a discoverable rule that every feature and bug fix requires tests.
8. THE Platform SHALL record in /.kiro/steering a discoverable rule that hardware dependencies are abstracted.
9. THE Platform SHALL record in /.kiro/steering a discoverable rule that core operation has no cloud dependency.
10. THE Platform SHALL record in /.kiro/steering a discoverable rule that raw logs always remain replayable.
11. THE Platform SHALL record in /.kiro/steering a discoverable rule that third-party UNO Q and library compatibility is verified rather than assumed.
12. THE Platform SHALL record in /.kiro/steering a discoverable rule that documentation updates are part of the definition of done.

### Requirement 23: Cross-Platform Host Development

**User Story:** As a developer, I want to develop and test the full stack on either my Mac or my PC, so that I can make progress without the Hardware_Target or the boat.

#### Acceptance Criteria

1. THE Platform SHALL run the host-testable stack, comprising the Protocol_Core, Ingestion_Service, Web_API, Web_UI, mock BLE_Publisher, and Simulator, on a macOS Host_Platform and on a Windows or Linux Host_Platform.
2. WHEN the make targets test, lint, run-sim, run-api, replay, and test-integration are run on a supported Host_Platform, THE Platform SHALL execute them without requiring physical CAN hardware, a Bluetooth radio, or the Hardware_Target.
3. THE Platform SHALL isolate any Host_Platform-specific dependency behind an abstraction so that host development does not depend on a single operating system.
4. IF a capability is unavailable on a given Host_Platform, THEN THE Platform SHALL substitute a mock or simulated implementation so that host-testable behavior remains verifiable.

### Requirement 24: Hardware Target Deployment and Validation

**User Story:** As a developer, I want to push validated software to the Arduino UNO Q and confirm the CAN capture path on real hardware, so that I can trust the device before taking it to the boat.

#### Acceptance Criteria

1. THE Platform SHALL provide a documented procedure to deploy the MCU firmware and the Linux Ingestion_Service, transports, and Web_UI to the Hardware_Target.
2. WHEN the firmware initializes on the Hardware_Target, THE Platform SHALL initialize the SPI interface and the MCP2518FDT CAN controller for classical CAN at 250 kbit/s accepting 29-bit extended frames.
3. WHEN the firmware initializes on the Hardware_Target, THE Platform SHALL configure the CAN controller and transceiver into silent or listen-only mode and SHALL report the configured mode as a verifiable health metric.
4. THE Platform SHALL keep the switchable 120-ohm termination off on the Hardware_Target and SHALL report the termination state as a verifiable health metric.
5. WHILE running on the Hardware_Target, THE Platform SHALL expose health metrics including total frames, frames per second, dropped frames, queue depth, CAN controller state, CAN error counters, and uptime over USB or the MCU-to-Linux Bridge/RPC.
6. WHEN the same Simulator or Golden_Fixture input that passes on a Host_Platform is exercised against the Hardware_Target capture path, THE Platform SHALL produce equivalent decoded Domain_Model values, allowing for real-time timing differences.
7. THE Platform SHALL confirm on the Hardware_Target that no CAN Transmit_Path is enabled, consistent with Requirement 1.
8. THE Platform SHALL, on the bench before any Backbone connection, measure and validate that the silent-mode control line (shield D4 with the P5 shunt fitted) holds the transceiver transmit channel non-driving throughout MCU reset, boot, and firmware startup.
9. WHERE firmware-only control cannot guarantee the non-driving state during reset and boot, THE Hardware_Setup_Guide SHALL document a hardware fail-safe (for example a pull-up making silent mode the default) so that silent mode is the power-on hardware default rather than relying solely on firmware.

### Requirement 25: Capture Promotion to Golden Fixtures

**User Story:** As a developer, I want to promote validated real boat captures into the golden fixture library, so that real-world traffic becomes a repeatable regression test.

#### Acceptance Criteria

1. THE Platform SHALL provide a documented procedure to convert a recorded Raw_Capture_Log into a Golden_Fixture in /fixtures/golden.
2. WHEN a capture is promoted to a Golden_Fixture, THE Platform SHALL require associated expected assertions for that fixture.
3. THE Platform SHALL preserve the raw frames and Provenance of a promoted capture within the resulting Golden_Fixture.
4. WHEN a promoted Golden_Fixture is added, THE Platform SHALL include it in the fixture-driven test suite so that it runs as a regression test.

### Requirement 26: Hardware Wiring and Setup Documentation

**User Story:** As a builder assembling the device, I want clear wiring and setup instructions, so that I can physically connect the Arduino UNO Q and CAN shield correctly and safely before powering it on.

#### Acceptance Criteria

1. THE Platform SHALL provide a Hardware_Setup_Guide at docs/hardware.md documenting the physical assembly of the Arduino UNO Q and the EVAL-ADM3055E-ARDZ CAN shield.
2. THE Hardware_Setup_Guide SHALL document the shield-to-UNO-Q connection, including the SPI signal mapping (MCP2518FDT to MCU) and the pins used, with a wiring diagram or pin table.
3. THE Hardware_Setup_Guide SHALL document the switchable 120-ohm termination control and SHALL state that termination is set off for a drop node, with an explanation that the existing Backbone termination is authoritative.
4. THE Hardware_Setup_Guide SHALL document the CAN-H, CAN-L, and GND2 terminal connections to the NMEA 2000 Backbone and SHALL identify the isolated (GND2) side of the ADM3055E transceiver.
5. THE Hardware_Setup_Guide SHALL document powering the Hardware_Target from USB-C and SHALL state, as a safety warning, that the UNO Q and shield are never powered from the NMEA 2000 +12V conductor in the MVP.
6. THE Hardware_Setup_Guide SHALL document how to verify silent or listen-only mode and the termination-off state using the health metrics defined in Requirement 24 before connecting to a live Backbone.
7. THE Hardware_Setup_Guide SHALL provide a step-ordered setup and first-power-on checklist that references the passive receive-only safety constraints in Requirement 1.
8. THE Platform SHALL document, in docs/boat-test.md, the on-boat connection and capture procedure, including termination-off verification and non-disruption of the Backbone.
9. WHEN the hardware wiring, pin mapping, jumper or termination settings, or power arrangement change, THE Platform SHALL update the Hardware_Setup_Guide as part of the definition of done.

### Requirement 27: BLE Wire Format and Fragmentation

**User Story:** As a client developer, I want a defined, versioned BLE payload encoding, so that multi-instance data and provenance fit within BLE constraints and decode unambiguously.

#### Acceptance Criteria

1. THE BLE_Service SHALL define a versioned, compact binary encoding (for example CBOR or a documented compact binary event envelope) for characteristic payloads, rather than arbitrary or unbounded JSON.
2. THE BLE_Service SHALL represent multiple engine and tank instances within the encoding without a fixed single-instance assumption.
3. WHERE a payload exceeds the negotiated BLE ATT MTU, THE BLE_Service SHALL fragment the payload into ordered segments that a client can reassemble, and SHALL indicate segment ordering and completion.
4. THE BLE_Service SHALL include an encoding or schema version in the payload or a companion characteristic so that clients can detect version changes.
5. THE encoding SHALL carry each value's provenance summary including Value_Origin and staleness.

### Requirement 29: User-Configurable Multi-Instance Dashboard

**User Story:** As a boat owner, I want to pick which discovered devices and sensors appear on my dashboard, see each one's attributes as readable metrics and gauges, and arrange them, so that my monitoring view matches my own vessel's instrumentation instead of a fixed generic layout.

#### Acceptance Criteria

1. THE Web_UI SHALL present every discovered sensor-bearing entity instance (each EngineState keyed by engine_instance/Source_Address, each TankState keyed by tank_instance/Source_Address, ElectricalState, NavigationState) as an individually selectable item, distinguishing instances of the same entity type (for example two engines) from each other.
2. THE Web_UI SHALL allow the user to select a subset of discovered entity instances, and SHALL render on the dashboard only the entity instances currently selected.
3. FOR an entity instance with more than one decoded attribute (for example EngineState exposing rpm, coolant_temp, oil_pressure, alternator_voltage, fuel_rate, engine_hours, trim), THE Web_UI SHALL allow the user to select which of that instance's attributes are shown on the dashboard.
4. WHEN a selected attribute has a defined natural display range (for example level_percent 0-100, rpm 0 to a configurable maximum), THE Web_UI SHALL render that attribute as a circular dial gauge showing the current value against its range, in addition to or instead of its numeric readout.
5. WHEN a selected attribute has no defined natural display range (for example position, status flags), THE Web_UI SHALL render that attribute as a numeric or textual metric rather than a dial.
6. THE Web_UI SHALL allow the user to reorder the selected entity-instance cards on the dashboard.
7. THE Web_UI SHALL persist the user's selected entity instances, selected attributes per instance, and card order locally in the browser (for example via localStorage) without transmitting the configuration off the device, so the arrangement survives a page reload (_steering 12: local-only, no outbound_).
8. THE Web_UI SHALL update each rendered gauge and metric in place, consuming the same Live_State_Cache data and the same staleness/quality Provenance markers as every other dashboard view (_Requirement 6.6, 20.6_), so a selected/arranged dashboard is never a second, inconsistent data path.
9. WHILE no entity instances are selected, THE Web_UI SHALL display an empty-state indicator in the dashboard area directing the user to the selection view, distinct from the discovery view's own empty-state indicator (_Requirement 15.9_).
10. WHEN a previously selected entity instance stops being discovered (for example its NodeKey goes offline or its instance key disappears from the Live_State_Cache), THE Web_UI SHALL continue displaying its card using the last known values marked stale, consistent with the staleness handling in Requirement 20.6, rather than silently removing the user's selection.
11. THE Web_UI SHALL provide a display-only unit toggle for volume and flow-rate attributes (capacity_litres, quantity_remaining_litres, fuel_rate), allowing the user to view them in US gallons / US gallons-per-hour as an alternative to litres / litres-per-hour, WHERE the underlying decoded and persisted value (TankState, EngineState, Provenance, derivation text) SHALL remain in litres unchanged; the toggle SHALL affect only the rendered display label and number, never the Domain_Model, the API payload, or the stored localStorage configuration value.
12. THE Web_UI SHALL persist the user's chosen display unit (litres or US gallons) in the same local-only configuration mechanism as Requirement 29.7, so the choice survives a page reload without transmitting it off the device.

### Requirement 30: Mobile-First Rendering and LAN Testing Access

**User Story:** As a boat owner, I want the dashboard to be designed primarily for a phone held in a cockpit, and I want to be able to load it from my phone over my own WiFi while developing, so that the primary way I will actually use this tool works well and I can test it from the device I will really use.

#### Acceptance Criteria

1. THE Web_UI SHALL be designed primarily for a mobile phone viewport (an "operate from your phone" surface first, a wider desktop/tablet layout second), with touch targets, gauge sizing, and card density chosen for a one-handed phone held at arm's length in variable outdoor light, rather than a desktop layout that merely reflows to fit a phone screen.
2. THE Web_UI SHALL size all interactive controls introduced by Requirement 29 (instance checkboxes, attribute checklist, reorder controls) to a minimum 44x44 CSS pixel touch target.
3. THE Web_UI SHALL render its primary dashboard content, including the Requirement 29 gauges and cards, without horizontal scrolling or clipped content at the 320-430 pixel phone-portrait width range, in addition to the existing 320-1920 pixel range already required by Requirement 15.3.
4. THE Platform SHALL provide a documented, explicit configuration mechanism (for example an environment variable) allowing the Web_API to bind to a configured private local-network interface address for the purpose of loading the Web_UI from another device, such as a phone, on the same local network during development or on-boat use.
5. THE configuration mechanism in Criterion 4 SHALL continue to default to loopback-only binding per Requirement 14.3, SHALL accept only loopback or an address within a private, non-publicly-routable range (RFC 1918: 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16), and SHALL refuse an unqualified wildcard bind address (0.0.0.0 or ::) and any address outside those ranges, requiring the caller to name a specific private interface address explicitly rather than binding every interface on the host.
6. THE Platform SHALL document, in docs/api.md or docs/testing.md, how to discover the Host_Platform's own private LAN address and configure the mechanism in Criterion 4 to load the Web_UI from a phone on the same network, including a warning that the Web_API has no authentication and SHOULD only be exposed on a trusted private network.
7. WHERE the Web_API process runs inside a container whose own network boundary (e.g. a container-runtime port-publish mapping) is the operative exposure control rather than this process's own bind address, THE Platform SHALL provide a SEPARATE, explicitly named configuration flag (never inferred from the runtime environment, never a default) that permits the wildcard bind address for that documented case ONLY, and this flag SHALL NOT relax the refusal of any publicly routable address outside loopback/RFC 1918 ranges.

### Requirement 28: BLE Access Security and Privacy

**User Story:** As a boat owner, I want my vessel data protected from nearby readers, so that read-only access does not mean open access to GPS position and vessel status.

#### Acceptance Criteria

1. THE BLE_Service SHALL support authenticated, bonded, encrypted pairing on the Hardware_Target BLE stack.
2. THE BLE_Service SHALL default to requiring authenticated pairing before a client may read or subscribe to vessel-data characteristics, and unauthenticated or open access SHALL be an explicit opt-in configuration.
3. WHILE authenticated pairing is required, THE BLE_Service SHALL reject reads and notification subscriptions from unpaired clients for vessel-data characteristics.
4. THE BLE_Service SHALL retain its read-only guarantee defined in Requirement 13 regardless of the pairing configuration.
5. THE Platform SHALL document the BLE security model and its default in docs/ble-protocol.md and docs/security.md.
