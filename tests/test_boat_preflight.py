"""The boat preflight must refuse a bench build (steering 01, owner approval of bench-only ACK).

It compares the board's sketch with the repo's production sketch and requires the
ListenOnly mode on the health line. Static checks: it needs no board.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

SCRIPT = Path("scripts/boat_preflight.sh")
PRODUCTION = Path("firmware/unoq_nmea2000_capture/sketch/sketch.ino")


def test_script_is_valid_bash():
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0


def test_script_compares_board_sketch_to_production_and_requires_listen_only():
    s = SCRIPT.read_text()
    assert "REPO_SKETCH" in s and "sketch/sketch.ino" in s
    assert "ListenOnly" in s
    assert "BENCH_ONLY_ACK" in s


def test_script_checks_the_recovery_service_and_bridge_liveness():
    s = SCRIPT.read_text()
    assert "unoq-mcu-recover" in s and "receiver_ok_age_s" in s


def test_script_is_read_only():
    s = SCRIPT.read_text()
    assert not re.search(r"\b(scp|rm|kill|app (start|stop|restart)|nmcli connection (add|modify|up|down))\b", s)


def test_production_sketch_carries_no_bench_code():
    assert "BENCH_ONLY_ACK" not in PRODUCTION.read_text()
