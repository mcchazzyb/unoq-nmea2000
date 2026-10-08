#!/usr/bin/env python3
"""Reset the UNO Q's MCU when its Bridge has stopped answering. Runs on the board (host OS).

Why this exists (bench evidence, 2026-10-08, 53 stalls in 10.4 h of sustained CAN traffic):
the Bridge's RPC decoder can wedge. The MCU keeps running, but no Bridge call is served until
the MCU is reset, and nothing on the Linux side (new client, app restart) clears it. The sketch
cannot reset itself usefully either: after ANY reset the base firmware waits for the host debug
probe to write a non-zero value to 0x40036400 before it loads the sketch. So the host does what
the platform's own flashing step does (hardware reset, then that write).

Signal: GET /api/v1/health -> receiver_ok_age_s, the seconds since the app's last SUCCESSFUL
Bridge health call. It keeps advancing on a quiet bus, so a silent CAN bus never triggers a reset.
If the app itself is not running there is nothing to judge, and the MCU is left alone.

Receive-only (steering 01): resetting the MCU transmits nothing. The MCP2518FD controller keeps
its ListenOnly mode, and D4 behaves as at power-up (task 9.2, accepted deviation).

Stdlib only. Install: see deploy/unoq-mcu-recover.service.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

HEALTH_URL = "http://127.0.0.1:8000/api/v1/health"
POLL_S = 5.0
DEAD_AFTER_S = 15.0       # Bridge silent this long -> reset (the app polls it every second)
NEVER_OK_AFTER_S = 90.0   # app up but its Bridge has never answered this long -> reset
MIN_GAP_S = 60.0          # never reset more often than this

OPENOCD = "/opt/openocd/bin/openocd"
# The platform's own sequence (flash_sketch.cfg): hardware reset, give the base firmware time to
# boot, then write the non-zero value its loader polls for before starting the sketch.
OPENOCD_ARGS = [
    OPENOCD, "-s", "/opt/openocd", "-s", "/opt/openocd/share/openocd/scripts",
    "-f", "/opt/openocd/openocd_gpiod.cfg",
    "-c", "reset_config srst_only srst_nogate srst_push_pull connect_assert_srst",
    "-c", "init", "-c", "reset", "-c", "sleep 100",
    "-c", "mww 0x40036400 0xCAFFEEEE", "-c", "shutdown",
]


@dataclass
class State:
    last_reset: float = float("-inf")
    app_up_since: float | None = None  # when we first saw the app answering this time round


def decide(state: State, now: float, health: dict | None) -> bool:
    """True if the MCU should be reset now. Mutates `state`. `health` is None if the app is down."""
    if health is None:
        state.app_up_since = None  # app not running: not our business, and restart the clock
        return False
    if state.app_up_since is None:
        state.app_up_since = now
    if now - state.last_reset < MIN_GAP_S:
        return False
    age = health.get("receiver_ok_age_s")
    if age is None:
        dead = now - state.app_up_since > NEVER_OK_AFTER_S
    else:
        dead = age > DEAD_AFTER_S
    if dead:
        state.last_reset = now
        state.app_up_since = now  # give the app a fresh NEVER_OK window after the reset
    return dead


def fetch_health() -> dict | None:
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=3) as r:
            return json.load(r)
    except (urllib.error.URLError, OSError, ValueError):
        return None


def reset_mcu() -> bool:
    r = subprocess.run(OPENOCD_ARGS, capture_output=True, text=True, timeout=60)
    return r.returncode == 0


def main() -> int:
    state = State()
    print("unoq-mcu-recover: watching", HEALTH_URL, flush=True)
    while True:
        health = fetch_health()
        if decide(state, time.monotonic(), health):
            age = None if health is None else health.get("receiver_ok_age_s")
            print(f"Bridge silent (receiver_ok_age_s={age}): resetting the MCU", flush=True)
            try:
                ok = reset_mcu()
            except (OSError, subprocess.SubprocessError) as exc:
                ok = False
                print("reset failed:", exc, flush=True)
            print("MCU reset", "done" if ok else "FAILED", flush=True)
        time.sleep(POLL_S)


if __name__ == "__main__":
    sys.exit(main())
