"""Fuel remaining, endurance and range for the /live payload.

Pure functions over the plain JSON-shaped dicts the Web_API already builds
(no cache or protocol imports), so they are trivial to test. Everything here
is a DERIVED value and says so in `derivation` (steering 06).

Fail-safe rules: a figure is None (never a partial sum) unless EVERY input it
needs is known. Summing only the tanks we can read would understate fuel, and
summing only the engines that report burn would overstate endurance.
"""
from __future__ import annotations

from collections import Counter

FUEL_TANK_TYPE = 0  # PGN 127505 tank type 0 = fuel
MPS_TO_KNOTS = 3600.0 / 1852.0
TANK_SIZE_SOURCE = "configured_capacity_litres (owner setting)"
DERIVATION = (
    "remaining = sum(fuel tanks level_percent/100 * capacity); "
    "endurance = remaining / sum(engine fuel_rate); range = endurance * sog"
)


def name_tank_key(nmea_name: int, instance: int) -> str:
    """Stable tank-settings key from the sender's 64-bit NMEA NAME + instance."""
    return f"name:{nmea_name:016x}:{instance}"


def to_stable_keys(capacities: dict, sa_to_name: dict | None, live_keys: set | None = None) -> dict:
    """Rewrite legacy '<instance>:<sa>' keys to stable 'name:<hex>:<instance>'
    keys when the sender's NAME is currently known. NAME keys and unresolvable
    SA keys pass through unchanged. Pure; the caller persists the result.

    `live_keys` (the '<instance>:<sa>' keys of tanks on the bus right now): when
    given, only those legacy keys are rewritten. A legacy key with no live tank
    is left alone, so a size saved for a tank that is offline is never re-bound
    to whatever different sender has since claimed that address."""
    out: dict = {}
    for key, cap in capacities.items():
        new_key = key
        if (
            sa_to_name
            and isinstance(key, str)
            and key.count(":") == 1
            and not key.startswith("name:")
            and not key.startswith("inst:")
            and (live_keys is None or key in live_keys)
        ):
            instance_str, sa_str = key.split(":")
            try:
                name = sa_to_name.get(int(sa_str))
            except (ValueError, TypeError):
                name = None
            if name is not None:
                new_key = name_tank_key(name, int(instance_str))
        out[new_key] = cap
    return out


def setting_key(live_key: str, sa_to_name: dict | None) -> str:
    """The key the UI should save a live tank's size under: the stable NAME key
    when the sender's NAME is known, else the live '<instance>:<sa>' key."""
    try:
        instance_str, sa_str = live_key.split(":")
        name = (sa_to_name or {}).get(_safe_int(sa_str))
        if name is not None:
            return name_tank_key(name, int(instance_str))
    except (ValueError, TypeError):
        pass
    return live_key


def _resolve_capacity(key: str, capacities: dict, sa_to_name: dict | None, inst_ok: bool):
    """Find the configured capacity for a live tank keyed '<instance>:<sa>'.

    Returns (litres, stored_key) or (None, None). Match order, most specific first:
      1. stable NAME key  'name:<hex>:<instance>'  (follows the physical sender
         across source-address changes and boats)
      2. legacy SA key    '<instance>:<sa>'        (saved before the NAME was known)
      3. instance-only    'inst:<instance>'        (entered offline, before any
         bus connection). Only when `inst_ok`: exactly ONE fuel tank presents
         that instance. With two (different addresses) it would give both the
         same size and overstate fuel, so neither gets it.
    """
    try:
        instance_str, _sa_str = key.split(":")
    except ValueError:
        return (capacities[key], key) if key in capacities else (None, None)
    nkey = setting_key(key, sa_to_name)
    if nkey != key and nkey in capacities:
        return capacities[nkey], nkey
    if key in capacities:
        return capacities[key], key
    ikey = f"inst:{instance_str}"
    if inst_ok and ikey in capacities:
        return capacities[ikey], ikey
    return None, None


def _safe_int(s: str) -> int | None:
    try:
        return int(s)
    except (ValueError, TypeError):
        return None


def apply_configured_capacity(tanks: dict, capacities: dict, sa_to_name: dict | None = None) -> None:
    """Owner-set tank size wins over whatever the sender reports (senders often
    send N/A or a factory default). Mutates the response dicts only, never the
    cache. Litres remaining becomes a derived value and says what it used.

    `sa_to_name` maps a current source address to the sender's 64-bit NMEA
    NAME, so a size saved against the stable NAME is matched even after the
    sender's source address changes (steering 08).

    Every tank also gets `setting_key` (the key the UI should save under) and,
    when a stored size matched, `setting_source_key` (the stored key it came
    from), so the Setup form shows one row per tank and can collapse old keys."""
    fuel_per_instance = Counter(
        k.split(":")[0] for k, t in tanks.items() if t.get("fluid_type") == FUEL_TANK_TYPE
    )
    for key, tank in tanks.items():
        tank["setting_key"] = setting_key(key, sa_to_name)
        inst_ok = tank.get("fluid_type") == FUEL_TANK_TYPE and fuel_per_instance[key.split(":")[0]] == 1
        capacity, source = _resolve_capacity(key, capacities, sa_to_name, inst_ok)
        if capacity is None:
            continue
        tank["setting_source_key"] = source
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
