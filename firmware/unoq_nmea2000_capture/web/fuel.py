"""Fuel remaining, endurance and range for the /live payload.

Pure functions over the plain JSON-shaped dicts the Web_API already builds
(no cache or protocol imports), so they are trivial to test. Everything here
is a DERIVED value and says so in `derivation` (steering 06).

Fail-safe rules: a figure is None (never a partial sum) unless EVERY input it
needs is known. Summing only the tanks we can read would understate fuel, and
summing only the engines that report burn would overstate endurance.
"""
from __future__ import annotations

FUEL_TANK_TYPE = 0  # PGN 127505 tank type 0 = fuel
MPS_TO_KNOTS = 3600.0 / 1852.0
TANK_SIZE_SOURCE = "configured_capacity_litres (owner setting)"
DERIVATION = (
    "remaining = sum(fuel tanks level_percent/100 * capacity); "
    "endurance = remaining / sum(engine fuel_rate); range = endurance * sog"
)


def apply_configured_capacity(tanks: dict, capacities: dict) -> None:
    """Owner-set tank size wins over whatever the sender reports (senders often
    send N/A or a factory default). Mutates the response dicts only, never the
    cache. Litres remaining becomes a derived value and says what it used."""
    for key, capacity in capacities.items():
        tank = tanks.get(key)
        if tank is None:
            continue
        tank["capacity_litres"] = capacity
        level = tank.get("level_percent")
        tank["quantity_remaining_litres"] = None if level is None else level / 100.0 * capacity
        provenance = tank.get("provenance")
        if provenance is not None and level is not None:
            provenance["value_origin"] = "derived"
            provenance["derivation"] = f"(level_percent / 100) * {TANK_SIZE_SOURCE}"


def fuel_summary(tanks: dict, engines: dict, navigation: dict | None) -> dict:
    fuel_tanks = {k: t for k, t in tanks.items() if t.get("fluid_type") == FUEL_TANK_TYPE}
    needs_capacity = sorted(
        k for k, t in fuel_tanks.items()
        if t.get("level_percent") is not None and t.get("capacity_litres") is None
    )
    quantities = [t.get("quantity_remaining_litres") for t in fuel_tanks.values()]
    remaining = sum(quantities) if fuel_tanks and None not in quantities else None

    rates = [e.get("fuel_rate") for e in engines.values()]
    burn = sum(rates) if engines and None not in rates else None

    endurance = remaining / burn if remaining is not None and burn else None  # burn 0/None: no figure
    sog = (navigation or {}).get("sog")
    range_nm = endurance * sog * MPS_TO_KNOTS if endurance is not None and sog is not None else None

    stamps = [x.get("updated_at") for x in (*fuel_tanks.values(), *engines.values())]
    stamps = [s for s in stamps if s is not None]
    return {
        "remaining_litres": remaining,
        "burn_lph": burn,
        "endurance_h": endurance,
        "range_nm": range_nm,
        "needs_capacity": needs_capacity,
        "oldest_input_updated_at": min(stamps) if stamps else None,
        "value_origin": "derived",
        "derivation": DERIVATION,
    }
