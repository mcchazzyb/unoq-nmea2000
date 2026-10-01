"""Golden fixture library builder (Requirement 17.4-17.7).

Generates `/fixtures/golden/*.jsonl` from known-good synthetic scenarios so
the on-disk fixtures are guaranteed byte-consistent with the encoders the
Stage 3 decoders were tested against, rather than hand-authored (and
therefore error-prone) raw bytes.

Run as `python -m fixtures.build_golden` to regenerate; the Stage 5 gate
test asserts the checked-in files match what this script produces.
"""
from __future__ import annotations

import json
import struct
from pathlib import Path

from protocol.receiver import CanFrame
from simulator.generator import (
    PGN_BATTERY_STATUS,
    PGN_UNKNOWN_PROPRIETARY,
    _battery_status_frame,
    _unknown_vendor_frame,
)
from simulator.scenarios import DF140SmokeTestScenario, _make_frame

GOLDEN_DIR = Path(__file__).parent / "golden"


def _write_jsonl(path: Path, frames: list[CanFrame], decode_failure_indices: set[int] = frozenset()) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for i, frame in enumerate(frames):
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
            if i in decode_failure_indices:
                record["decode_failure"] = True
            f.write(json.dumps(record) + "\n")


def build_engine_idle() -> None:
    scenario = DF140SmokeTestScenario(engine_rpm=750.0, sog_knots=0.0, cog_deg=0.0)
    _write_jsonl(GOLDEN_DIR / "engine_idle.jsonl", scenario.frames())


def build_engine_rpm_change() -> None:
    frames = []
    t = 0.0
    for rpm in (750.0, 2000.0, 3800.0, 5500.0):
        s = DF140SmokeTestScenario(engine_rpm=rpm)
        cycle = s.frames()
        for f in cycle:
            frames.append(
                CanFrame(
                    can_id=f.can_id,
                    priority=f.priority,
                    pgn=f.pgn,
                    source_address=f.source_address,
                    dest_address=f.dest_address,
                    dlc=f.dlc,
                    payload=f.payload,
                    capture_time=t + f.capture_time,
                    malformed=f.malformed,
                )
            )
        t += 1.0
    _write_jsonl(GOLDEN_DIR / "engine_rpm_change.jsonl", frames)


def build_trim_change() -> None:
    """Engine Rapid Update frames with varying tilt/trim values, isolating
    the one field 127488 carries that the DF140 scenario leaves N/A."""
    frames = []
    t = 0.0
    for trim in (-10, 0, 10, 20):
        rpm_raw = round(3800.0 / 0.25)
        # engine_instance(u8), rpm(u16), boost(u16 NA), trim(i8), reserved(2)
        payload = (
            bytes([0x00])
            + struct.pack("<H", rpm_raw)
            + struct.pack("<H", 0xFFFF)
            + struct.pack("<b", trim)
            + bytes([0xFF, 0xFF])
        )
        frames.append(_make_frame(2, 127488, 35, payload, t))
        t += 1.0
    _write_jsonl(GOLDEN_DIR / "trim_change.jsonl", frames)


def build_fuel_tank() -> None:
    frames = []
    t = 0.0
    for level_pct, capacity_l in ((80.0, 189.0), (50.0, 189.0), (12.0, 189.0)):
        level_raw = round(level_pct / 0.004)
        capacity_raw = round(capacity_l / 0.1)
        payload = bytes([0x00]) + struct.pack("<h", level_raw) + struct.pack("<I", capacity_raw) + bytes([0xFF])
        frames.append(_make_frame(6, 127505, 42, payload, t))
        t += 1.0
    _write_jsonl(GOLDEN_DIR / "fuel_tank.jsonl", frames)


def build_electrical() -> None:
    frames = []
    t = 0.0
    for voltage, current in ((12.6, -4.2), (13.8, 6.0), (11.9, -12.0)):
        frames.append(_battery_status_frame(voltage, current, 50, t))
        t += 1.0
    _write_jsonl(GOLDEN_DIR / "electrical.jsonl", frames)


def build_navigation() -> None:
    scenario = DF140SmokeTestScenario(sog_knots=15.0, cog_deg=180.0)
    frames = [f for f in scenario.frames() if f.pgn in (129025, 129026)]
    _write_jsonl(GOLDEN_DIR / "navigation.jsonl", frames)


def build_unknown_vendor() -> None:
    frames = [_unknown_vendor_frame(99, 0.0), _unknown_vendor_frame(99, 1.0)]
    _write_jsonl(GOLDEN_DIR / "unknown_vendor.jsonl", frames)


def build_malformed_frames() -> None:
    scenario = DF140SmokeTestScenario()
    frames = scenario.frames()[:2]
    malformed = [
        CanFrame(
            can_id=f.can_id,
            priority=f.priority,
            pgn=f.pgn,
            source_address=f.source_address,
            dest_address=f.dest_address,
            dlc=f.dlc,
            payload=f.payload,
            capture_time=f.capture_time,
            malformed=True,
        )
        for f in frames
    ]
    # A clean frame after the malformed ones proves the pipeline keeps going.
    clean = scenario.frames()[2:3]
    _write_jsonl(GOLDEN_DIR / "malformed_frames.jsonl", malformed + clean)


def build_mixed_multi_node() -> None:
    from simulator.generator import MultiNodeScenario

    scenario = MultiNodeScenario()
    _write_jsonl(GOLDEN_DIR / "mixed_multi_node.jsonl", scenario.frames())


def main() -> int:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    build_engine_idle()
    build_engine_rpm_change()
    build_trim_change()
    build_fuel_tank()
    build_electrical()
    build_navigation()
    build_unknown_vendor()
    build_malformed_frames()
    build_mixed_multi_node()
    print(f"Golden fixtures written to {GOLDEN_DIR}")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
