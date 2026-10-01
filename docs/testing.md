# Testing

## Running the suite

```bash
make test    # full unit + property suite, plus both coverage gates
make lint    # syntax check + import-boundary enforcement + secret scan
```

`make test` runs the suite **twice** — once scoped to `protocol/` with a
90% coverage floor (Requirement 19.2), once scoped to all host-testable
code with an 80% floor (Requirement 19.3) — since `pytest-cov` supports
only one `--cov-fail-under` threshold per invocation and the spec requires
two different thresholds over two different scopes. Either gate failing
fails the build; see `docs/architecture.md` for the measured baseline
(protocol/ 98%, overall 93%) this was wired against.

A single targeted file or test, during development:

```bash
.venv/bin/pytest -q tests/test_stage3_gate.py
.venv/bin/pytest -q tests/test_stage3_gate.py::test_engine_dynamic_full_frame_decodes_all_mapped_fields
```

Other make targets:

```bash
make run-sim                              # synthetic traffic generator
make run-api                              # Web_API + Web_UI on 127.0.0.1:8000
make replay FILE=fixtures/golden/x.jsonl  # replay a capture/fixture file
make test-integration                     # tests tagged `integration` only
```

## Test file layout

Each stage's "Stage Gate" test file (named `test_stageN_*.py`) covers that
stage's own tasks.md deliverables and the design.md properties it claims to
prove. `tests/test_import_boundary.py` is cross-cutting (enforces steering
05's `protocol` dependency rule on every `make lint` run, not just once).

| Area (Requirement 19.4) | Primary test file(s) |
|---|---|
| CAN/protocol parsing | `test_stage1_gate.py`, `test_stage2_gate.py` |
| Fuel/tank decoding | `test_stage3_gate.py` |
| BLE | `test_stage6_ble_encoding.py`, `test_stage6_ble_publisher_gate.py` |
| Linux ingestion | `test_stage4_gate.py` |
| Persistence/replay | `test_stage4_gate.py`, `test_stage5_gate.py` |
| UI | `test_stage7_web_ui_gate.py` |
| Integration flows | `test_stage7_gate.py` (`@pytest.mark.integration`) |
| Security hardening | `test_stage8_security_hardening.py` |

Two cross-cutting tests exist specifically because Requirement 19.5 names
them: `test_stage6_cache.py::test_ingestion_service_wires_updates_into_cache`
(a raw `CanFrame` through the real parser/decoder/ingestion path into a
canonical `EngineState`) and
`test_stage7_gate.py::test_same_domain_fixture_identical_values_across_ble_rest_ws`
(one fixture, identical decoded values across all three transports).

## Property tests

Correctness properties from `design.md` → *Correctness Properties* are
proven by Hypothesis-based tests (`@given`/`@settings(max_examples=100)` or
more), each tagged in its docstring exactly as:

```
Feature: nmea2000-diagnostic-platform, Property N: <text>
```

(steering 09). Grep for `Property N:` across `tests/` to find the test
proving a specific numbered property.

## Regression discipline

Every feature or fix landed after the initial implementation plan ships
with at least one test that fails without the change and passes with it
(steering 10, Requirement 19.6) — this is a standing rule for the life of
the project, not a one-time Stage 8 task. `tests/test_stage8_security_hardening.py`
is the clearest recent example: each of its 8 tests targets one specific
gap found and fixed in that same change.

## Slow tests

`tests/test_stage5_gate.py::test_fixture_replay_preserves_inter_frame_timing_within_5ms`
genuinely sleeps (it proves real-time replay timing via `time.sleep`), so
it is the one test in the suite that takes meaningfully longer than the
rest. It is NOT excluded from `make test` or the coverage gates — the
canonical gate runs the full suite, slow test included. During fast
iteration on an unrelated change, deselect it explicitly rather than
assuming it's optional:

```bash
.venv/bin/pytest -q --deselect tests/test_stage5_gate.py::test_fixture_replay_preserves_inter_frame_timing_within_5ms
```

## No hardware required

The full suite, `run-sim`, and `replay` all run on a laptop with no CAN
controller, no Bluetooth radio, and no vessel (Requirement 23.1, 23.2) —
`simulator/generator.py` and `simulator/replay.py` both implement the same
`ICanReceiver` Protocol real hardware would, so nothing in `service/` or
above can tell the difference (Requirement 23, 24.6).
