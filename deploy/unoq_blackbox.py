#!/usr/bin/env python3
"""Host-side black box for the UNO Q: network, clock, services, Bluetooth and the app's bus status,
written with fsync every few seconds so it survives a dirty power-off.

Why: on 2026-10-09 the boat session left almost no evidence. The board has no battery-backed clock, the
journal loses its last minutes when power is cut, and App Lab deletes the app container (and its log)
at the next boot. The in-app recorder (service/flight_recorder.py) covers the app; this covers
everything the app cannot see: which WiFi the board joined and at which address, whether the clock
had synced, whether a phone connected over Bluetooth, and whether the services were running.

Stdlib only. Read-only toward the system: it runs a few status commands and one GET to the dashboard
on loopback. It transmits nothing on the CAN bus. Install: see deploy/unoq-blackbox.service and
scripts/install_blackbox.sh.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
RECORDER_PATH = HERE.parent / "service" / "flight_recorder.py"
OUT_DIR = Path(os.environ.get("UNOQ_BLACKBOX_HOST_DIR", "/home/arduino/unoq-blackbox"))
APP_URL = os.environ.get("UNOQ_APP_URL", "http://127.0.0.1:8000")
INTERVAL_S = float(os.environ.get("UNOQ_BLACKBOX_INTERVAL_S", "10"))
SERVICES = ("unoq-mcu-recover", "unoq-ble-publisher", "docker", "bluetooth", "NetworkManager")
WIFI_IFACE = "wlan0"


def _load_recorder_module():
    spec = importlib.util.spec_from_file_location("flight_recorder", RECORDER_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["flight_recorder"] = module
    spec.loader.exec_module(module)
    return module


def _run(cmd: list[str], timeout: float = 4.0) -> str | None:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def wifi_state() -> dict:
    """Active WiFi connection name, its mode (infrastructure / ap), and the IPv4 address on wlan0."""
    state: dict = {"connection": None, "mode": None, "ip": None}
    active = _run(["nmcli", "-t", "-f", "NAME,TYPE,DEVICE", "con", "show", "--active"])
    for line in (active or "").splitlines():
        parts = line.split(":")
        if len(parts) >= 3 and parts[2] == WIFI_IFACE:
            state["connection"] = parts[0]
            state["mode"] = _run(["nmcli", "-g", "802-11-wireless.mode", "con", "show", parts[0]])
    addr = _run(["ip", "-4", "-o", "addr", "show", "dev", WIFI_IFACE])
    if addr:
        fields = addr.split()
        if "inet" in fields:
            state["ip"] = fields[fields.index("inet") + 1].split("/")[0]
    return state


def clock_synced() -> bool | None:
    out = _run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"])
    return None if out is None else out == "yes"


def service_states() -> dict:
    return {name: (_run(["systemctl", "is-active", name]) or "unknown") for name in SERVICES}


def ble_clients() -> list[str]:
    out = _run(["bluetoothctl", "devices", "Connected"])
    return sorted(line.strip() for line in (out or "").splitlines() if line.strip())


_APP_KEYS = ("state", "frames_total", "frames_per_s", "last_frame_age_s", "receiver_ok_age_s", "receiver_errors")


def app_status() -> dict | None:
    """A short digest of the app's bus status (the app's own recorder keeps the full detail)."""
    try:
        with urllib.request.urlopen(f"{APP_URL}/api/v1/bus-status", timeout=2) as resp:  # noqa: S310 - loopback
            full = json.loads(resp.read().decode("utf-8"))
    except (OSError, ValueError):
        return None
    digest = {k: full.get(k) for k in _APP_KEYS}
    mcu = full.get("mcu") or {}
    digest.update(mode=mcu.get("mode"), error_counters=mcu.get("error_counters"), mcu_frames=mcu.get("mcu_frames"))
    return digest


def snapshot() -> dict:
    disk = shutil.disk_usage("/home/arduino") if Path("/home/arduino").exists() else None
    return {
        "ntp_synced": clock_synced(),
        "wifi": wifi_state(),
        "services": service_states(),
        "ble_clients": ble_clients(),
        "disk_free_mb": None if disk is None else disk.free // (1024 * 1024),
        "app": app_status(),
    }


def changed_facts(prev: dict | None, cur: dict) -> list[str]:
    """Which watched facts changed since the last tick (these also get their own 'change' line)."""
    if prev is None:
        return []
    watched = {
        "wifi": lambda s: s["wifi"],
        "ntp_synced": lambda s: s["ntp_synced"],
        "services": lambda s: s["services"],
        "ble_clients": lambda s: s["ble_clients"],
        "app_up": lambda s: s["app"] is not None,
        "bus_state": lambda s: (s["app"] or {}).get("state"),
    }
    return [name for name, get in watched.items() if get(prev) != get(cur)]


def main() -> int:
    module = _load_recorder_module()
    recorder = module.FlightRecorder(OUT_DIR, source="host")
    recorder.record("start", note="host black box started", interval_s=INTERVAL_S)
    print("unoq-blackbox: writing to", OUT_DIR, flush=True)
    prev: dict | None = None
    while True:
        cur = snapshot()
        changes = changed_facts(prev, cur)
        if changes:
            recorder.record("change", changed=changes, **cur)
        recorder.record("tick", **cur)
        prev = cur
        time.sleep(INTERVAL_S)


if __name__ == "__main__":
    sys.exit(main())
