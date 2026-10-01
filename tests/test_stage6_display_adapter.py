"""Stage 6.2 -- DisplayAdapter extensibility stub (Requirement 18.1)."""
from __future__ import annotations

import struct
import tempfile
from pathlib import Path

from protocol.decoders.engine import PGN_ENGINE_RAPID_UPDATE
from protocol.display_adapter import DisplayAdapter, NoOpDisplayAdapter
from protocol.identifier import Identifier, compose_identifier
from protocol.receiver import CanFrame
from protocol.registry import DEFAULT_REGISTRY
import protocol.decoders  # noqa: F401 -- registers decoders
from service.cache import LiveStateCache
from service.ingestion import IngestionService
from service.raw_log import RawCaptureLog
from service.session_store import SessionStore


def test_noop_display_adapter_satisfies_protocol():
    adapter: DisplayAdapter = NoOpDisplayAdapter()
    assert hasattr(adapter, "on_update")
    assert hasattr(adapter, "on_snapshot")


def test_display_adapter_wired_into_fan_out_receives_every_decoded_value():
    """Requirement 18.1: a DisplayAdapter wired into the fan-out receives the
    same decoded events the Live_State_Cache does, consuming only
    Domain_Model types (no CAN frame reaches it)."""
    received = []

    class RecordingAdapter:
        def on_update(self, result):
            received.append(result)

        def on_snapshot(self, vessel):
            pass

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        log = RawCaptureLog(tmp_path / "raw")
        store = SessionStore(tmp_path / "session.db")
        cache = LiveStateCache()
        adapter = RecordingAdapter()
        service = IngestionService(
            log, store, DEFAULT_REGISTRY, live_cache=cache, display_adapter=adapter
        )

        ident = Identifier(
            priority=2, pgn=PGN_ENGINE_RAPID_UPDATE, source_address=5, dest_address=None
        )
        can_id = compose_identifier(ident)
        payload = struct.pack("<BHHb", 0, 4000, 0xFFFF, 0x7F) + b"\x00\x00"
        frame = CanFrame(
            can_id=can_id,
            priority=2,
            pgn=PGN_ENGINE_RAPID_UPDATE,
            source_address=5,
            dest_address=None,
            dlc=8,
            payload=payload,
            capture_time=0.0,
        )
        service.ingest_batch([frame])
        log.close()
        store.close()

        assert len(received) == 1
        assert received[0].entity.rpm == 1000.0
        # Nothing transport-specific crossed the boundary -- the adapter
        # only ever saw a DecodeResult carrying a Domain_Model entity.
        from protocol.domain import EngineState

        assert isinstance(received[0].entity, EngineState)


def test_default_display_adapter_is_noop_when_not_provided():
    """IngestionService works with no display_adapter supplied -- the
    fan-out call is a no-op, never an error."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        log = RawCaptureLog(tmp_path / "raw")
        store = SessionStore(tmp_path / "session.db")
        service = IngestionService(log, store, DEFAULT_REGISTRY)

        ident = Identifier(
            priority=2, pgn=PGN_ENGINE_RAPID_UPDATE, source_address=5, dest_address=None
        )
        can_id = compose_identifier(ident)
        payload = struct.pack("<BHHb", 0, 4000, 0xFFFF, 0x7F) + b"\x00\x00"
        frame = CanFrame(
            can_id=can_id,
            priority=2,
            pgn=PGN_ENGINE_RAPID_UPDATE,
            source_address=5,
            dest_address=None,
            dlc=8,
            payload=payload,
            capture_time=0.0,
        )
        stats = service.ingest_batch([frame])  # must not raise
        log.close()
        store.close()
        assert stats.decoded == 1
