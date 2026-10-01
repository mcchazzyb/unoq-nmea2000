# Architecture decisions

Documented judgment calls made during implementation where the spec
(requirements.md / design.md) left a detail unspecified. Each entry names the
requirement/stage, the gap, the decision, and why.

## CAPTURE_HALTED_STORAGE_FULL: subscriber behavior during the transition

**Stage:** 4 (Ingestion_Service)
**Requirements:** 12.6, 12.7, 20.4

design.md specifies the steady-state behavior of `CAPTURE_HALTED_STORAGE_FULL`
precisely: persistence and decoding both stop, the last persisted/live state
is served marked **stale**, and nothing is decoded or exposed without a
persisted raw source frame (Property 35). It does not specify what happens to
an **already-connected** WebSocket/BLE subscriber at the moment the halt
begins.

**Decision:** an in-flight subscriber is **not disconnected**. It continues
receiving the frozen last-known state, with that state marked stale, exactly
as a subscriber connecting *during* the halted period would see. The halt
transition is invisible at the connection-lifecycle level — only the
staleness flag on served values changes.

**Why:** disconnecting a subscriber on every storage-pressure event would be
a worse failure mode than serving stale data honestly flagged as such — the
platform's own fail-safe principle (steering 11) is "no single fault
terminates the pipeline", and a subscriber connection is part of that
pipeline. Reconnection churn under sustained low-disk conditions would also
make the UI/BLE client flap for no diagnostic benefit, since the client gets
the same stale-flagged data either way.

`IngestionService` in `service/ingestion.py` implements this by calling
`live_cache.mark_stale()` on halt rather than closing any transport-level
connection; Stage 6/7 (Live_State_Cache, Web_API, BLE_Publisher) must preserve
this behavior — a halt must never be implemented as a forced disconnect.

## quantity_remaining_litres derivation formula

**Stage:** 3 (Tank decoder)
**Requirements:** 8.6; design.md Data Models (`TankState.quantity_remaining_litres`)

requirements.md (8.6) and design.md originally stated the derivation
literally as `level_percent * capacity_litres`, with `level_percent` on its
native 0..100 scale. Taken literally, this was 100x larger than a physically
sensible litres value (e.g. 50% of a 200 L tank derived to 10,000, not 100 L).

**Decision (2026-10-01, confirmed by user):** corrected to
`(level_percent / 100) * capacity_litres`. The user confirmed the real-world
convention: a tank sender's capacity is configured once in litres (e.g. 90 L,
120 L) and `level_percent` is reported as a percentage of that configured
capacity — matching the NMEA 2000 PGN 127505 field definition (`Level` is a
percentage field; `Capacity` is the tank's own volume). `requirements.md`,
`design.md`, `protocol/decoders/tank.py`, `protocol/domain.py`, and the
Stage 3/6 pinning tests were all updated together to the corrected formula.

## BLE wire format: custom envelope, not CBOR

**Stage:** 6 (`ble/encoding.py`)
**Requirements:** 27.1

design.md names "CBOR, or a documented compact binary event envelope" as the
two acceptable options for Requirement 27.1. No CBOR library was already a
project dependency, and the actual encoding need (a handful of fixed-shape
scalar records) is fully covered by stdlib `struct` with no meaningful loss
of compactness versus CBOR's general-purpose overhead.

**Decision:** implemented the documented-custom-envelope option. No new
dependency added (ponytail steering: don't add a dependency if it can be
avoided). The format is versioned (`SCHEMA_VERSION` embedded in every
payload) and documented in `ble/encoding.py`'s module docstring; a future
stage needing CBOR's self-describing flexibility (e.g. a field set that
varies per client) can still add it without breaking the versioning scheme,
since clients already gate on `schema_version` before trusting field
semantics.

**Known limit, by design, not a bug:** the fragmentation sequence number is
7 bits (`fragment()` in `ble/encoding.py`), addressing at most 128 ordered
segments per envelope. A payload needing more segments at a given MTU raises
`ValueError` rather than silently wrapping the sequence number and corrupting
reassembly. Real BLE ATT MTUs (23-517 bytes) keep realistic envelopes well
under this ceiling; the limit is in `test_stage6_ble_encoding.py` as
`test_fragment_rejects_payload_exceeding_128_segment_ceiling`.

## BLE_Publisher: MockBLEPublisher is the Stage 6 deliverable, not a real radio

**Stage:** 6 (`ble/publisher.py`)
**Requirements:** 13.10, 23.3, 23.4

Requirement 13.10 asks for a mock in-memory `BLEPublisher` implementation
specifically so the BLE schema is testable without hardware. A real BlueZ
(Linux-only) publisher is explicitly Stage 10 hardware work per the task
plan and steering 05 (BlueZ is the one OS-specific dependency, confined to
`ble/`, never built ahead of its own stage).

**Decision:** `MockBLEPublisher` is the ONLY `BLEPublisher` implementation
built in Stage 6. It satisfies the full `BLEPublisher` Protocol (`publish()`,
`schema_version()`) and serves the complete GATT schema (device info,
connection/health, live engine/fuel/electrical/navigation, alerts/status,
diagnostics) with the correct access-control and staleness semantics, so
every Requirement 13/28 behavior is testable on a laptop with no CAN or BLE
hardware. A `BlueZBLEPublisher` implementing the same Protocol against a real
radio is Stage 10's job; nothing in `ble/encoding.py` or the `BLEPublisher`
Protocol itself needs to change for that to slot in.

## Staleness/quality surfacing is per-value, consistent across every transport

**Stage:** 7.4 (`web/static/index.html`, `ble/encoding.py`)
**Requirements:** 20.6

Requirement 20.6 asks that every served value be distinguishable as
stale/invalid/unknown versus current valid data, at the transport interface
-- not just that the platform *tracks* this internally. REST/WS already
serialized the full `Provenance` object (including `quality` and
`staleness_s`) via `_provenance_to_dict()` in `web/api.py` from Stage 7.1, so
that half was already correct. Two real gaps existed:

1. **BLE's wire envelope had no `quality` field at all** -- only
   `value_origin` and `staleness_s`. A BLE client could see a value was
   "derived 12s old" but not that its underlying Provenance was marked
   `invalid`, which REST/WS clients could see.
2. **The Web_UI never read per-value Provenance** -- its only staleness
   signal was a page-level `transportStale` flag (no WebSocket push for
   >3s), which conflates "the socket is slow" with "the served value's own
   Provenance says invalid/stale/unknown" (e.g. during
   `CAPTURE_HALTED_STORAGE_FULL`, pushes keep arriving on schedule carrying
   the frozen last-known state marked stale by Provenance -- the UI showed
   that identically to fresh valid data).

**Decision:**
- `ble/encoding.py`'s `EntityRecord` header gained a `quality` byte
  (`QualityCode` enum mirroring Provenance's four string values). This is a
  genuine wire-format change to an already-shipped envelope, so
  `SCHEMA_VERSION` bumped `1 -> 2` rather than silently reshaping v1's byte
  layout -- consistent with the embedded-version contract (Requirement
  27.4) that already warns callers to check `schema_version` before
  trusting field offsets.
- The Web_UI now reads `entity.provenance.quality` /
  `.staleness_s` per rendered field, independent of the transport-level
  `transportStale` flag. A non-`"valid"` quality adds a `.quality-badge`
  next to the value (naming the quality and age) and recolors the value
  itself (`is-stale` / `is-invalid` / `is-unknown-quality`), distinct from
  the existing socket-connectivity indicator. Both signals are shown, never
  collapsed into one, since they answer different questions (is the
  transport delivering pushes vs. is the served value itself current).

**Why:** the requirement's own wording ("distinguishable from current valid
data") is about the served VALUE, not the connection. Collapsing them would
have silently hidden the exact `CAPTURE_HALTED_STORAGE_FULL` scenario
Stage 4's fail-safe design (steering 11) exists to make visible.

## Correlation_Mode: correlation-strength scoring formula

**Stage:** 7 (`service/correlation.py`)
**Requirements:** 16.1

Neither requirements.md nor design.md specifies an exact formula for the
`[0.0, 1.0]` correlation strength score -- only that it must exist and be
bounded. This is a genuine open modeling choice, not a documented contract.

**Decision:** strength = (changes observed strictly inside the action
window) / (total changes observed inside PLUS outside the window, across
the baseline-before and after windows). A field that changes ONLY during
the action scores 1.0; a field that also drifts during the before/after
windows (coincidental or unrelated traffic touching the same field) scores
proportionally lower. This rewards a field a physical action plausibly
controls and penalizes spurious correlation, without needing calibration
data this MVP has no way to collect. If real-world correlation sessions
show this heuristic misranks obvious cases, the formula is isolated to one
function (`_strength()`) and can be replaced without touching the
candidate-detection or confirmation-repetition logic around it.

## Security hardening pass: three real gaps closed, no new dependency

**Stage:** 8.2 (`service/raw_log.py`, `simulator/replay.py`, `web/api.py`,
`ble/publisher.py`, `scripts/secret_scan.py`, `Makefile`)
**Requirements:** 20.1, 20.2, 20.3, 20.7

Auditing the fail-safe/hostile-input requirements against the actual Stage
4-7 code (rather than assuming prior stages already covered them) found
three real gaps:

1. **A corrupted replay/log line raised instead of being quarantined.**
   `service/raw_log.iter_records()` called `json.loads()` with no exception
   handling -- one truncated or garbage JSONL line anywhere in a capture
   file raised `json.JSONDecodeError` straight through the generator,
   aborting replay of every subsequent (valid) line in the file, and
   propagating as an unhandled exception through `GET /api/v1/logs/{id}`,
   `POST /api/v1/replay`, and `make replay`. This directly violated
   steering 11's "no single fault terminates the pipeline" rule applied to
   replay, and Requirement 20.1/20.3's "corrupted replay files and
   unexpected payloads are quarantined/discarded with the process
   continuing."

   **Decision:** `iter_records()` now yields a `CorruptLine(line_number,
   raw, error)` record in place of raising, and continues to the next line.
   Every caller (`simulator/replay.py`'s `load_fixture_frames()`,
   `web/api.py`'s `get_log()`) was updated to skip a `CorruptLine` rather
   than crash on it; the REST endpoint additionally reports a
   `corrupt_lines` count in its response so a quarantined line is visible,
   not silently dropped.

2. **An unknown BLE characteristic name raised a bare `KeyError`.**
   `MockBLEPublisher.read_characteristic(name)` did `self._states[name]`
   with no validation -- a hostile or malformed client request naming a
   characteristic outside `GATT_SCHEMA` raised an unhandled `KeyError`
   rather than a named rejection (Requirement 20.2: "rejected with an error
   naming the failed parameter... while valid clients keep being served").

   **Decision:** added `UnknownCharacteristicError(KeyError)`, raised with
   the offending name in its message before any dict lookup. The Web_API
   side of 20.2 (malformed/out-of-range REST parameters, path traversal)
   was already covered by Stage 7.1/7.5's validation tests -- this closed
   the one remaining hostile-input gap, on the BLE transport.

3. **No secret-scan lint step existed at all** (Requirement 20.7).
   **Decision:** `scripts/secret_scan.py`, a small stdlib-only regex
   scanner (known key/token formats -- PEM private key blocks, AWS/GitHub/
   Slack token shapes -- plus a generic "secret-sounding name assigned a
   plaintext literal" heuristic with a placeholder allowlist), wired into
   `make lint` as a third step. ponytail steering: a dependency like
   `detect-secrets` was deliberately not added -- this project's actual
   surface (no cloud SDKs, no stored credentials beyond the git-ignored
   `.env`) doesn't need a general-purpose entropy scanner. **Ceiling:** it
   will miss a secret in an unrecognized format or split across lines; the
   upgrade path if that ever matters is `detect-secrets` or `gitleaks`.
   Only git-tracked files are scanned (`git ls-files`), so `.env` itself
   (already git-ignored per steering 12) is never read by the scanner.

All three are covered by regression tests in
`tests/test_stage8_security_hardening.py` (steering 10): each test fails
against the pre-fix code and passes with the fix.

## message_rate's 60-second window is closed/inclusive at the boundary

**Stage:** 5 (`service/discovery.py`), surfaced by Stage 8.3's full `make test`
**Requirements:** 10.1, 10.2 (Property 17)

Property 17 ("message rate equals the count within the trailing 60-second
window") does not pin whether a timestamp exactly 60.000s before the latest
frame counts as "within" the window. `service/discovery.py`'s eviction rule
(`while timestamps[0] < cutoff: popleft()`) evicts strictly-older-than-cutoff
and keeps anything at or after it -- a closed/inclusive window at the
boundary. Running the full `make test` for Stage 8.3's coverage-gate work
surfaced that `tests/test_stage5_discovery_gate.py`'s Hypothesis property
test assumed the opposite (open/exclusive, `t > last_t - 60.0`), and
Hypothesis shrunk a failing case to `timestamps=[0.0, 60.0]` where the two
conventions disagree (closed: both count, rate=2; open: only the latest
counts, rate=1).

**Decision:** kept the implementation's closed-window convention (an
instant exactly at the 60s boundary is still "within" the trailing window --
the more standard look-back-window reading) and corrected the test to match
it, rather than changing `discovery.py`'s already-shipped, documented
eviction rule. No other code reads or depends on the opposite convention.

## Coverage gates: two scoped `--cov-fail-under` runs, not one

**Stage:** 8.3 (`Makefile`)
**Requirements:** 19.2, 19.3

Requirement 19.2/19.3 asks for two different coverage floors over two
different scopes in the same `make test`: pure protocol/decoder modules
(`protocol/`) at >=90%, all host-testable code
(`protocol`+`service`+`ble`+`web`+`simulator`) at >=80%. `pytest-cov`'s
`--cov-fail-under` only supports a single global threshold per invocation.

**Decision:** `make test` runs the suite twice -- once scoped to `--cov=protocol`
with `--cov-fail-under=90`, once scoped to all five packages with
`--cov-fail-under=80` -- rather than reaching for a third-party multi-scope
coverage tool. Both gates were measured against the real suite before being
wired in (protocol/ aggregate: 98%; overall: 93%), so this is enforcement of
an already-met bar, not a speculative threshold. Running the full suite
twice costs real time (`make test` now takes ~60-90s depending on load
rather than ~20-40s); if that becomes a problem, a single
`coverage combine`-based report with per-path thresholds (via a
`.coveragerc` `[report] fail_under` is still global, so this would need a
small custom script reading `coverage.json`) is the next step -- not
attempted here per ponytail (don't build the fancier version until the
simple one is proven insufficient).

## Per-area test audit (Requirement 19.4, 19.5)

**Stage:** 8.3

Audited the existing test suite against Requirement 19.4's seven required
areas and 19.5's two specific cross-cutting tests. All were already present
from Stages 1-7's own gates -- no new test file was needed:

| Area (19.4) | Covered by |
|---|---|
| CAN/protocol parsing | `test_stage1_gate.py`, `test_stage2_gate.py` |
| Fuel/tank decoding | `test_stage3_gate.py` |
| BLE | `test_stage6_ble_encoding.py`, `test_stage6_ble_publisher_gate.py` |
| Linux ingestion | `test_stage4_gate.py` (`IngestionService`) |
| Persistence/replay | `test_stage4_gate.py`, `test_stage5_gate.py` |
| UI | `test_stage7_web_ui_gate.py` |
| Integration flows | `test_stage7_gate.py` (`integration`-marked) |

19.5's two named tests: `test_stage6_cache.py::test_ingestion_service_wires_updates_into_cache`
drives a real `CanFrame` through `compose_identifier`/`IngestionService`/
`DEFAULT_REGISTRY` into a canonical `EngineState` read back from
`LiveStateCache` (raw fixture -> parser -> Decoder -> Domain_Model, in full);
`test_stage7_gate.py::test_same_domain_fixture_identical_values_across_ble_rest_ws`
proves one fixture yields identical values across BLE/REST/WS.

## Stage 8.5 sign-off status (2026-10-01)

The full host-testable sign-off gate was run as far as a single macOS session
can take it. Status:

**Green, solo-confirmed:**
- `make test lint run-sim run-api replay test-integration` — all pass on macOS
  (157/157 tests, 93.11% overall coverage, 98% `protocol/` coverage, both
  thresholds above their required floors; lint clean including the secret
  scanner; `run-api` confirmed loopback-only; `run-sim`/`replay` both produce
  correct output).
- Capture promotion (`scripts/promote_capture.py`) demonstrated end-to-end on a
  fresh 750 RPM idle capture generated via the simulator — promoted,
  validated, byte-identical. Not kept as a permanent fixture: `engine_idle.jsonl`
  already proves the identical scenario, and adding a duplicate would be test
  noise rather than new coverage, per the project's "don't promote a redundant
  fixture" discipline.
- OS-dependency audit: zero OS-specific imports exist anywhere in
  `protocol/`, `service/`, `ble/`, `web/`, or `simulator/` today. BlueZ itself
  is not yet wired in (that's Stage 10's `BlueZPublisher`) — there is currently
  nothing OS-specific in the codebase to isolate, which trivially satisfies the
  audit.
- Coverage gates and per-area test audit: both already confirmed in Stage 8.3
  (93.11% overall, 98% protocol/, all 7 required areas + both 19.5 cross-cutting
  tests present).

**Blocked — needs the user:**
1. **Second-OS run.** Requirement 23.1/23.2 explicitly requires the same gate
   sequence on a Windows or Linux host (CI matrix or local VM). This agent has
   no such host reachable from this macOS session. Nothing here contradicts a
   second-OS pass — the codebase has no OS-specific code outside the
   not-yet-built BlueZ boundary — but the gate's own text requires it be
   *demonstrated*, not inferred. Needs the user to run the sequence on a second
   OS (or stand up a CI matrix) and report the result.
2. **`quantity_remaining_litres` formula — RESOLVED 2026-10-01.** User
   confirmed the real-world convention: tank capacity is configured once in
   litres (e.g. 90 L, 120 L) and `level_percent` reports the percentage of
   that configured capacity. Corrected to
   `(level_percent / 100) * capacity_litres` in requirements.md, design.md,
   `protocol/decoders/tank.py`, and the Stage 3/6 pinning tests. Property 33
   is now certified against the corrected, physically-sensible formula.
