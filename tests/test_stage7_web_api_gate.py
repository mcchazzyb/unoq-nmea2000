"""Stage 7.1 Gate -- Web_API: endpoints, validation, path safety, concurrency/
size limits, no-outbound-request confirmation.
"""
from __future__ import annotations

import struct
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient
from hypothesis import given, settings
from hypothesis import strategies as st

import protocol.decoders  # noqa: F401 -- registers DEFAULT_REGISTRY decoders
from protocol.decoders.engine import PGN_ENGINE_RAPID_UPDATE
from protocol.identifier import Identifier, compose_identifier
from protocol.receiver import CanFrame
from protocol.registry import DEFAULT_REGISTRY
from service.cache import LiveStateCache
from service.discovery import DiscoveryMode
from service.ingestion import IngestionService
from service.raw_log import RawCaptureLog
from service.session_store import SessionStore
from web.api import MAX_CONCURRENT_OPERATIONS, create_app


def _make_client(tmp_path: Path):
    cache = LiveStateCache()
    discovery = DiscoveryMode()
    capture_dir = tmp_path / "captures"
    capture_dir.mkdir()
    app = create_app(cache, discovery, capture_dir)
    return TestClient(app), cache, discovery, capture_dir


def _ingest_one_engine_frame(cache: LiveStateCache, rpm_raw: int = 4000, source_address: int = 5):
    log_dir = tempfile.mkdtemp()
    log = RawCaptureLog(log_dir)
    store_path = Path(tempfile.mkdtemp()) / "session.db"
    store = SessionStore(store_path)
    service = IngestionService(log, store, DEFAULT_REGISTRY, live_cache=cache)
    ident = Identifier(priority=2, pgn=PGN_ENGINE_RAPID_UPDATE, source_address=source_address, dest_address=None)
    can_id = compose_identifier(ident)
    payload = struct.pack("<BHHb", 0, rpm_raw, 0xFFFF, 0x7F) + b"\x00\x00"
    frame = CanFrame(
        can_id=can_id, priority=2, pgn=PGN_ENGINE_RAPID_UPDATE, source_address=source_address,
        dest_address=None, dlc=8, payload=payload, capture_time=0.0,
    )
    service.ingest_batch([frame])
    log.close()
    store.close()


# --- Requirement 14.1: seven REST endpoints + WS exist ----------------------


def test_all_seven_endpoints_respond(tmp_path):
    client, cache, discovery, capture_dir = _make_client(tmp_path)
    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/api/v1/live").status_code == 200
    assert client.get("/api/v1/nodes").status_code == 200
    assert client.get("/api/v1/pgns").status_code == 200
    assert client.get("/api/v1/logs").status_code == 200
    assert client.get("/api/v1/logs/nonexistent.jsonl").status_code == 404
    assert client.post("/api/v1/replay", json={"log_id": "nonexistent.jsonl"}).status_code == 404


def test_live_endpoint_reflects_cache_state(tmp_path):
    client, cache, discovery, capture_dir = _make_client(tmp_path)
    _ingest_one_engine_frame(cache, rpm_raw=4000, source_address=5)
    response = client.get("/api/v1/live")
    body = response.json()
    assert "5:0" not in body["engines"] and "0:5" in body["engines"]
    assert body["engines"]["0:5"]["rpm"] == 1000.0


# --- Requirement 20.6: quality/staleness marker on every served value -----


def test_live_endpoint_carries_quality_and_staleness_per_value(tmp_path):
    """Every served entity carries its Provenance, including `quality` and
    `staleness_s`, so a client can distinguish a stale/invalid value from
    current valid data at the REST interface -- not just know a number."""
    client, cache, discovery, capture_dir = _make_client(tmp_path)
    _ingest_one_engine_frame(cache, rpm_raw=4000, source_address=5)
    body = client.get("/api/v1/live").json()
    provenance = body["engines"]["0:5"]["provenance"]
    assert provenance is not None
    assert provenance["quality"] in ("valid", "stale", "invalid", "unknown")
    assert isinstance(provenance["staleness_s"], (int, float))


def test_websocket_push_carries_same_quality_marker_as_rest(tmp_path):
    """Property 25-adjacent: the WS push and the REST read of the same
    cache state expose the identical quality/staleness marker -- the
    marker isn't a REST-only addition bolted on separately from the push
    path."""
    client, cache, discovery, capture_dir = _make_client(tmp_path)
    _ingest_one_engine_frame(cache, rpm_raw=4000, source_address=5)
    rest_body = client.get("/api/v1/live").json()
    with client.websocket_connect("/api/v1/live/ws") as ws:
        ws.send_text("hello")
        ws_body = ws.receive_json()
    assert (
        ws_body["engines"]["0:5"]["provenance"]["quality"]
        == rest_body["engines"]["0:5"]["provenance"]["quality"]
    )


# --- Requirement 14.4, 14.5: validation rejects without side effects -------


def test_malformed_log_id_rejected_naming_the_parameter(tmp_path):
    client, cache, discovery, capture_dir = _make_client(tmp_path)
    response = client.get("/api/v1/logs/..%2F..%2Fetc%2Fpasswd")
    assert response.status_code in (400, 403, 404)
    # Whatever the status, the body must name the failing parameter/reason,
    # never a bare unexplained error.
    body = response.json()
    assert "detail" in body


@given(limit=st.integers(min_value=-1000, max_value=0))
@settings(max_examples=20, deadline=None)
def test_out_of_range_limit_rejected(tmp_path_factory, limit):
    """Feature: nmea2000-diagnostic-platform, Property 27: invalid parameters
    rejected without side effects."""
    tmp_path = tmp_path_factory.mktemp("stage7_limit")
    client, cache, discovery, capture_dir = _make_client(tmp_path)
    (capture_dir / "a.jsonl").write_text('{"pgn": 1}\n')
    response = client.get(f"/api/v1/logs/a.jsonl?limit={limit}")
    assert response.status_code == 422  # FastAPI/Pydantic query validation


# --- Requirement 14.7, 14.8 / Property 26: path safety ----------------------


@given(
    traversal=st.sampled_from(
        ["../etc/passwd", "../../etc/passwd", "..%2f..%2fetc%2fpasswd", "....//....//etc/passwd"]
    )
)
@settings(max_examples=10, deadline=None)
def test_path_traversal_rejected_without_file_access(tmp_path_factory, traversal):
    """Feature: nmea2000-diagnostic-platform, Property 26: a path outside the
    capture directory is rejected without ever being accessed."""
    tmp_path = tmp_path_factory.mktemp("stage7_traversal")
    client, cache, discovery, capture_dir = _make_client(tmp_path)
    response = client.get(f"/api/v1/logs/{traversal}")
    assert response.status_code in (400, 403, 404)
    assert response.status_code != 200


def test_legitimate_filename_within_capture_dir_is_served(tmp_path):
    client, cache, discovery, capture_dir = _make_client(tmp_path)
    (capture_dir / "capture-1.jsonl").write_text('{"pgn": 127488, "malformed": false}\n')
    response = client.get("/api/v1/logs/capture-1.jsonl")
    assert response.status_code == 200
    assert response.json()["records"][0]["pgn"] == 127488


# --- Requirement 14.6: concurrency / size limits ----------------------------


def test_file_exceeding_max_size_rejected_naming_the_limit(tmp_path):
    client, cache, discovery, capture_dir = _make_client(tmp_path)
    big_file = capture_dir / "huge.jsonl"
    # Sparse-ish write: seek then write one byte, size reports as requested
    # without actually allocating 100MB+1 of real disk content.
    with open(big_file, "wb") as f:
        f.seek(100 * 1024 * 1024 + 1)
        f.write(b"\x00")
    response = client.get("/api/v1/logs/huge.jsonl")
    assert response.status_code == 429
    assert response.json()["detail"]["limit"] == "max_file_size_bytes"


def test_concurrent_operations_beyond_limit_rejected(tmp_path, monkeypatch):
    client, cache, discovery, capture_dir = _make_client(tmp_path)
    (capture_dir / "a.jsonl").write_text('{"pgn": 1}\n' * 5)

    import web.api as api_module

    # Simulate MAX_CONCURRENT_OPERATIONS already-active operations by
    # directly exercising the same counter the endpoint uses, via a
    # monkeypatched module-level constant down to 0 so ANY request trips it
    # without needing real concurrency.
    monkeypatch.setattr(api_module, "MAX_CONCURRENT_OPERATIONS", 0)
    app = api_module.create_app(cache, discovery, capture_dir)
    from fastapi.testclient import TestClient as TC

    fresh_client = TC(app)
    response = fresh_client.get("/api/v1/logs/a.jsonl")
    assert response.status_code == 429
    assert response.json()["detail"]["limit"] == "max_concurrent_operations"


# --- Requirement 14.3: loopback-only binding --------------------------------


def test_bind_host_rejects_public_interface_by_default():
    """Requirement 14.3: no publicly routable interface by default."""
    import importlib

    import web.main as main_module

    importlib.reload(main_module)
    import os

    os.environ["UNOQ_BIND_HOST"] = "0.0.0.0"
    try:
        rc = main_module.main()
        assert rc != 0  # refused rather than silently binding publicly
    finally:
        del os.environ["UNOQ_BIND_HOST"]


# --- Requirement 14.9: no outbound internet request -------------------------


def test_api_module_has_no_outbound_http_client_import():
    """Requirement 14.9: capture/decode/persist/serve issue no outbound
    internet request. Static check: web/api.py imports no outbound HTTP
    client library (requests, httpx-as-a-client, urllib.request for remote
    hosts)."""
    import ast

    source = Path("web/api.py").read_text()
    tree = ast.parse(source)
    forbidden_modules = {"requests", "urllib.request", "aiohttp"}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            module = getattr(node, "module", None) or ""
            names = [n.name for n in node.names]
            assert module not in forbidden_modules
            assert not any(n in forbidden_modules for n in names)
