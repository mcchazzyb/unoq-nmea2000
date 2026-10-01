"""Domain_Model and Provenance — generic, transport-independent entities
(design.md: Data Models; Requirements 6, 11).

No monolithic manufacturer class (no ``SuzukiData``) — Requirement 6.2. ``UNSET``
is a sentinel distinct from ``None`` and from ``0`` (Property 13): it marks a
field structurally present in a PGN whose value is absent, invalid, or reserved
on the wire (NMEA 2000's "data not available" convention). Never use ``None`` or
``0`` for that — ``0`` is a valid reading (0 RPM, 0% tank level) and ``None``
conflates "not applicable" with "not yet decoded".

This module imports NOTHING from ``/service``, ``/ble``, or ``/web``
(Requirement 21.2); the Domain_Model is consumed by transports, never the other
way round.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


class _UnsetType:
    """Singleton sentinel type. Use the module-level ``UNSET`` instance. Falsy so
    ``if value:`` is False for an unset field, but distinct from both ``None``
    and ``0`` under ``is`` and ``==`` comparison."""

    _instance: "_UnsetType | None" = None

    def __new__(cls) -> "_UnsetType":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "UNSET"

    def __bool__(self) -> bool:
        return False


UNSET = _UnsetType()

# `Optional[T]` in this module reads as "T or UNSET" per design.md.
# NodeKey is a stable node identity string: "name:<64-bit-nmea-name-hex>" when
# an ISO Address Claim is known, else "provisional:sa<N>" (Requirement 10.1).
NodeKey = str


@dataclass
class Provenance:
    """Attached to EVERY decoded value (Requirement 11). No undeterminable field
    is omitted — it is recorded as the string ``"unknown"`` instead
    (Requirement 11.2)."""

    source_address: int | str  # 'unknown' if undeterminable, never omitted
    source_pgn: int | str
    node_identity: str  # resolved identity or 'unknown'
    raw_frame_ref: str  # reference into the Raw_Capture_Log
    timestamp: float
    decoder_version: str
    quality: str  # "valid" | "stale" | "invalid" | "unknown"
    staleness_s: float  # age since decode
    vendor_specific: bool  # standard-decoded vs vendor-specific
    value_origin: str  # "decoded" | "derived"
    derivation: str | None = None  # for derived values, inputs used


@dataclass
class TankState:
    tank_instance: int
    fluid_type: Optional[int] = UNSET
    level_percent: Optional[float] = UNSET  # directly decoded; value_origin "decoded"
    capacity_litres: Optional[float] = UNSET  # directly decoded; value_origin "decoded"
    # DERIVED: (level_percent / 100) * capacity_litres when both present, else UNSET.
    quantity_remaining_litres: Optional[float] = UNSET
    source_address: int = 0
    source_pgn: int = 0
    updated_at: float = 0.0  # time values were last decoded (Req 6.5)
    provenance: Provenance | None = None


@dataclass
class EngineState:
    engine_instance: int
    rpm: Optional[float] = UNSET
    coolant_temp: Optional[float] = UNSET
    oil_pressure: Optional[float] = UNSET
    alternator_voltage: Optional[float] = UNSET
    engine_hours: Optional[float] = UNSET
    fuel_rate: Optional[float] = UNSET
    trim: Optional[float] = UNSET
    status: Optional[int] = UNSET
    source_address: int = 0
    source_pgn: int = 0
    updated_at: float = 0.0
    provenance: Provenance | None = None


@dataclass
class ElectricalState:
    battery_voltage: Optional[float] = UNSET
    charging_voltage: Optional[float] = UNSET
    current: Optional[float] = UNSET
    battery_state: Optional[int] = UNSET
    dc_source: Optional[int] = UNSET
    source_address: int = 0
    source_pgn: int = 0
    updated_at: float = 0.0
    provenance: Provenance | None = None


@dataclass
class NavigationState:
    position: Optional[tuple[float, float]] = UNSET
    cog: Optional[float] = UNSET
    sog: Optional[float] = UNSET
    heading: Optional[float] = UNSET
    depth: Optional[float] = UNSET
    water_temp: Optional[float] = UNSET
    wind: Optional[float] = UNSET
    source_address: int = 0
    source_pgn: int = 0
    updated_at: float = 0.0
    provenance: Provenance | None = None


@dataclass
class NodeRecord:
    """A discovered node. Keyed in NetworkState by a stable NodeKey (NMEA NAME),
    never by source address, which is a MUTABLE attribute here (Requirement
    10.1, 10.2, 10.11)."""

    node_key: NodeKey
    nmea_name: int | None = None  # 64-bit NMEA NAME once an Address Claim is seen
    current_source_address: int = 0  # MUTABLE — updated as the NAME moves address
    observed_pgns: set[int] = field(default_factory=set)
    first_seen: float = 0.0
    last_seen: float = 0.0
    message_rate: int = 0  # count within the trailing 60 s window
    manufacturer_code: int | None = None
    product_info: str | None = None
    device_class: int | None = None
    device_function: int | None = None
    device_type: str | None = None
    raw_identity: bytes | None = None  # retained when identity unresolved
    online: bool = True
    address_conflicts: list[int] = field(default_factory=list)  # SAs in contention


@dataclass
class NetworkState:
    nodes: dict[NodeKey, NodeRecord] = field(default_factory=dict)


@dataclass
class DeviceHealth:
    product_name: Optional[str] = UNSET
    sw_version: Optional[str] = UNSET
    fw_version: Optional[str] = UNSET
    device_id: Optional[str] = UNSET
    uptime: Optional[float] = UNSET
    can_health: object | None = None  # a protocol.receiver.CanHealth when known


@dataclass
class VesselState:
    engines: dict = field(default_factory=dict)  # dict[(instance, source_address), EngineState]
    tanks: dict = field(default_factory=dict)  # dict[(instance, source_address), TankState]
    electrical: ElectricalState | None = None
    navigation: NavigationState | None = None
    network: NetworkState = field(default_factory=NetworkState)
    health: DeviceHealth | None = None
