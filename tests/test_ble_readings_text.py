"""Plain-text BLE readings characteristic (owner request 2026-10-08, for LightBlue).

Regression (steering 10). The formatter is pure, so it is tested directly; the characteristic's
wiring into the schema and the mock publisher is tested through MockBLEPublisher.
"""
from __future__ import annotations

import pytest

from ble.publisher import GATT_SCHEMA, CharacteristicProperty, MockBLEPublisher, assert_no_writable_characteristic
from ble.readings_text import format_readings
from protocol.domain import UNSET, ElectricalState, EngineState, NavigationState, TankState, VesselState


def _vessel():
    v = VesselState()
    v.engines[(0, 23)] = EngineState(engine_instance=0, rpm=2663.5, source_address=23)
    v.tanks[(0, 42)] = TankState(tank_instance=0, fluid_type=0, level_percent=98.9, source_address=42)
    v.electrical = ElectricalState(battery_voltage=12.63, source_address=43)
    v.navigation = NavigationState(sog=2.7, wind=6.2, source_address=50)
    return v


def test_whole_numbers_knots_and_fluid_name():
    s = format_readings(_vessel())
    assert "RPM 2664" in s                 # whole number, rounded
    assert "Fuel 99%" in s                 # fluid type 0 named, level rounded
    assert "Batt 12.6V" in s
    assert "SOG 5.2kn" in s                 # 2.7 m/s -> 5.2 kn
    assert "Wind 12.1kn" in s


def test_unset_fields_are_omitted_not_guessed():
    v = VesselState()
    v.engines[(0, 23)] = EngineState(engine_instance=0, rpm=1200.0, source_address=23)  # only rpm
    s = format_readings(v)
    assert s == "RPM 1200"
    assert "Coolant" not in s and "Fuel" not in s and "Batt" not in s


def test_unknown_fluid_type_falls_back_to_tank():
    v = VesselState()
    v.tanks[(0, 9)] = TankState(tank_instance=0, fluid_type=7, level_percent=50.0, source_address=9)
    assert "Tank 50%" in format_readings(v)


def test_empty_and_stale():
    assert format_readings(VesselState()) == "(no data yet)"
    assert format_readings(VesselState(), stale=True) == "STALE (no data)"
    assert format_readings(_vessel(), stale=True).startswith("STALE | RPM 2664")


def test_schema_has_a_read_notify_text_characteristic_and_no_writable_one():
    chars = {c.name: c for c in GATT_SCHEMA}
    assert chars["readings_text"].properties is CharacteristicProperty.READ_NOTIFY
    assert_no_writable_characteristic()  # the new one must not break the receive-only structure


def test_mock_publisher_serves_the_text_value():
    pub = MockBLEPublisher(require_pairing=False)
    pub.update_can_health(_health(connected=True))
    pub.publish(_vessel(), now=1000.0)
    val = pub.read_characteristic("readings_text")
    assert val is not None and b"RPM 2664" in val
    assert pub.notified_at("readings_text") == 1000.0


def test_mock_publisher_marks_text_stale_when_can_is_quiet():
    pub = MockBLEPublisher(require_pairing=False)
    pub.update_can_health(_health(connected=True), now=0.0)
    pub.publish(_vessel(), now=100.0)   # >3 s since the last frame -> stale
    assert pub.read_characteristic("readings_text").startswith(b"STALE")


def _health(connected: bool):
    from protocol.receiver import CanHealth

    return CanHealth(connected=connected, fps=0.0, dropped_frames=0, error_count=0,
                     controller_state="ListenOnly", termination_on=False)
