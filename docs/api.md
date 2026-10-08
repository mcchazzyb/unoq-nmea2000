# Web_API reference

FastAPI app (`web/api.py`), built by `create_app(cache, discovery,
capture_dir, device_info=None)`. Binds to loopback/configured local
interfaces **only** by default (Requirement 14.3) — see `docs/security.md`.
No endpoint here issues an outbound internet request (Requirement 14.9).

Run it with `make run-api` (serves at `http://127.0.0.1:8000` by default).
Interactive OpenAPI docs are available at `/docs` (FastAPI's built-in Swagger
UI) once the server is running — this file is a prose companion to that
live, auto-generated reference, not a replacement for it.

## REST endpoints

### `GET /api/v1/health`

```json
{"status": "ok", "can_connected": true, "capture_halted": false, "receiver_ok_age_s": 0.4, "device_info": {}}
```

`receiver_ok_age_s` is the seconds since the receiver (on the UNO Q, the Bridge to the MCU)
last answered a health call, or `null` before it first does. It keeps advancing normally on a
quiet bus and only grows when the Bridge stops answering, so it is the signal
`deploy/unoq_mcu_recover.py` uses to decide the MCU needs a reset.

### `GET /api/v1/live`

The current `VesselState` snapshot. Every entity is serialized with its
full `Provenance` (quality, staleness, origin — Requirement 20.6):

```json
{
  "engines": {"0:1": {"rpm": 1500.0, "...": "...", "provenance": {"quality": "valid", "staleness_s": 0.1, "...": "..."}}},
  "tanks": {"0:2": {"...": "..."}},
  "electrical": null,
  "navigation": null,
  "stale": false
}
```

Keys in `engines`/`tanks` are `"{instance}:{source_address}"` — the same
instance-keying rule as the domain model itself (steering 07). A field with
no decoded value (`UNSET` internally) is serialized as JSON `null`.

`/live` (and the WebSocket) also carry a `fuel` object: `remaining_litres`,
`burn_lph`, `endurance_h`, `range_nm`, `needs_capacity` (fuel tank keys that
have a level but no size), `oldest_input_updated_at`, and `derivation`. Each
figure is `null` unless every input it needs is known (never a partial sum).
It is a derived value: all fuel tanks (tank type 0) divided by all engines'
`fuel_rate`, and range uses speed over ground.

### `GET /api/v1/settings` and `PUT /api/v1/settings/tanks`

Owner-set fuel tank sizes in litres, saved on the board (`settings.json` in
the capture directory, so they survive restarts). Keys are
`"<instance>:<source_address>"`, the same key `/live` uses. `PUT` replaces the
whole map: `{"capacities": {"0:42": 200}}`. A size must be above 0 and at most
20000; a bad value returns 400 naming `capacities`. A set size overrides the
sender's own capacity and the tank's litres remaining is marked `derived`
(`... * configured_capacity_litres (owner setting)`).

### `GET /api/v1/nodes`

Discovered node inventory from `DiscoveryMode`, split into `known` (NAME
resolved) and `unknown` (provisional) lists. See `docs/nmea-discovery.md`.

### `GET /api/v1/pgns`

```json
{"observed_pgns": [59904, 60928, 126996, 127488, 127505]}
```

Every PGN observed across every discovered node, sorted ascending.

### `GET /api/v1/logs`

```json
{"logs": ["capture-1234567890.jsonl", "capture-1234567891.jsonl"]}
```

Bare filenames of capture files in the configured capture directory.

### `GET /api/v1/logs/{log_id}?limit=1000`

Reads up to `limit` (1-100000, default 1000) records from `log_id`.

```json
{"log_id": "capture-1234567890.jsonl", "records": [...], "corrupt_lines": 0}
```

`corrupt_lines` counts any line that failed to parse as JSON — quarantined,
not raised (Requirement 20.1, 20.3; see `docs/log-format.md`).

**Validation (Requirement 14.4, 14.5):**
- `log_id` containing `/` or `\` → `400 {"parameter": "log_id", "error": "must be a bare filename"}`
- `log_id` resolving outside the capture directory (traversal, symlink
  escape) → `403` **without ever opening the file** (`Path.resolve()` +
  `is_relative_to()`, Property 26)
- Nonexistent file → `404`
- File exceeding 100 MB → `429 {"limit": "max_file_size_bytes", ...}`
- 10 concurrent log/replay operations already in flight → `429 {"limit": "max_concurrent_operations", ...}`

### `POST /api/v1/replay`

```json
// request
{"log_id": "capture-1234567890.jsonl"}
// response
{"accepted": true, "log_id": "capture-1234567890.jsonl"}
```

Same path/size/concurrency validation as `GET /logs/{id}`. This endpoint
validates and accepts the request; actually feeding the replay into a live
`IngestionService` is wired by whatever process owns that service instance
(this module adds no ingestion state of its own).

## WebSocket: `GET /api/v1/live/ws`

Pushes the same shape as `GET /api/v1/live`, on its own schedule — **not**
gated on receiving further client messages after the initial connection
(a client sends at most one hello frame on open; the server does not block
waiting for a second one). Capped at one push per client per 250ms
(Requirement 14.2, Property 22). A client should expect a push at roughly
that cadence whenever the underlying cache changes, and should treat a
longer-than-expected gap as its own staleness signal independent of the
`stale` field in the payload (which reflects `CAPTURE_HALTED_STORAGE_FULL`,
not transport health).

## Error shape

Every rejected request returns a JSON body naming the specific failed
parameter or exceeded limit — never a bare 500 or an unqualified 400
(Requirement 14.5, 14.6):

```json
{"detail": {"parameter": "log_id", "error": "must be a bare filename"}}
{"detail": {"limit": "max_concurrent_operations", "error": "10 operations already active"}}
```
