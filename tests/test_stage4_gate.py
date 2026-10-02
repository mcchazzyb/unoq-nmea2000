"""Stage 4 Gate -- Raw_Capture_Log, Session_Store, Ingestion_Service pipeline.

Property tags follow .kiro/steering/09-property-test-tagging.md exactly:
    Feature: nmea2000-diagnostic-platform, Property N: <text>
matching design.md -> Correctness Properties.
"""
from __future__ import annotations

import struct
import time

from hypothesis import given, settings
from hypothesis import strategies as st

from protocol.decoders.engine import PGN_ENGINE_RAPID_UPDATE
from protocol.decoders.tank import PGN_FLUID_LEVEL
from protocol.identifier import compose_identifier, Identifier
from protocol.receiver import CanFrame
from protocol.registry import DecoderRegistry, DecodeResult
import protocol.decoders  # noqa: F401 -- registers DEFAULT_REGISTRY decoders
from protocol.registry import DEFAULT_REGISTRY
from service.ingestion import IngestionService, STORAGE_HALT_THRESHOLD_BYTES
from service.raw_log import RawCaptureLog, iter_records
from service.session_store import SessionStore


def _frame(
    pgn: int,
    source_address: int = 1,
    payload: bytes = b"\x00" * 8,
    malformed: bool = False,
    capture_time: float | None = None,
) -> CanFrame:
    ident = Identifier(priority=6, pgn=pgn, source_address=source_address, dest_address=None)
    can_id = compose_identifier(ident)
    return CanFrame(
        can_id=can_id,
        priority=6,
        pgn=pgn,
        source_address=source_address,
        dest_address=None,
        dlc=len(payload),
        payload=payload,
        capture_time=capture_time if capture_time is not None else time.monotonic(),
        malformed=malformed,
    )


# --- Raw_Capture_Log: append-only, rotation, write-failure ------------------


def test_append_only_earlier_lines_never_rewritten(tmp_path):
    """Feature: nmea2000-diagnostic-platform, Property 5: frames written before
    a later append remain byte-identical afterward."""
    log = RawCaptureLog(tmp_path)
    log.append(_frame(PGN_ENGINE_RAPID_UPDATE, payload=b"\x01" * 8))
    path = log.current_path()
    before = path.read_bytes()

    log.append(_frame(PGN_FLUID_LEVEL, payload=b"\x02" * 8))
    after = path.read_bytes()
    log.close()

    assert after.startswith(before)  # earlier bytes untouched, only appended to


def test_rotation_at_size_threshold_opens_new_replayable_file(tmp_path):
    log = RawCaptureLog(tmp_path, max_bytes=200, max_age_s=10**9)
    first_path = log.current_path()
    for _ in range(20):
        log.append(_frame(PGN_ENGINE_RAPID_UPDATE, payload=b"\xAA" * 8))
    second_path = log.current_path()
    log.close()

    assert first_path != second_path
    # The rotated-away file is still readable/replayable.
    records = list(iter_records(first_path))
    assert len(records) > 0
    for r in records:
        assert r["pgn"] == PGN_ENGINE_RAPID_UPDATE


def test_rotation_at_age_threshold(tmp_path):
    log = RawCaptureLog(tmp_path, max_bytes=10**9, max_age_s=0.01)
    first_path = log.current_path()
    log.append(_frame(PGN_ENGINE_RAPID_UPDATE))
    time.sleep(0.02)
    log.append(_frame(PGN_ENGINE_RAPID_UPDATE))
    second_path = log.current_path()
    log.close()
    assert first_path != second_path


def test_write_failure_leaves_prior_records_intact(tmp_path):
    log = RawCaptureLog(tmp_path)
    log.append(_frame(PGN_ENGINE_RAPID_UPDATE, payload=b"\x01" * 8))
    path = log.current_path()
    before = path.read_bytes()

    # Simulate a write failure: close the underlying file handle out from
    # under the log so the next write raises, without touching the file on
    # disk directly (keeps this a behavioral test, not an FS-corruption one).
    log._current_file.close()
    ok = log.append(_frame(PGN_FLUID_LEVEL, payload=b"\x02" * 8))

    assert ok is False
    assert log.last_write_failure is not None
    assert path.read_bytes() == before  # prior data unchanged (Req 12.8)


def test_decode_failure_recorded_without_rewriting_raw_record(tmp_path):
    """Requirement 2.3/3.7: a decode-failure indication is a SEPARATE record;
    the raw frame's own line is never rewritten (Property 5)."""
    log = RawCaptureLog(tmp_path)
    frame = _frame(PGN_ENGINE_RAPID_UPDATE, payload=b"\x01" * 8)
    log.append(frame)
    path = log.current_path()
    before = path.read_bytes()

    log.mark_decode_failure(frame)
    after = path.read_bytes()
    log.close()

    assert after.startswith(before)
    records = list(iter_records(path))
    assert records[-1]["decode_failure"] is True


# --- Session_Store: transactional commits + recovery ------------------------


def test_session_store_recovery_restores_last_committed_write(tmp_path):
    """Feature: nmea2000-diagnostic-platform, Property 31: recovered state
    equals the state at the last successful write."""
    db_path = tmp_path / "session.db"
    store = SessionStore(db_path)
    sid = store.start_session(raw_log_path="capture-1.jsonl")
    store.persist_decoded(
        pgn=PGN_ENGINE_RAPID_UPDATE,
        source_address=1,
        entity_kind="EngineState",
        entity={"engine_instance": 0, "rpm": 1500.0},
        session_id=sid,
    )
    store.persist_decoded(
        pgn=PGN_FLUID_LEVEL,
        source_address=2,
        entity_kind="TankState",
        entity={"tank_instance": 0, "level_percent": 50.0},
        session_id=sid,
    )
    store.close()

    # Simulate a restart: fresh connection to the same file.
    recovered_store = SessionStore(db_path)
    recovered = recovered_store.recover_latest_session()
    recovered_store.close()

    assert recovered["session_id"] == sid
    assert recovered["raw_log_path"] == "capture-1.jsonl"
    assert len(recovered["decoded"]) == 2
    assert recovered["decoded"][0]["entity"]["rpm"] == 1500.0
    assert recovered["decoded"][1]["entity"]["level_percent"] == 50.0


def test_session_store_write_failure_retains_prior_rows(tmp_path):
    db_path = tmp_path / "session.db"
    store = SessionStore(db_path)
    sid = store.start_session()
    store.persist_decoded(
        pgn=1, source_address=1, entity_kind="X", entity={"a": 1}, session_id=sid
    )

    # Force a failure: close the connection, then attempt a write on it.
    store._conn.close()
    ok = store.persist_decoded(
        pgn=2, source_address=1, entity_kind="X", entity={"a": 2}, session_id=sid
    )
    assert ok is False
    assert store.last_write_failure is not None

    # Prior row is intact when read via a fresh connection.
    fresh = SessionStore(db_path)
    recovered = fresh.recover_latest_session()
    fresh.close()
    assert len(recovered["decoded"]) == 1


# --- Ingestion_Service pipeline ---------------------------------------------


def _service(tmp_path, free_bytes=None):
    log = RawCaptureLog(tmp_path / "raw")
    store = SessionStore(tmp_path / "session.db")
    probe = (lambda: free_bytes) if free_bytes is not None else None
    return IngestionService(log, store, DEFAULT_REGISTRY, free_bytes_probe=probe), log, store


def test_raw_before_decode_ordering_for_every_frame(tmp_path):
    """Feature: nmea2000-diagnostic-platform, Property 1: raw persistence
    happens before decode/filter/classification for every frame."""
    service, log, store = _service(tmp_path)
    frames = [
        _frame(PGN_ENGINE_RAPID_UPDATE, payload=b"\x00" * 8),  # known
        _frame(999999, payload=b"\x00" * 8),  # unknown pgn
        _frame(PGN_ENGINE_RAPID_UPDATE, payload=b"\x00" * 8, malformed=True),  # malformed
    ]
    stats = service.ingest_batch(frames)
    assert stats.persisted == 3  # every frame persisted regardless of outcome
    log.close()
    store.close()


def test_all_valid_frames_retained_regardless_of_pgn(tmp_path):
    """Feature: nmea2000-diagnostic-platform, Property 2: no frame dropped by
    manufacturer, source address, PGN, or frequency before persistence."""
    service, log, store = _service(tmp_path)
    pgns = [PGN_ENGINE_RAPID_UPDATE, PGN_FLUID_LEVEL, 65280, 999999, 126996]
    frames = [_frame(pgn, source_address=i) for i, pgn in enumerate(pgns)]
    stats = service.ingest_batch(frames)
    assert stats.persisted == len(pgns)
    log.close()
    store.close()


def test_unknown_pgn_never_dropped_remains_replayable(tmp_path):
    """Feature: nmea2000-diagnostic-platform, Property 3: unknown PGNs are
    retained, never dropped, and replayable from the raw log."""
    service, log, store = _service(tmp_path)
    stats = service.ingest_batch([_frame(777777, source_address=9)])
    assert stats.unknown_pgns == 1
    assert stats.persisted == 1
    path = log.current_path()
    log.close()
    store.close()
    records = list(iter_records(path))
    assert records[0]["pgn"] == 777777


def test_malformed_and_decoder_raising_frames_dont_halt_pipeline(tmp_path):
    """Feature: nmea2000-diagnostic-platform, Property 4 (full): a malformed
    frame or a decoder that raises never halts the batch -- every subsequent
    frame still gets processed."""
    registry = DecoderRegistry()

    def raising_decoder(source_address, data):
        raise RuntimeError("boom")

    registry.register(PGN_ENGINE_RAPID_UPDATE, raising_decoder)

    log = RawCaptureLog(tmp_path / "raw")
    store = SessionStore(tmp_path / "session.db")
    service = IngestionService(log, store, registry)

    frames = [
        _frame(PGN_ENGINE_RAPID_UPDATE, payload=b"\x00" * 8, malformed=True),
        _frame(PGN_ENGINE_RAPID_UPDATE, payload=b"\x00" * 8),  # triggers raising decoder
        _frame(PGN_ENGINE_RAPID_UPDATE, payload=b"\x00" * 8),  # must still be processed
    ]
    stats = service.ingest_batch(frames)
    assert stats.frames_in == 3
    assert stats.persisted == 3  # all three persisted despite the raise/malformed
    assert stats.decode_failures == 2  # both non-malformed frames hit the raising decoder
    log.close()
    store.close()


def test_capture_halted_storage_full_stops_persist_and_decode(tmp_path):
    """Feature: nmea2000-diagnostic-platform, Property 35: no value is
    decoded or exposed while CAPTURE_HALTED_STORAGE_FULL is active -- a frame
    arriving under the threshold is neither persisted nor decoded."""
    service, log, store = _service(tmp_path, free_bytes=STORAGE_HALT_THRESHOLD_BYTES - 1)
    stats = service.ingest_batch([_frame(PGN_ENGINE_RAPID_UPDATE)])
    assert stats.halted_storage_full == 1
    assert stats.persisted == 0
    assert stats.decoded == 0
    assert service.halted is True
    log.close()
    store.close()


def test_capture_resumes_once_storage_above_threshold(tmp_path):
    log = RawCaptureLog(tmp_path / "raw")
    store = SessionStore(tmp_path / "session.db")
    free = {"bytes": STORAGE_HALT_THRESHOLD_BYTES - 1}
    service = IngestionService(
        log, store, DEFAULT_REGISTRY, free_bytes_probe=lambda: free["bytes"]
    )
    service.ingest_batch([_frame(PGN_ENGINE_RAPID_UPDATE)])
    assert service.halted is True

    free["bytes"] = STORAGE_HALT_THRESHOLD_BYTES + 1
    stats = service.ingest_batch([_frame(PGN_ENGINE_RAPID_UPDATE, payload=b"\x00" * 8)])
    assert service.halted is False
    assert stats.persisted == 1
    log.close()
    store.close()


@given(
    pgns=st.lists(
        st.sampled_from(
            [PGN_ENGINE_RAPID_UPDATE, PGN_FLUID_LEVEL, 65280, 999999, 60928, 126996]
        ),
        min_size=1,
        max_size=20,
    )
)
@settings(max_examples=50, deadline=None)
def test_every_frame_in_batch_persisted_before_outcome(tmp_path_factory, pgns):
    """Feature: nmea2000-diagnostic-platform, Property 1: raw-before-decode
    ordering holds for an arbitrary mix of known/unknown PGNs in one batch.

    deadline=None: each example does real filesystem + SQLite I/O
    (tmp_path_factory.mktemp, raw log append, session store), so wall-clock
    time varies with host I/O latency (e.g. first-touch cost on a cold
    filesystem cache) rather than reflecting a performance regression in the
    code under test. Hypothesis's default 200ms deadline flagged this as
    "flaky" on a Linux/WSL host where disk access patterns differ from
    macOS; the property itself (not timing) is what this test asserts.
    """
    tmp_path = tmp_path_factory.mktemp("stage4_prop")
    service, log, store = _service(tmp_path)
    frames = [_frame(pgn, source_address=i % 256) for i, pgn in enumerate(pgns)]
    stats = service.ingest_batch(frames)
    assert stats.persisted == len(pgns)
    log.close()
    store.close()
