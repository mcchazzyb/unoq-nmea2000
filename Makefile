.PHONY: test lint run-sim run-api replay test-integration install

PY := .venv/bin/python
PYTEST := .venv/bin/pytest

install:
	python3 -m venv .venv 2>/dev/null || true
	.venv/bin/pip install -q -e ".[dev]"

# Full unit + property suite, plus the two coverage gates (Requirement 19.2,
# 19.3): pure protocol/decoder modules (`protocol/`) must be >=90% covered,
# and all host-testable code (protocol+service+ble+web+simulator) must be
# >=80% covered. Two separate --cov-fail-under invocations rather than one,
# since pytest-cov supports only a single global threshold per run and the
# spec requires two different thresholds over two different scopes. Either
# gate failing fails the build; a real test failure fails earlier and
# `$$rc` carries it through to the final exit status.
test:
	$(PYTEST) -q --cov=protocol --cov-report=term-missing --cov-fail-under=90; rc=$$?; \
	if [ $$rc -ne 0 ]; then \
		echo "COVERAGE GATE FAILED: protocol/ (pure protocol+decoder modules) below 90% (Requirement 19.2)" >&2; \
		exit $$rc; \
	fi
	$(PYTEST) -q --cov=protocol --cov=service --cov=ble --cov=web --cov=simulator \
		--cov-report=term-missing --cov-fail-under=80; rc=$$?; \
	if [ $$rc -ne 0 ]; then \
		echo "COVERAGE GATE FAILED: overall host-testable coverage below 80% (Requirement 19.3)" >&2; \
		exit $$rc; \
	fi

# Lint: syntax check every tracked .py, enforce the Layer-2 import boundary
# (/protocol must not import /service /ble /web — Requirement 21.2), and scan
# tracked source for plaintext secrets/credentials (Requirement 20.7). All
# three steps exit non-zero on failure, so `make lint` fails the build on any.
lint:
	$(PY) -c "import ast, glob; [ast.parse(open(f).read(), f) for f in glob.glob('**/*.py', recursive=True) if '.venv' not in f]" && echo "syntax OK"
	$(PYTEST) -q tests/test_import_boundary.py
	$(PY) scripts/secret_scan.py

# Run the simulator (synthetic multi-node generator: engine, tank, GPS,
# electrical, and an unknown-vendor node -- Requirement 17.2).
run-sim:
	$(PY) -m simulator.generator

# Run the Web_API (FastAPI, loopback only by default -- Requirement 14.3).
run-api:
	$(PY) -m web.main

# Replay a capture file in realtime via the Stage 5 FixtureReplay engine.
# FILE is required; a missing FILE fails fast with a non-zero exit.
replay:
	@test -n "$(FILE)" || { echo "replay: FILE=... is required" >&2; exit 2; }
	@test -f "$(FILE)" || { echo "replay: FILE not found: $(FILE)" >&2; exit 2; }
	$(PY) -m simulator.replay "$(FILE)"

# Integration suite (full mock-to-domain pipeline). The suite lands in Stage 6/7;
# this target runs any tests tagged `integration`. pytest exit 5 means "no
# integration tests collected yet", which is not a failure at this stage; any
# other non-zero exit (a real failure) still fails the build.
test-integration:
	$(PYTEST) -q -m integration; rc=$$?; [ $$rc -eq 0 ] || [ $$rc -eq 5 ]
