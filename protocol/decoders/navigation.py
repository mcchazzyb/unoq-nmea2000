"""Navigation decoder: PGN 129025 (Position Rapid Update) and PGN 129026
(COG & SOG Rapid Update), populating the canonical
``protocol.domain.NavigationState`` directly (Requirement 9.2, 9.3).

Standard GNSS PGNs emitted by any onboard GPS receiver -- these decode the
"speed/position fed from the onboard GPS" requirement without assuming any
particular chartplotter or GPS brand (Requirement 6.2 / steering rule 04).

Note on speed semantics: a GPS can only ever report Speed Over Ground (SOG,
PGN 129026) -- it measures position change relative to the earth, not
relative to the surrounding water, so it cannot produce a true Speed
Through Water value without a dedicated water-referenced sensor (e.g. a
paddlewheel, PGN 128259). A GPS unit's "speed" readout is SOG regardless of
how its own display may label it. PGN 128259 is intentionally NOT decoded
here: it requires a physical water-speed sensor this platform's vessels may
not carry, and decoding it would need a design decision (a new
NavigationState.stw field) only worth making once a vessel actually has
that sensor.

NavigationState carries no instance/source-address key per design.md (one
NavigationState per VesselState); both PGNs decode into the SAME entity's
different fields, so each decoder here only sets the fields its PGN carries
and leaves the rest at the caller-supplied/UNSET defaults -- callers merge
successive decodes into the vessel's single NavigationState record.
"""
from __future__ import annotations

import math
import struct
import time

from protocol.domain import UNSET, NavigationState, Provenance
from protocol.registry import DecodeResult, decoder_for

DECODER_VERSION = "1.0.0"

PGN_POSITION_RAPID_UPDATE = 129025
PGN_COG_SOG_RAPID_UPDATE = 129026

_I32_NA = 0x7FFFFFFF
_U16_NA = 0xFFFF


def _provenance(source_address: int, pgn: int) -> Provenance:
    return Provenance(
        source_address=source_address,
        source_pgn=pgn,
        node_identity="unknown",
        raw_frame_ref="unknown",
        timestamp=time.time(),
        decoder_version=DECODER_VERSION,
        quality="valid",
        staleness_s=0.0,
        vendor_specific=False,
        value_origin="decoded",
    )


@decoder_for(PGN_POSITION_RAPID_UPDATE, version=DECODER_VERSION)
def decode_position_rapid_update(source_address: int, data: bytes) -> DecodeResult:
    """PGN 129025, single-frame, 8 bytes.

    Byte layout:
      0-3: Latitude, signed, resolution 1e-7 deg, 0x7FFFFFFF = N/A
      4-7: Longitude, signed, resolution 1e-7 deg, 0x7FFFFFFF = N/A
    """
    state = NavigationState()
    state.source_address = source_address
    state.source_pgn = PGN_POSITION_RAPID_UPDATE
    state.updated_at = time.time()
    state.provenance = _provenance(source_address, PGN_POSITION_RAPID_UPDATE)

    lat = UNSET
    lon = UNSET
    if len(data) >= 4:
        raw = struct.unpack_from("<i", data, 0)[0]
        lat = UNSET if raw == _I32_NA else raw * 1e-7
    if len(data) >= 8:
        raw = struct.unpack_from("<i", data, 4)[0]
        lon = UNSET if raw == _I32_NA else raw * 1e-7

    state.position = UNSET if (lat is UNSET or lon is UNSET) else (lat, lon)

    return DecodeResult(
        pgn=PGN_POSITION_RAPID_UPDATE, source_address=source_address, entity=state
    )


@decoder_for(PGN_COG_SOG_RAPID_UPDATE, version=DECODER_VERSION)
def decode_cog_sog_rapid_update(source_address: int, data: bytes) -> DecodeResult:
    """PGN 129026, single-frame, 8 bytes.

    Byte layout:
      0:   SID (u8, unused by NavigationState)
      1:   bits 0-1 COG Reference (unused by NavigationState), bits 2-7 Reserved
      2-3: COG, resolution 0.0001 rad, 0xFFFF = N/A
      4-5: SOG, resolution 0.01 m/s, 0xFFFF = N/A
      6-7: Reserved
    """
    state = NavigationState()
    state.source_address = source_address
    state.source_pgn = PGN_COG_SOG_RAPID_UPDATE
    state.updated_at = time.time()
    state.provenance = _provenance(source_address, PGN_COG_SOG_RAPID_UPDATE)

    if len(data) >= 4:
        raw = struct.unpack_from("<H", data, 2)[0]
        state.cog = UNSET if raw == _U16_NA else math.degrees(raw * 0.0001)

    if len(data) >= 6:
        raw = struct.unpack_from("<H", data, 4)[0]
        state.sog = UNSET if raw == _U16_NA else raw * 0.01

    return DecodeResult(
        pgn=PGN_COG_SOG_RAPID_UPDATE, source_address=source_address, entity=state
    )
