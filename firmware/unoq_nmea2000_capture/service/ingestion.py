"""Ingestion_Service pipeline: `ingest_batch()` implements the exact stage
order from design.md -- persist raw first, parse, classify, reassemble fast
packets, decode, update cache, feed discovery, persist decoded
(Requirement 2.2, 2.3, 3.1-3.7, 12.1).

Stage ordering is unconditional (steering 02): raw_log.append() is ALWAYS
stage 1 for every frame -- malformed, unknown-PGN, or decoder-raising alike --
and nothing above this module may reorder it.

Live_State_Cache (Stage 6) and Discovery_Mode (Stage 5) are not yet built;
this module depends on them only through narrow callback Protocols so Stage 4
does not have to build ahead of its own stage. `IngestionService` works with
no-op defaults for both, and Stage 5/6 wire in the real implementations
without changing this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Protocol

from protocol.fast_packet import KeyedFastPacketReassembler, is_fastpacket_pgn
from protocol.identifier import parse_identifier
from protocol.receiver import CanFrame
from protocol.registry import DecodeResult, DecoderRegistry
from service.raw_log import RawCaptureLog
from service.session_store import SessionStore

#: Below this many free bytes, halt persistence AND decoding
#: (Requirement 12.6, 12.7; Property 35).
STORAGE_HALT_THRESHOLD_BYTES = 50 * 1024 * 1024


class LiveStateCacheProtocol(Protocol):
    def update(self, result: DecodeResult) -> None: ...
    def mark_stale(self) -> None: ...


class DiscoveryModeProtocol(Protocol):
    def observe(self, frame: CanFrame, decoded: DecodeResult | None) -> None: ...


class DisplayAdapterProtocol(Protocol):
    def on_update(self, result: DecodeResult) -> None: ...


class _NoOpCache:
    def update(self, result: DecodeResult) -> None:  # pragma: no cover - trivial
        pass

    def mark_stale(self) -> None:  # pragma: no cover - trivial
        pass


class _NoOpDisplayAdapter:
    def on_update(self, result: DecodeResult) -> None:  # pragma: no cover - trivial
        pass


class _NoOpDiscovery:
    def observe(self, frame: CanFrame, decoded: DecodeResult | None) -> None:  # pragma: no cover
        pass


@dataclass
class IngestionStats:
    """Per-batch counters, useful for tests and operator visibility. Not a
    domain entity -- purely diagnostic."""

    frames_in: int = 0
    persisted: int = 0
    decode_failures: int = 0
    unknown_pgns: int = 0
    decoded: int = 0
    awaiting_fastpacket: int = 0
    halted_storage_full: int = 0


FreeBytesProbe = Callable[[], int]


class IngestionService:
    """Owns one Raw_Capture_Log + one Session_Store + one DecoderRegistry and
    runs frames through the pipeline in the exact design.md order.
    """

    def __init__(
        self,
        raw_log: RawCaptureLog,
        session_store: SessionStore,
        registry: DecoderRegistry,
        live_cache: LiveStateCacheProtocol | None = None,
        discovery: DiscoveryModeProtocol | None = None,
        display_adapter: DisplayAdapterProtocol | None = None,
        free_bytes_probe: FreeBytesProbe | None = None,
    ) -> None:
        self.raw_log = raw_log
        self.session_store = session_store
        self.registry = registry
        self.live_cache: LiveStateCacheProtocol = live_cache or _NoOpCache()
        self.discovery: DiscoveryModeProtocol = discovery or _NoOpDiscovery()
        self.display_adapter: DisplayAdapterProtocol = display_adapter or _NoOpDisplayAdapter()
        self._free_bytes_probe = free_bytes_probe
        self._fastpacket = KeyedFastPacketReassembler()
        self.halted = False

    def _free_bytes(self) -> int | None:
        if self._free_bytes_probe is None:
            return None
        return self._free_bytes_probe()

    def ingest_batch(self, batch: Iterable[CanFrame]) -> IngestionStats:
        stats = IngestionStats()
        for frame in batch:
            stats.frames_in += 1

            free = self._free_bytes()
            if free is not None and free < STORAGE_HALT_THRESHOLD_BYTES:
                # CAPTURE_HALTED_STORAGE_FULL: stop persisting AND stop
                # decoding this and every subsequent frame in the batch
                # (Requirement 12.6, 12.7; Property 35). Serve the last state,
                # marked stale, rather than disconnecting any live subscriber
                # (documented judgment call, docs/architecture.md).
                self.halted = True
                self.live_cache.mark_stale()
                stats.halted_storage_full += 1
                continue
            self.halted = False

            # 1. Persist raw BEFORE any decode/filter/classification
            #    (Requirement 3.1, 12.1; Property 1) -- unconditional, even
            #    for a frame already marked malformed by the parser layer.
            persisted = self.raw_log.append(frame)
            if persisted:
                stats.persisted += 1
            else:
                # Write failure already recorded on raw_log; prior data is
                # untouched (Requirement 12.8). Do not decode a frame whose
                # raw source failed to persist (capture-first integrity).
                continue

            if frame.malformed:
                # Malformed frames are persisted (above) and the pipeline
                # moves on without decoding (Property 4).
                continue

            # 2-3. Normalize + classify. parse_identifier re-derives pgn from
            # can_id; frame.pgn (set by the hardware layer) is trusted as-is
            # since Stage 2's parser is the single source of truth for this.
            parsed = parse_identifier(frame.can_id)
            pgn = parsed.pgn

            if is_fastpacket_pgn(pgn):
                reassembled = self._fastpacket.feed(
                    pgn, frame.source_address, frame.payload, timestamp=frame.capture_time
                )
                if reassembled is None:
                    stats.awaiting_fastpacket += 1
                    continue
                payload = reassembled
            else:
                payload = frame.payload

            # 4. Decode -- a raising decoder is caught here; the raw frame is
            # already safe, so this never drops data (Requirement 2.3, 3.7).
            try:
                result = self.registry.decode(pgn, frame.source_address, payload)
            except Exception:
                self.raw_log.mark_decode_failure(frame)
                stats.decode_failures += 1
                continue

            if not result.known:
                # Unknown PGN: retained, visible, never decoded
                # (Requirement 5.4; Property 3).
                self.discovery.observe(frame, decoded=None)
                stats.unknown_pgns += 1
                continue

            # 5. Update Live_State_Cache, feed Discovery_Mode, persist decoded.
            self.live_cache.update(result)
            self.display_adapter.on_update(result)
            self.discovery.observe(frame, decoded=result)
            entity_kind = type(result.entity).__name__ if result.entity is not None else "none"
            self.session_store.persist_decoded(
                pgn=pgn,
                source_address=frame.source_address,
                entity_kind=entity_kind,
                entity=result.entity,
            )
            stats.decoded += 1

        return stats
