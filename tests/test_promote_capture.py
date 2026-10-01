"""Regression test for scripts/promote_capture.py (Requirement 25.1-25.4).

Feature: nmea2000-diagnostic-platform. Proves a recorded capture promotes
into /fixtures/golden with its raw frames byte-identical, and that a
malformed/incomplete capture is rejected rather than silently promoted.
"""
from __future__ import annotations

import json

import pytest

from scripts.promote_capture import load_and_validate, promote


def _write_capture(tmp_path, records):
    path = tmp_path / "capture.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")
    return path


SAMPLE_RECORD = {
    "capture_time": 0.0,
    "can_id": 166854691,
    "priority": 2,
    "pgn": 127488,
    "source": 35,
    "dest": None,
    "dlc": 8,
    "payload_hex": "00b80bffff7fffff",
    "malformed": False,
}


def test_promote_preserves_frames_byte_identical(tmp_path, monkeypatch):
    """Promoted fixture's records equal the source capture's records exactly
    (Requirement 25.3) -- no re-encoding, reordering, or field loss."""
    import scripts.promote_capture as mod

    monkeypatch.setattr(mod, "GOLDEN_DIR", tmp_path / "golden")
    capture = _write_capture(tmp_path, [SAMPLE_RECORD, {**SAMPLE_RECORD, "capture_time": 0.01}])

    dest = promote(capture, "my_fixture", "127488 decodes rpm near 750")

    assert dest == tmp_path / "golden" / "my_fixture.jsonl"
    promoted = [json.loads(line) for line in dest.read_text().splitlines()]
    assert promoted == [SAMPLE_RECORD, {**SAMPLE_RECORD, "capture_time": 0.01}]


def test_promote_rejects_record_missing_required_field(tmp_path):
    """A record missing a documented field (e.g. no `pgn`) is rejected with
    an error naming the problem, not silently promoted (Requirement 25.1)."""
    bad_record = {k: v for k, v in SAMPLE_RECORD.items() if k != "pgn"}
    capture = _write_capture(tmp_path, [bad_record])

    with pytest.raises(ValueError, match="pgn"):
        load_and_validate(capture)


def test_promote_rejects_empty_capture(tmp_path):
    """An empty capture file has nothing to promote and is rejected rather
    than producing a hollow fixture."""
    capture = tmp_path / "empty.jsonl"
    capture.write_text("")

    with pytest.raises(ValueError, match="no records"):
        load_and_validate(capture)
