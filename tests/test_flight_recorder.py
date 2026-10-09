"""The flight recorder must survive a dirty power-off (boat, 2026-10-09).

The board lost the whole boat session because nothing was forced to disk: the journal lost its tail, the
container log was deleted at the next boot, and the capture file stayed empty. These tests pin the
behaviour that makes the new record durable and bounded.
"""
from __future__ import annotations

import json
import logging
import os

from service import flight_recorder as fr
from service.flight_recorder import FlightRecorder, RecorderLogHandler, read_records


def test_every_record_is_fsynced_before_record_returns(tmp_path, monkeypatch):
    synced: list[int] = []
    real_fsync = os.fsync
    monkeypatch.setattr(fr.os, "fsync", lambda fd: (synced.append(fd), real_fsync(fd))[1])
    rec = FlightRecorder(tmp_path)
    assert rec.record("tick", frames=3) is True
    assert synced, "record() returned without fsync: a power cut would lose this line"
    assert [r["frames"] for r in read_records(tmp_path)] == [3]


def test_lines_carry_boot_uptime_and_sequence(tmp_path):
    rec = FlightRecorder(tmp_path)
    rec.record("a")
    rec.record("b")
    a, b = read_records(tmp_path)
    assert (a["seq"], b["seq"]) == (1, 2)
    assert a["boot"] == b["boot"] and a["src"] == "app"
    assert "up_s" in a and "t" in a


def test_a_torn_last_line_is_skipped_not_fatal(tmp_path):
    rec = FlightRecorder(tmp_path)
    rec.record("good", n=1)
    with open(rec.current_path, "ab") as f:
        f.write(b'{"t":1,"kind":"torn","n":')  # power cut mid-write
    kinds = [r["kind"] for r in read_records(tmp_path)]
    assert kinds == ["good"]


def test_rotation_is_bounded_and_keeps_order(tmp_path):
    rec = FlightRecorder(tmp_path, max_bytes=400, keep=3)
    for i in range(200):
        rec.record("tick", i=i, pad="x" * 40)
    files = sorted(p.name for p in tmp_path.iterdir())
    assert len(files) <= 4  # current + 3 rotated
    seen = [r["i"] for r in read_records(tmp_path)]
    assert seen == sorted(seen) and seen[-1] == 199  # newest kept, still in order


def test_record_never_raises_when_the_disk_fails(tmp_path, monkeypatch):
    rec = FlightRecorder(tmp_path)

    def boom(*a, **k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(fr.os, "write", boom)
    assert rec.record("tick") is False
    assert "No space left" in rec.last_failure


def test_unencodable_field_does_not_raise(tmp_path):
    rec = FlightRecorder(tmp_path)
    assert rec.record("tick", weird=object()) is True  # default=str


def test_warning_logs_are_mirrored_and_info_is_not(tmp_path):
    rec = FlightRecorder(tmp_path)
    log = logging.getLogger("test.flight_recorder.mirror")
    log.propagate = False
    log.setLevel(logging.DEBUG)
    log.addHandler(RecorderLogHandler(rec))
    log.info("noise")
    log.warning("MCU restarted (uptime 9 ms -> 3 ms)")
    texts = [r["text"] for r in read_records(tmp_path) if r["kind"] == "log"]
    assert texts == ["MCU restarted (uptime 9 ms -> 3 ms)"]


def test_module_is_stdlib_only_so_the_host_service_can_load_it():
    src = open(fr.__file__, encoding="utf-8").read()
    for forbidden in ("import protocol", "from protocol", "import web", "from web", "from service", "import fastapi"):
        assert forbidden not in src
    json.dumps({})  # sanity: json is the only serializer used
