"""Stage 7.5 Gate -- Web_API, Web_UI, Correlation_Mode.

Most of this stage's properties are already proven by the per-task gate
files (test_stage7_web_api_gate.py, test_stage7_web_ui_gate.py,
test_stage7_correlation_gate.py). This file covers what those don't:
Property 16 FULL (identical values across all three transports -- BLE,
REST, WebSocket -- not just REST/WS parity) and Property 25 at the BLE leg
of that same comparison, as an `integration` test exercising the full
mock-to-domain pipeline end to end (Requirement 19.5).
"""
from __future__ import annotations

import struct
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import protocol.decoders  # noqa: F401 -- registers DEFAULT_REGISTRY decoders
from ble.encoding import decode_envelope
from ble.publisher import MockBLEPublisher
from protocol.decoders.engine import PGN_ENGINE_RAPID_UPDATE
from protocol.identifier import Identifier, compose_identifier
from protocol.receiver import CanFrame, CanHealth
from protocol.registry import DEFAULT_REGISTRY
from service.cache import LiveStateCache
from service.discovery import DiscoveryMode
from service.ingestion import IngestionService
from service.raw_log import RawCaptureLog
from service.session_store import SessionStore
from web.api import create_app


@pytest.mark.integration
def test_same_domain_fixture_identical_values_across_ble_rest_ws(tmp_path):
    """Feature: nmea2000-diagnostic-platform, Property 16: for any domain
    fixture, the field values observed over BLE, REST, and WebSocket are
    identical. Also covers Property 25 at the BLE leg: the quality marker
    present on the REST/WS value is present and equal on the BLE value too.
    """
    cache = LiveStateCache()
    discovery = DiscoveryMode()
    capture_dir = tmp_path / "captures"
    capture_dir.mkdir()

    log = RawCaptureLog(tempfile.mkdtemp())
    store = SessionStore(Path(tempfile.mkdtemp()) / "session.db")
    service = IngestionService(log, store, DEFAULT_REGISTRY, live_cache=cache)

    rpm_raw = 4000
    source_address = 5
    ident = Identifier(
        priority=2, pgn=PGN_ENGINE_RAPID_UPDATE, source_address=source_address, dest_address=None
    )
    can_id = compose_identifier(ident)
    payload = struct.pack("<BHHb", 0, rpm_raw, 0xFFFF, 0x7F) + b"\x00\x00"
    frame = CanFrame(
        can_id=can_id, priority=2, pgn=PGN_ENGINE_RAPID_UPDATE, source_address=source_address,
        dest_address=None, dlc=8, payload=payload, capture_time=0.0,
    )
    service.ingest_batch([frame])
    log.close()
    store.close()

    # --- REST ---
    app = create_app(cache, discovery, capture_dir)
    client = TestClient(app)
    rest_body = client.get("/api/v1/live").json()
    rest_engine = rest_body["engines"]["0:5"]
    assert rest_engine["rpm"] == 1000.0  # rpm_raw * 0.25

    # --- WebSocket ---
    with client.websocket_connect("/api/v1/live/ws") as ws:
        ws.send_text("hello")
        ws_body = ws.receive_json()
    ws_engine = ws_body["engines"]["0:5"]
    assert ws_engine["rpm"] == rest_engine["rpm"]
    assert ws_engine["provenance"]["quality"] == rest_engine["provenance"]["quality"]

    # --- BLE ---
    publisher = MockBLEPublisher(require_pairing=False)
    publisher.update_can_health(
        CanHealth(
            connected=True, fps=10.0, dropped_frames=0, error_count=0,
            controller_state="normal", termination_on=True,
        )
    )
    publisher.publish(cache.snapshot())
    encoded = publisher.read_characteristic("live_engine")
    assert encoded is not None
    decoded = decode_envelope(encoded)
    ble_entity = decoded.entities[0]
    rpm_field = next(f for f in ble_entity.fields if f.field_id == 1)  # ENGINE_FIELD_IDS["rpm"]

    assert rpm_field.value == pytest.approx(rest_engine["rpm"])
    assert ble_entity.quality == rest_engine["provenance"]["quality"]
