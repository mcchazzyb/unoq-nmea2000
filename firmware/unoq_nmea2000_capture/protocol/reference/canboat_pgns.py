"""Reference dataset of NMEA 2000 PGN signatures for autodetection and matching.

This module holds structural *reference data* only -- PGN number, frame type
(Single vs Fast), byte length, field count and (for proprietary PGNs) the
manufacturer code. It exists so the platform can MATCH observed traffic against
a catalogue of known PGNs across every manufacturer, not decode it.

Project rule: never assume traffic is from one manufacturer. Proprietary PGN
numbers (e.g. 65280) are reused by many vendors, so each PGN maps to a *list* of
candidate signatures. This is reference/matching data, never hard-coded
single-manufacturer decode logic. All byte layouts and field counts are taken
from the open-source CANboat database (canboat.json).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PGNSignature:
    """One candidate structural signature for a PGN.

    ``length`` is None for variable-length PGNs; use ``min_length`` instead.
    """

    pgn: int
    name: str
    description: str
    pgn_type: str  # "Single" | "Fast"
    length: int | None
    min_length: int | None
    field_count: int | None
    is_proprietary: bool
    manufacturer_code: int | None
    source: str = "canboat_v2"


def _std(pgn: int, name: str, pgn_type: str, length: int | None,
         field_count: int | None, min_length: int | None = None) -> PGNSignature:
    """Build a standard (non-proprietary) signature."""
    return PGNSignature(
        pgn=pgn,
        name=name,
        description=name,
        pgn_type=pgn_type,
        length=length,
        min_length=min_length,
        field_count=field_count,
        is_proprietary=False,
        manufacturer_code=None,
    )


def _prop(pgn: int, name: str, manufacturer_code: int, pgn_type: str = "Single",
          length: int | None = 8, field_count: int | None = 4) -> PGNSignature:
    """Build a proprietary signature carrying a manufacturer code."""
    return PGNSignature(
        pgn=pgn,
        name=name,
        description=name,
        pgn_type=pgn_type,
        length=length,
        min_length=None,
        field_count=field_count,
        is_proprietary=True,
        manufacturer_code=manufacturer_code,
    )


# Standard (manufacturer-agnostic) PGNs. Format from caller: PGN(type,length,field_count).
_STANDARD: list[PGNSignature] = [
    _std(59392, "ISO Acknowledgement", "Single", 8, 4),
    _std(59904, "ISO Request", "Single", 3, 1),
    _std(60928, "ISO Address Claim", "Single", 8, 10),
    _std(126992, "System Time", "Single", 8, 5),
    _std(126993, "Heartbeat", "Single", 8, 6),
    _std(126996, "Product Information", "Fast", 134, 8),
    _std(127245, "Rudder", "Single", 8, 6),
    _std(127250, "Vessel Heading", "Single", 8, 6),
    _std(127251, "Rate of Turn", "Single", 8, 3),
    _std(127257, "Attitude", "Single", 8, 5),
    _std(127258, "Magnetic Variation", "Single", 8, 6),
    _std(127488, "Engine Parameters, Rapid Update", "Single", 8, 5),
    _std(127489, "Engine Parameters, Dynamic", "Fast", 26, 14),
    _std(127493, "Transmission Parameters, Dynamic", "Single", 8, 7),
    _std(127501, "Binary Status Report", "Single", 8, 29),
    _std(127505, "Fluid Level", "Single", 8, 5),
    _std(127506, "DC Detailed Status", "Fast", 11, 8),
    _std(127508, "Battery Status", "Single", 8, 5),
    _std(128259, "Speed", "Single", 8, 6),
    _std(128267, "Water Depth", "Single", 8, 4),
    _std(128275, "Distance Log", "Fast", 14, 4),
    _std(129025, "Position, Rapid Update", "Single", 8, 2),
    _std(129026, "COG & SOG, Rapid Update", "Single", 8, 6),
    _std(129029, "GNSS Position Data", "Fast", None, 18, min_length=43),
    _std(129033, "Time & Date", "Single", 8, 3),
    _std(129038, "AIS Class A Position Report", "Fast", 28, 20),
    _std(129039, "AIS Class B Position Report", "Fast", 27, 24),
    _std(129283, "Cross Track Error", "Single", 8, 6),
    _std(129284, "Navigation Data", "Fast", 34, 15),
    _std(130306, "Wind Data", "Single", 8, 5),
    _std(130311, "Environmental Parameters", "Single", 8, 6),
    _std(130312, "Temperature", "Single", 8, 6),
    _std(130313, "Humidity", "Single", 8, 6),
    _std(130314, "Actual Pressure", "Single", 8, 5),
    _std(130316, "Temperature, Extended Range", "Single", 8, 5),
    _std(130576, "Trim Tab Status", "Single", 8, 3),
    _std(130577, "Direction Data", "Fast", 14, 10),
]

# Proprietary PGNs. Multiple manufacturers reuse the same PGN number, so each
# number maps to several candidate signatures distinguished by manufacturer_code.
_PROPRIETARY: list[PGNSignature] = [
    _prop(65298, "suzukiEngineDataA", 586),
    _prop(65280, "furunoHeave", 1855),
    _prop(65280, "hondaEngineData", 257),
    _prop(65280, "yanmarEngineDataA", 172),
    _prop(65280, "mercuryEngineData", 144),
    _prop(65280, "maretronKeelPosition", 137),
    _prop(65280, "navicoDeviceStatus", 275),
    _prop(65280, "bepMarineCzoneCircuitControl", 295),
    _prop(65280, "sleipnerThrusterCommand", 306),
]


def _build() -> dict[int, list[PGNSignature]]:
    out: dict[int, list[PGNSignature]] = {}
    for sig in (*_STANDARD, *_PROPRIETARY):
        out.setdefault(sig.pgn, []).append(sig)
    return out


PGN_SIGNATURES: dict[int, list[PGNSignature]] = _build()


def lookup_pgn(pgn: int) -> list[PGNSignature]:
    """Return all candidate signatures for ``pgn`` (empty list if unknown)."""
    return PGN_SIGNATURES.get(pgn, [])
