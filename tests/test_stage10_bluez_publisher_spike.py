"""Stage 10.3 spike -- real BLE host-side JSON-to-Domain_Model reconstruction.

`ble/bluez_publisher.py` runs as a separate host-OS process (outside the App
Lab container; see that module's own docstring for why) and rebuilds real
protocol.domain dataclass instances from GET /api/v1/live's JSON so
ble.encoding.encode_envelope and MockBLEPublisher's proven notify/pairing
logic are reused unchanged. This file tests ONLY that reconstruction layer
-- the pure functions with no `gi`/D-Bus/BlueZ dependency -- so it stays
host-testable with no real Bluetooth hardware, matching Requirement 17.9/
17.10's host-testable-MVP discipline. `BlueZPublisher` itself (the D-Bus
registration class) needs `gi`/a real system bus and is exercised only on
the board, per this module's own docstring.
"""
from __future__ import annotations

from protocol.domain import UNSET
from ble.bluez_publisher import (
    can_health_from_health_json,
    vessel_state_from_live_json,
)


def test_vessel_state_from_live_json_rebuilds_engine_with_unset_distinction():
    """Requirement 11: a None in the REST JSON (web/api.py's own UNSET -> None
    mapping) must round-trip back to UNSET, not None or 0, so
    ble.encoding.encode_entity's own UNSET check behaves identically whether
    fed from a live LiveStateCache or from this JSON-polling boundary."""
    live = {
        "engines": {
            "0:35": {
                "rpm": 3800.0,
                "coolant_temp": None,  # UNSET on the wire -> None in REST JSON
                "oil_pressure": 310.0,
                "alternator_voltage": 13.8,
                "engine_hours": None,
                "fuel_rate": 18.5,
                "trim": -5.0,
                "status": None,
                "provenance": {
                    "source_address": 35,
                    "source_pgn": 127488,
                    "node_identity": "unknown",
                    "quality": "valid",
                    "staleness_s": 0.0,
                    "value_origin": "decoded",
                    "vendor_specific": False,
                    "derivation": None,
                },
            }
        },
        "tanks": {},
        "electrical": None,
        "navigation": None,
        "stale": False,
    }

    vessel = vessel_state_from_live_json(live)

    engine = vessel.engines[(0, 35)]
    assert engine.rpm == 3800.0
    assert engine.coolant_temp is UNSET
    assert engine.engine_hours is UNSET
    assert engine.oil_pressure == 310.0
    assert engine.source_address == 35
    assert engine.provenance is not None
    assert engine.provenance.quality == "valid"
    assert engine.provenance.source_pgn == 127488


def test_vessel_state_from_live_json_rebuilds_multiple_entity_kinds():
    """Requirement 27.2: no fixed single-instance assumption -- engines,
    tanks, electrical, and navigation all reconstruct from one /api/v1/live
    payload, matching MultiNodeScenario's own multi-node coverage."""
    live = {
        "engines": {"0:35": {"rpm": 4000.0, "provenance": None}},
        "tanks": {"0:42": {"level_percent": 54.0, "capacity_litres": 189.0, "provenance": None}},
        "electrical": {"battery_voltage": 12.6, "current": -4.2, "provenance": None},
        "navigation": {"sog": 22.4, "cog": 95.0, "provenance": None},
        "stale": False,
    }

    vessel = vessel_state_from_live_json(live)

    assert vessel.engines[(0, 35)].rpm == 4000.0
    assert vessel.tanks[(0, 42)].level_percent == 54.0
    assert vessel.electrical is not None and vessel.electrical.battery_voltage == 12.6
    assert vessel.navigation is not None and vessel.navigation.sog == 22.4


def test_vessel_state_from_live_json_handles_empty_payload():
    """An empty cache (board just started, no CAN traffic yet) must not
    raise -- mirrors the real bench-isolated hardware state this session
    verified live (`can_connected: true`, empty engines/tanks)."""
    live = {"engines": {}, "tanks": {}, "electrical": None, "navigation": None, "stale": True}

    vessel = vessel_state_from_live_json(live)

    assert vessel.engines == {}
    assert vessel.tanks == {}
    assert vessel.electrical is None
    assert vessel.navigation is None


def test_can_health_from_health_json_maps_connected_flag():
    health_connected = can_health_from_health_json({"can_connected": True})
    health_disconnected = can_health_from_health_json({"can_connected": False})

    assert health_connected.connected is True
    assert health_disconnected.connected is False
    # Requirement 1.5/24.4: termination is never reported on by this mapping,
    # matching the MVP's drop-node-only guarantee.
    assert health_connected.termination_on is False
