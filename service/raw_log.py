"""Raw_Capture_Log: append-only JSONL persistence for raw CAN frames
(Requirement 12.3, 12.4, 12.8; Properties 1, 2, 3, 5).

Capture-first discipline (steering 02): every valid frame is persisted here
BEFORE any decode/filter/classification. This module itself does not enforce
call ordering -- the Ingestion_Service does, by calling `append()` as its
first pipeline stage -- but it does enforce append-only: earlier lines are
NEVER rewritten (Property 5), and a write failure never touches previously
persisted bytes (Requirement 12.8).
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from protocol.receiver import CanFrame

DEFAULT_MAX_BYTES = 100 * 1024 * 1024  # 100 MB (Requirement 12.4)
DEFAULT_MAX_AGE_S = 24 * 60 * 60  # 24 hours (Requirement 12.4)


@dataclass
class WriteFailure:
    """Recorded when a write to the Raw_Capture_Log fails. Previously
    persisted data is retained unchanged (Requirement 12.8)."""

    timestamp: float
    error: str


def _frame_to_record(frame: CanFrame, decode_failure: bool = False) -> dict:
    record = {
        "capture_time": frame.capture_time,
        "can_id": frame.can_id,
        "priority": frame.priority,
        "pgn": frame.pgn,
        "source": frame.source_address,
        "dest": frame.dest_address,
        "dlc": frame.dlc,
        "payload_hex": frame.payload.hex(),
        "malformed": frame.malformed,
    }
    if frame.wall_clock_time is not None:
        record["wall_clock_time"] = frame.wall_clock_time
    if decode_failure:
        record["decode_failure"] = True
    return record


class RawCaptureLog:
    """Append-only JSONL writer with size/age rotation.

    Each call to `append()` writes exactly one JSON object per line and
    flushes immediately, so a line that returns successfully is durable
    before the next frame is considered. Rotation closes the current file
    and opens a timestamped successor; rotated files are never reopened for
    writing, so they remain replayable as a frozen record (Requirement 12.4).
    """

    def __init__(
        self,
        directory: str | Path,
        max_bytes: int = DEFAULT_MAX_BYTES,
        max_age_s: float = DEFAULT_MAX_AGE_S,
    ) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes
        self.max_age_s = max_age_s
        self._current_path: Path | None = None
        self._current_file = None
        self._current_size = 0
        self._current_opened_at = 0.0
        self.last_write_failure: WriteFailure | None = None
        self._open_new_file()

    def _open_new_file(self) -> None:
        if self._current_file is not None:
            self._current_file.close()
        timestamp_ns = time.time_ns()
        self._current_path = self.directory / f"capture-{timestamp_ns}.jsonl"
        self._current_file = open(self._current_path, "a", encoding="utf-8")
        self._current_size = 0
        self._current_opened_at = time.time()

    def _should_rotate(self) -> bool:
        if self._current_size >= self.max_bytes:
            return True
        if (time.time() - self._current_opened_at) >= self.max_age_s:
            return True
        return False

    def append(self, frame: CanFrame, decode_failure: bool = False) -> bool:
        """Append one frame as a JSONL record. Returns True on success. On
        failure, records a WriteFailure and leaves prior persisted data
        unchanged (Requirement 12.8) -- it does NOT raise, so the caller's
        pipeline is never halted by a single bad write.
        """
        if self._should_rotate():
            try:
                self._open_new_file()
            except OSError as exc:
                self.last_write_failure = WriteFailure(timestamp=time.time(), error=str(exc))
                return False

        record = _frame_to_record(frame, decode_failure=decode_failure)
        line = json.dumps(record) + "\n"
        try:
            self._current_file.write(line)
            self._current_file.flush()
            os.fsync(self._current_file.fileno())
        except (OSError, ValueError) as exc:
            # ValueError covers "I/O operation on closed file" -- a file
            # handle closed out from under the log (e.g. a crashed/killed
            # writer) is a write failure, not a crash of this method.
            self.last_write_failure = WriteFailure(timestamp=time.time(), error=str(exc))
            return False
        self._current_size += len(line.encode("utf-8"))
        return True

    def mark_decode_failure(self, frame: CanFrame) -> bool:
        """Record a decode-failure indication for a frame whose raw bytes are
        already safely persisted (Requirement 2.3, 3.7). Appends a SEPARATE
        record rather than mutating the already-written line, preserving
        append-only (Property 5)."""
        return self.append(frame, decode_failure=True)

    def current_path(self) -> Path:
        return self._current_path

    def close(self) -> None:
        if self._current_file is not None:
            self._current_file.close()
            self._current_file = None

    def __enter__(self) -> "RawCaptureLog":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


@dataclass
class CorruptLine:
    """A line in a replay/log file that failed to parse as JSON. Yielded in
    place of a record rather than raising, so one corrupted line never halts
    replay of the rest of the file (Requirement 20.1, 20.3; steering 11's
    no-single-fault-terminates-the-pipeline rule applied to replay, not just
    live capture)."""

    line_number: int
    raw: str
    error: str


def iter_records(path: str | Path):
    """Replay helper: yield decoded JSON records from a (possibly rotated)
    JSONL file in order, skipping nothing -- unknown/malformed/decode-failure
    records are all retained and replayable (Property 3).

    A line that is not valid JSON (a corrupted/truncated replay file, or a
    hostile/garbage file fed to `/api/v1/replay` or `/logs/{id}`) is
    quarantined: it is yielded as a `CorruptLine` instead of raising, and
    every subsequent line is still processed (Requirement 20.1, 20.3)."""
    with open(path, "r", encoding="utf-8") as f:
        for line_number, raw_line in enumerate(f, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                yield CorruptLine(line_number=line_number, raw=line, error=str(exc))
