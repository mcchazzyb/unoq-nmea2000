"""Stage 7.13 Gate -- Live_Ingestion_Runtime: a continuous receiver loop
genuinely keeps Live_State_Cache live while served, selected by
configuration, never crashing the process on a receiver error.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

import protocol.decoders  # noqa: F401 -- registers DEFAULT_REGISTRY decoders
from service.cache import LiveStateCache
from service.discovery import DiscoveryMode
from web.runtime import LiveIngestionRuntime, SimulatorReceiver, build_receiver


def test_build_receiver_simulator_returns_simulator_receiver():
    receiver = build_receiver("simulator")
    assert isinstance(receiver, SimulatorReceiver)


def test_build_receiver_unrecognized_kind_fails_fast_named_error():
    with pytest.raises(ValueError, match="UNOQ_RECEIVER"):
        build_receiver("not-a-real-receiver")


def test_build_receiver_hardware_fails_only_on_board_only_dependency():
    """On a host with no board attached, build_receiver('hardware') must
    reach bridge_receiver.py's own import (proving the host/container
    dual-layout fallback import resolves correctly) and fail ONLY on the
    board-only `arduino.app_utils` dependency -- never on a broken import
    path for the Bridge client module itself."""
    with pytest.raises(ModuleNotFoundError, match="arduino"):
        build_receiver("hardware")


def test_simulator_receiver_health_reports_connected_listen_only():
    receiver = SimulatorReceiver()
    health = receiver.health()
    assert health.connected is True
    assert health.controller_state == "listen-only"
    assert health.termination_on is False


def test_live_ingestion_runtime_updates_cache_from_simulator(tmp_path: Path):
    """Requirement 14.10: make run-api's default receiver genuinely feeds
    Live_State_Cache -- not a frozen empty cache. Confirms engine/tank
    entities populate within a short wall-clock window, and that
    cache.vessel.health.can_health is populated too (previously nothing in
    the host codebase wrote this field -- GET /api/v1/health's
    can_connected always read False/None; found via this task's own
    live-server check against the running web.main process)."""
    cache = LiveStateCache()
    discovery = DiscoveryMode()
    capture_dir = tmp_path / "captures"
    capture_dir.mkdir()

    runtime = LiveIngestionRuntime(cache, discovery, capture_dir, SimulatorReceiver())
    runtime.start()
    try:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if cache.vessel.engines or cache.vessel.tanks:
                break
            time.sleep(0.05)
        assert cache.vessel.engines or cache.vessel.tanks, (
            "Live_Ingestion_Runtime did not populate the cache within 5s"
        )
        assert cache.vessel.health is not None
        assert cache.vessel.health.can_health is not None
        assert cache.vessel.health.can_health.connected is True
    finally:
        runtime.stop()


class _RaisingReceiver:
    """An ICanReceiver whose frames() raises immediately -- proves a
    receiver error is caught at the loop level and does not crash the
    Web_API process (steering 11 fail-safe applied to this new seam)."""

    def frames(self):
        raise RuntimeError("simulated hardware disconnect")
        yield  # pragma: no cover - unreachable, makes this a generator

    def health(self):
        from protocol.receiver import CanHealth

        return CanHealth(
            connected=False, fps=0.0, dropped_frames=0, error_count=1,
            controller_state="error", termination_on=False,
        )


class _ZeroTrafficReceiver:
    """An ICanReceiver that is genuinely connected (health().connected is
    True) but NEVER yields a frame -- the exact real-hardware bench
    condition this task's own live deploy hit (ArduinoBridgeCanReceiver
    polling the Bridge successfully with no CAN traffic on an isolated
    bus). Proves cache.vessel.health updates independent of batch
    completion -- the real bug this test is a regression test for: health
    polling that only runs after `_batched()` yields a full batch NEVER
    fires when zero traffic means no batch ever completes."""

    def frames(self):
        while True:
            time.sleep(0.01)
            if False:
                yield  # pragma: no cover - unreachable, makes this a generator

    def health(self):
        from protocol.receiver import CanHealth

        return CanHealth(
            connected=True, fps=0.0, dropped_frames=0, error_count=0,
            controller_state="listen-only", termination_on=False,
        )


def test_live_ingestion_runtime_updates_health_with_zero_traffic(tmp_path: Path):
    """Regression test for the real bug found via an actual board deploy
    with no CAN traffic on the bus: GET /api/v1/health's can_connected
    stayed False even though the receiver was genuinely connected, because
    health was only polled after a batch of _BATCH_SIZE frames completed --
    which never happens with zero traffic. Health must update on its own
    timer, independent of frame volume."""
    cache = LiveStateCache()
    discovery = DiscoveryMode()
    capture_dir = tmp_path / "captures"
    capture_dir.mkdir()

    runtime = LiveIngestionRuntime(cache, discovery, capture_dir, _ZeroTrafficReceiver())
    runtime.start()
    try:
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if cache.vessel.health is not None and cache.vessel.health.can_health is not None:
                break
            time.sleep(0.05)
        assert cache.vessel.health is not None, (
            "health was never polled -- regresses the zero-traffic bench bug"
        )
        assert cache.vessel.health.can_health.connected is True
        assert cache.vessel.engines == {} and cache.vessel.tanks == {}, (
            "sanity check: this receiver genuinely yields no frames"
        )
    finally:
        runtime.stop()


def test_live_ingestion_runtime_survives_receiver_error(tmp_path: Path):
    cache = LiveStateCache()
    discovery = DiscoveryMode()
    capture_dir = tmp_path / "captures"
    capture_dir.mkdir()

    runtime = LiveIngestionRuntime(cache, discovery, capture_dir, _RaisingReceiver())
    runtime.start()
    try:
        time.sleep(0.3)
        # The background thread must still be alive -- a raising receiver
        # does not take the whole process down.
        assert runtime._thread.is_alive()
    finally:
        runtime.stop()


def test_live_ingestion_runtime_background_thread_is_daemon(tmp_path: Path):
    """Requirement 14.11: does not block the Web_API's own request
    handling -- confirmed structurally by the thread being a daemon."""
    cache = LiveStateCache()
    discovery = DiscoveryMode()
    capture_dir = tmp_path / "captures"
    capture_dir.mkdir()

    runtime = LiveIngestionRuntime(cache, discovery, capture_dir, SimulatorReceiver())
    runtime.start()
    try:
        assert runtime._thread.daemon is True
    finally:
        runtime.stop()
