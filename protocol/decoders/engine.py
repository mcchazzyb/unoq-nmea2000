"""Engine decoders: PGN 127488 (Rapid Update) and 127489 (Dynamic), populating
the canonical ``protocol.domain.EngineState`` directly (Requirement 7.1-7.4).

Both PGNs are standard NMEA 2000 engine messages emitted by ANY compliant
engine interface -- a Suzuki SMIS Engine Interface box, a Yamaha Command Link
gauge, or a bare Volvo Penta EVC all emit the same bytes. Per Requirement 6.2 /
steering rule 04, this module makes no manufacturer assumption: it decodes the
bytes the standard defines, nothing else.

Field layouts and resolutions sourced from the CANboat v2 PGN database
(docs/canboat.json), the de facto open reference for NMEA 2000 byte-level
encoding (the official NMEA spec text is paywalled).

Instance keying: callers key the returned ``EngineState`` by
``(engine_instance, source_address)`` per Requirement 7.2 / steering 07 --
this module only populates the entity, it does not maintain the keyed dict.
"""
from __future__ import annotations

import struct
import time

from protocol.domain import UNSET, EngineState, Provenance
from protocol.registry import DecodeResult, decoder_for

DECODER_VERSION = "1.0.0"

PGN_ENGINE_RAPID_UPDATE = 127488
PGN_ENGINE_DYNAMIC = 127489

_U16_NA = 0xFFFF
_U32_NA = 0xFFFFFFFF
_I8_NA = 0x7F
_I16_NA = 0x7FFF


def _provenance(source_address: int, pgn: int, raw_frame_ref: str) -> Provenance:
    return Provenance(
        source_address=source_address,
        source_pgn=pgn,
        node_identity="unknown",
        raw_frame_ref=raw_frame_ref,
        timestamp=time.time(),
        decoder_version=DECODER_VERSION,
        quality="valid",
        staleness_s=0.0,
        vendor_specific=False,
        value_origin="decoded",
    )


@decoder_for(PGN_ENGINE_RAPID_UPDATE, version=DECODER_VERSION)
def decode_engine_rapid_update(source_address: int, data: bytes) -> DecodeResult:
    """PGN 127488, single-frame, 8 bytes.

    Byte layout (little-endian multi-byte fields):
      0:    Engine Instance (u8)
      1-2:  Engine Speed, resolution 0.25 rpm, 0xFFFF = not available
      3-4:  Boost Pressure, resolution 100 Pa (unused by EngineState)
      5:    Tilt/Trim, signed %, 0x7F = not available
      6-7:  Reserved
    """
    engine_instance = data[0] if len(data) >= 1 else 0
    state = EngineState(engine_instance=engine_instance)
    state.source_address = source_address
    state.source_pgn = PGN_ENGINE_RAPID_UPDATE
    state.updated_at = time.time()
    state.provenance = _provenance(source_address, PGN_ENGINE_RAPID_UPDATE, "unknown")

    if len(data) >= 3:
        raw = struct.unpack_from("<H", data, 1)[0]
        state.rpm = UNSET if raw == _U16_NA else raw * 0.25

    if len(data) >= 6:
        raw = struct.unpack_from("<b", data, 5)[0]
        state.trim = UNSET if raw == _I8_NA else raw

    return DecodeResult(pgn=PGN_ENGINE_RAPID_UPDATE, source_address=source_address, entity=state)


@decoder_for(PGN_ENGINE_DYNAMIC, version=DECODER_VERSION)
def decode_engine_dynamic(source_address: int, data: bytes) -> DecodeResult:
    """PGN 127489, fast-packet, up to 26 bytes (reassembled by Stage 2 before
    reaching this decoder).

    Byte layout (offsets relative to the reassembled payload):
      0:     Engine Instance (u8)
      1-2:   Oil pressure, resolution 100 Pa (unused by EngineState)
      3-4:   Oil temperature, resolution 0.1 K (unused by EngineState)
      5-6:   Coolant/engine temperature, resolution 0.01 K, 0xFFFF = N/A
      7-8:   Alternator potential, signed, resolution 0.01 V, 0x7FFF = N/A
      9-10:  Fuel rate, signed, resolution 0.1 L/h, 0x7FFF = N/A
      11-14: Total engine hours, resolution 1 s, 0xFFFFFFFF = N/A
      15-18: Coolant/fuel pressure (unused by EngineState)
      19:    Reserved
      20-21: Discrete Status 1 (bitfield) -> EngineState.status
      22-23: Discrete Status 2 (unused by EngineState)
      24-25: Engine load/torque % (unused by EngineState)
    """
    engine_instance = data[0] if len(data) >= 1 else 0
    state = EngineState(engine_instance=engine_instance)
    state.source_address = source_address
    state.source_pgn = PGN_ENGINE_DYNAMIC
    state.updated_at = time.time()
    state.provenance = _provenance(source_address, PGN_ENGINE_DYNAMIC, "unknown")

    if len(data) >= 7:
        raw = struct.unpack_from("<H", data, 5)[0]
        # NMEA2000 stores Kelvin; EngineState.coolant_temp documents no unit,
        # convert to degrees Celsius for operator readability.
        state.coolant_temp = UNSET if raw == _U16_NA else (raw * 0.01) - 273.15

    if len(data) >= 3:
        raw = struct.unpack_from("<H", data, 1)[0]
        state.oil_pressure = UNSET if raw == _U16_NA else raw * 100

    if len(data) >= 9:
        raw = struct.unpack_from("<h", data, 7)[0]
        state.alternator_voltage = UNSET if raw == _I16_NA else raw * 0.01

    if len(data) >= 15:
        raw = struct.unpack_from("<I", data, 11)[0]
        state.engine_hours = UNSET if raw == _U32_NA else raw

    if len(data) >= 11:
        raw = struct.unpack_from("<h", data, 9)[0]
        state.fuel_rate = UNSET if raw == _I16_NA else raw * 0.1

    if len(data) >= 22:
        state.status = struct.unpack_from("<H", data, 20)[0]

    return DecodeResult(pgn=PGN_ENGINE_DYNAMIC, source_address=source_address, entity=state)
