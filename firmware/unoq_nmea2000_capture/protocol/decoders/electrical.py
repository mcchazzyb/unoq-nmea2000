"""Electrical decoder: PGN 127508 (Battery Status), populating the canonical
``protocol.domain.ElectricalState`` directly (Requirement 9.1, 9.3).

Standard NMEA 2000 PGN emitted by any compliant battery monitor / DC source
interface. No manufacturer assumption is made here (Requirement 6.2 / steering
rule 04).

Byte layout, single-frame, 8 bytes:
  0:   Battery Instance (u8) -- not carried by ElectricalState (design.md has
       no instance field on this entity; one ElectricalState per VesselState)
  1-2: Battery Voltage, resolution 0.01 V, signed, 0x7FFF = N/A
  3-4: Battery Current, resolution 0.1 A, signed, 0x7FFF = N/A
  5-6: Battery Case Temperature, resolution 0.01 K, 0xFFFF = N/A (unused by
       ElectricalState, which has no temperature field)
  7:   SID (unused)
"""
from __future__ import annotations

import struct
import time

from protocol.domain import UNSET, ElectricalState, Provenance
from protocol.registry import DecodeResult, decoder_for

DECODER_VERSION = "1.0.0"

PGN_BATTERY_STATUS = 127508

_I16_NA = 0x7FFF


@decoder_for(PGN_BATTERY_STATUS, version=DECODER_VERSION)
def decode_battery_status(source_address: int, data: bytes) -> DecodeResult:
    state = ElectricalState()
    state.source_address = source_address
    state.source_pgn = PGN_BATTERY_STATUS
    state.updated_at = time.time()
    state.provenance = Provenance(
        source_address=source_address,
        source_pgn=PGN_BATTERY_STATUS,
        node_identity="unknown",
        raw_frame_ref="unknown",
        timestamp=time.time(),
        decoder_version=DECODER_VERSION,
        quality="valid",
        staleness_s=0.0,
        vendor_specific=False,
        value_origin="decoded",
    )

    if len(data) >= 3:
        raw = struct.unpack_from("<h", data, 1)[0]
        state.battery_voltage = UNSET if raw == _I16_NA else raw * 0.01

    if len(data) >= 5:
        raw = struct.unpack_from("<h", data, 3)[0]
        state.current = UNSET if raw == _I16_NA else raw * 0.1

    return DecodeResult(pgn=PGN_BATTERY_STATUS, source_address=source_address, entity=state)
