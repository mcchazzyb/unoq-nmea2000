"""Stage 6.3 -- BLE wire format: multi-instance encoding, schema version,
provenance summary, ATT-MTU fragmentation (Requirement 27.1-27.5).
"""
from __future__ import annotations

import time

from hypothesis import given, settings
from hypothesis import strategies as st

from ble.encoding import (
    EntityKind,
    Reassembler,
    SCHEMA_VERSION,
    decode_envelope,
    encode_envelope,
    fragment,
)
from protocol.domain import UNSET, EngineState, Provenance, TankState


def _make_engine(instance: int, source_address: int, rpm: float | object = UNSET) -> EngineState:
    state = EngineState(engine_instance=instance)
    state.source_address = source_address
    state.rpm = rpm
    state.provenance = Provenance(
        source_address=source_address,
        source_pgn=127488,
        node_identity="unknown",
        raw_frame_ref="unknown",
        timestamp=time.time(),
        decoder_version="1.0.0",
        quality="valid",
        staleness_s=1.5,
        vendor_specific=False,
        value_origin="decoded",
    )
    return state


def test_multi_instance_envelope_carries_every_engine_without_fixed_count():
    """Requirement 27.2: multiple engine instances carried without a fixed
    single-instance assumption."""
    engines = [_make_engine(i, 10 + i, rpm=1000.0 + i) for i in range(5)]
    envelope_bytes = encode_envelope(engines)
    decoded = decode_envelope(envelope_bytes)
    assert len(decoded.entities) == 5
    for i, entity in enumerate(decoded.entities):
        assert entity.kind == EntityKind.ENGINE
        assert entity.instance == i
        assert entity.source_address == 10 + i


def test_schema_version_embedded_in_payload():
    """Requirement 27.4: schema version embedded in the payload itself."""
    envelope_bytes = encode_envelope([_make_engine(0, 1, rpm=1500.0)])
    decoded = decode_envelope(envelope_bytes)
    assert decoded.schema_version == SCHEMA_VERSION


def test_provenance_summary_carries_value_origin_and_staleness():
    """Requirement 27.5: each value's provenance summary includes
    value_origin and staleness."""
    engine = _make_engine(0, 1, rpm=1500.0)
    envelope_bytes = encode_envelope([engine])
    decoded = decode_envelope(envelope_bytes)
    entity = decoded.entities[0]
    assert entity.value_origin == "decoded"
    assert abs(entity.staleness_s - 1.5) < 1e-6


def test_unset_field_decodes_as_not_present_never_a_fabricated_zero():
    """UNSET must round-trip as present=False, never a decoded 0.0
    (Property 13 extended to the wire format)."""
    engine = _make_engine(0, 1, rpm=UNSET)
    envelope_bytes = encode_envelope([engine])
    decoded = decode_envelope(envelope_bytes)
    rpm_field = next(f for f in decoded.entities[0].fields if f.field_id == 1)  # "rpm"
    assert rpm_field.present is False
    assert rpm_field.value is None


def test_derived_tank_value_origin_round_trips():
    tank = TankState(tank_instance=0)
    tank.source_address = 42
    tank.level_percent = 50.0
    tank.capacity_litres = 200.0
    tank.quantity_remaining_litres = (50.0 / 100.0) * 200.0
    tank.provenance = Provenance(
        source_address=42,
        source_pgn=127505,
        node_identity="unknown",
        raw_frame_ref="unknown",
        timestamp=time.time(),
        decoder_version="1.0.0",
        quality="valid",
        staleness_s=0.2,
        vendor_specific=False,
        value_origin="derived",
        derivation="(level_percent / 100) * capacity_litres",
    )
    envelope_bytes = encode_envelope([tank])
    decoded = decode_envelope(envelope_bytes)
    assert decoded.entities[0].value_origin == "derived"


# --- Requirement 27.3: ATT-MTU fragmentation --------------------------------


@given(
    payload_len=st.integers(min_value=0, max_value=500),
    mtu=st.integers(min_value=3, max_value=50),
)
@settings(max_examples=100)
def test_fragment_and_reassemble_round_trip(payload_len, mtu):
    data = bytes((i * 7 + 1) % 256 for i in range(payload_len))
    chunk_size = mtu - 2
    segment_count = -(-max(payload_len, 1) // chunk_size)
    if segment_count > 128:
        return  # exceeds the 7-bit sequence number's addressable range (documented ceiling)
    segments = fragment(data, mtu=mtu)
    reassembler = Reassembler()
    result = None
    for segment in segments:
        result = reassembler.feed(segment)
    assert result == data


def test_fragment_rejects_payload_exceeding_128_segment_ceiling():
    """A payload needing more than 128 segments at this MTU cannot be
    addressed by the 7-bit sequence number -- fragment() must refuse rather
    than silently wrap and corrupt reassembly."""
    data = bytes(200)
    try:
        fragment(data, mtu=3)  # chunk_size=1 -> 200 segments needed
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for a payload exceeding the segment ceiling")


def test_fragment_marks_only_the_last_segment_final():
    data = bytes(range(20))
    segments = fragment(data, mtu=8)
    for seg in segments[:-1]:
        assert (seg[0] & 0x80) == 0
    assert (segments[-1][0] & 0x80) == 0x80


def test_fragment_segments_carry_increasing_sequence_numbers():
    data = bytes(range(50))
    segments = fragment(data, mtu=10)
    seqs = [seg[0] & 0x7F for seg in segments]
    assert seqs == list(range(len(segments)))


def test_full_engine_envelope_fragmented_and_reassembled():
    """End-to-end: encode a multi-instance envelope, fragment it to a small
    MTU, reassemble, and decode -- the whole Requirement 27 pipeline."""
    engines = [_make_engine(i, 10 + i, rpm=1000.0 + i * 100) for i in range(3)]
    envelope_bytes = encode_envelope(engines)
    segments = fragment(envelope_bytes, mtu=20)
    assert len(segments) > 1  # genuinely exercises fragmentation

    reassembler = Reassembler()
    result = None
    for seg in segments:
        result = reassembler.feed(seg)
    assert result == envelope_bytes

    decoded = decode_envelope(result)
    assert len(decoded.entities) == 3


# --- Requirement 20.6: quality/staleness marker on every served value ------


def test_quality_round_trips_for_every_quality_value():
    """Every Provenance.quality value ("valid"/"stale"/"invalid"/"unknown")
    must round-trip through the wire envelope unchanged, so a stale or
    invalid value is distinguishable from current valid data at the BLE
    interface too, not just over REST/WS (Requirement 20.6)."""
    for quality in ("valid", "stale", "invalid", "unknown"):
        engine = _make_engine(0, 1, rpm=1500.0)
        engine.provenance.quality = quality
        decoded = decode_envelope(encode_envelope([engine]))
        assert decoded.entities[0].quality == quality


def test_stale_value_distinguishable_from_valid_on_the_wire():
    """Property 25-adjacent: a stale value's quality marker differs from a
    valid one's after the full encode/decode round trip -- a client reading
    the envelope can tell them apart without any extra channel."""
    valid_engine = _make_engine(0, 1, rpm=1500.0)
    stale_engine = _make_engine(0, 1, rpm=1500.0)
    stale_engine.provenance.quality = "stale"
    stale_engine.provenance.staleness_s = 12.0

    valid_decoded = decode_envelope(encode_envelope([valid_engine])).entities[0]
    stale_decoded = decode_envelope(encode_envelope([stale_engine])).entities[0]

    assert valid_decoded.quality == "valid"
    assert stale_decoded.quality == "stale"
    assert stale_decoded.staleness_s > valid_decoded.staleness_s
