"""Synthetic multi-node traffic generator (Requirement 17.2).

Builds on the Suzuki-DF140-shaped scenario frames from `simulator.scenarios`
and adds node types the Stage 3 decoders deliberately do NOT cover: an
electrical/battery monitor, and an unknown/proprietary-PGN node -- so a
generated session always has at least one node Discovery_Mode must track
without ever invoking a decoder for it (Requirement 5.4, 10.12).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

from protocol.receiver import CanFrame
from simulator.scenarios import DF140SmokeTestScenario, _make_frame

_ELECTRICAL_SOURCE_ADDRESS = 50
_UNKNOWN_VENDOR_SOURCE_ADDRESS = 99
PGN_BATTERY_STATUS = 127508
PGN_UNKNOWN_PROPRIETARY = 65280  # reused by many vendors; deliberately undecoded here


def _battery_status_frame(
    battery_voltage: float, current: float, source_address: int, timestamp: float
) -> CanFrame:
    voltage_raw = round(battery_voltage / 0.01)
    current_raw = round(current / 0.1)
    payload = bytes([0x00]) + struct.pack("<hh", voltage_raw, current_raw) + bytes([0xFF] * 3)
    return _make_frame(6, PGN_BATTERY_STATUS, source_address, payload, timestamp)


def _unknown_vendor_frame(source_address: int, timestamp: float) -> CanFrame:
    """A frame on a reused proprietary PGN with no registered decoder --
    exercises the unknown-PGN path (retained, never decoded, Requirement 5.4)."""
    payload = bytes([0xAA, 0xBB, 0xCC, 0xDD, 0x01, 0x02, 0x03, 0x04])
    return _make_frame(7, PGN_UNKNOWN_PROPRIETARY, source_address, payload, timestamp)


@dataclass
class MultiNodeScenario:
    """One reporting cycle across several distinct node types: engine+GPS+
    tank (via DF140SmokeTestScenario), an electrical/battery monitor, and an
    unknown-vendor node. Covers Requirement 17.2's "engine, fuel tank,
    GPS/navigation, electrical, and unknown/proprietary node types"."""

    df140: DF140SmokeTestScenario = field(default_factory=DF140SmokeTestScenario)
    battery_voltage: float = 12.6
    battery_current: float = -4.2

    def frames(self) -> list[CanFrame]:
        frames = list(self.df140.frames())
        t = frames[-1].capture_time + 0.01 if frames else 0.0
        frames.append(
            _battery_status_frame(
                self.battery_voltage, self.battery_current, _ELECTRICAL_SOURCE_ADDRESS, t
            )
        )
        t += 0.01
        frames.append(_unknown_vendor_frame(_UNKNOWN_VENDOR_SOURCE_ADDRESS, t))
        return frames


def main() -> int:
    scenario = MultiNodeScenario()
    frames = scenario.frames()
    for f in frames:
        print(
            f"pgn={f.pgn} sa={f.source_address} dlc={f.dlc} "
            f"payload={f.payload.hex()} t={f.capture_time:.3f}"
        )
    print(f"# {len(frames)} frames emitted across {len({f.source_address for f in frames})} nodes")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
