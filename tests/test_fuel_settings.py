"""Owner-set tank sizes, persisted; fuel remaining / endurance / range.

Regression tests (steering 10): each fails without the matching change.
"""
from __future__ import annotations

import json
import os

import pytest
from fastapi.testclient import TestClient

from protocol.domain import UNSET, EngineState, NavigationState, TankState
from protocol.registry import DecodeResult
from service.cache import LiveStateCache
from service.discovery import DiscoveryMode
from web.api import create_app
from web.fuel import apply_configured_capacity, fuel_summary
from web.settings import SettingsStore, validate_tank_capacities

KNOT_IN_MPS = 1852.0 / 3600.0


def _tank(level=50.0, capacity=None, fluid_type=0):
    return {
        "fluid_type": fluid_type, "level_percent": level, "capacity_litres": capacity,
        "quantity_remaining_litres": None if capacity is None or level is None else level / 100 * capacity,
        "updated_at": 100.0, "provenance": {"value_origin": "decoded", "derivation": None},
    }


def _engine(rate=20.0):
    return {"fuel_rate": rate, "updated_at": 101.0}


# --- applying the owner's tank size ------------------------------------------


def test_configured_capacity_fills_missing_size_and_marks_derived():
    tanks = {"0:42": _tank(level=50.0, capacity=None)}
    apply_configured_capacity(tanks, {"0:42": 200.0})
    assert tanks["0:42"]["capacity_litres"] == 200.0
    assert tanks["0:42"]["quantity_remaining_litres"] == pytest.approx(100.0)
    assert tanks["0:42"]["provenance"]["value_origin"] == "derived"
    assert "owner setting" in tanks["0:42"]["provenance"]["derivation"]


def test_configured_capacity_ignores_unknown_tank_and_keeps_others():
    tanks = {"0:42": _tank(capacity=90.0)}
    apply_configured_capacity(tanks, {"1:99": 500.0})
    assert tanks["0:42"]["capacity_litres"] == 90.0


# --- endurance and range -------------------------------------------------------


def test_endurance_and_range():
    tanks = {"0:42": _tank(50.0, 200.0), "1:42": _tank(25.0, 200.0)}  # 100 L + 50 L
    engines = {"0:23": _engine(30.0)}
    nav = {"sog": 10 * KNOT_IN_MPS}
    s = fuel_summary(tanks, engines, nav)
    assert s["remaining_litres"] == pytest.approx(150.0)
    assert s["endurance_h"] == pytest.approx(5.0)
    assert s["range_nm"] == pytest.approx(50.0)
    assert s["value_origin"] == "derived" and s["derivation"]


def test_water_tank_is_not_fuel():
    s = fuel_summary({"0:42": _tank(50.0, 200.0, fluid_type=1)}, {"0:23": _engine()}, None)
    assert s["remaining_litres"] is None


def test_unknown_tank_size_gives_no_figure_and_asks_for_it():
    tanks = {"0:42": _tank(50.0, 200.0), "1:42": _tank(50.0, None)}
    s = fuel_summary(tanks, {"0:23": _engine()}, {"sog": 5.0})
    assert s["remaining_litres"] is None and s["endurance_h"] is None  # never a partial sum
    assert s["needs_capacity"] == ["1:42"]


def test_engine_without_fuel_rate_gives_no_endurance():
    tanks = {"0:42": _tank(50.0, 200.0)}
    engines = {"0:23": _engine(20.0), "1:23": _engine(None)}  # partial burn would overstate endurance
    assert fuel_summary(tanks, engines, {"sog": 5.0})["endurance_h"] is None


def test_zero_burn_gives_no_endurance_not_a_division_error():
    s = fuel_summary({"0:42": _tank(50.0, 200.0)}, {"0:23": _engine(0.0)}, {"sog": 0.0})
    assert s["endurance_h"] is None and s["range_nm"] is None


def test_no_sog_gives_endurance_but_no_range():
    s = fuel_summary({"0:42": _tank(50.0, 200.0)}, {"0:23": _engine(20.0)}, None)
    assert s["endurance_h"] == pytest.approx(5.0) and s["range_nm"] is None


# --- persistence and validation ------------------------------------------------


def test_settings_survive_restart(tmp_path):
    path = tmp_path / "settings.json"
    SettingsStore(path).set_tank_capacities({"0:42": 189.0})
    assert SettingsStore(path).tank_capacities() == {"0:42": 189.0}


def test_corrupt_or_missing_settings_file_means_empty_not_crash(tmp_path):
    path = tmp_path / "settings.json"
    assert SettingsStore(path).tank_capacities() == {}
    path.write_text("{not json")
    assert SettingsStore(path).tank_capacities() == {}
    path.write_text(json.dumps({"tank_capacity_litres": {"0:42": -5}}))
    assert SettingsStore(path).tank_capacities() == {}


@pytest.mark.parametrize("bad", [
    {"0:42": 0}, {"0:42": -1}, {"0:42": float("nan")}, {"0:42": float("inf")},
    {"0:42": True}, {"0:42": "200"}, {"0:42": 1e9}, {"tank1": 100}, {"../x": 100}, [], "x",
])
def test_invalid_capacities_rejected(bad):
    with pytest.raises(ValueError):
        validate_tank_capacities(bad)


def test_failed_write_keeps_previous_value(tmp_path, monkeypatch):
    store = SettingsStore(tmp_path / "settings.json")
    store.set_tank_capacities({"0:42": 100.0})

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        store.set_tank_capacities({"0:42": 999.0})
    assert store.tank_capacities() == {"0:42": 100.0}


# --- end to end through the API -------------------------------------------------


def _client(tmp_path):
    cache = LiveStateCache()
    capture_dir = tmp_path / "captures"
    capture_dir.mkdir()
    return TestClient(create_app(cache, DiscoveryMode(), capture_dir)), cache, capture_dir


def test_api_tank_size_flows_into_live_and_fuel_and_persists(tmp_path):
    client, cache, capture_dir = _client(tmp_path)
    tank = TankState(tank_instance=0, fluid_type=0, level_percent=50.0, source_address=42, updated_at=1.0)
    engine = EngineState(engine_instance=0, fuel_rate=25.0, source_address=23, updated_at=1.0)
    nav = NavigationState(sog=10 * KNOT_IN_MPS, source_address=9, updated_at=1.0)
    for entity in (tank, engine, nav):
        cache.update(DecodeResult(pgn=0, source_address=entity.source_address, entity=entity))

    before = client.get("/api/v1/live").json()
    assert before["fuel"]["endurance_h"] is None and before["fuel"]["needs_capacity"] == ["0:42"]

    response = client.put("/api/v1/settings/tanks", json={"capacities": {"0:42": 200.0}})
    assert response.status_code == 200

    live = client.get("/api/v1/live").json()
    assert live["tanks"]["0:42"]["quantity_remaining_litres"] == pytest.approx(100.0)
    assert live["fuel"]["endurance_h"] == pytest.approx(4.0)
    assert live["fuel"]["range_nm"] == pytest.approx(40.0)
    assert cache.get_tank(0, 42).capacity_litres is UNSET  # the cache itself is never altered
    assert json.loads((capture_dir / "settings.json").read_text())["tank_capacity_litres"] == {"0:42": 200.0}


def test_api_rejects_bad_capacity_naming_the_parameter(tmp_path):
    client, _, _ = _client(tmp_path)
    response = client.put("/api/v1/settings/tanks", json={"capacities": {"0:42": -3}})
    assert response.status_code == 400
    assert response.json()["detail"]["parameter"] == "capacities"
    assert client.get("/api/v1/settings").json() == {"tank_capacity_litres": {}}
