"""Stage 7.2 Gate -- Web_UI: single-page served with no build step, live
dashboard reflects cache changes, WebSocket pushes without waiting on
further client messages (the reactivity fix for fast-changing values like
RPM), discovery empty-state, zero third-party assets.
"""
from __future__ import annotations

import re
import struct
from pathlib import Path

from fastapi.testclient import TestClient

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
from web.api import create_app

INDEX_PATH = Path("web/static/index.html")


def test_entity_no_data_after_constant_is_defined():
    """Regression test for a real UX bug found via an actual deployed
    dashboard: dials flickered value/UNSET/value because the backend's
    EngineState cache flat-replaced (fixed separately in
    service/cache.py's _merge_engine). This constant is the complementary
    fix the user asked for -- a sender that genuinely stops reporting for
    10s should show no-data, not a frozen last value with no signal."""
    html = INDEX_PATH.read_text()
    assert "const ENTITY_NO_DATA_AFTER_MS = 10000;" in html


def test_entity_no_data_check_wired_into_both_render_paths():
    """Both the legacy fixed panels (renderEntityFields) and the Stage 7.6
    selectable dashboard cards read the SAME Live_State_Cache data
    (Requirement 6.6/20.6 consistency) -- the no-data check must be wired
    into both, not just one, or the two views would disagree about
    whether a sender has gone quiet."""
    html = INDEX_PATH.read_text()
    assert html.count("entityNoData") >= 2
    assert "const entityAgeMs = entity.updated_at" in html
    assert "const entityAgeMs = sourceEntity.updated_at" in html


def _make_client(tmp_path: Path):
    cache = LiveStateCache()
    discovery = DiscoveryMode()
    capture_dir = tmp_path / "captures"
    capture_dir.mkdir()
    app = create_app(cache, discovery, capture_dir)
    return TestClient(app), cache, discovery


def _ingest_engine_frame(cache: LiveStateCache, tmp_path: Path, rpm_raw: int, source_address: int = 5):
    log = RawCaptureLog(tmp_path / f"raw-{rpm_raw}")
    store = SessionStore(tmp_path / f"session-{rpm_raw}.db")
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


# --- Requirement 15.1: single self-contained page, no build step ----------


def test_index_served_and_is_self_contained(tmp_path):
    client, cache, discovery = _make_client(tmp_path)
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "<script" in body  # inline script, no separate JS file reference
    assert "<style" in body  # inline style, no separate CSS file reference


def test_no_build_step_artifacts_referenced():
    """Requirement 15.1: no build step -- the page references no bundler
    output (no /dist, /build, /_next, webpack chunk names, etc.)."""
    html = INDEX_PATH.read_text()
    forbidden_patterns = [r"/dist/", r"/_next/", r"webpackChunk", r"\.chunk\.js"]
    for pattern in forbidden_patterns:
        assert not re.search(pattern, html)


def test_no_third_party_asset_or_script_reference():
    """Requirement 15.6: zero outbound requests after initial page load --
    statically, this means no third-party script/font/stylesheet src/href
    anywhere in the page (a CDN reference would violate this at runtime)."""
    html = INDEX_PATH.read_text()
    for tag_match in re.finditer(r'<(script|link)\b[^>]*\b(src|href)="([^"]+)"', html):
        url = tag_match.group(3)
        assert not url.startswith("http"), f"third-party reference found: {url}"
        assert not url.startswith("//"), f"protocol-relative third-party reference: {url}"


# --- Requirement 15.4: UI reflects a cache change ---------------------------


def test_live_endpoint_reflects_engine_update(tmp_path):
    client, cache, discovery = _make_client(tmp_path)
    _ingest_engine_frame(cache, tmp_path, rpm_raw=4000, source_address=5)
    response = client.get("/api/v1/live")
    assert response.json()["engines"]["0:5"]["rpm"] == 1000.0


# --- Requirement 15.9: discovery empty-state --------------------------------


def test_nodes_endpoint_empty_when_no_discovery_yet(tmp_path):
    client, cache, discovery = _make_client(tmp_path)
    response = client.get("/api/v1/nodes")
    body = response.json()
    assert body["known"] == []
    assert body["unknown"] == []


def test_index_page_renders_empty_state_markup_for_discovery():
    """Requirement 15.9: an empty-state indicator exists in markup for the
    discovery view (rendered client-side when both known/unknown are empty)."""
    html = INDEX_PATH.read_text()
    assert 'id="discovery-empty"' in html
    assert "No nodes discovered yet" in html


# --- Reactivity: WebSocket pushes without waiting on further client text --


def test_websocket_pushes_live_data_without_requiring_a_second_client_message():
    """The steering fix: the server-side push loop must not block waiting
    for another client message between pushes -- it has its own schedule,
    capped at MIN_PUSH_INTERVAL_S, so fast-changing values (RPM) update
    reactively rather than stalling after the client's one hello frame."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        client, cache, discovery = _make_client(tmp_path)
        _ingest_engine_frame(cache, tmp_path, rpm_raw=4000, source_address=5)

        with client.websocket_connect("/api/v1/live/ws") as websocket:
            websocket.send_text("hello")
            first = websocket.receive_json()
            assert first["engines"]["0:5"]["rpm"] == 1000.0

            # Change the cache value WITHOUT the client sending anything
            # else -- a second push must still arrive on the server's own
            # schedule.
            _ingest_engine_frame(cache, tmp_path, rpm_raw=8000, source_address=5)
            second = websocket.receive_json()
            assert second["engines"]["0:5"]["rpm"] == 2000.0


# --- Requirement 15.3: responsive, no horizontal scroll (static check) -----


def test_layout_uses_fluid_grid_not_fixed_pixel_widths():
    """Requirement 15.3: no horizontal scroll from 320px to 1920px -- the
    main layout grid must be fluid (auto-fit/minmax or flex-wrap), not a
    fixed total pixel width."""
    html = INDEX_PATH.read_text()
    assert "auto-fit" in html or "flex-wrap" in html
    assert "min-width: 320px" in html  # explicit floor matching the spec's lower bound
