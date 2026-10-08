"""Recovery from the wedged Bridge (bench-proven stall, 2026-10-08). Regression (steering 10).

The host service resets the MCU when the app reports the Bridge silent. These tests pin the
decision logic, the signal it reads, and the two lessons learned on the board:
  * the sketch must NOT reset itself (the base firmware then never starts it again), and
  * a quiet CAN bus must never cause a reset (the signal is Bridge liveness, not data age).
"""
from __future__ import annotations

import importlib.util
import logging
import re
import sys
import time
import types
from pathlib import Path

import pytest

from service.cache import LiveStateCache

_spec = importlib.util.spec_from_file_location("unoq_mcu_recover", "deploy/unoq_mcu_recover.py")
rec = importlib.util.module_from_spec(_spec)
sys.modules["unoq_mcu_recover"] = rec
_spec.loader.exec_module(rec)

SKETCHES = [Path("firmware/unoq_nmea2000_capture/sketch/sketch.ino"), Path("bench/ack_only/sketch.ino.bench")]


# --- decision logic -----------------------------------------------------------------------
def test_healthy_bridge_is_left_alone():
    s = rec.State()
    for t in range(0, 600, 5):
        assert not rec.decide(s, float(t), {"receiver_ok_age_s": 0.4})


def test_silent_bridge_triggers_one_reset_then_waits_out_the_min_gap():
    s = rec.State()
    assert not rec.decide(s, 0.0, {"receiver_ok_age_s": 1.0})
    assert not rec.decide(s, 5.0, {"receiver_ok_age_s": rec.DEAD_AFTER_S})       # not past the limit yet
    assert rec.decide(s, 10.0, {"receiver_ok_age_s": rec.DEAD_AFTER_S + 0.1})    # past it
    assert not rec.decide(s, 15.0, {"receiver_ok_age_s": 40.0})                  # inside MIN_GAP_S
    assert not rec.decide(s, 10.0 + rec.MIN_GAP_S - 1, {"receiver_ok_age_s": 90.0})
    assert rec.decide(s, 10.0 + rec.MIN_GAP_S, {"receiver_ok_age_s": 90.0})      # still dead -> try again


def test_app_not_running_means_the_mcu_is_not_touched():
    s = rec.State()
    for t in range(0, 1000, 5):
        assert not rec.decide(s, float(t), None)


def test_bridge_that_never_answered_is_reset_only_after_the_grace_period():
    s = rec.State()
    assert not rec.decide(s, 0.0, {"receiver_ok_age_s": None})                   # app just started
    assert not rec.decide(s, rec.NEVER_OK_AFTER_S, {"receiver_ok_age_s": None})
    assert rec.decide(s, rec.NEVER_OK_AFTER_S + 1, {"receiver_ok_age_s": None})


def test_app_restart_restarts_the_never_ok_clock():
    s = rec.State()
    rec.decide(s, 0.0, {"receiver_ok_age_s": None})
    rec.decide(s, 50.0, None)                                                     # app went down
    assert not rec.decide(s, 100.0, {"receiver_ok_age_s": None})                  # new clock from 100
    assert not rec.decide(s, 100.0 + rec.NEVER_OK_AFTER_S, {"receiver_ok_age_s": None})


def test_reset_sequence_is_the_platforms_own_and_transmits_nothing():
    args = " ".join(rec.OPENOCD_ARGS)
    assert "srst_only" in args and "-c reset " in args and "mww 0x40036400 0xCAFFEEEE" in args
    assert args.index("reset") < args.index("mww 0x40036400")                    # handshake AFTER the reset
    assert "flash" not in args                                                    # never rewrites the sketch


# --- the signal ----------------------------------------------------------------------------
def test_cache_reports_receiver_liveness_independent_of_data():
    c = LiveStateCache()
    assert c.receiver_ok_age_s() is None
    c.note_receiver_ok()
    time.sleep(0.05)
    assert 0.0 <= c.receiver_ok_age_s() < 1.0   # no frames were ever decoded: a quiet bus is still "ok"


def test_health_endpoint_exposes_it_and_the_runtime_feeds_it():
    from fastapi.testclient import TestClient
    from web.api import create_app
    from service.discovery import DiscoveryMode

    cache = LiveStateCache()
    client = TestClient(create_app(cache, DiscoveryMode(), Path("."), settings=None))
    assert client.get("/api/v1/health").json()["receiver_ok_age_s"] is None
    cache.note_receiver_ok()
    assert client.get("/api/v1/health").json()["receiver_ok_age_s"] is not None
    src = Path("web/runtime.py").read_text()
    loop = src[src.index("def _health_poll_loop"):src.index("class LiveIngestionRuntime")]
    assert loop.index("receiver.health()") < loop.index("cache.note_receiver_ok()")


# --- the sketch ---------------------------------------------------------------------------
@pytest.mark.parametrize("path", SKETCHES)
def test_sketch_never_resets_itself(path):
    """Found on the board 2026-10-08: after any reset the base firmware waits for the host to write
    0x40036400 before loading the sketch, so a sketch-side reset leaves the MCU dead."""
    code = "\n".join(l for l in path.read_text().splitlines() if not l.strip().startswith("//"))
    for banned in ("0xE000ED0C", "NVIC_SystemReset", "sys_reboot", "SYSRESETREQ", "wdt_"):
        assert banned not in code


@pytest.mark.parametrize("path", SKETCHES)
def test_health_line_carries_uptime(path):
    s = path.read_text()
    assert "uptime_ms" in s and "String(millis())" in s


# --- Linux-side restart log ---------------------------------------------------------------
@pytest.fixture()
def receiver_module(monkeypatch):
    class FakeBridge:
        lines: list[str] = []

        @staticmethod
        def call(method):
            if method == "get_health":
                return FakeBridge.lines.pop(0)
            return {"get_can_init_ok": True, "get_frame_batch": ""}[method]

    pkg = types.ModuleType("arduino")
    app_utils = types.ModuleType("arduino.app_utils")
    app_utils.App = object
    app_utils.Bridge = FakeBridge
    monkeypatch.setitem(sys.modules, "arduino", pkg)
    monkeypatch.setitem(sys.modules, "arduino.app_utils", app_utils)
    monkeypatch.syspath_prepend("firmware/unoq_nmea2000_capture/python")
    sys.modules.pop("bridge_receiver", None)
    import bridge_receiver

    yield bridge_receiver, FakeBridge
    sys.modules.pop("bridge_receiver", None)


def test_uptime_going_backwards_is_logged_as_an_mcu_restart(receiver_module, caplog):
    mod, bridge = receiver_module
    bridge.lines = [
        "10,0,0,ListenOnly,true,true,0,50000",
        "20,0,0,ListenOnly,true,true,0,51000",
        "0,0,0,ListenOnly,true,true,0,1200",
    ]
    r = mod.ArduinoBridgeCanReceiver()
    with caplog.at_level(logging.WARNING):
        r.health()
        r.health()
        assert r.mcu_restarts == 0
        r.health()
    assert r.mcu_restarts == 1
    assert any("MCU restarted" in rec_.message for rec_ in caplog.records)


def test_health_line_without_uptime_still_parses(receiver_module):
    mod, bridge = receiver_module
    bridge.lines = ["1,2,3,ListenOnly,true,true,4"]
    h = mod.ArduinoBridgeCanReceiver().health()
    assert h.dropped_frames == 2 and h.error_count == 4
