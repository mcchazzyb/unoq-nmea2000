"""Live_Ingestion_Runtime: the previously-missing seam that keeps
Live_State_Cache genuinely live while the Web_API serves (Requirement
14.10, 14.11).

Before this module, `make run-sim` (prints to stdout) and `make run-api`
(serves Live_State_Cache) were two disconnected processes -- nothing in the
host codebase ever ran a continuous `receiver.frames() -> ingest_batch()`
loop against the SAME cache the Web_API reads. `/api/v1/live` and the
WebSocket feed were only ever genuinely live under test harnesses that wired
IngestionService by hand. This module closes that gap.

The receiver is selected by configuration only (`UNOQ_RECEIVER`), never by
code change -- consistent with Requirement 17.9's existing swap guarantee
that Layers 2/3 cannot tell a real receiver from the Simulator.
"""
from __future__ import annotations

import logging
import random
import threading
import time
from typing import Iterator

from protocol.receiver import CanFrame, CanHealth, ICanReceiver
from protocol.registry import DEFAULT_REGISTRY
from service.bus_stats import BusStats
from service.cache import LiveStateCache
from service.discovery import DiscoveryMode
from service.flight_recorder import FlightRecorder
from service.ingestion import IngestionService
from service.raw_log import RawCaptureLog
from service.session_store import SessionStore
from simulator.generator import MultiNodeScenario
from simulator.scenarios import DF140SmokeTestScenario

logger = logging.getLogger(__name__)

#: Frames pulled from the receiver and handed to one ingest_batch() call. MUST stay 1: a receiver's
#: frames() is an endless generator, so a batch of N waits for N frames. On a quiet bus (a few frames
#: a second) a batch of 32 held frames in memory for tens of seconds, unsaved and not on the
#: dashboard, and lost them in a power cut. Every frame is fsynced on its own anyway.
_BATCH_SIZE = 1
#: Idle poll interval when a receiver's frames() generator is momentarily
#: exhausted (e.g. between simulator cycles) -- avoids a busy-loop.
_IDLE_POLL_S = 0.05
#: How often `receiver.health()` is polled into the cache, INDEPENDENT of
#: batch completion -- found via an actual deploy against real hardware
#: with no CAN traffic on the bus: `_batched()` only yields once _BATCH_SIZE
#: frames accumulate, so with zero traffic no batch EVER completes and
#: cache.vessel.health is starved even though the receiver is genuinely
#: polling correctly underneath. A separate timer thread, not tied to the
#: frame-batching cadence, is the fix.
_HEALTH_POLL_INTERVAL_S = 1.0
#: How often the flight recorder writes a tick (fsynced), and a fuller summary with per-PGN counts.
_TICK_INTERVAL_S = 10.0
_SUMMARY_EVERY_TICKS = 6


#: Per-cycle random walk bounds for SimulatorReceiver's live demo feed --
#: ponytail: a small fixed step range is enough to make dials visibly move
#: without needing a real motion model; tune these if a field over/undershoots.
_RPM_STEP = 60.0
_COOLANT_STEP_C = 0.3
_OIL_PRESSURE_STEP_KPA = 4.0
_ALTERNATOR_STEP_V = 0.05
_FUEL_RATE_STEP_LPH = 0.4
_TANK_LEVEL_STEP_PCT = 0.05
_SOG_STEP_KNOTS = 0.3
_COG_STEP_DEG = 1.5
_BATTERY_V_STEP = 0.03
_BATTERY_I_STEP = 0.2


class SimulatorReceiver:
    """`ICanReceiver` that cycles `MultiNodeScenario` forever, pacing frames
    to their recorded `capture_time` deltas (same pacing contract as
    `FixtureReplay`, Property 30) -- the default `UNOQ_RECEIVER` so `make
    run-api` is genuinely live with zero hardware required, matching
    existing host-testable behavior (Requirement 17.9, 17.10).

    Each cycle's values take a small bounded random-walk step from the
    previous cycle (not a fresh fixed snapshot every time) so dashboard
    dials visibly move under live/demo use -- found via actual user
    testing on the deployed board: the fixture scenarios are intentionally
    static dataclasses for golden-fixture determinism (simulator/scenarios.py,
    simulator/generator.py), so the variance belongs here, in the live-only
    receiver, rather than in the fixtures those modules exist to keep fixed.
    """

    def __init__(self) -> None:
        self._connected = True
        self._total_frames = 0
        self._start_monotonic = time.monotonic()
        base = DF140SmokeTestScenario()
        self._state = {
            "engine_rpm": base.engine_rpm,
            "coolant_temp_c": base.coolant_temp_c,
            "oil_pressure_kpa": base.oil_pressure_kpa,
            "alternator_voltage": base.alternator_voltage,
            "fuel_rate_lph": base.fuel_rate_lph,
            "tank_level_percent": base.tank_level_percent,
            "sog_knots": base.sog_knots,
            "cog_deg": base.cog_deg,
            "battery_voltage": 12.6,
            "battery_current": -4.2,
        }

    def _step(self, key: str, step: float, lo: float, hi: float) -> float:
        value = self._state[key] + random.uniform(-step, step)
        value = min(max(value, lo), hi)
        self._state[key] = value
        return value

    def _next_scenario(self) -> MultiNodeScenario:
        df140 = DF140SmokeTestScenario(
            engine_rpm=self._step("engine_rpm", _RPM_STEP, 600.0, 5500.0),
            oil_pressure_kpa=self._step("oil_pressure_kpa", _OIL_PRESSURE_STEP_KPA, 150.0, 450.0),
            coolant_temp_c=self._step("coolant_temp_c", _COOLANT_STEP_C, 60.0, 95.0),
            alternator_voltage=self._step("alternator_voltage", _ALTERNATOR_STEP_V, 12.8, 14.6),
            fuel_rate_lph=self._step("fuel_rate_lph", _FUEL_RATE_STEP_LPH, 2.0, 35.0),
            tank_level_percent=self._step("tank_level_percent", _TANK_LEVEL_STEP_PCT, 0.0, 100.0),
            sog_knots=self._step("sog_knots", _SOG_STEP_KNOTS, 0.0, 35.0),
            cog_deg=self._step("cog_deg", _COG_STEP_DEG, 0.0, 359.9),
        )
        return MultiNodeScenario(
            df140=df140,
            battery_voltage=self._step("battery_voltage", _BATTERY_V_STEP, 11.8, 14.6),
            battery_current=self._step("battery_current", _BATTERY_I_STEP, -20.0, 20.0),
        )

    def frames(self) -> Iterator[CanFrame]:
        while True:
            scenario = self._next_scenario()
            cycle_frames = scenario.frames()
            if not cycle_frames:
                time.sleep(_IDLE_POLL_S)
                continue
            cycle_start_capture = cycle_frames[0].capture_time
            cycle_start_wall = time.monotonic()
            for frame in cycle_frames:
                target_wall = cycle_start_wall + (frame.capture_time - cycle_start_capture)
                sleep_s = target_wall - time.monotonic()
                if sleep_s > 0:
                    time.sleep(sleep_s)
                self._total_frames += 1
                yield frame

    def health(self) -> CanHealth:
        elapsed = max(time.monotonic() - self._start_monotonic, 1e-6)
        return CanHealth(
            connected=self._connected,
            fps=self._total_frames / elapsed,
            dropped_frames=0,
            error_count=0,
            controller_state="listen-only",
            termination_on=False,
        )


def build_receiver(kind: str) -> ICanReceiver:
    """Requirement 14.10: select the ICanReceiver by configuration only.
    An unrecognized kind fails fast with a named error rather than
    silently defaulting to the simulator."""
    if kind == "simulator":
        return SimulatorReceiver()
    if kind == "hardware":
        # Imported lazily: the Bridge client depends on `arduino.app_utils`,
        # only importable inside the App Lab Python container on the actual
        # board -- importing it eagerly would break `UNOQ_RECEIVER=simulator`
        # on every host with no board attached.
        #
        # Two valid import locations, because this module is vendored into
        # two different layouts (see bridge_receiver.py's DEPLOYMENT NOTE):
        # on the HOST repo, the Bridge client lives at
        # firmware/unoq_nmea2000_capture/python/bridge_receiver.py; inside
        # the App Lab CONTAINER, this file itself is vendored to /app/web/
        # runtime.py with bridge_receiver.py as a sibling at
        # /app/python/bridge_receiver.py -- but `python/` there has no
        # `__init__.py` (App Lab's own scaffold convention, confirmed by an
        # actual deploy), so it is not an importable package by dotted
        # name; insert /app/python onto sys.path directly instead and
        # import the bare module name.
        try:
            import sys

            sys.path.insert(0, "/app/python")
            from bridge_receiver import ArduinoBridgeCanReceiver
        except ImportError:
            from firmware.unoq_nmea2000_capture.python.bridge_receiver import (
                ArduinoBridgeCanReceiver,
            )

        return ArduinoBridgeCanReceiver()
    raise ValueError(
        f"UNOQ_RECEIVER={kind!r} is not recognized; expected 'simulator' or 'hardware'"
    )


def _batched(iterator: Iterator[CanFrame], size: int) -> Iterator[list[CanFrame]]:
    batch: list[CanFrame] = []
    for item in iterator:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def _run_loop(
    discovery: DiscoveryMode,
    cache: LiveStateCache,
    capture_dir,
    receiver: ICanReceiver,
    stop: threading.Event,
    stats: BusStats | None = None,
    recorder: FlightRecorder | None = None,
) -> None:
    """The background thread body. Constructs `RawCaptureLog`/`SessionStore`
    HERE, on this thread, rather than in the constructor on the caller's
    thread -- sqlite3 connections are not usable across threads by default
    (`sqlite3.ProgrammingError`, caught live via this task's own end-to-end
    test against a running `web.main` process, which is exactly the
    verification steering 10 asks for). `IngestionService` itself is cheap
    to construct, so owning it entirely within this thread is simpler than
    passing a `check_same_thread=False` connection across threads.

    A receiver error (hardware disconnect, simulator exhaustion) is caught
    here and does not crash the Web_API process (steering 11 fail-safe
    applied to this new seam) -- the loop logs and retries rather than
    propagating, leaving the cache's existing staleness reporting
    (IngestionService's own halt/stale path) to reflect the gap rather than
    a second, separate error path.

    Health is polled by a SEPARATE thread (`_health_poll_loop`), not here --
    see `_HEALTH_POLL_INTERVAL_S`'s docstring for why batch-coupled polling
    starves `cache.vessel.health` under real zero-traffic conditions.
    """
    from pathlib import Path

    capture_dir = Path(capture_dir)
    raw_log = RawCaptureLog(capture_dir)
    session_store = SessionStore(capture_dir / "session.db")
    service = IngestionService(
        raw_log,
        session_store,
        DEFAULT_REGISTRY,
        live_cache=cache,
        discovery=discovery,
    )

    while not stop.is_set():
        try:
            for batch in _batched(receiver.frames(), _BATCH_SIZE):
                if stop.is_set():
                    return
                result = service.ingest_batch(batch)
                if stats is not None:
                    stats.note_batch(batch, result)
        except Exception as exc:  # noqa: BLE001 - deliberate: never let this thread die silently
            if stats is not None:
                stats.note_receiver_error(exc)
            logger.exception("Live_Ingestion_Runtime: receiver loop raised; retrying")
            time.sleep(_IDLE_POLL_S)


def _health_poll_loop(
    receiver: ICanReceiver,
    cache: LiveStateCache,
    stop: threading.Event,
    stats: BusStats | None = None,
) -> None:
    """Polls `receiver.health()` into `cache.vessel.health.can_health` on a
    FIXED timer, independent of the frame-batching loop (`_run_loop`) and
    its data volume -- this is the ONLY place in the host codebase that
    ever wrote that field before this module existed (`GET /api/v1/health`'s
    `can_connected` previously always read None/False). Found and fixed in
    two stages: first wiring it into the batch loop (worked under the
    Simulator's constant traffic), then finding via an actual deploy
    against real hardware with ZERO CAN traffic that batch-coupled polling
    never fires at all when no batch ever completes -- a receiver can be
    genuinely healthy and connected while reporting no frames yet.
    """
    from protocol.domain import DeviceHealth

    while not stop.is_set():
        try:
            if cache.vessel.health is None:
                cache.vessel.health = DeviceHealth()
            cache.vessel.health.can_health = receiver.health()
            cache.note_receiver_ok()
            if stats is not None:
                snapshot = getattr(receiver, "mcu_snapshot", None)
                stats.note_mcu(snapshot() if callable(snapshot) else None)
        except Exception:  # noqa: BLE001 - deliberate: health polling must never crash the process
            logger.exception("Live_Ingestion_Runtime: health poll raised; retrying")
        stop.wait(_HEALTH_POLL_INTERVAL_S)


def _flight_recorder_loop(
    recorder: FlightRecorder,
    stats: BusStats,
    cache: LiveStateCache,
    stop: threading.Event,
    tick_interval_s: float = _TICK_INTERVAL_S,
) -> None:
    """Write a durable tick every few seconds and an event whenever the bus state changes
    (no frames yet / receiving / silent). The point is a record that is still on disk after the power
    was cut: how many frames the app and the MCU saw, and when they started or stopped."""
    recorder.record("start", note="app flight recorder started")
    last_state = stats.state()
    ticks = 0
    while not stop.is_set():
        try:
            state = stats.state()
            if state != last_state:
                recorder.record("bus_state", old=last_state, new=state, **stats.snapshot(include_pgns=False))
                last_state = state
            ticks += 1
            full = ticks % _SUMMARY_EVERY_TICKS == 0
            snap = stats.snapshot(include_pgns=full)
            vessel = cache.snapshot()
            recorder.record(
                "summary" if full else "tick",
                receiver_ok_age_s=cache.receiver_ok_age_s(),
                capture_halted=cache.is_stale(),
                engines=len(vessel.engines),
                tanks=len(vessel.tanks),
                **snap,
            )
        except Exception:  # noqa: BLE001 - the recorder must never take the app down
            logger.exception("flight recorder tick failed")
        stop.wait(tick_interval_s)


class LiveIngestionRuntime:
    """Owns the background daemon thread that runs one IngestionService
    feeding the Web_API's own LiveStateCache/DiscoveryMode instances
    (Requirement 14.11: never blocks the Web_API's own request handling).
    The IngestionService itself (and its SQLite-backed SessionStore) is
    constructed INSIDE the thread, not here -- see `_run_loop`'s docstring.
    """

    def __init__(
        self,
        cache: LiveStateCache,
        discovery: DiscoveryMode,
        capture_dir,
        receiver: ICanReceiver,
        stats: BusStats | None = None,
        recorder: FlightRecorder | None = None,
    ) -> None:
        self.cache = cache
        self.discovery = discovery
        self.capture_dir = capture_dir
        self.receiver = receiver
        self.stats = stats
        self.recorder = recorder
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._health_thread: threading.Thread | None = None
        self._recorder_thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=_run_loop,
            args=(
                self.discovery, self.cache, self.capture_dir, self.receiver, self._stop,
                self.stats, self.recorder,
            ),
            name="live-ingestion-runtime",
            daemon=True,  # Requirement 14.11: process exit is clean, no join required
        )
        self._thread.start()
        self._health_thread = threading.Thread(
            target=_health_poll_loop,
            args=(self.receiver, self.cache, self._stop, self.stats),
            name="live-ingestion-runtime-health",
            daemon=True,
        )
        self._health_thread.start()
        if self.recorder is not None and self.stats is not None:
            self._recorder_thread = threading.Thread(
                target=_flight_recorder_loop,
                args=(self.recorder, self.stats, self.cache, self._stop),
                name="flight-recorder",
                daemon=True,
            )
            self._recorder_thread.start()

    def stop(self) -> None:
        self._stop.set()
