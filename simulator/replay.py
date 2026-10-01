"""Fixture replay engine implementing `ICanReceiver` (Requirement 17.1, 17.9).

Because this implements the SAME `ICanReceiver` Protocol as real hardware,
Layers 2/3 cannot tell replay from a live CAN bus (Requirement 23, 24.6) --
`Ingestion_Service` is handed a `FixtureReplay` exactly as it would a real
receiver.

Fault injection (Requirement 17.3, 17.8) is applied at the frame-stream level
and surfaced through `health()`'s `CanHealth`, never by silently terminating
the stream.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Iterator

from protocol.receiver import CanFrame, CanHealth
from service.raw_log import CorruptLine


@dataclass
class FaultConfig:
    """Fault-injection knobs (Requirement 17.3, 17.8). All disabled by
    default -- a plain replay with no faults reproduces the fixture exactly."""

    malformed_frame_rate: float = 0.0  # fraction of frames marked malformed
    drop_frame_rate: float = 0.0  # fraction of frames dropped entirely
    max_burst_rate_hz: float | None = None  # cap frames/sec (burst/high-rate fault)
    seed: int | None = None


class FixtureReplay:
    """Replays a fixed list of `CanFrame`s, implementing `ICanReceiver`.

    `frames()` preserves inter-frame timing from the fixture's own
    `capture_time` values within +/-5ms (Property 30) by sleeping between
    yields; pass `realtime=False` for tests that want the frames without
    wall-clock delay.
    """

    def __init__(
        self,
        frames: list[CanFrame],
        faults: FaultConfig | None = None,
        realtime: bool = True,
    ) -> None:
        self._frames = frames
        self.faults = faults or FaultConfig()
        self.realtime = realtime
        self._rng = random.Random(self.faults.seed)
        self._dropped_frames = 0
        self._error_count = 0
        self._connected = True

    def frames(self) -> Iterator[CanFrame]:
        if not self._frames:
            return
        min_interval = (1.0 / self.faults.max_burst_rate_hz) if self.faults.max_burst_rate_hz else 0.0
        start_capture = self._frames[0].capture_time
        start_wall = time.monotonic()
        last_emit_wall = start_wall

        for frame in self._frames:
            if self.faults.drop_frame_rate and self._rng.random() < self.faults.drop_frame_rate:
                self._dropped_frames += 1
                continue

            if self.realtime:
                target_wall = start_wall + (frame.capture_time - start_capture)
                now = time.monotonic()
                if min_interval and (now - last_emit_wall) < min_interval:
                    target_wall = max(target_wall, last_emit_wall + min_interval)
                    now = time.monotonic()
                sleep_s = target_wall - now
                if sleep_s > 0:
                    time.sleep(sleep_s)
                last_emit_wall = time.monotonic()

            if self.faults.malformed_frame_rate and self._rng.random() < self.faults.malformed_frame_rate:
                frame = CanFrame(
                    can_id=frame.can_id,
                    priority=frame.priority,
                    pgn=frame.pgn,
                    source_address=frame.source_address,
                    dest_address=frame.dest_address,
                    dlc=frame.dlc,
                    payload=frame.payload,
                    capture_time=frame.capture_time,
                    wall_clock_time=frame.wall_clock_time,
                    malformed=True,
                )
                self._error_count += 1

            yield frame

    def health(self) -> CanHealth:
        total = len(self._frames) or 1
        fps = total / max(self._frames[-1].capture_time - self._frames[0].capture_time, 1e-6) if len(self._frames) > 1 else 0.0
        return CanHealth(
            connected=self._connected,
            fps=fps,
            dropped_frames=self._dropped_frames,
            error_count=self._error_count,
            controller_state="listen-only",
            termination_on=False,
        )


def load_fixture_frames(records: list[dict]) -> list[CanFrame]:
    """Build CanFrame objects from parsed JSONL fixture records (the same
    record shape service.raw_log writes/reads), for replay or test setup.

    A `CorruptLine` (a line that failed to parse as JSON -- Requirement
    20.1, 20.3) or a dict missing a required field is quarantined: it is
    skipped rather than raising, so one bad line in a replay file does not
    abort replay of the rest."""
    frames = []
    for r in records:
        if isinstance(r, CorruptLine):
            continue
        try:
            frames.append(
                CanFrame(
                    can_id=r["can_id"],
                    priority=r["priority"],
                    pgn=r["pgn"],
                    source_address=r["source"],
                    dest_address=r.get("dest"),
                    dlc=r["dlc"],
                    payload=bytes.fromhex(r["payload_hex"]),
                    capture_time=r["capture_time"],
                    wall_clock_time=r.get("wall_clock_time"),
                    malformed=r.get("malformed", False),
                )
            )
        except (KeyError, ValueError):
            continue
    return frames


def main() -> int:
    """`make replay FILE=...`: replay a JSONL capture file in realtime,
    printing each frame as it is emitted."""
    import sys

    from service.raw_log import iter_records

    if len(sys.argv) < 2:
        print("usage: python -m simulator.replay <fixture.jsonl>", file=sys.stderr)
        return 2
    records = list(iter_records(sys.argv[1]))
    frames = load_fixture_frames(records)
    replay = FixtureReplay(frames, realtime=True)
    count = 0
    for frame in replay.frames():
        print(f"pgn={frame.pgn} sa={frame.source_address} t={frame.capture_time:.3f}")
        count += 1
    print(f"# replayed {count} frames, health={replay.health()}")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
