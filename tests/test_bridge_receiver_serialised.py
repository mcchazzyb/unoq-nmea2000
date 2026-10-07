"""Only one Bridge call may be in flight at a time (frames() and health() run on
different threads sharing one Bridge client).

Regression (steering 10): overlapping Bridge calls can interleave on the router
socket and leave the MCU's Bridge waiting on a half-sent request.
"""
from __future__ import annotations

import sys
import threading
import time
import types

import pytest


class _Stop(Exception):
    pass


@pytest.fixture()
def receiver_module(monkeypatch):
    state = {"active": 0, "max_active": 0, "calls": 0, "lock": threading.Lock()}

    class FakeBridge:
        @staticmethod
        def call(method):
            with state["lock"]:
                state["active"] += 1
                state["max_active"] = max(state["max_active"], state["active"])
                state["calls"] += 1
                if state["calls"] > 200:
                    state["active"] -= 1
                    raise _Stop()
            time.sleep(0.002)  # a real call takes time; overlap is possible while it runs
            with state["lock"]:
                state["active"] -= 1
            return {"get_can_init_ok": True, "get_frame_batch": "", "get_health": "0,0,0,ListenOnly,true,true,0"}[method]

    pkg = types.ModuleType("arduino")
    app_utils = types.ModuleType("arduino.app_utils")
    app_utils.App = object
    app_utils.Bridge = FakeBridge
    monkeypatch.setitem(sys.modules, "arduino", pkg)
    monkeypatch.setitem(sys.modules, "arduino.app_utils", app_utils)
    monkeypatch.syspath_prepend("firmware/unoq_nmea2000_capture/python")
    sys.modules.pop("bridge_receiver", None)
    import bridge_receiver

    monkeypatch.setattr(bridge_receiver, "POLL_INTERVAL_S", 0.0)
    yield bridge_receiver, state
    sys.modules.pop("bridge_receiver", None)


def test_frames_and_health_never_overlap_on_the_bridge(receiver_module):
    bridge_receiver, state = receiver_module
    rx = bridge_receiver.ArduinoBridgeCanReceiver()

    def run_frames():
        try:
            for _ in rx.frames():
                pass
        except _Stop:
            pass

    def run_health():
        try:
            while True:
                rx.health()
        except _Stop:
            pass

    threads = [threading.Thread(target=run_frames), threading.Thread(target=run_health)]
    [t.start() for t in threads]
    [t.join(timeout=20) for t in threads]
    assert state["calls"] > 200
    assert state["max_active"] == 1
