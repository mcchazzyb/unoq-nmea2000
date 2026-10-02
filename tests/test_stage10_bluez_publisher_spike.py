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


def test_characteristic_flags_require_encryption_when_pairing_required():
    """Real bug found via a live phone test (Stage 10.3 spike): a connected,
    UNPAIRED phone successfully read a live-data characteristic with no
    pairing prompt at all. Root cause: MockBLEPublisher.read_characteristic
    DOES raise PairingRejectedError, but nothing caught it, and critically
    nothing told BlueZ itself to refuse the read before our handler ran --
    application-level client tracking was never going to be sufficient.
    The real fix is link-layer: 'encrypt-read'/'encrypt-notify' GATT Flags
    make BlueZ enforce pairing/encryption BEFORE ReadValue is ever called.
    This test pins that every live-data characteristic gets the encrypted
    flag when require_pairing=True, and that schema_version alone stays
    plain 'read' (Requirement 13.1: a client must be able to identify the
    service before pairing)."""
    import ble.bluez_publisher as m
    from ble.publisher import GATT_SCHEMA, MockBLEPublisher

    mock = MockBLEPublisher(require_pairing=True)
    chars_by_name = {
        c.name: m._Characteristic(i, "/svc", c, mock, require_pairing=True)
        for i, c in enumerate(GATT_SCHEMA)
    }

    assert "encrypt-read" in chars_by_name["connection_health"].flags()
    assert "encrypt-notify" in chars_by_name["live_engine"].flags()
    # schema_version is the one exempt characteristic -- plain read, no
    # encryption required, so a client can identify the service pre-pairing.
    assert chars_by_name["schema_version"].flags() == ["read"]


def test_characteristic_flags_stay_plain_when_pairing_not_required():
    """require_pairing=False (Requirement 28.2's explicit open-access
    opt-in) must NOT add encrypt-read/encrypt-notify -- those flags force
    BlueZ-level pairing regardless of the publisher's own require_pairing
    setting, so an operator who explicitly chose open access must see it
    actually take effect over the air, not just in application logic."""
    import ble.bluez_publisher as m
    from ble.publisher import GATT_SCHEMA, MockBLEPublisher

    mock = MockBLEPublisher(require_pairing=False)
    live_engine_char = next(c for c in GATT_SCHEMA if c.name == "live_engine")
    characteristic = m._Characteristic(0, "/svc", live_engine_char, mock, require_pairing=False)

    assert characteristic.flags() == ["notify"]


def test_agent_capability_routes_headless_confirmation_to_our_agent():
    """Real root-level btmon trace on the UNO Q showed the exact failure:
    NoInputNoOutput caused BlueZ to emit User Confirmation Negative Reply
    without ever calling this agent's RequestConfirmation handler, so every
    protected read failed Insufficient Encryption. DisplayYesNo is required
    to route the confirmation to the agent; the handler auto-accepts it,
    preserving the required no-keypad/no-board-button OBD-style UX. This is
    encrypted/bonded but not human-verified MITM, documented in the module."""
    import ble.bluez_publisher as m

    assert m._AGENT_CAPABILITY == "DisplayYesNo"
