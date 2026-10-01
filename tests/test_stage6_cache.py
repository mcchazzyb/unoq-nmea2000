"""Stage 6.1 -- Live_State_Cache: instance-keyed storage, stale flag,
single-cache cross-transport consistency (Requirement 6.6).

Full Stage 6 Stage Gate (6.1-6.5 combined, with BLE wire format and
BLEPublisher) lands once 6.2-6.5 are built; this file covers 6.1 alone.
"""
from __future__ import annotations

from protocol.domain import UNSET
from protocol.decoders.engine import PGN_ENGINE_RAPID_UPDATE, decode_engine_rapid_update
from protocol.decoders.tank import PGN_FLUID_LEVEL, decode_fluid_level
from protocol.decoders.navigation import decode_cog_sog_rapid_update, decode_position_rapid_update
from service.cache import LiveStateCache


def test_distinct_engine_instances_keyed_separately():
    """Property 15 (cache half): two distinct (instance, source_address)
    engines never collide in the cache."""
    cache = LiveStateCache()
    import struct

    data0 = struct.pack("<BHHb", 0, 4000, 0xFFFF, 0x7F) + b"\x00\x00"
    data1 = struct.pack("<BHHb", 1, 5000, 0xFFFF, 0x7F) + b"\x00\x00"
    cache.update(decode_engine_rapid_update(source_address=10, data=data0))
    cache.update(decode_engine_rapid_update(source_address=10, data=data1))
    cache.update(decode_engine_rapid_update(source_address=11, data=data0))

    assert len(cache.vessel.engines) == 3
    assert cache.get_engine(0, 10).rpm == 1000.0
    assert cache.get_engine(1, 10).rpm == 1250.0
    assert cache.get_engine(0, 11).rpm == 1000.0


def test_tank_update_replaces_same_instance_key():
    import struct

    cache = LiveStateCache()
    data_full = struct.pack("<Bh", 0, 25000) + struct.pack("<I", 2000) + b"\x00"
    data_half = struct.pack("<Bh", 0, 12500) + struct.pack("<I", 2000) + b"\x00"
    cache.update(decode_fluid_level(source_address=7, data=data_full))
    cache.update(decode_fluid_level(source_address=7, data=data_half))
    assert len(cache.vessel.tanks) == 1
    assert round(cache.get_tank(0, 7).level_percent, 1) == 50.0


def test_navigation_merges_position_and_cog_sog_without_clobbering():
    """129025 (position) and 129026 (cog/sog) both populate NavigationState;
    the cache must merge rather than let the second decode erase the first's
    fields (Requirement 9.2 -- fields populated only where present)."""
    cache = LiveStateCache()
    pos_data = __import__("struct").pack("<ii", 508198000, -19870000)
    cache.update(decode_position_rapid_update(source_address=20, data=pos_data))
    assert cache.vessel.navigation.position != UNSET

    cog_sog_data = bytes([0, 0]) + __import__("struct").pack("<HH", 31416, 500)
    cache.update(decode_cog_sog_rapid_update(source_address=20, data=cog_sog_data))

    # Position from the FIRST decode must still be present after the SECOND.
    assert cache.vessel.navigation.position != UNSET
    assert cache.vessel.navigation.cog != UNSET
    assert cache.vessel.navigation.sog != UNSET


def test_mark_stale_flags_without_clearing_contents():
    import struct

    cache = LiveStateCache()
    data = struct.pack("<BHHb", 0, 4000, 0xFFFF, 0x7F) + b"\x00\x00"
    cache.update(decode_engine_rapid_update(source_address=10, data=data))
    assert cache.is_stale() is False

    cache.mark_stale()
    assert cache.is_stale() is True
    assert cache.get_engine(0, 10) is not None  # contents retained, only flagged

    # A fresh update implies capture resumed -- stale flag clears.
    cache.update(decode_engine_rapid_update(source_address=10, data=data))
    assert cache.is_stale() is False


def test_ingestion_service_wires_updates_into_cache():
    """Requirement 6.6: the Ingestion_Service's fan-out updates THIS cache,
    not a transport-private copy."""
    import tempfile
    from pathlib import Path

    import protocol.decoders  # noqa: F401 -- registers DEFAULT_REGISTRY decoders
    from protocol.registry import DEFAULT_REGISTRY
    from protocol.identifier import compose_identifier, Identifier
    from protocol.receiver import CanFrame
    from service.ingestion import IngestionService
    from service.raw_log import RawCaptureLog
    from service.session_store import SessionStore

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        log = RawCaptureLog(tmp_path / "raw")
        store = SessionStore(tmp_path / "session.db")
        cache = LiveStateCache()
        service = IngestionService(log, store, DEFAULT_REGISTRY, live_cache=cache)

        import struct

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

        assert cache.get_engine(0, 5) is not None
        assert cache.get_engine(0, 5).rpm == 1000.0
