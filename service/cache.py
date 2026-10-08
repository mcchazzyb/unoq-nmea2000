"""Live_State_Cache: in-memory store of the latest decoded Domain_Model
values, updated by the Ingestion_Service, read by every transport
(Requirement 6.6).

Every transport (BLE_Service, Web_API, Web_UI) reads from THIS ONE cache, so
the same field values are observable everywhere for a given decoded frame
(Requirement 6.6) -- no transport may hold its own copy of decoded state.

Engines and tanks are keyed by (instance, source_address), never by instance
alone and never by source address alone (steering 07; Property 15).
Electrical and navigation have no instance per design.md's VesselState (one
of each per vessel), so each decode simply replaces the prior value.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from protocol.domain import ElectricalState, EngineState, NavigationState, TankState, VesselState
from protocol.registry import DecodeResult

#: Below this per-value age, data is still "fresh"; at or above it, a reader
#: should treat the value as stale even without an explicit halt
#: (operator-visibility aid; Requirement 12.6/12.7 drive the authoritative
#: CAPTURE_HALTED_STORAGE_FULL staleness marking in service.ingestion).
DEFAULT_STALE_AFTER_S = 10.0


@dataclass
class LiveStateCache:
    """Holds the current `VesselState` plus an explicit stale flag the
    Ingestion_Service sets during `CAPTURE_HALTED_STORAGE_FULL`
    (Requirement 12.6) -- read by `is_stale()` independent of per-value age.
    """

    vessel: VesselState = field(default_factory=VesselState)
    _halted_stale: bool = False
    _last_update_at: float = 0.0
    # Monotonic time of the last receiver.health() that succeeded. For a hardware
    # receiver that call crosses the Bridge, so this is the Bridge's liveness: it keeps
    # advancing on a quiet bus, and stops when the Bridge wedges (unlike data age).
    _receiver_ok_at: float | None = None

    def note_receiver_ok(self) -> None:
        self._receiver_ok_at = time.monotonic()

    def receiver_ok_age_s(self) -> float | None:
        """Seconds since the receiver last answered; None if it never has."""
        return None if self._receiver_ok_at is None else round(time.monotonic() - self._receiver_ok_at, 1)

    def update(self, result: DecodeResult) -> None:
        """Apply one decoded entity to the cache, keyed per the entity's own
        instance-keying rule."""
        entity = result.entity
        if entity is None:
            return
        self._last_update_at = time.time()
        self._halted_stale = False  # a successful update implies capture resumed

        if isinstance(entity, EngineState):
            key = (entity.engine_instance, entity.source_address)
            # PGN 127488 (Engine Rapid Update: rpm, trim) and PGN 127489
            # (Engine Dynamic: coolant_temp, oil_pressure, alternator_voltage,
            # fuel_rate, engine_hours, status) both target the SAME
            # EngineState key but each decode only populates its own PGN's
            # fields -- flat-replacing (as this did before) made every
            # OTHER PGN's fields flicker back to UNSET between updates, a
            # real visible bug (not just a display-timing artifact), found
            # via an actual deployed dashboard showing dials flash
            # UNSET/value/UNSET as 127488 and 127489 alternate. Merge like
            # NavigationState already does (Requirement 9.2 pattern).
            self.vessel.engines[key] = _merge_engine(self.vessel.engines.get(key), entity)
        elif isinstance(entity, TankState):
            key = (entity.tank_instance, entity.source_address)
            self.vessel.tanks[key] = entity
        elif isinstance(entity, ElectricalState):
            self.vessel.electrical = entity
        elif isinstance(entity, NavigationState):
            # 129025 and 129026 both decode into NavigationState but each
            # only populates its own PGN's fields; merge into the existing
            # record rather than overwrite fields the other PGN already set
            # (Requirement 9.2 -- fields populated only where present).
            self.vessel.navigation = _merge_navigation(self.vessel.navigation, entity)
        # Unknown entity types are simply not cached -- Discovery_Mode
        # (service/discovery.py) is the inventory for unknown/proprietary
        # traffic, not this cache.

    def mark_stale(self) -> None:
        """Called by the Ingestion_Service when entering
        CAPTURE_HALTED_STORAGE_FULL (Requirement 12.6) -- the cache's
        contents are NOT cleared, only flagged, so the last known state
        keeps being served."""
        self._halted_stale = True

    def is_stale(self) -> bool:
        return self._halted_stale

    def get_engine(self, instance: int, source_address: int) -> EngineState | None:
        return self.vessel.engines.get((instance, source_address))

    def get_tank(self, instance: int, source_address: int) -> TankState | None:
        return self.vessel.tanks.get((instance, source_address))

    def snapshot(self) -> VesselState:
        """Return the current VesselState. Callers (transports) read this
        directly rather than being handed a mutable reference they could
        corrupt; the dicts/entities themselves are still shared references,
        which is fine since this process has no concurrent writers besides
        the single Ingestion_Service."""
        return self.vessel


def _merge_engine(existing: EngineState | None, incoming: EngineState) -> EngineState:
    """PGN 127488 and 127489 both populate the same EngineState key but each
    carries a disjoint field subset -- merge rather than replace, same
    pattern as `_merge_navigation` below (Requirement 9.2's "fields
    populated only where present" applied across the two source PGNs,
    not just within one decode)."""
    if existing is None:
        return incoming
    from protocol.domain import UNSET

    for field_name in (
        "rpm", "coolant_temp", "oil_pressure", "alternator_voltage",
        "engine_hours", "fuel_rate", "trim", "status",
    ):
        incoming_value = getattr(incoming, field_name)
        if incoming_value is not UNSET:
            setattr(existing, field_name, incoming_value)
    existing.source_address = incoming.source_address
    existing.source_pgn = incoming.source_pgn
    existing.updated_at = incoming.updated_at
    existing.provenance = incoming.provenance
    return existing


def _merge_navigation(
    existing: NavigationState | None, incoming: NavigationState
) -> NavigationState:
    if existing is None:
        return incoming
    from protocol.domain import UNSET

    for field_name in ("position", "cog", "sog", "heading", "depth", "water_temp", "wind"):
        incoming_value = getattr(incoming, field_name)
        if incoming_value is not UNSET:
            setattr(existing, field_name, incoming_value)
    existing.source_address = incoming.source_address
    existing.source_pgn = incoming.source_pgn
    existing.updated_at = incoming.updated_at
    existing.provenance = incoming.provenance
    return existing
