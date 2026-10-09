"""Flight recorder ("black box"): a diagnostic log that survives a dirty power-off.

The board has no battery-backed clock and is powered from a boat circuit that gets switched off
without a shutdown. On 2026-10-09 that cost us the whole boat session: the journal lost its tail, the
container (and its log) was deleted at the next boot, and nothing recorded how many frames the CAN
controller had seen. This module is the fix for the app side.

Rules:
- One JSON object per line, written with a single O_APPEND write, then ``flush()`` and ``os.fsync()``
  before ``record()`` returns. After a power cut at most the line being written is torn; every earlier
  line is on disk. ``read_records()`` skips a torn line.
- Never raises. A full disk or a vanished directory makes ``record()`` return False, nothing more
  (steering 11: a diagnostic must not take the pipeline down).
- Bounded: the current file rotates at ``max_bytes`` and ``keep`` rotated files are retained.
- Stdlib only, so the host-side black box (deploy/unoq_blackbox.py) can load this one file.
- Every line carries ``boot`` (kernel boot id), ``up_s`` (kernel uptime) and ``seq``. The board has no
  RTC, so ``t`` (wall clock) is wrong until NTP syncs; ``boot`` + ``up_s`` stay trustworthy and the
  report script uses them to order lines and to correct ``t``.
- Nothing here touches the CAN bus.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Iterator

CURRENT_NAME = "blackbox.jsonl"
DEFAULT_MAX_BYTES = 2_000_000
DEFAULT_KEEP = 8  # rotated files kept besides the current one (about 4 days at one line per 10 s)
_MAX_TEXT = 2000


def _read_first_line(path: str) -> str | None:
    try:
        with open(path, "r", encoding="ascii") as f:
            return f.readline().strip() or None
    except OSError:
        return None


def boot_id() -> str:
    """Kernel boot id (shared with the host from inside the container); 'unknown' if unreadable."""
    raw = _read_first_line("/proc/sys/kernel/random/boot_id")
    return raw.replace("-", "")[:12] if raw else "unknown"


def uptime_s() -> float | None:
    raw = _read_first_line("/proc/uptime")
    try:
        return round(float(raw.split()[0]), 1) if raw else None
    except (ValueError, IndexError):
        return None


class FlightRecorder:
    def __init__(
        self,
        directory: str | Path,
        max_bytes: int = DEFAULT_MAX_BYTES,
        keep: int = DEFAULT_KEEP,
        source: str = "app",
    ) -> None:
        self.directory = Path(directory)
        self.max_bytes = max_bytes
        self.keep = keep
        self.source = source
        self.last_failure: str | None = None
        self._lock = threading.Lock()
        self._seq = 0
        self._boot = boot_id()
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.last_failure = f"mkdir: {exc}"

    @property
    def current_path(self) -> Path:
        return self.directory / CURRENT_NAME

    def record(self, kind: str, **fields: Any) -> bool:
        """Append one line and make it durable before returning. Never raises."""
        with self._lock:
            self._seq += 1
            line_obj = {
                "t": round(time.time(), 3),
                "boot": self._boot,
                "up_s": uptime_s(),
                "seq": self._seq,
                "src": self.source,
                "kind": kind,
            }
            line_obj.update(fields)
            try:
                line = json.dumps(line_obj, default=str, separators=(",", ":")) + "\n"
            except (TypeError, ValueError) as exc:
                self.last_failure = f"encode: {exc}"
                return False
            try:
                self._rotate_if_needed(len(line))
                fd = os.open(self.current_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
                try:
                    os.write(fd, line.encode("utf-8"))
                    os.fsync(fd)
                finally:
                    os.close(fd)
            except OSError as exc:
                self.last_failure = f"write: {exc}"
                return False
            return True

    def _rotate_if_needed(self, incoming: int) -> None:
        try:
            size = self.current_path.stat().st_size
        except FileNotFoundError:
            return
        if size + incoming <= self.max_bytes:
            return
        oldest = self.directory / f"{CURRENT_NAME}.{self.keep}"
        if oldest.exists():
            oldest.unlink()
        for n in range(self.keep - 1, 0, -1):
            src = self.directory / f"{CURRENT_NAME}.{n}"
            if src.exists():
                src.rename(self.directory / f"{CURRENT_NAME}.{n + 1}")
        self.current_path.rename(self.directory / f"{CURRENT_NAME}.1")
        self._fsync_directory()

    def _fsync_directory(self) -> None:
        try:
            fd = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError:
            pass


def read_records(directory: str | Path) -> Iterator[dict]:
    """Yield every readable record, oldest file first. A torn or garbled line (the one being written
    when power was cut) is skipped, not fatal."""
    directory = Path(directory)
    names = [p for p in directory.glob(f"{CURRENT_NAME}*") if p.is_file()]

    def order(p: Path) -> int:
        suffix = p.name[len(CURRENT_NAME):].lstrip(".")
        return -int(suffix) if suffix.isdigit() else 1  # .8 oldest ... .1, then the current file

    for path in sorted(names, key=order):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for raw in f:
                    try:
                        obj = json.loads(raw)
                    except ValueError:
                        continue
                    if isinstance(obj, dict):
                        yield obj
        except OSError:
            continue


class RecorderLogHandler(logging.Handler):
    """Mirror WARNING+ log records (e.g. 'MCU restarted', receiver exceptions) into the recorder, so
    they outlive the container's own log, which is deleted when App Lab recreates the container."""

    def __init__(self, recorder: FlightRecorder, level: int = logging.WARNING) -> None:
        super().__init__(level)
        self._recorder = recorder

    def emit(self, record: logging.LogRecord) -> None:
        try:
            text = self.format(record)
            self._recorder.record(
                "log", level=record.levelname, logger=record.name, text=text[:_MAX_TEXT]
            )
        except Exception:  # noqa: BLE001 - a logging handler must never raise
            pass
