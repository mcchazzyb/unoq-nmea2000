"""Plain-text one-line summary of the live readings, for a generic BLE scanner.

The live_* characteristics carry a compact binary envelope (ble/encoding.py) meant for a
future client app; a generic scanner (LightBlue, nRF Connect) shows those as hex. This module
produces a human-readable UTF-8 line for the `readings_text` characteristic so the owner can
read the boat's state on a phone with no custom app.

Pure functions of a VesselState (plus a stale flag). No hardware, no BLE, no I/O -- so the
whole thing is host-testable, like the rest of protocol/ consumers (steering 05).
Conventions match the dashboard: knots for SOG and wind, whole-number RPM and tank level,
fluid type by name. UNSET fields are omitted rather than shown as a guess (steering 03).
"""
from __future__ import annotations

from protocol.domain import UNSET, VesselState

MPS_TO_KNOTS = 3600.0 / 1852.0  # exact: 1 knot = 1852 m per hour (matches web/fuel.py)

# PGN 127505 tank type codes (matches web/static/index.html).
_FLUID_TYPE_NAMES = {0: "Fuel", 1: "Fresh water", 2: "Grey water", 3: "Live well", 4: "Oil", 5: "Black water"}


def _has(value) -> bool:
    return value is not UNSET and value is not None


def format_readings(vessel: VesselState, stale: bool = False) -> str:
    """One line like 'RPM 2664 | Fuel 99% | Batt 12.6V | SOG 5.2kn | Wind 12.0kn'.

    Only fields that are actually present appear. Empty when nothing is known yet, or
    'STALE | ...' / 'STALE (no data)' when the bus has gone quiet, so a scanner never shows
    an old value as if it were live.
    """
    parts: list[str] = []

    for (_inst, _sa), e in sorted(vessel.engines.items()):
        if _has(e.rpm):
            parts.append(f"RPM {round(e.rpm)}")
        if _has(e.coolant_temp):
            parts.append(f"Coolant {e.coolant_temp:.0f}C")

    for (_inst, _sa), t in sorted(vessel.tanks.items()):
        if _has(t.level_percent):
            label = _FLUID_TYPE_NAMES.get(t.fluid_type, "Tank") if _has(t.fluid_type) else "Tank"
            parts.append(f"{label} {round(t.level_percent)}%")

    el = vessel.electrical
    if el is not None and _has(el.battery_voltage):
        parts.append(f"Batt {el.battery_voltage:.1f}V")

    nav = vessel.navigation
    if nav is not None:
        if _has(nav.sog):
            parts.append(f"SOG {nav.sog * MPS_TO_KNOTS:.1f}kn")
        if _has(nav.depth):
            parts.append(f"Depth {nav.depth:.1f}m")
        if _has(nav.wind):
            parts.append(f"Wind {nav.wind * MPS_TO_KNOTS:.1f}kn")

    body = " | ".join(parts)
    if stale:
        return f"STALE | {body}" if body else "STALE (no data)"
    return body if body else "(no data yet)"


def format_readings_bytes(vessel: VesselState, stale: bool = False) -> bytes:
    """UTF-8 bytes for the GATT characteristic value."""
    return format_readings(vessel, stale).encode("utf-8")
