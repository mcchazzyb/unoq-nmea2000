"""BLE_Publisher abstraction and in-memory mock (Requirement 13.1-13.10,
18.4, 28.1-28.4).

`BLEPublisher` consumes ONLY Domain_Model types (protocol.domain) -- it
references neither Web_UI nor Web_API models (Requirement 13.9), matching the
same transport-independence rule every consumer of Live_State_Cache follows.

`MockBLEPublisher` serves the full GATT schema in memory with no Bluetooth
radio (Requirement 13.10) -- this IS the host-testable implementation for
this MVP; a real BlueZ-backed publisher is Stage 10 hardware work and is not
built here (steering: BlueZ is the one OS-specific dependency, confined to
`ble/`, never built ahead of its own stage).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

from ble.encoding import SCHEMA_VERSION, encode_envelope
from protocol.domain import ElectricalState, EngineState, NavigationState, TankState, VesselState
from protocol.receiver import CanHealth

#: Below this per-characteristic gap, a value updates via GATT read rather
#: than notification (Requirement 13.4). Above it, notifications are used,
#: capped at the rate below (Requirement 13.3).
NOTIFY_THRESHOLD_HZ = 1.0
#: Maximum notification rate per characteristic (Requirement 13.3; Property 22).
MIN_NOTIFY_INTERVAL_S = 0.25
#: CAN silence duration before live vessel data is marked stale (Requirement 13.8).
STALE_AFTER_NO_FRAME_S = 3.0


class CharacteristicProperty(str, Enum):
    """GATT characteristic access modes this schema ever uses. There is
    deliberately NO WRITE/WRITE_NO_RESPONSE member -- the type itself cannot
    express a writable characteristic (Requirement 13.2; Property 23), the
    same structural-enforcement pattern as ICanReceiver's receive-only
    contract (steering 01)."""

    READ = "read"
    NOTIFY = "notify"
    READ_NOTIFY = "read_notify"


@dataclass(frozen=True)
class Characteristic:
    uuid: str
    name: str
    properties: CharacteristicProperty


# GATT schema (Requirement 13.1, 13.6) -- UUIDs are documented placeholders
# per design.md, 16-bit shorts under a base 128-bit vendor UUID.
GATT_SCHEMA: tuple[Characteristic, ...] = (
    Characteristic("A2D1", "schema_version", CharacteristicProperty.READ),
    Characteristic("A2D2", "device_info", CharacteristicProperty.READ),
    Characteristic("A2D3", "connection_health", CharacteristicProperty.READ_NOTIFY),
    Characteristic("A2D4", "live_engine", CharacteristicProperty.NOTIFY),
    Characteristic("A2D5", "live_fuel", CharacteristicProperty.NOTIFY),
    Characteristic("A2D6", "live_electrical", CharacteristicProperty.NOTIFY),
    Characteristic("A2D7", "live_navigation", CharacteristicProperty.NOTIFY),
    Characteristic("A2D8", "alerts_status", CharacteristicProperty.READ_NOTIFY),
    Characteristic("A2D9", "diagnostics", CharacteristicProperty.READ),
)


def assert_no_writable_characteristic() -> None:
    """Structural check backing Property 23: every characteristic in
    GATT_SCHEMA is read and/or notify ONLY. Raises if that is ever violated,
    so a future edit introducing a writable characteristic fails loudly."""
    for c in GATT_SCHEMA:
        if c.properties not in (
            CharacteristicProperty.READ,
            CharacteristicProperty.NOTIFY,
            CharacteristicProperty.READ_NOTIFY,
        ):
            raise AssertionError(f"characteristic {c.name} has a non-read/notify property")


class UnknownCharacteristicError(KeyError):
    """Raised when a client requests a characteristic name that is not part
    of GATT_SCHEMA (Requirement 20.2) -- a named rejection, not an unhandled
    KeyError, so a hostile/malformed request is distinguishable from a bug
    and the publisher keeps serving every other client afterward."""


class PairingRejectedError(PermissionError):
    """Raised when an unpaired client attempts to read/subscribe while
    authenticated pairing is required (Requirement 28.3)."""


class BLEPublisher(Protocol):
    """Structural contract for anything that serves the BLE GATT schema.
    Consumes ONLY Domain_Model types (Requirement 13.9) -- no Web_UI/Web_API
    import anywhere in an implementation of this Protocol."""

    def publish(self, vessel: VesselState) -> None:
        """Push the current VesselState snapshot; the publisher decides
        internally which characteristics to notify vs. leave for the next
        read, per the notify/read split (Requirement 13.3, 13.4)."""
        ...

    def schema_version(self) -> int:
        ...


@dataclass
class _CharacteristicState:
    last_notified_at: float = 0.0
    last_value: bytes | None = None


@dataclass
class MockBLEPublisher:
    """In-memory GATT server requiring no Bluetooth radio (Requirement
    13.10) -- serves the full schema for host-testable verification.

    Pairing defaults to REQUIRED (Requirement 28.2's default-secure
    posture); `require_pairing=False` is the explicit opt-in for open access
    the requirement allows.
    """

    require_pairing: bool = True
    paired_client_ids: set[str] = field(default_factory=set)
    device_info: dict = field(default_factory=dict)
    _can_health: CanHealth | None = None
    _last_frame_at: float = 0.0
    _states: dict[str, _CharacteristicState] = field(default_factory=dict)
    _discovered_node_count: int = 0
    _observed_pgn_count: int = 0
    _unknown_pgn_count: int = 0
    _capture_state: str = "capturing"

    def __post_init__(self) -> None:
        self._states = {c.name: _CharacteristicState() for c in GATT_SCHEMA}

    def schema_version(self) -> int:
        return SCHEMA_VERSION

    # --- Pairing / access control (Requirement 28.2, 28.3) -----------------

    def pair(self, client_id: str) -> None:
        self.paired_client_ids.add(client_id)

    def _check_access(self, client_id: str | None) -> None:
        if not self.require_pairing:
            return  # explicit opt-in open access (Requirement 28.2)
        if client_id is None or client_id not in self.paired_client_ids:
            raise PairingRejectedError(
                "authenticated pairing is required; client is not paired"
            )

    # --- CAN health / staleness (Requirement 13.8) --------------------------

    def update_can_health(self, health: CanHealth, now: float | None = None) -> None:
        self._can_health = health
        self._last_frame_at = now if now is not None else time.time()

    def is_stale(self, now: float | None = None) -> bool:
        """Live vessel data is stale when CAN is disconnected OR no frame
        has arrived for >= 3 seconds (Requirement 13.8)."""
        now = now if now is not None else time.time()
        if self._can_health is None or not self._can_health.connected:
            return True
        return (now - self._last_frame_at) >= STALE_AFTER_NO_FRAME_S

    # --- Diagnostics / connection-health reads (Requirement 13.1) ----------

    def update_diagnostics(
        self, discovered_node_count: int, observed_pgn_count: int, unknown_pgn_count: int, capture_state: str
    ) -> None:
        self._discovered_node_count = discovered_node_count
        self._observed_pgn_count = observed_pgn_count
        self._unknown_pgn_count = unknown_pgn_count
        self._capture_state = capture_state

    def read_connection_health(self, client_id: str | None = None) -> dict:
        self._check_access(client_id)
        health = self._can_health
        return {
            "can_connected": health.connected if health else False,
            "fps": health.fps if health else 0.0,
            "dropped_frames": health.dropped_frames if health else 0,
            "error_count": health.error_count if health else 0,
            "discovered_node_count": self._discovered_node_count,
        }

    def read_diagnostics(self, client_id: str | None = None) -> dict:
        self._check_access(client_id)
        return {
            "observed_pgn_count": self._observed_pgn_count,
            "unknown_pgn_count": self._unknown_pgn_count,
            "capture_state": self._capture_state,
        }

    def read_schema_version(self, client_id: str | None = None) -> int:
        # Schema version is readable WITHOUT pairing -- a client needs it to
        # even know how to speak to this service before pairing.
        return SCHEMA_VERSION

    # --- publish(): notify-vs-read split (Requirement 13.3, 13.4) ----------

    def publish(self, vessel: VesselState, now: float | None = None) -> None:
        """Encode and (rate-limited) notify each live-data characteristic.
        Values are always ENCODED on every call -- `read_*` methods below
        return the latest encoded value even between notifications
        (Requirement 13.7: reconnecting client gets current values on next
        read/notify with no reconfiguration)."""
        now = now if now is not None else time.time()

        groups: dict[str, list[object]] = {
            "live_engine": list(vessel.engines.values()),
            "live_fuel": list(vessel.tanks.values()),
            "live_electrical": [vessel.electrical] if vessel.electrical else [],
            "live_navigation": [vessel.navigation] if vessel.navigation else [],
        }
        for characteristic_name, entities in groups.items():
            if not entities:
                continue
            encoded = encode_envelope(entities)
            state = self._states[characteristic_name]
            state.last_value = encoded
            # Rate-limit to at most one notification per 250ms per
            # characteristic (Property 22) regardless of how often
            # publish() itself is called.
            if (now - state.last_notified_at) >= MIN_NOTIFY_INTERVAL_S:
                state.last_notified_at = now

    def read_characteristic(self, name: str, client_id: str | None = None) -> bytes | None:
        """Vessel-data characteristic reads/notification values require
        pairing when `require_pairing` is set (Requirement 28.3); the
        schema-version/device-info reads above are exempt by design (a
        client must be able to identify the service before pairing).

        A `name` outside GATT_SCHEMA (a hostile or malformed request) is
        rejected by name via `UnknownCharacteristicError` rather than an
        unhandled `KeyError` (Requirement 20.2) -- the caller gets a named
        reason, and this client's bad request has no effect on any other
        client's connection."""
        if name not in self._states:
            raise UnknownCharacteristicError(f"no such characteristic: {name!r}")
        self._check_access(client_id)
        return self._states[name].last_value

    def notified_at(self, name: str) -> float:
        return self._states[name].last_notified_at
