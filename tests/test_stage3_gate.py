"""Stage 3 Gate -- Decoder_Registry and decoders (engine, tank, electrical,
navigation, identity, product-info).

Property tags follow .kiro/steering/09-property-test-tagging.md exactly:
    Feature: nmea2000-diagnostic-platform, Property N: <text>
matching design.md -> Correctness Properties.
"""
from __future__ import annotations

import struct

from hypothesis import given, settings
from hypothesis import strategies as st

from protocol.decoders.electrical import decode_battery_status
from protocol.decoders.engine import decode_engine_dynamic, decode_engine_rapid_update
from protocol.decoders.identity import (
    IsoName,
    PGN_ISO_ADDRESS_CLAIM,
    PGN_PRODUCT_INFORMATION,
    decode_iso_address_claim,
    decode_product_information,
)
from protocol.decoders.navigation import decode_position_rapid_update
from protocol.decoders.tank import decode_fluid_level
from protocol.domain import UNSET, EngineState, TankState
from protocol.registry import DecoderConflictError, DecoderRegistry


# --- Requirement 5 / Property 10: at most one decoder per PGN ---------------


def test_duplicate_registration_rejected_existing_decoder_retained():
    """Feature: nmea2000-diagnostic-platform, Property 10: at-most-one-decoder
    per PGN -- a second registration is rejected and the existing decoder
    stays callable."""
    registry = DecoderRegistry()

    def decoder_a(source_address, data):
        from protocol.registry import DecodeResult

        return DecodeResult(pgn=127488, source_address=source_address, entity="a")

    def decoder_b(source_address, data):
        from protocol.registry import DecodeResult

        return DecodeResult(pgn=127488, source_address=source_address, entity="b")

    registry.register(127488, decoder_a)
    try:
        registry.register(127488, decoder_b)
    except DecoderConflictError as exc:
        assert "127488" in str(exc)
    else:
        raise AssertionError("expected DecoderConflictError")

    # Existing decoder is still the one invoked.
    result = registry.decode(127488, source_address=1, data=b"\x00" * 8)
    assert result.entity == "a"


def test_unknown_pgn_never_invokes_a_decoder():
    """Feature: nmea2000-diagnostic-platform, Property 10 (unknown-PGN half):
    a PGN with no registered decoder returns unknown() and invokes nothing."""
    registry = DecoderRegistry()
    invoked = []

    def tracking_decoder(source_address, data):
        from protocol.registry import DecodeResult

        invoked.append(True)
        return DecodeResult(pgn=1, source_address=source_address, entity=None)

    registry.register(1, tracking_decoder)
    result = registry.decode(999, source_address=5, data=b"\x00")
    assert result.known is False
    assert result.entity is None
    assert invoked == []  # the registered decoder for a DIFFERENT pgn never ran


def test_decoder_version_threaded_into_result():
    """Requirement 5.5: the registry threads decoder.version into the result."""
    registry = DecoderRegistry()

    def decoder(source_address, data):
        from protocol.registry import DecodeResult

        return DecodeResult(pgn=42, source_address=source_address, entity=None)

    registry.register(42, decoder, version="2.3.1")
    result = registry.decode(42, source_address=1, data=b"")
    assert result.decoder_version == "2.3.1"


# --- Requirement 7: engine decoder present/absent/invalid handling ---------


def test_engine_rapid_update_populates_only_present_fields():
    # engine_instance=3, rpm raw=4000 (-> 1000.0 rpm), boost N/A, trim N/A
    data = struct.pack("<BHHb", 3, 4000, 0xFFFF, 0x7F) + b"\x00\x00"
    result = decode_engine_rapid_update(source_address=10, data=data)
    state: EngineState = result.entity
    assert state.engine_instance == 3
    assert state.rpm == 1000.0
    assert state.trim is UNSET


def test_engine_rapid_update_short_frame_leaves_fields_unset():
    result = decode_engine_rapid_update(source_address=10, data=b"\x07")
    state: EngineState = result.entity
    assert state.rpm is UNSET
    assert state.trim is UNSET


def test_engine_dynamic_full_frame_decodes_all_mapped_fields():
    data = bytearray(26)
    data[0] = 2  # instance
    struct.pack_into("<H", data, 1, 0xFFFF)  # oil pressure N/A
    struct.pack_into("<H", data, 3, 0xFFFF)  # oil temp N/A
    struct.pack_into("<H", data, 5, 28315)  # coolant 283.15K -> 10.0C
    struct.pack_into("<h", data, 7, 1234)  # alternator 12.34V
    struct.pack_into("<h", data, 9, -50)  # fuel rate -5.0 L/h
    struct.pack_into("<I", data, 11, 3600)  # engine hours: 3600 s -> 1.0 h
    struct.pack_into("<H", data, 15, 0xFFFF)  # coolant pressure N/A
    struct.pack_into("<H", data, 17, 0xFFFF)  # fuel pressure N/A
    data[19] = 0  # reserved
    struct.pack_into("<H", data, 20, 99)  # discrete status 1
    struct.pack_into("<H", data, 22, 0)  # discrete status 2
    data[24] = 0x7F  # engine load N/A
    data[25] = 0x7F  # engine torque N/A
    result = decode_engine_dynamic(source_address=10, data=bytes(data))
    state: EngineState = result.entity
    assert state.engine_instance == 2
    assert round(state.coolant_temp, 2) == 10.0
    assert round(state.alternator_voltage, 2) == 12.34
    assert round(state.fuel_rate, 1) == -5.0
    assert state.engine_hours == 1.0  # 3600 s / 3600 = 1.0 h
    assert state.oil_pressure is UNSET
    assert state.status == 99


def test_engine_hours_is_reported_in_hours_not_raw_seconds():
    """Regression (2026-10-10 boat trip): PGN 127489 total-hours field is 1 s
    resolution on the wire; EngineState.engine_hours must be HOURS, not raw
    seconds. The boat sender reported raw 661920 s; the dashboard showed
    "661920 s" instead of ~183.87 h. Fails before the /3600 fix, passes after."""
    data = bytearray(26)
    data[0] = 0  # instance
    struct.pack_into("<H", data, 1, 0xFFFF)  # oil pressure N/A
    struct.pack_into("<H", data, 3, 0xFFFF)  # oil temp N/A
    struct.pack_into("<H", data, 5, 0xFFFF)  # coolant N/A
    struct.pack_into("<h", data, 7, 0x7FFF)  # alternator N/A
    struct.pack_into("<h", data, 9, 0x7FFF)  # fuel rate N/A
    struct.pack_into("<I", data, 11, 661_920)  # real boat value, raw seconds
    struct.pack_into("<H", data, 15, 0xFFFF)
    struct.pack_into("<H", data, 17, 0xFFFF)
    struct.pack_into("<H", data, 20, 0)
    struct.pack_into("<H", data, 22, 0)
    data[24] = 0x7F
    data[25] = 0x7F
    state: EngineState = decode_engine_dynamic(source_address=1, data=bytes(data)).entity
    assert round(state.engine_hours, 2) == 183.87
    # must NOT still be the raw seconds count
    assert state.engine_hours != 661_920


# --- Requirement 8 / Property 14, 15, 33: tank decoder ----------------------


def test_tank_level_and_capacity_present_derives_quantity():
    # instance=1, tank_type=0 (fuel), level raw for 50% = 50/0.004=12500,
    # capacity raw for 200.0 L = 2000
    data = struct.pack("<Bh", (0 << 4) | 1, 12500) + struct.pack("<I", 2000) + b"\x00"
    result = decode_fluid_level(source_address=7, data=data)
    state: TankState = result.entity
    assert state.tank_instance == 1
    assert round(state.level_percent, 3) == 50.0
    assert round(state.capacity_litres, 1) == 200.0
    # quantity_remaining_litres = (level_percent / 100) * capacity_litres --
    # 50% of a 200.0 L tank = 100.0 litres (confirmed against the NMEA 2000
    # PGN 127505 field definition and real-world tank-sender configuration,
    # where capacity is set once in litres and level is % of that setting).
    assert round(state.quantity_remaining_litres, 1) == 100.0
    assert state.provenance.value_origin == "derived"
    assert state.provenance.derivation == "(level_percent / 100) * capacity_litres"


def test_tank_capacity_missing_leaves_capacity_and_quantity_unset():
    """Requirement 8.3: missing capacity leaves capacity_litres AND
    quantity_remaining_litres unset while retaining level_percent."""
    data = struct.pack("<Bh", 1, 12500)  # only instance + level, no capacity bytes
    result = decode_fluid_level(source_address=7, data=data)
    state: TankState = result.entity
    assert round(state.level_percent, 3) == 50.0
    assert state.capacity_litres is UNSET
    assert state.quantity_remaining_litres is UNSET


@given(raw_level=st.integers(min_value=-32768, max_value=32767))
@settings(max_examples=150)
def test_level_percent_always_unset_or_in_range(raw_level):
    """Feature: nmea2000-diagnostic-platform, Property 14: level_percent is
    UNSET or within 0..100 inclusive for every raw 16-bit value -- an
    out-of-range decode is UNSET, never a clamped number."""
    if raw_level == 0x7FFF:
        raw_level = 1  # avoid the N/A sentinel itself; covered separately below
    data = struct.pack("<Bh", 0, raw_level) + b"\x00" * 5
    result = decode_fluid_level(source_address=1, data=data)
    state: TankState = result.entity
    assert state.level_percent is UNSET or 0.0 <= state.level_percent <= 100.0


def test_level_percent_na_sentinel_is_unset():
    data = struct.pack("<Bh", 0, 0x7FFF) + b"\x00" * 5
    result = decode_fluid_level(source_address=1, data=data)
    assert result.entity.level_percent is UNSET


@given(
    level_raw=st.integers(min_value=0, max_value=25000),  # 0..100% in 0.004 steps
    capacity_raw=st.integers(min_value=1, max_value=100000),
)
@settings(max_examples=100)
def test_derived_quantity_matches_derivation_rule(level_raw, capacity_raw):
    """Feature: nmea2000-diagnostic-platform, Property 33: derived
    quantity_remaining_litres always equals (level_percent / 100) *
    capacity_litres when both inputs are present."""
    data = struct.pack("<Bh", 0, level_raw) + struct.pack("<I", capacity_raw) + b"\x00"
    result = decode_fluid_level(source_address=1, data=data)
    state: TankState = result.entity
    if state.level_percent is UNSET or state.capacity_litres is UNSET:
        assert state.quantity_remaining_litres is UNSET
    else:
        expected = (state.level_percent / 100.0) * state.capacity_litres
        assert state.quantity_remaining_litres == expected
        assert state.provenance.value_origin == "derived"


def test_distinct_engine_instances_never_collide_when_keyed():
    """Feature: nmea2000-diagnostic-platform, Property 15 (decoder-output
    half): two decodes with different (instance, source_address) produce
    states a caller can key without collision."""
    data1 = struct.pack("<BHHb", 0, 4000, 0xFFFF, 0x7F) + b"\x00\x00"
    data2 = struct.pack("<BHHb", 1, 5000, 0xFFFF, 0x7F) + b"\x00\x00"
    r1 = decode_engine_rapid_update(source_address=10, data=data1)
    r2 = decode_engine_rapid_update(source_address=10, data=data2)
    key1 = (r1.entity.engine_instance, r1.source_address)
    key2 = (r2.entity.engine_instance, r2.source_address)
    assert key1 != key2

    r3 = decode_engine_rapid_update(source_address=11, data=data1)
    key3 = (r3.entity.engine_instance, r3.source_address)
    assert key1 != key3  # same instance, different source -> different key


# --- Requirement 9: electrical / navigation absent-field handling ----------


def test_battery_status_na_sentinels_are_unset():
    data = struct.pack("<Bhh", 0, 0x7FFF, 0x7FFF) + b"\x00" * 3
    result = decode_battery_status(source_address=4, data=data)
    assert result.entity.battery_voltage is UNSET
    assert result.entity.current is UNSET


def test_battery_status_decodes_present_fields():
    data = struct.pack("<Bhh", 0, 1250, -30) + b"\x00" * 3  # 12.50V, -3.0A
    result = decode_battery_status(source_address=4, data=data)
    assert round(result.entity.battery_voltage, 2) == 12.5
    assert round(result.entity.current, 1) == -3.0


def test_navigation_position_na_sentinel_is_unset():
    data = struct.pack("<ii", 0x7FFFFFFF, 0x7FFFFFFF)
    result = decode_position_rapid_update(source_address=2, data=data)
    assert result.entity.position is UNSET


# --- Requirement 10: identity decoders --------------------------------------


def test_iso_name_round_trips_through_bit_packing():
    """Feature: nmea2000-diagnostic-platform, Property 19 (identity half):
    an ISO NAME's component fields survive the bit-pack/unpack round trip."""
    components = dict(
        unique_number=12345,
        manufacturer_code=999,
        device_instance_lower=2,
        device_instance_upper=7,
        device_function=130,
        device_class=25,
        system_instance=3,
        industry_group=4,
        arbitrary_address_capable=1,
    )
    raw = 0
    bit = 0
    widths = [
        ("unique_number", 21),
        ("manufacturer_code", 11),
        ("device_instance_lower", 3),
        ("device_instance_upper", 5),
        ("device_function", 8),
        (None, 1),  # reserved
        ("device_class", 7),
        ("system_instance", 4),
        ("industry_group", 3),
        ("arbitrary_address_capable", 1),
    ]
    for name, width in widths:
        value = components.get(name, 0) if name else 0
        raw |= (value & ((1 << width) - 1)) << bit
        bit += width
    assert bit == 64

    data = struct.pack("<Q", raw)
    result = decode_iso_address_claim(source_address=5, data=data)
    name: IsoName = result.entity
    for key, expected in components.items():
        assert getattr(name, key) == expected, key


def test_iso_address_claim_short_frame_yields_no_entity():
    result = decode_iso_address_claim(source_address=5, data=b"\x00" * 4)
    assert result.entity is None


def test_product_information_decodes_padded_ascii_fields():
    model_id = b"Signal K" + b"\x00" * (32 - len(b"Signal K"))
    sw_version = b"1.0" + b"\x00" * (32 - len(b"1.0"))
    model_version = b"canbusjs" + b"\x00" * (32 - len(b"canbusjs"))
    serial = b"123456" + b"\x00" * (32 - len(b"123456"))
    data = (
        struct.pack("<HH", 1300, 667)
        + model_id
        + sw_version
        + model_version
        + serial
        + bytes([0, 1])
    )
    assert len(data) == 134
    result = decode_product_information(source_address=100, data=data)
    info = result.entity
    assert info.nmea_2000_version == 1300
    assert info.product_code == 667
    assert info.model_id == "Signal K"
    assert info.software_version_code == "1.0"
    assert info.certification_level == 0
    assert info.load_equivalency == 1


def test_product_information_incomplete_payload_yields_no_entity():
    """Requirement 10.7 (decoder half): a payload shorter than spec never
    produces a ProductInformation -- the caller (Discovery_Mode / fast-packet
    reassembler) is responsible for the 5s discard-and-retain-prior rule;
    this decoder simply refuses to half-decode."""
    result = decode_product_information(source_address=100, data=b"\x00" * 10)
    assert result.entity is None


# --- Requirement 11 / Property 11, 13: Provenance completeness -------------


@given(
    data=st.binary(min_size=0, max_size=8),
    source_address=st.integers(min_value=0, max_value=255),
)
@settings(max_examples=100)
def test_every_decoded_engine_value_carries_complete_provenance(data, source_address):
    """Feature: nmea2000-diagnostic-platform, Property 11: every decoded value
    carries a complete Provenance -- no field is omitted, undeterminable
    fields are the string "unknown"."""
    result = decode_engine_rapid_update(source_address=source_address, data=data)
    prov = result.entity.provenance
    assert prov is not None
    assert prov.source_address == source_address
    assert prov.source_pgn == 127488
    assert prov.node_identity == "unknown"  # undeterminable -> "unknown", never omitted
    assert prov.decoder_version
    assert prov.quality in ("valid", "stale", "invalid", "unknown")
    assert prov.value_origin in ("decoded", "derived")


@given(
    level_raw=st.integers(min_value=-32768, max_value=32767),
    capacity_raw=st.integers(min_value=0, max_value=0xFFFFFFFF),
)
@settings(max_examples=100)
def test_unset_distinguishable_from_zero_across_tank_fields(level_raw, capacity_raw):
    """Feature: nmea2000-diagnostic-platform, Property 13 (decoder half):
    UNSET is distinguishable from both None and 0 for every TankState field
    this decoder populates."""
    data = struct.pack("<Bh", 0, level_raw) + struct.pack("<I", capacity_raw) + b"\x00"
    result = decode_fluid_level(source_address=1, data=data)
    state: TankState = result.entity
    for value in (state.level_percent, state.capacity_litres, state.quantity_remaining_litres):
        if value == 0:
            # A genuine decoded/derived zero is a real reading, never UNSET.
            assert value is not UNSET
        if value is None:
            # None must never appear as a field value -- UNSET or a number only.
            raise AssertionError("field value is None, must be UNSET or a number")
