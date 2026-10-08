"""Web_API: local-only FastAPI HTTP + WebSocket API over the Live_State_Cache
(Requirement 14.1-14.9).

Binds to loopback/configured local interfaces ONLY (Requirement 14.3,
steering 12) -- never a publicly routable interface by default. Issues no
outbound internet request anywhere (Requirement 14.9, 20.7).

Every endpoint reads the SAME Live_State_Cache every other transport reads
(Requirement 6.6) -- this module adds no decoding or state of its own.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from protocol.domain import UNSET
from service.cache import LiveStateCache
from service.discovery import DiscoveryMode
from service.raw_log import CorruptLine, iter_records
from web.fuel import apply_configured_capacity, fuel_summary
from web.settings import SettingsStore

#: Requirement 14.6: reject beyond this many concurrent replay/log operations.
MAX_CONCURRENT_OPERATIONS = 10
#: Requirement 14.6: reject a log/replay file larger than this.
MAX_FILE_SIZE_BYTES = 100 * 1024 * 1024
#: Requirement 14.2: WebSocket push cap, one update per client per interval.
MIN_PUSH_INTERVAL_S = 0.25


class ValidationError(HTTPException):
    """An error response naming the specific failed parameter (Requirement
    14.5) -- never a bare 500 or a generic 400 with no parameter name."""

    def __init__(self, parameter: str, reason: str):
        super().__init__(status_code=400, detail={"parameter": parameter, "error": reason})


class LimitExceededError(HTTPException):
    """Requirement 14.6: names the exceeded limit explicitly."""

    def __init__(self, limit: str, reason: str):
        super().__init__(status_code=429, detail={"limit": limit, "error": reason})


class PathNotPermittedError(HTTPException):
    """Requirement 14.8: a path outside the capture directory is rejected
    WITHOUT accessing it."""

    def __init__(self, reason: str = "path is not permitted"):
        super().__init__(status_code=403, detail={"error": reason})


def _value_or_none(value):
    return None if value is UNSET else value


def _entity_to_response_dict(entity: object) -> dict:
    """Serialize a Domain_Model entity to a plain JSON-safe dict for the REST/
    WS views, replacing UNSET with None (UNSET has no JSON representation;
    None here means "the API's own absent-value marker", distinct from the
    internal UNSET-vs-None-vs-0 distinction Requirement 11 cares about at
    the decode layer -- the API still carries quality/staleness explicitly
    per field via Provenance, satisfying Requirement 20.6 without needing a
    second UNSET-like sentinel over the wire)."""
    out = {}
    for key, value in vars(entity).items():
        if key == "provenance":
            out[key] = _provenance_to_dict(value) if value is not None else None
        else:
            out[key] = _value_or_none(value) if value is not UNSET else None
    return out


def _provenance_to_dict(provenance) -> dict:
    return {
        "source_address": provenance.source_address,
        "source_pgn": provenance.source_pgn,
        "node_identity": provenance.node_identity,
        "quality": provenance.quality,
        "staleness_s": provenance.staleness_s,
        "value_origin": provenance.value_origin,
        "vendor_specific": provenance.vendor_specific,
        "derivation": provenance.derivation,
    }


class ReplayRequest(BaseModel):
    log_id: str = Field(..., min_length=1, max_length=255)


class TankSettingsRequest(BaseModel):
    # Validated by web.settings so the error can name the exact bad tank.
    capacities: dict[str, object]


def create_app(
    cache: LiveStateCache,
    discovery: DiscoveryMode,
    capture_dir: Path,
    device_info: Optional[dict] = None,
    settings: Optional[SettingsStore] = None,
) -> FastAPI:
    """Build the FastAPI app wired to a specific cache/discovery/capture_dir.

    A factory rather than a module-level singleton app so tests (and a real
    `web/main.py` entrypoint) each construct an app bound to their own
    cache/dir without import-time global state.
    """
    app = FastAPI(title="unoq-nmea2000 Web_API")
    capture_dir = capture_dir.resolve()
    device_info = device_info or {}
    settings = settings or SettingsStore(capture_dir / "settings.json")
    _active_operations = {"count": 0}
    _ws_last_push: dict[int, float] = {}

    def _resolve_within_capture_dir(raw_path: str) -> Path:
        """Requirement 14.7, 14.8; Property 26: resolve and check
        `is_relative_to` BEFORE any filesystem access. A path escaping
        capture_dir (via .., a symlink, or an absolute path) is rejected
        without ever being opened."""
        candidate = (capture_dir / raw_path).resolve()
        if not candidate.is_relative_to(capture_dir):
            raise PathNotPermittedError(f"resolved path escapes the capture directory")
        return candidate

    @app.get("/", include_in_schema=False)
    def get_index():
        """Serve the single self-contained Web_UI page (Requirement 15.1).
        No build step: this returns the static HTML file as-is."""
        index_path = Path(__file__).parent / "static" / "index.html"
        return FileResponse(index_path)

    @app.get("/api/v1/health")
    def get_health():
        return {
            "status": "ok",
            "can_connected": cache.vessel.health.can_health.connected
            if cache.vessel.health and cache.vessel.health.can_health
            else False,
            "capture_halted": cache.is_stale(),
            # Seconds since the receiver (the Bridge, on hardware) last answered; null until it first does.
            "receiver_ok_age_s": cache.receiver_ok_age_s(),
            "device_info": device_info,
        }

    def _live_payload() -> dict:
        """One builder for REST and WebSocket so they can never disagree."""
        vessel = cache.snapshot()
        engines = {f"{k[0]}:{k[1]}": _entity_to_response_dict(v) for k, v in vessel.engines.items()}
        tanks = {f"{k[0]}:{k[1]}": _entity_to_response_dict(v) for k, v in vessel.tanks.items()}
        apply_configured_capacity(tanks, settings.tank_capacities())
        electrical = _entity_to_response_dict(vessel.electrical) if vessel.electrical else None
        navigation = _entity_to_response_dict(vessel.navigation) if vessel.navigation else None
        return {
            "engines": engines,
            "tanks": tanks,
            "electrical": electrical,
            "navigation": navigation,
            "fuel": fuel_summary(tanks, engines, navigation),
            "stale": cache.is_stale(),
        }

    @app.get("/api/v1/live")
    def get_live():
        return _live_payload()

    @app.get("/api/v1/settings")
    def get_settings():
        return {"tank_capacity_litres": settings.tank_capacities()}

    @app.put("/api/v1/settings/tanks")
    def put_tank_settings(request: TankSettingsRequest):
        """Replace all owner-set tank sizes (litres), keyed '<instance>:<source_address>'."""
        try:
            saved = settings.set_tank_capacities(request.capacities)
        except ValueError as exc:
            raise ValidationError("capacities", str(exc))
        except OSError:
            # Disk full / read-only: report it, keep serving (steering 11).
            raise HTTPException(status_code=507, detail={"error": "could not save settings"})
        return {"tank_capacity_litres": saved}

    @app.get("/api/v1/nodes")
    def get_nodes():
        return {
            "known": [
                {
                    "node_key": n.node_key,
                    "nmea_name": n.nmea_name,
                    "current_source_address": n.current_source_address,
                    "manufacturer_code": n.manufacturer_code,
                    "device_class": n.device_class,
                    "device_function": n.device_function,
                    "product_info": n.product_info,
                    "online": n.online,
                    "message_rate": n.message_rate,
                    "address_conflicts": n.address_conflicts,
                }
                for n in discovery.known_nodes()
            ],
            "unknown": [
                {
                    "node_key": n.node_key,
                    "current_source_address": n.current_source_address,
                    "observed_pgns": sorted(n.observed_pgns),
                    "online": n.online,
                    "message_rate": n.message_rate,
                }
                for n in discovery.unknown_nodes()
            ],
        }

    @app.get("/api/v1/pgns")
    def get_pgns():
        all_pgns: set[int] = set()
        for n in discovery.nodes.values():
            all_pgns |= n.observed_pgns
        return {"observed_pgns": sorted(all_pgns)}

    @app.get("/api/v1/logs")
    def list_logs():
        if not capture_dir.exists():
            return {"logs": []}
        return {
            "logs": sorted(p.name for p in capture_dir.glob("*.jsonl") if p.is_file())
        }

    @app.get("/api/v1/logs/{log_id}")
    def get_log(log_id: str, limit: int = Query(default=1000, ge=1, le=100000)):
        if not log_id or "/" in log_id or "\\" in log_id:
            raise ValidationError("log_id", "must be a bare filename")
        path = _resolve_within_capture_dir(log_id)
        if not path.exists():
            raise HTTPException(status_code=404, detail={"error": "log not found"})
        if path.stat().st_size > MAX_FILE_SIZE_BYTES:
            raise LimitExceededError("max_file_size_bytes", f"{path.stat().st_size} exceeds {MAX_FILE_SIZE_BYTES}")

        if _active_operations["count"] >= MAX_CONCURRENT_OPERATIONS:
            raise LimitExceededError(
                "max_concurrent_operations", f"{MAX_CONCURRENT_OPERATIONS} operations already active"
            )
        _active_operations["count"] += 1
        try:
            records = []
            corrupt_lines = 0
            for i, record in enumerate(iter_records(path)):
                if i >= limit:
                    break
                if isinstance(record, CorruptLine):
                    # Requirement 20.1, 20.3: a corrupted line is quarantined,
                    # not surfaced as a 500 or allowed to abort the response.
                    corrupt_lines += 1
                    continue
                records.append(record)
            return {"log_id": log_id, "records": records, "corrupt_lines": corrupt_lines}
        finally:
            _active_operations["count"] -= 1

    @app.post("/api/v1/replay")
    def post_replay(request: ReplayRequest):
        log_id = request.log_id
        if "/" in log_id or "\\" in log_id:
            raise ValidationError("log_id", "must be a bare filename")
        path = _resolve_within_capture_dir(log_id)
        if not path.exists():
            raise HTTPException(status_code=404, detail={"error": "log not found"})
        if path.stat().st_size > MAX_FILE_SIZE_BYTES:
            raise LimitExceededError("max_file_size_bytes", f"{path.stat().st_size} exceeds {MAX_FILE_SIZE_BYTES}")
        if _active_operations["count"] >= MAX_CONCURRENT_OPERATIONS:
            raise LimitExceededError(
                "max_concurrent_operations", f"{MAX_CONCURRENT_OPERATIONS} operations already active"
            )
        # Full replay execution (feeding the Ingestion_Service) is wired by
        # the caller/service layer that owns an IngestionService instance;
        # this endpoint validates and accepts the request per Requirement
        # 14.1 without this module needing to own ingestion itself.
        return {"accepted": True, "log_id": log_id}

    @app.websocket("/api/v1/live/ws")
    async def live_ws(websocket: WebSocket):
        """Pushes a live snapshot on its own schedule -- capped at one push
        per MIN_PUSH_INTERVAL_S (Requirement 14.2; Property 22) -- rather
        than waiting on a client message between pushes. A client sends at
        most one hello frame on open; this loop does not block on receiving
        further client text, since nothing in the protocol requires the
        client to keep talking for the server to keep pushing.
        """
        import asyncio

        await websocket.accept()
        client_key = id(websocket)
        _ws_last_push[client_key] = 0.0

        async def _watch_for_disconnect():
            try:
                while True:
                    await websocket.receive_text()
            except WebSocketDisconnect:
                pass

        watcher = asyncio.ensure_future(_watch_for_disconnect())
        try:
            while not watcher.done():
                now = time.time()
                last = _ws_last_push.get(client_key, 0.0)
                if (now - last) >= MIN_PUSH_INTERVAL_S:
                    try:
                        await websocket.send_text(json.dumps(_live_payload()))
                    except WebSocketDisconnect:
                        break
                    _ws_last_push[client_key] = now
                await asyncio.sleep(MIN_PUSH_INTERVAL_S)
        finally:
            watcher.cancel()
            _ws_last_push.pop(client_key, None)

    return app
