# BLE protocol: GATT schema, wire format, and security model

The BLE transport is a **read-only** diagnostic feed. There is no writable
characteristic in the schema at all — not "write access is denied", but
"the type offering characteristic properties has no write-capable member"
(`CharacteristicProperty` in `ble/publisher.py` enumerates only `READ`,
`NOTIFY`, `READ_NOTIFY` — Property 23, mirroring the same structural
enforcement `ICanReceiver` uses for the CAN side, steering 01).

This MVP's deliverable is `MockBLEPublisher` — an in-memory implementation
of the `BLEPublisher` Protocol requiring no Bluetooth radio (Requirement
13.10), so every behavior below is host-testable. A real BlueZ-backed
publisher implementing the same Protocol is Stage 10 hardware work.

## GATT schema

| UUID (short) | Name | Properties |
|---|---|---|
| A2D1 | `schema_version` | READ |
| A2D2 | `device_info` | READ |
| A2D3 | `connection_health` | READ_NOTIFY |
| A2D4 | `live_engine` | NOTIFY |
| A2D5 | `live_fuel` | NOTIFY |
| A2D6 | `live_electrical` | NOTIFY |
| A2D7 | `live_navigation` | NOTIFY |
| A2D8 | `alerts_status` | READ_NOTIFY |
| A2D9 | `diagnostics` | READ |
| A2DA | `readings_text` | READ_NOTIFY |

`readings_text` is a **human-readable UTF-8 line** for a generic scanner (LightBlue,
nRF Connect) — the `live_*` characteristics above are the compact binary envelope and show
as hex. Example: `RPM 2664 | Fuel 99% | Batt 12.6V | SOG 5.2kn | Wind 12.1kn`. SOG and wind
in knots, RPM and tank level whole, fluid type by name, UNSET fields omitted; `STALE | …`
(or `STALE (no data)`) when the bus has gone quiet. Read-only like the rest (Property 23).

UUIDs are documented placeholders under a base 128-bit vendor UUID, per
design.md — not yet registered.

## Wire format (`ble/encoding.py`)

A versioned, compact binary envelope (not CBOR — see
`docs/architecture.md`'s entry on this choice). All integers/floats
little-endian.

```
Envelope:
    u8   schema_version
    u8   entity_count
    entity_count × EntityRecord

EntityRecord:
    u8   entity_kind       (1=engine, 2=tank, 3=electrical, 4=navigation)
    u8   instance           (0xFF = no instance, e.g. electrical/navigation)
    u8   source_address
    u8   value_origin       (0=decoded, 1=derived)
    u8   quality            (0=valid, 1=stale, 2=invalid, 3=unknown)
    f32  staleness_s
    u8   field_count
    field_count × FieldRecord

FieldRecord:
    u8   field_id            (per-entity-kind table, see FIELD_IDS in ble/encoding.py)
    u8   present              (0=UNSET, 1=present)
    f32  value                 (omitted entirely when present=0)
```

**Multi-instance:** an envelope carries however many `EntityRecord`s are
live right now — there is no fixed single-engine/single-tank assumption
(Requirement 27.2). Two engines produce two `EntityRecord`s in the same
notification.

**Schema versioning (Requirement 27.4):** `SCHEMA_VERSION` is embedded in
every envelope. It is currently `2` — version 1 shipped without the
per-entity `quality` byte; adding it was a genuine wire-layout change, so
the version bumped rather than reshaping v1 silently. A client **must**
check `schema_version` before trusting field offsets, since a v1 reader
parsing v2 bytes (or vice versa) would misalign every field after the
header.

**Fragmentation (Requirement 27.3):** `fragment(data, mtu)` splits an
encoded envelope into ordered segments, each `[seq_byte, ...payload]`,
where the low 7 bits of `seq_byte` are a 0-based sequence number and the
top bit (`0x80`) marks the final segment. `Reassembler` is the
corresponding client-side helper — feed segments as they arrive, get the
complete envelope back once the final one lands, regardless of arrival
order (out-of-order delivery is tolerated; segments are indexed by sequence
number, not append order).

**Known ceiling, not a bug:** the sequence number is 7 bits, addressing at
most 128 ordered segments. A payload needing more at a given MTU raises
`ValueError` rather than silently wrapping the sequence number and
corrupting reassembly. Real BLE ATT MTUs (23-517 bytes) keep realistic
envelopes well under this; if it's ever hit in practice, the fix is a wider
sequence field (a new schema version), not changing MTU negotiation.

## Notify-vs-read split (Requirement 13.3, 13.4)

Every characteristic is **always** re-encoded with the latest value on
every `publish()` call — a client reading at any time gets the current
value, never a stale cached one from before the last notification. Active
*notification* (as opposed to a passive read) is additionally rate-limited
to at most one per `MIN_NOTIFY_INTERVAL_S` (250ms) per characteristic
(Property 22), matching the same 250ms cap the Web_API's WebSocket push
enforces — a BLE and a REST/WS client observing the same underlying change
see it on the same cadence ceiling.

## Access control (Requirement 28.2, 28.3)

`MockBLEPublisher.require_pairing` defaults to `True` — the secure-by-default
posture. An unpaired client calling `read_characteristic()`,
`read_connection_health()`, or `read_diagnostics()` is rejected with
`PairingRejectedError`. Two reads are **exempt** by design, since a client
needs them before it can even negotiate pairing:

- `read_schema_version()` — a client must know the schema before it can
  decode anything, pairing or not.

`require_pairing=False` is an explicit, deliberate opt-in to open access —
never the default.

## Hostile/malformed request handling (Requirement 20.2)

A request naming a characteristic outside `GATT_SCHEMA` is rejected with
`UnknownCharacteristicError` (a `KeyError` subclass carrying the offending
name) — not an unhandled bare `KeyError`. The publisher's internal state is
unaffected by a rejected request; a subsequent valid read from the same or
a different client is served normally. See `docs/security.md` for the
platform-wide threat-handling posture this is one instance of.

## Staleness and quality, per value (Requirement 20.6)

Every `EntityRecord` carries its own `quality` and `staleness_s` — the same
`Provenance.quality`/`staleness_s` fields REST/WS expose for the identical
underlying value (`docs/architecture.md` has the full cross-transport
consistency note). A BLE client should treat a non-`"valid"` quality the
same way a REST/WS client does: visually distinct from a fresh valid
reading, never presented identically.

## Device-level staleness (Requirement 13.8)

Independent of per-value quality, `MockBLEPublisher.is_stale()` reports
whether CAN itself is disconnected or has been silent for ≥3 seconds —
this is the device-level signal a reconnecting client should check before
trusting any per-value staleness number at all.
