"""Bus status: the dashboard must tell 'no frames from the bus' apart from 'frames, not decoded'.

Boat, 2026-10-09: the owner could not tell whether the bus had delivered anything. The green dot only
means the browser reaches the board.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from fastapi.testclient import TestClient

from protocol.receiver import CanFrame, CanHealth
from service.bus_stats import BusStats, STATE_NO_FRAMES, STATE_RECEIVING, STATE_SILENT
from service.cache import LiveStateCache
from service.discovery import DiscoveryMode
from service.flight_recorder import FlightRecorder, read_records
from web.api import create_app
from web.runtime import LiveIngestionRuntime, _BATCH_SIZE


def _frame(pgn: int = 127488, src: int = 4, n: int = 0) -> CanFrame:
    return CanFrame(
        can_id=(6 << 26) | (pgn << 8) | src, priority=6, pgn=pgn, source_address=src, dest_address=None,
        dlc=8, payload=bytes(8), capture_time=float(n), wall_clock_time=None,
    )


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_state_goes_no_frames_then_receiving_then_silent():
    clock = _Clock()
    stats = BusStats(clock=clock)
    assert stats.state() == STATE_NO_FRAMES
    stats.note_batch([_frame()])
    assert stats.state() == STATE_RECEIVING
    clock.now += 11
    assert stats.state() == STATE_SILENT


def test_counts_frames_pgns_and_sources():
    stats = BusStats(clock=_Clock())
    stats.note_batch([_frame(127488, 4), _frame(127505, 42), _frame(127488, 4)])
    snap = stats.snapshot()
    assert snap["frames_total"] == 3
    assert snap["pgn_counts"] == {"127488": 2, "127505": 1}
    assert snap["sources"] == [4, 42] and snap["sources_seen"] == 2


def test_no_frames_snapshot_is_explicit_not_zero_rate_ambiguity():
    snap = BusStats(clock=_Clock()).snapshot()
    assert snap["state"] == STATE_NO_FRAMES
    assert snap["last_frame_age_s"] is None and snap["frames_total"] == 0


def test_bus_status_endpoint_reports_state_and_mcu(tmp_path):
    stats = BusStats()
    stats.note_mcu({"mode": "ListenOnly", "error_counters": 0, "mcu_frames": 0})
    client = TestClient(create_app(LiveStateCache(), DiscoveryMode(), tmp_path, bus_stats=stats))
    body = client.get("/api/v1/bus-status").json()
    assert body["available"] is True and body["state"] == STATE_NO_FRAMES
    assert body["mcu"]["mode"] == "ListenOnly"
    stats.note_batch([_frame()])
    assert client.get("/api/v1/bus-status").json()["state"] == STATE_RECEIVING


def test_bus_status_endpoint_without_stats_says_unavailable(tmp_path):
    client = TestClient(create_app(LiveStateCache(), DiscoveryMode(), tmp_path))
    assert client.get("/api/v1/bus-status").json() == {"available": False}


def test_dashboard_page_has_the_bus_status_strip():
    html = Path("web/static/index.html").read_text(encoding="utf-8")
    assert 'id="bus-status"' in html and "/api/v1/bus-status" in html
    assert "No frames received from the bus yet" in html


class _TrickleReceiver:
    """Three frames, then the bus goes quiet while the generator stays open (a real quiet bus)."""

    def __init__(self) -> None:
        self.stop = False

    def frames(self):
        for n in range(3):
            yield _frame(n=n)
        while not self.stop:
            time.sleep(0.02)
        return

    def health(self) -> CanHealth:
        return CanHealth(True, 0.0, 0, 0, "ListenOnly", False)

    def mcu_snapshot(self):
        return {"mode": "ListenOnly", "error_counters": 0, "mcu_frames": 3}


def test_frames_on_a_quiet_bus_are_saved_at_once_not_held_for_a_full_batch(tmp_path):
    """Before: _BATCH_SIZE was 32, so three frames waited in memory (unsaved, invisible, lost in a power
    cut) until 29 more arrived. Each frame must reach the capture log and the stats immediately."""
    assert _BATCH_SIZE == 1
    receiver = _TrickleReceiver()
    stats = BusStats()
    recorder = FlightRecorder(tmp_path / "blackbox")
    runtime = LiveIngestionRuntime(
        LiveStateCache(), DiscoveryMode(), tmp_path / "captures", receiver, stats, recorder
    )
    runtime.start()
    try:
        deadline = time.time() + 5
        while time.time() < deadline and stats.snapshot()["frames_total"] < 3:
            time.sleep(0.05)
        assert stats.snapshot()["frames_total"] == 3
        lines = sum(len(p.read_text().splitlines()) for p in (tmp_path / "captures").glob("capture-*.jsonl"))
        assert lines == 3, "frames must be fsynced to the capture log as they arrive"
        deadline = time.time() + 5
        while time.time() < deadline and not any(r["kind"] in ("tick", "summary") for r in read_records(tmp_path / "blackbox")):
            time.sleep(0.05)
        kinds = [r["kind"] for r in read_records(tmp_path / "blackbox")]
        assert "start" in kinds and ("tick" in kinds or "summary" in kinds)
    finally:
        receiver.stop = True
        runtime.stop()


def test_a_bus_state_change_is_written_to_the_recorder(tmp_path, monkeypatch):
    import web.runtime as rt

    monkeypatch.setattr(rt, "_TICK_INTERVAL_S", 0.05)
    receiver = _TrickleReceiver()
    stats = BusStats()
    recorder = FlightRecorder(tmp_path / "blackbox")
    runtime = LiveIngestionRuntime(
        LiveStateCache(), DiscoveryMode(), tmp_path / "captures", receiver, stats, recorder
    )
    # the module default is bound at definition time, so drive the loop directly with a fast tick
    import threading

    stop = threading.Event()
    t = threading.Thread(
        target=rt._flight_recorder_loop, args=(recorder, stats, runtime.cache, stop, 0.05), daemon=True
    )
    t.start()
    try:
        time.sleep(0.2)
        stats.note_batch([_frame()])
        deadline = time.time() + 3
        while time.time() < deadline and not any(r["kind"] == "bus_state" for r in read_records(tmp_path / "blackbox")):
            time.sleep(0.05)
        change = [r for r in read_records(tmp_path / "blackbox") if r["kind"] == "bus_state"]
        assert change and change[0]["old"] == STATE_NO_FRAMES and change[0]["new"] == STATE_RECEIVING
    finally:
        stop.set()
        t.join(timeout=2)
