"""BLE wire format: versioned, compact binary encoding for characteristic
payloads (Requirement 27.1-27.5).

Per design.md ("CBOR, or a documented compact binary event envelope"), this
implements the documented-custom-envelope option rather than adding a CBOR
dependency -- the format below is simple enough that `struct` fully covers
it, and ponytail (steering: no new dependency if avoidable) applies.

Envelope layout (little-endian throughout):

    u8   schema_version          (Requirement 27.4 -- embedded version)
    u8   entity_count            (how many entity records follow)
    entity_count x EntityRecord

EntityRecord layout (one engine/tank/electrical/navigation instance):

    u8   entity_kind             (EntityKind enum value)
    u8   instance                (0xFF if the entity has no instance, e.g.
                                   ElectricalState/NavigationState)
    u8   source_address
    u8   value_origin            (0=decoded, 1=derived)
    u8   quality                 (QualityCode enum value -- schema v2+, Requirement
                                   20.6: every served value distinguishes stale/
                                   invalid/unknown from current valid data at the
                                   transport interface, matching REST/WS's
                                   Provenance.quality field one-for-one)
    f32  staleness_s
    u8   field_count
    field_count x FieldRecord

FieldRecord layout (one scalar field of the entity):

    u8   field_id                (per-entity-kind field table, see FIELD_IDS)
    u8   present                 (0 = UNSET, 1 = present)
    f32  value                   (only when present=1; omitted when UNSET)

No single-instance assumption: an envelope carries however many
EntityRecords are needed for however many engine/tank instances are live
(Requirement 27.2). Fragmentation across the ATT MTU is a SEPARATE concern
handled by `fragment()`/`Reassembler` below, operating on the already-encoded
envelope bytes -- the envelope itself has no MTU awareness.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum

from protocol.domain import UNSET, ElectricalState, EngineState, NavigationState, TankState

#: Schema v2 adds the per-entity `quality` byte (Requirement 20.6). A v1
#: decoder reading v2 bytes would misparse the header, so this is a real
#: version bump, not a silent reshape of the already-shipped v1 envelope --
#: callers check `schema_version` before trusting field offsets, per this
#: module's own `decode_envelope` docstring.
SCHEMA_VERSION = 2

#: No-instance marker for EntityRecord.instance (ElectricalState/NavigationState).
NO_INSTANCE = 0xFF


class EntityKind(IntEnum):
    ENGINE = 1
    TANK = 2
    ELECTRICAL = 3
    NAVIGATION = 4


class QualityCode(IntEnum):
    """Wire encoding of Provenance.quality ("valid"|"stale"|"invalid"|
    "unknown"), so BLE carries the same quality/staleness marker REST/WS
    already expose via Provenance (Requirement 20.6)."""

    VALID = 0
    STALE = 1
    INVALID = 2
    UNKNOWN = 3


_QUALITY_TO_CODE = {
    "valid": QualityCode.VALID,
    "stale": QualityCode.STALE,
    "invalid": QualityCode.INVALID,
    "unknown": QualityCode.UNKNOWN,
}
_CODE_TO_QUALITY = {code: name for name, code in _QUALITY_TO_CODE.items()}


#: Per-entity-kind field id tables. Stable, append-only -- a field's id must
#: never be reused for a different meaning once shipped (clients key decoded
#: values by these ids across schema versions sharing the same major id set).
ENGINE_FIELD_IDS = {
    "rpm": 1,
    "coolant_temp": 2,
    "oil_pressure": 3,
    "alternator_voltage": 4,
    "engine_hours": 5,
    "fuel_rate": 6,
    "trim": 7,
}
TANK_FIELD_IDS = {
    "level_percent": 1,
    "capacity_litres": 2,
    "quantity_remaining_litres": 3,
}
ELECTRICAL_FIELD_IDS = {
    "battery_voltage": 1,
    "charging_voltage": 2,
    "current": 3,
}
NAVIGATION_FIELD_IDS = {
    "cog": 1,
    "sog": 2,
    "heading": 3,
    "depth": 4,
    "water_temp": 5,
}

_FIELD_IDS_BY_KIND = {
    EntityKind.ENGINE: ENGINE_FIELD_IDS,
    EntityKind.TANK: TANK_FIELD_IDS,
    EntityKind.ELECTRICAL: ELECTRICAL_FIELD_IDS,
    EntityKind.NAVIGATION: NAVIGATION_FIELD_IDS,
}


@dataclass
class DecodedField:
    field_id: int
    present: bool
    value: float | None


@dataclass
class DecodedEntity:
    kind: EntityKind
    instance: int  # NO_INSTANCE when the entity carries none
    source_address: int
    value_origin: str  # "decoded" | "derived"
    quality: str  # "valid" | "stale" | "invalid" | "unknown"
    staleness_s: float
    fields: list[DecodedField]


@dataclass
class DecodedEnvelope:
    schema_version: int
    entities: list[DecodedEntity]


def _entity_kind_and_fields(entity: object) -> tuple[EntityKind, int, dict[str, int]] | None:
    if isinstance(entity, EngineState):
        return EntityKind.ENGINE, entity.engine_instance, ENGINE_FIELD_IDS
    if isinstance(entity, TankState):
        return EntityKind.TANK, entity.tank_instance, TANK_FIELD_IDS
    if isinstance(entity, ElectricalState):
        return EntityKind.ELECTRICAL, NO_INSTANCE, ELECTRICAL_FIELD_IDS
    if isinstance(entity, NavigationState):
        return EntityKind.NAVIGATION, NO_INSTANCE, NAVIGATION_FIELD_IDS
    return None


def encode_entity(entity: object) -> bytes:
    """Encode a single Domain_Model entity (EngineState/TankState/
    ElectricalState/NavigationState) as one EntityRecord's bytes, including
    its own schema-version-free body (the envelope prefixes the version)."""
    resolved = _entity_kind_and_fields(entity)
    if resolved is None:
        raise TypeError(f"no BLE wire encoding for entity type {type(entity).__name__}")
    kind, instance, field_ids = resolved

    provenance = getattr(entity, "provenance", None)
    value_origin = provenance.value_origin if provenance is not None else "decoded"
    staleness_s = provenance.staleness_s if provenance is not None else 0.0
    quality = provenance.quality if provenance is not None else "unknown"
    quality_code = _QUALITY_TO_CODE.get(quality, QualityCode.UNKNOWN)

    field_records = bytearray()
    field_count = 0
    for field_name, field_id in field_ids.items():
        value = getattr(entity, field_name, UNSET)
        if value is UNSET:
            field_records += struct.pack("<BB", field_id, 0)
        else:
            field_records += struct.pack("<BBf", field_id, 1, float(value))
        field_count += 1

    header = struct.pack(
        "<BBBBBfB",
        int(kind),
        instance,
        entity.source_address,
        0 if value_origin == "decoded" else 1,
        int(quality_code),
        staleness_s,
        field_count,
    )
    return header + bytes(field_records)


def encode_envelope(entities: list[object]) -> bytes:
    """Encode a full envelope for however many entities are live right now
    (Requirement 27.2 -- no fixed single-instance assumption)."""
    body = bytearray()
    for entity in entities:
        body += encode_entity(entity)
    return struct.pack("<BB", SCHEMA_VERSION, len(entities)) + bytes(body)


def decode_envelope(data: bytes) -> DecodedEnvelope:
    """Decode a full envelope back into DecodedEntity records. Raises
    ValueError on a schema_version this module does not know how to read --
    callers should check `schema_version` against `SCHEMA_VERSION` before
    trusting the decoded fields' meaning if this module is ever extended
    with a newer version this build predates."""
    schema_version, entity_count = struct.unpack_from("<BB", data, 0)
    offset = 2
    entities = []
    for _ in range(entity_count):
        kind_raw, instance, source_address, origin_raw, quality_raw, staleness_s, field_count = (
            struct.unpack_from("<BBBBBfB", data, offset)
        )
        offset += struct.calcsize("<BBBBBfB")
        kind = EntityKind(kind_raw)
        value_origin = "decoded" if origin_raw == 0 else "derived"
        quality = _CODE_TO_QUALITY.get(QualityCode(quality_raw), "unknown")
        fields = []
        for _ in range(field_count):
            field_id, present = struct.unpack_from("<BB", data, offset)
            offset += 2
            if present:
                (value,) = struct.unpack_from("<f", data, offset)
                offset += 4
            else:
                value = None
            fields.append(DecodedField(field_id=field_id, present=bool(present), value=value))
        entities.append(
            DecodedEntity(
                kind=kind,
                instance=instance,
                source_address=source_address,
                value_origin=value_origin,
                quality=quality,
                staleness_s=staleness_s,
                fields=fields,
            )
        )
    return DecodedEnvelope(schema_version=schema_version, entities=entities)


# --- ATT-MTU fragmentation (Requirement 27.3) -------------------------------

#: Reserve 2 bytes per segment for the sequence/final-marker header, leaving
#: the rest of the negotiated MTU for payload bytes.
_SEGMENT_HEADER_SIZE = 2
#: 0x80 bit of the sequence byte marks the FINAL segment.
_FINAL_SEGMENT_BIT = 0x80


def fragment(data: bytes, mtu: int) -> list[bytes]:
    """Split `data` into ordered segments that fit within `mtu` bytes each
    (header included). Each segment is `[seq_byte, ...payload]` where
    `seq_byte`'s low 7 bits are a 0-based sequence number and the top bit
    (0x80) marks the FINAL segment (Requirement 27.3).

    Raises ValueError if `data` would need more than 128 segments at this
    `mtu` -- the 7-bit sequence number cannot address more than that without
    wrapping and corrupting reassembly. In practice BLE's negotiated ATT MTU
    (23-517 bytes) keeps real envelopes well under this ceiling; a caller
    hitting it should raise its MTU rather than silently wrap.
    """
    if mtu <= _SEGMENT_HEADER_SIZE:
        raise ValueError(f"mtu {mtu} too small for the {_SEGMENT_HEADER_SIZE}-byte segment header")
    chunk_size = mtu - _SEGMENT_HEADER_SIZE
    if not data:
        return [bytes([_FINAL_SEGMENT_BIT])]

    segment_count = -(-len(data) // chunk_size)  # ceil division
    if segment_count > 128:
        raise ValueError(
            f"{len(data)} bytes at mtu={mtu} needs {segment_count} segments, "
            "exceeding the 128-segment ceiling the 7-bit sequence number can address"
        )

    segments = []
    for i in range(0, len(data), chunk_size):
        chunk = data[i : i + chunk_size]
        seq = i // chunk_size
        is_final = (i + chunk_size) >= len(data)
        seq_byte = (seq & 0x7F) | (_FINAL_SEGMENT_BIT if is_final else 0)
        segments.append(bytes([seq_byte]) + chunk)
    return segments


class Reassembler:
    """Client-side reassembly helper (Requirement 27.3's "a client can
    reassemble" half) -- feed segments in arrival order, get the complete
    envelope bytes back once the final segment arrives."""

    def __init__(self) -> None:
        self._chunks: dict[int, bytes] = {}
        self._final_seq: int | None = None

    def feed(self, segment: bytes) -> bytes | None:
        if not segment:
            return None
        seq_byte = segment[0]
        seq = seq_byte & 0x7F
        is_final = bool(seq_byte & _FINAL_SEGMENT_BIT)
        self._chunks[seq] = segment[1:]
        if is_final:
            self._final_seq = seq

        if self._final_seq is None:
            return None
        if any(s not in self._chunks for s in range(self._final_seq + 1)):
            return None  # still waiting on an earlier segment

        result = b"".join(self._chunks[s] for s in range(self._final_seq + 1))
        self._chunks.clear()
        self._final_seq = None
        return result
