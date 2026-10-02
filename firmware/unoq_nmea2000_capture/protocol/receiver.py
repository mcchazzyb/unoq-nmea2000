"""Hardware abstraction boundary — the ONLY contract Layer 2/3 knows about a
CAN source (design.md: Hardware Abstraction Boundary).

``ICanReceiver`` is a structural (Protocol) type that exposes ONLY ``frames()``
and ``health()``. There is deliberately no ``send()`` / ``transmit()`` /
``write()`` method anywhere in the type. That absence is the structural
enforcement of receive-only safety: no code above the boundary can transmit,
because the interface offers no way to (Requirement 1.1, 1.4, 20.5; Property 28).
Do not add a transmit-capable method here.

The Simulator and fixture replay implement this SAME interface, so Layers 2/3
cannot tell real hardware from a host simulator (Requirements 17.9, 23, 24.6).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Protocol, runtime_checkable


@dataclass(frozen=True)
class CanFrame:
    """A single raw CAN frame as received, before any decode. Immutable so the
    raw bytes can never be mutated downstream (Requirement 4.6)."""

    can_id: int  # raw 29-bit identifier
    priority: int
    pgn: int
    source_address: int
    dest_address: int | None  # None for PDU2 broadcast
    dlc: int
    payload: bytes  # exact raw bytes, never mutated
    capture_time: float  # monotonic seconds, always present
    wall_clock_time: float | None = None  # UTC epoch seconds where an RTC is available
    malformed: bool = False


@dataclass(frozen=True)
class CanHealth:
    """CAN link health snapshot. ``termination_on`` MUST be False in the MVP —
    the device is a drop node and never enables its switchable termination
    (Requirement 1.5, 24.4)."""

    connected: bool
    fps: float
    dropped_frames: int
    error_count: int
    controller_state: str  # e.g. "listen-only"
    termination_on: bool  # MUST be False in MVP


@runtime_checkable
class ICanReceiver(Protocol):
    """Structural contract for anything that supplies CAN frames.

    Exposes only a receive path: ``frames()`` and ``health()``. There is NO
    ``send()``/``transmit()``/``write()`` method, by design (Requirement 1,
    Property 28). Do not add one.
    """

    def frames(self) -> Iterator[CanFrame]:
        """Yield frames/batches as they become available."""
        ...

    def health(self) -> CanHealth:
        """Return the current CAN link health."""
        ...
