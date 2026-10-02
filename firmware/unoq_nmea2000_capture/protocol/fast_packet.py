"""Fast-packet reassembler for multi-frame NMEA 2000 PGNs.

NMEA 2000 "fast-packet" PGNs (e.g. 127489, 129029) span multiple 8-byte CAN
frames. Data byte 0 of every frame is a control byte: its HIGH bits are a
rolling sequence id (identifying which multi-frame message the frame belongs
to) and its LOW bits are the frame counter within that sequence (0..N):

    sequence_id   = (control_byte >> 5) & 0x07   # top 3 bits
    frame_counter = control_byte & 0x1F          # bottom 5 bits

Frame 0 (frame_counter == 0): data byte 1 is the total message length in
bytes, and the remaining 6 bytes are payload. Subsequent frames: the 7 bytes
after the control byte are all payload.

Reassembly is keyed by (pgn, source_address, sequence_id). The frame counter
must be contiguous (0, 1, 2, ...). A buffer is DISCARDED without emitting any
partial or wrong message when a counter is missing/non-contiguous OR when more
than 5 seconds have elapsed since the first frame's timestamp. A discarded
buffer never emits; a fresh correct sequence afterwards still reassembles.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

# Fast-packet transport is decided by PGN metadata, NOT by inspecting the
# frame -- a raw CAN frame carries no "I am fast-packet" flag (Property 34).
_FASTPACKET_PGNS: frozenset[int] = frozenset(
    {
        126996,  # Product Information
        127489,  # Engine Parameters, Dynamic
        126464,  # PGN List (Tx/Rx)
        126998,  # Configuration Information
        127497,  # Trip Parameters, Engine
        127498,  # Engine Parameters, Static
        129029,  # GNSS Position Data
        129038,  # AIS Class A Position Report
        129039,  # AIS Class B Position Report
        129284,  # Navigation Data
        129285,  # Navigation Route/WP Information
        130577,  # Direction Data
    }
)

#: Buffers that neither complete nor receive a next frame within this many
#: seconds of their first frame are discarded (design.md: 5-second gap).
REASSEMBLY_TIMEOUT_S: float = 5.0


def is_fastpacket_pgn(pgn: int) -> bool:
    """Decide fast-packet routing from PGN metadata, never from frame content.

    Single-frame PGNs return False and must bypass the reassembler entirely;
    known fast-packet PGNs return True regardless of any frame's byte content
    (Property 34).
    """
    return pgn in _FASTPACKET_PGNS


@dataclass
class _InProgress:
    sequence_id: int
    total_length: int
    next_frame_index: int
    start_time: float
    data: bytearray = field(default_factory=bytearray)


class FastPacketReassembler:
    """Stateful reassembler for a single (source_address, pgn) stream the
    caller tracks. Feed frames in arrival order; get back the full payload
    once all frames of a sequence have arrived, else None.

    The sequence id in the control byte distinguishes concurrent messages on
    the same stream, so a new frame 0 (any sequence id) always starts a fresh
    buffer. Non-contiguous counters, a sequence-id change mid-sequence, or a
    gap exceeding 5s since the first frame all discard the buffer WITHOUT
    emitting any partial/wrong message.
    """

    def __init__(self) -> None:
        self._in_progress: _InProgress | None = None

    def feed(self, frame_data: bytes, timestamp: float | None = None) -> bytes | None:
        """Feed one fast-packet CAN frame's payload bytes.

        timestamp is monotonic seconds for this frame; it defaults to
        time.monotonic() so existing callers that pass no timestamp keep
        working, while tests can inject synthetic times to exercise the
        5-second discard. Returns the reassembled payload once complete,
        else None.
        """
        if not frame_data:
            return None
        if timestamp is None:
            timestamp = time.monotonic()

        # Expire a stale in-progress buffer before processing this frame.
        if (
            self._in_progress is not None
            and timestamp - self._in_progress.start_time > REASSEMBLY_TIMEOUT_S
        ):
            self._in_progress = None

        control_byte = frame_data[0]
        sequence_id = (control_byte >> 5) & 0x07
        frame_counter = control_byte & 0x1F

        if frame_counter == 0:
            total_length = frame_data[1] if len(frame_data) > 1 else 0
            payload = bytes(frame_data[2:])
            self._in_progress = _InProgress(
                sequence_id=sequence_id,
                total_length=total_length,
                next_frame_index=1,
                start_time=timestamp,
                data=bytearray(payload),
            )
            return self._maybe_complete()

        state = self._in_progress
        if (
            state is None
            or state.sequence_id != sequence_id
            or state.next_frame_index != frame_counter
        ):
            # Missing/out-of-order counter or unknown sequence: discard the
            # buffer, emit nothing, let subsequent frames start afresh.
            self._in_progress = None
            return None

        state.data.extend(frame_data[1:])
        state.next_frame_index += 1
        return self._maybe_complete()

    def _maybe_complete(self) -> bytes | None:
        state = self._in_progress
        if state is None:
            return None
        if len(state.data) >= state.total_length:
            result = bytes(state.data[: state.total_length])
            self._in_progress = None
            return result
        return None


class KeyedFastPacketReassembler:
    """Routes frames to a per-(pgn, source_address, sequence_id) reassembler.

    The Ingestion pipeline hands every fast-packet frame here with its parsed
    pgn + source_address and the frame's capture time; this class owns the
    buffer dict the design keys by (pgn, source, sequence id). Each distinct
    sequence id gets its own underlying FastPacketReassembler so concurrent
    multi-frame messages on the same stream do not clobber each other.

    ponytail: open buffers live in a plain dict.
    Ceiling: unbounded if a flood of distinct (pgn, source, seq) never completes.
    Upgrade path: the 5s timeout already bounds each buffer's lifetime; add an
    LRU cap on open buffers if burst memory becomes an issue.
    """

    def __init__(self) -> None:
        self._streams: dict[tuple[int, int], FastPacketReassembler] = {}

    def feed(
        self,
        pgn: int,
        source_address: int,
        frame_data: bytes,
        timestamp: float | None = None,
    ) -> bytes | None:
        """Feed one fast-packet frame; returns the payload once complete."""
        key = (pgn, source_address)
        reassembler = self._streams.get(key)
        if reassembler is None:
            reassembler = FastPacketReassembler()
            self._streams[key] = reassembler
        return reassembler.feed(frame_data, timestamp=timestamp)
