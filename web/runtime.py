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
import threading
import time
from typing import Iterator

from protocol.receiver import CanFrame, CanHealth, ICanReceiver
from protocol.registry import DEFAULT_REGISTRY
from service.cache import LiveStateCache
from service.discovery import DiscoveryMode
from service.ingestion import IngestionService
from service.raw_log import RawCaptureLog
from service.session_store import SessionStore
from simulator.generator import MultiNodeScenario

logger = logging.getLogger(__name__)

#: Frames pulled from the receiver and handed to one ingest_batch() call.
_BATCH_SIZE = 32
#: Idle poll interval when a receiver's frames() generator is momentarily
#: exhausted (e.g. between simulator cycles) -- avoids a busy-loop.
_IDLE_POLL_S = 0.05


class SimulatorReceiver:
    """`ICanReceiver` that cycles `MultiNodeScenario` forever, pacing frames
    to their recorded `capture_time` deltas (same pacing contract as
    `FixtureReplay`, Property 30) -- the default `UNOQ_RECEIVER` so `make
    run-api` is genuinely live with zero hardware required, matching
    existing host-testable behavior (Requirement 17.9, 17.10)."""

    def __init__(self) -> None:
        self._connected = True
        self._total_frames = 0
        self._start_monotonic = time.monotonic()

    def frames(self) -> Iterator[CanFrame]:
        while True:
            scenario = MultiNodeScenario()
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
        # Imported lazily: the firmware package's Bridge client depends on
        # `arduino.app_utils`, only importable inside the App Lab Python
        # container on the actual board -- importing it eagerly would break
        # `UNOQ_RECEIVER=simulator` on every host with no board attached.
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

    Also polls `receiver.health()` into `cache.vessel.health.can_health` on
    every batch -- this is the ONLY place in the host codebase that ever
    wrote that field before this module existed (`GET /api/v1/health`'s
    `can_connected` previously always read None/False, since nothing fed
    it; found and fixed while verifying this task's own live-server test).
    """
    from pathlib import Path

    from protocol.domain import DeviceHealth

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
                service.ingest_batch(batch)
                if cache.vessel.health is None:
                    cache.vessel.health = DeviceHealth()
                cache.vessel.health.can_health = receiver.health()
        except Exception:  # noqa: BLE001 - deliberate: never let this thread die silently
            logger.exception("Live_Ingestion_Runtime: receiver loop raised; retrying")
            time.sleep(_IDLE_POLL_S)


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
    ) -> None:
        self.cache = cache
        self.discovery = discovery
        self.capture_dir = capture_dir
        self.receiver = receiver
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=_run_loop,
            args=(self.discovery, self.cache, self.capture_dir, self.receiver, self._stop),
            name="live-ingestion-runtime",
            daemon=True,  # Requirement 14.11: process exit is clean, no join required
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
