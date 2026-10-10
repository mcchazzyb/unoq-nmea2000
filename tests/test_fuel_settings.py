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


# --- tank size follows the sender's stable NMEA NAME, not its source address --
#
# Regression (surgical multi-boat fix, 2026-10-10): a tank size must stay
# applied when the sender's source address changes (Address Claim), and a new
# save must persist against the stable NAME so it is portable. Fails before the
# NAME-resolution change, passes after.
from service.discovery import DiscoveryMode as _DiscoveryMode  # noqa: E402
from protocol.decoders.identity import decode_iso_address_claim  # noqa: E402
from protocol.receiver import CanFrame  # noqa: E402
from protocol.registry import DecodeResult as _DR  # noqa: E402
from web.fuel import name_tank_key, to_stable_keys  # noqa: E402


def _claim_name(discovery, source_address, nmea_name):
    """Feed one ISO Address Claim so discovery learns source_address -> NAME."""
    data = nmea_name.to_bytes(8, "little")
    frame = CanFrame(
        can_id=0, priority=6, pgn=60928, source_address=source_address,
        dest_address=None, dlc=8, payload=data, capture_time=1.0,
    )
    decoded = decode_iso_address_claim(source_address=source_address, data=data)
    discovery.observe(frame, _DR(pgn=60928, source_address=source_address, entity=decoded.entity), now=1.0)


def test_name_keyed_size_applies_regardless_of_source_address():
    name = 0xC0FFEE1234567890
    tanks = {"0:7": _tank(level=50.0, capacity=None)}  # sender now at SA 7
    caps = {name_tank_key(name, 0): 200.0}             # size saved against the NAME
    apply_configured_capacity(tanks, caps, sa_to_name={7: name})
    assert tanks["0:7"]["capacity_litres"] == 200.0
    assert tanks["0:7"]["quantity_remaining_litres"] == pytest.approx(100.0)


def test_save_rewrites_sa_key_to_stable_name_key():
    name = 0xC0FFEE1234567890
    rewritten = to_stable_keys({"0:4": 90.0}, sa_to_name={4: name})
    assert rewritten == {name_tank_key(name, 0): 90.0}
    # an SA with no known NAME passes through unchanged (nothing to key against yet)
    assert to_stable_keys({"0:9": 90.0}, sa_to_name={4: name}) == {"0:9": 90.0}


def test_instance_only_size_set_offline_binds_when_tank_appears():
    """A size entered with NO bus connected (key 'inst:0') must apply to the
    fuel tank once a sender presents instance 0, whatever its address."""
    tanks = {"0:4": _tank(level=50.0, capacity=None)}
    apply_configured_capacity(tanks, {"inst:0": 150.0}, sa_to_name=None)
    assert tanks["0:4"]["capacity_litres"] == 150.0
    assert tanks["0:4"]["quantity_remaining_litres"] == pytest.approx(75.0)


def test_more_specific_key_wins_over_instance_only():
    """If both an instance-only and an SA/NAME key exist, the specific one wins."""
    tanks = {"0:4": _tank(level=50.0, capacity=None)}
    apply_configured_capacity(tanks, {"inst:0": 100.0, "0:4": 200.0}, sa_to_name=None)
    assert tanks["0:4"]["capacity_litres"] == 200.0


def test_instance_only_key_accepted_and_persists_offline(tmp_path):
    """Owner sets a tank size with NO bus connected: the API accepts an
    'inst:<N>' key, saves it, and it survives a restart (offline-first)."""
    client, cache, capture_dir = _client(tmp_path)
    assert client.get("/api/v1/live").json()["tanks"] == {}  # nothing on the bus
    r = client.put("/api/v1/settings/tanks", json={"capacities": {"inst:0": 90.0}})
    assert r.status_code == 200
    assert client.get("/api/v1/settings").json()["tank_capacity_litres"] == {"inst:0": 90.0}
    client2 = TestClient(create_app(LiveStateCache(), DiscoveryMode(), capture_dir))
    assert client2.get("/api/v1/settings").json()["tank_capacity_litres"] == {"inst:0": 90.0}


def test_api_tank_size_survives_source_address_change(tmp_path):
    """End to end: set a size while the tank is at SA 4, then the SAME sender
    reappears at SA 11 (Address Claim). The size must still apply."""
    name = 0xA1B2C3D4E5F60718
    cache = LiveStateCache()
    discovery = _DiscoveryMode()
    capture_dir = tmp_path / "captures"
    capture_dir.mkdir()
    client = TestClient(create_app(cache, discovery, capture_dir))

    # Sender known at SA 4; a fuel tank (instance 0) decoded there.
    _claim_name(discovery, 4, name)
    cache.update(DecodeResult(pgn=0, source_address=4,
                              entity=TankState(tank_instance=0, fluid_type=0, level_percent=50.0,
                                               source_address=4, updated_at=1.0)))
    assert client.put("/api/v1/settings/tanks", json={"capacities": {"0:4": 90.0}}).status_code == 200
    # Saved against the stable NAME, not the SA.
    saved = client.get("/api/v1/settings").json()["tank_capacity_litres"]
    assert saved == {name_tank_key(name, 0): 90.0}

    # Same sender now claims SA 11; the tank entity moves with it.
    _claim_name(discovery, 11, name)
    cache.update(DecodeResult(pgn=0, source_address=11,
                              entity=TankState(tank_instance=0, fluid_type=0, level_percent=50.0,
                                               source_address=11, updated_at=2.0)))
    live = client.get("/api/v1/live").json()
    assert live["tanks"]["0:11"]["capacity_litres"] == 90.0
    assert live["tanks"]["0:11"]["quantity_remaining_litres"] == pytest.approx(45.0)


# --- review findings (cross-model review gate, 2026-10-10) --------------------
#
# Each fails before the fix: an instance-only size must never be handed to
# several tanks (that overstates fuel), and the Setup form must show one row
# per tank and collapse old keys when saved.


def test_instance_only_size_not_applied_when_two_fuel_tanks_share_an_instance():
    tanks = {"0:4": _tank(level=50.0, capacity=None), "0:5": _tank(level=50.0, capacity=None)}
    apply_configured_capacity(tanks, {"inst:0": 100.0}, sa_to_name=None)
    assert tanks["0:4"]["capacity_litres"] is None and tanks["0:5"]["capacity_litres"] is None
    s = fuel_summary(tanks, {"0:23": _engine(10.0)}, None)
    assert s["remaining_litres"] is None and s["needs_capacity"] == ["0:4", "0:5"]  # asks, never guesses


def test_instance_only_size_applies_to_fuel_not_water_tank_of_same_instance():
    tanks = {"0:4": _tank(level=50.0, capacity=None, fluid_type=0),
             "0:6": _tank(level=50.0, capacity=None, fluid_type=1)}
    apply_configured_capacity(tanks, {"inst:0": 90.0}, sa_to_name=None)
    assert tanks["0:4"]["capacity_litres"] == 90.0
    assert tanks["0:6"]["capacity_litres"] is None


def test_each_tank_reports_the_key_to_save_under_and_where_its_size_came_from():
    name = 0xC0FFEE1234567890
    tanks = {"0:4": _tank(level=50.0, capacity=None)}
    apply_configured_capacity(tanks, {"inst:0": 90.0}, sa_to_name={4: name})
    assert tanks["0:4"]["setting_key"] == name_tank_key(name, 0)  # stable key for the next save
    assert tanks["0:4"]["setting_source_key"] == "inst:0"         # the old key the form must collapse
    unsized = {"0:9": _tank(level=50.0, capacity=None)}
    apply_configured_capacity(unsized, {}, sa_to_name=None)
    assert unsized["0:9"]["setting_key"] == "0:9" and "setting_source_key" not in unsized["0:9"]


def test_setup_form_uses_server_keys_and_collapses_stale_aliases():
    html = open("web/static/index.html", encoding="utf-8").read()
    assert "tank.setting_key" in html and "tank.setting_source_key" in html
    assert "delete capacities[stale]" in html          # saving drops the old alias
    assert "consumed.has(key)" in html                 # a folded-in key is not shown as a second row


def test_offline_legacy_key_is_not_rebound_to_a_new_sender_on_save():
    """Review finding: a size saved for a tank that is offline ('0:4') must not be
    rewritten onto whichever different sender now holds address 4."""
    other = 0x1122334455667788
    caps = {"0:4": 90.0, "1:7": 50.0}
    out = to_stable_keys(caps, sa_to_name={4: other, 7: other}, live_keys={"1:7"})
    assert out["0:4"] == 90.0                      # offline: left exactly as saved
    assert out == {"0:4": 90.0, name_tank_key(other, 1): 50.0}  # live tank 1:7 is rewritten
