"""The bench-only ACK sketch must stay fenced off from production (steering 01).

Regression (steering 10): the temporary ACK exception must keep its barriers:
compile-time flag, D6 runtime jumper, never reachable from the production path,
and no send calls. Static checks.
"""
from __future__ import annotations

import re
from pathlib import Path

BENCH = Path("bench/ack_only/sketch.ino.bench").read_text()
PROD = Path("firmware/unoq_nmea2000_capture/sketch/sketch.ino").read_text()


def test_bench_sketch_needs_compile_flag_and_jumper():
    assert re.search(r"#ifndef BENCH_ONLY_ACK\s*\n#error", BENCH)
    assert "pinMode(BENCH_ENABLE_PIN, INPUT_PULLUP)" in BENCH
    assert "benchAck ? ACAN2517Settings::Normal20B : ACAN2517Settings::ListenOnly" in BENCH
    assert "if (canInitOk && benchAck) digitalWrite(TRANSCEIVER_SILENT_PIN, LOW)" in BENCH


def test_bench_sketch_never_sends_and_is_not_a_buildable_ino():
    for token in ("tryToSend", "can.send", "CANMessage frame", ".send("):
        assert token not in BENCH
    assert not list(Path("bench").rglob("*.ino"))  # only .bench, so nothing builds it by accident


def test_production_sketch_has_no_ack_mode():
    assert "Normal20B ?" not in PROD and "BENCH" not in PROD
    assert "mRequestedMode = ACAN2517Settings::ListenOnly" in PROD
    assert "digitalWrite(TRANSCEIVER_SILENT_PIN, LOW)" not in PROD
