"""Tank/fluid level decoder: PGN 127505, populating the canonical
``protocol.domain.TankState`` directly (Requirement 8.1-8.7).

Standard NMEA 2000 PGN emitted by any tank sender interface -- a Suzuki analog
fuel sender through a generic 0-190ohm-to-NMEA2000 gateway, a Maretron TLA100,
or any other compliant tank module. No manufacturer assumption is made here
(Requirement 6.2 / steering rule 04).

Instance keying: callers key the returned ``TankState`` by
``(tank_instance, source_address)`` per Requirement 8.2 / steering 07 -- this
module only populates the entity.
"""
from __future__ import annotations

import struct
import time

from protocol.domain import UNSET, Provenance, TankState
from protocol.registry import DecodeResult, decoder_for

DECODER_VERSION = "1.0.0"

PGN_FLUID_LEVEL = 127505

_I16_NA = 0x7FFF
_U32_NA = 0xFFFFFFFF


def _provenance(source_address: int) -> Provenance:
    return Provenance(
        source_address=source_address,
        source_pgn=PGN_FLUID_LEVEL,
        node_identity="unknown",
        raw_frame_ref="unknown",
        timestamp=time.time(),
        decoder_version=DECODER_VERSION,
        quality="valid",
        staleness_s=0.0,
        vendor_specific=False,
        value_origin="decoded",
    )


@decoder_for(PGN_FLUID_LEVEL, version=DECODER_VERSION)
def decode_fluid_level(source_address: int, data: bytes) -> DecodeResult:
    """PGN 127505, single-frame, 8 bytes.

    Byte layout:
      0:    bits 0-3 Instance (u4), bits 4-7 Tank Type (u4)
      1-2:  Level, signed, resolution 0.004 %, 0x7FFF = N/A
      3-6:  Capacity, resolution 0.1 L, 0xFFFFFFFF = N/A
      7:    Reserved

    level_percent out of [0, 100] decodes to UNSET (Requirement 8.5, Property
    14). quantity_remaining_litres is derived as
    (level_percent / 100) * capacity_litres, only when both inputs are present
    (Requirement 8.6). level_percent is a 0-100 percentage of capacity_litres
    (confirmed against the NMEA 2000 PGN 127505 field definition and against
    real-world tank-sender configuration, where capacity is set once in litres
    -- e.g. 90 L, 120 L -- and level is reported as % of that setting); the
    /100 division converts the percentage to a fraction before scaling.
    """
    tank_instance = (data[0] & 0x0F) if len(data) >= 1 else 0
    fluid_type = ((data[0] >> 4) & 0x0F) if len(data) >= 1 else UNSET

    state = TankState(tank_instance=tank_instance)
    state.fluid_type = fluid_type
    state.source_address = source_address
    state.source_pgn = PGN_FLUID_LEVEL
    state.updated_at = time.time()
    state.provenance = _provenance(source_address)

    if len(data) >= 3:
        raw = struct.unpack_from("<h", data, 1)[0]
        if raw == _I16_NA:
            level_percent = UNSET
        else:
            candidate = raw * 0.004
            # Reject out-of-range decodes rather than clamping (Property 14).
            level_percent = candidate if 0.0 <= candidate <= 100.0 else UNSET
        state.level_percent = level_percent

    if len(data) >= 7:
        raw = struct.unpack_from("<I", data, 3)[0]
        state.capacity_litres = UNSET if raw == _U32_NA else raw * 0.1

    if state.level_percent is not UNSET and state.capacity_litres is not UNSET:
        quantity = (state.level_percent / 100.0) * state.capacity_litres
        state.quantity_remaining_litres = quantity
        state.provenance.value_origin = "derived"
        state.provenance.derivation = "(level_percent / 100) * capacity_litres"
    else:
        state.quantity_remaining_litres = UNSET

    return DecodeResult(pgn=PGN_FLUID_LEVEL, source_address=source_address, entity=state)
