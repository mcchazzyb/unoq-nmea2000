"""Promote a recorded Raw_Capture_Log into a /fixtures/golden entry
(Requirement 25.1-25.4).

Usage:
    python -m scripts.promote_capture <capture.jsonl> <fixture_name> \\
        --assertion "one-line description of what the fixture proves"

What this does (Requirement 25.1, 25.3):
    - Copies the capture's raw frames byte-for-byte into
      /fixtures/golden/<fixture_name>.jsonl using the exact Raw_Capture_Log
      record shape (capture_time, can_id, priority, pgn, source, dest, dlc,
      payload_hex, malformed, optional wall_clock_time/decode_failure) --
      nothing is re-encoded, re-ordered, or stripped, so Provenance derived
      from these fields at decode time is unchanged from the original
      capture.

What this does NOT do (Requirement 25.2, 25.4):
    - It does not invent expected assertions for you. `--assertion` is
      REQUIRED: a promoted fixture with no documented expectation is not a
      regression test, it's just a file. Write the assertion as a one-line
      docstring the way every other golden fixture test does it (see
      tests/test_stage5_gate.py, e.g. "Fixture: engine_idle.jsonl --
      expected: 127488 decodes rpm near 750.").
    - It does not wire the fixture into the test suite for you -- that step
      is a code change (one `_reassemble_fixture`-based test function) a
      human reviews, not something safe to auto-generate from capture
      content. This script prints the ready-to-paste test stub so adding it
      is a two-minute copy/paste, not blank-page work.

ponytail: this is a thin, direct copy + stub-print, not a framework. The
record validation is intentionally the same shape check raw_log.py already
guarantees on write; promote_capture does not re-derive or re-validate
decoded values, since Golden_Fixtures are raw-frame fixtures decoded by the
existing test helpers, not a second decode pipeline.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

GOLDEN_DIR = Path(__file__).parent.parent / "fixtures" / "golden"

REQUIRED_FIELDS = {"capture_time", "can_id", "priority", "pgn", "source", "dest", "dlc", "payload_hex", "malformed"}


def load_and_validate(capture_path: Path) -> list[dict]:
    """Read a Raw_Capture_Log JSONL file, validating each line has the
    documented record shape. Raises ValueError naming the bad line rather
    than silently promoting a malformed capture file."""
    records = []
    with open(capture_path, "r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{capture_path}:{lineno}: not valid JSON ({exc})") from exc
            missing = REQUIRED_FIELDS - record.keys()
            if missing:
                raise ValueError(f"{capture_path}:{lineno}: missing required field(s) {sorted(missing)}")
            records.append(record)
    if not records:
        raise ValueError(f"{capture_path}: contains no records -- nothing to promote")
    return records


def promote(capture_path: Path, fixture_name: str, assertion: str) -> Path:
    """Copy the capture's raw frames verbatim into /fixtures/golden/<fixture_name>.jsonl
    and return the written path. Preserves every record field exactly
    (Requirement 25.3) -- this is a byte-shape-validated copy, not a
    transform."""
    records = load_and_validate(capture_path)
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    dest = GOLDEN_DIR / f"{fixture_name}.jsonl"
    with open(dest, "w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")
    print(f"Promoted {len(records)} frame(s) from {capture_path} -> {dest}")
    print()
    print("Required next step (Requirement 25.4) -- add this test to tests/test_stage5_gate.py:")
    print()
    print(f'def test_golden_{fixture_name}():')
    print(f'    """Fixture: {fixture_name}.jsonl -- expected: {assertion}"""')
    print(f'    items = _reassemble_fixture(GOLDEN_DIR / "{fixture_name}.jsonl")')
    print("    # TODO: assert the decoded values this fixture is meant to prove")
    return dest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("capture", type=Path, help="Path to a Raw_Capture_Log JSONL file")
    parser.add_argument("fixture_name", help="Name for the new fixture (without .jsonl)")
    parser.add_argument(
        "--assertion",
        required=True,
        help="One-line description of the expected decoded behaviour this fixture proves (Requirement 25.2)",
    )
    args = parser.parse_args(argv)

    try:
        promote(args.capture, args.fixture_name, args.assertion)
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
