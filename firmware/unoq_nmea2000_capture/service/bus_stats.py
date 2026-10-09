"""Bus statistics: what has actually arrived from the CAN bus, independent of what was decoded.

The dashboard's green "online" dot only says the browser is talking to the board. It says nothing about
whether the bus delivers frames. This counts frames at the ingestion seam so the answer is one number
on the phone ("frames received", "last frame 0.4 s ago") and one line in the flight recorder.

Pure counters; no I/O; nothing here can transmit.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Callable, Iterable

_MAX_PGNS_REPORTED = 64
_RATE_WINDOW_S = 10.0
#: No frame for this long after frames were arriving means the bus went quiet (or the Bridge stalled).
SILENT_AFTER_S = 10.0

STATE_NO_FRAMES = "no_frames_yet"
STATE_RECEIVING = "receiving"
STATE_SILENT = "silent"


class BusStats:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._started = clock()
        self.frames_total = 0
        self.malformed_total = 0
        self.persisted_total = 0
        self.decode_failures_total = 0
        self.unknown_pgn_total = 0
        self.receiver_errors = 0
        self.last_receiver_error: str | None = None
        self._first_frame_at: float | None = None
        self._last_frame_at: float | None = None
        self._pgn_counts: dict[int, int] = {}
        self._sources: set[int] = set()
        self._recent: deque[tuple[float, int]] = deque()
        self._mcu: dict[str, Any] | None = None

    def note_batch(self, frames: Iterable[Any], ingestion_stats: Any = None) -> None:
        """Count a batch handed to ingestion. ``frames`` are CanFrame-like (pgn, source_address, malformed)."""
        now = self._clock()
        count = 0
        with self._lock:
            for frame in frames:
                count += 1
                if getattr(frame, "malformed", False):
                    self.malformed_total += 1
                pgn = getattr(frame, "pgn", None)
                if isinstance(pgn, int):
                    if pgn in self._pgn_counts or len(self._pgn_counts) < _MAX_PGNS_REPORTED:
                        self._pgn_counts[pgn] = self._pgn_counts.get(pgn, 0) + 1
                source = getattr(frame, "source_address", None)
                if isinstance(source, int):
                    self._sources.add(source)
            if count == 0:
                return
            self.frames_total += count
            if self._first_frame_at is None:
                self._first_frame_at = now
            self._last_frame_at = now
            self._recent.append((now, count))
            self._trim(now)
            if ingestion_stats is not None:
                self.persisted_total += getattr(ingestion_stats, "persisted", 0)
                self.decode_failures_total += getattr(ingestion_stats, "decode_failures", 0)
                self.unknown_pgn_total += getattr(ingestion_stats, "unknown_pgns", 0)

    def note_receiver_error(self, exc: BaseException) -> None:
        with self._lock:
            self.receiver_errors += 1
            self.last_receiver_error = f"{type(exc).__name__}: {exc}"[:300]

    def note_mcu(self, snapshot: dict[str, Any] | None) -> None:
        """The MCU's own view (frames it received, error counters, controller mode)."""
        with self._lock:
            self._mcu = dict(snapshot) if snapshot else None

    def _trim(self, now: float) -> None:
        while self._recent and now - self._recent[0][0] > _RATE_WINDOW_S:
            self._recent.popleft()

    def state(self) -> str:
        with self._lock:
            return self._state_locked(self._clock())

    def _state_locked(self, now: float) -> str:
        if self._last_frame_at is None:
            return STATE_NO_FRAMES
        return STATE_SILENT if now - self._last_frame_at > SILENT_AFTER_S else STATE_RECEIVING

    def snapshot(self, include_pgns: bool = True) -> dict[str, Any]:
        with self._lock:
            now = self._clock()
            self._trim(now)
            window = min(_RATE_WINDOW_S, now - self._started)
            recent = sum(n for _, n in self._recent)
            snap: dict[str, Any] = {
                "state": self._state_locked(now),
                "frames_total": self.frames_total,
                "frames_per_s": round(recent / window, 1) if window > 0 else 0.0,
                "last_frame_age_s": None if self._last_frame_at is None else round(now - self._last_frame_at, 1),
                "first_frame_age_s": None if self._first_frame_at is None else round(now - self._first_frame_at, 1),
                "malformed_total": self.malformed_total,
                "persisted_total": self.persisted_total,
                "decode_failures_total": self.decode_failures_total,
                "unknown_pgn_total": self.unknown_pgn_total,
                "sources_seen": len(self._sources),
                "pgns_seen": len(self._pgn_counts),
                "receiver_errors": self.receiver_errors,
                "last_receiver_error": self.last_receiver_error,
                "mcu": dict(self._mcu) if self._mcu else None,
                "app_uptime_s": round(now - self._started, 1),
            }
            if include_pgns:
                snap["pgn_counts"] = {str(k): v for k, v in sorted(self._pgn_counts.items())}
                snap["sources"] = sorted(self._sources)
            return snap
