#!/usr/bin/env python3
"""Summarise the black box logs copied off the board.

    scripts/blackbox_report.py --fetch arduino@<board>
    scripts/blackbox_report.py bb-app bb-host        # directories already copied

One section per power-on (kernel boot id). The board has no battery-backed clock, so wall-clock times
are wrong until NTP syncs. Lines are ordered by uptime, and the clock is corrected from a moment when
the board reported NTP synced; otherwise times are marked 'clock unsynced'.
"""
from __future__ import annotations

import argparse
import datetime as dt
import statistics
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from service.flight_recorder import read_records  # noqa: E402


def _clock_offset(lines: list[dict]) -> float | None:
    """wall - uptime, taken only from host ticks that said NTP was synced."""
    samples = [
        r["t"] - r["up_s"]
        for r in lines
        if r.get("src") == "host" and r.get("ntp_synced") is True and r.get("up_s") is not None
    ]
    return statistics.median(samples) if samples else None


def _stamp(r: dict, offset: float | None) -> str:
    up = r.get("up_s")
    up_txt = f"+{up:>8.1f}s" if up is not None else "+      ?s"
    if offset is None or up is None:
        return f"{up_txt} (clock unsynced)"
    return f"{up_txt} {dt.datetime.fromtimestamp(up + offset, dt.timezone.utc):%Y-%m-%d %H:%M:%S}Z"


def _describe(r: dict) -> str | None:
    kind = r.get("kind")
    if kind == "start":
        return f"{r.get('src')} recorder started"
    if kind == "bus_state":
        return (
            f"BUS {r.get('old')} -> {r.get('new')}: frames {r.get('frames_total')}, "
            f"{r.get('frames_per_s')}/s, last frame {r.get('last_frame_age_s')} s ago"
        )
    if kind == "change":
        wifi = r.get("wifi") or {}
        changed = r.get("changed", [])
        bits = [f"changed {','.join(changed)}"]
        if "wifi" in changed:
            bits.append(f"wifi={wifi.get('connection')} mode={wifi.get('mode')} ip={wifi.get('ip')}")
        if "ble_clients" in changed:
            bits.append(f"ble={r.get('ble_clients')}")
        if "ntp_synced" in changed:
            bits.append(f"ntp_synced={r.get('ntp_synced')}")
        if "services" in changed:
            bits.append(f"services={r.get('services')}")
        return "HOST " + "; ".join(bits)
    if kind == "log":
        return f"LOG {r.get('level')} {r.get('text')}"
    return None


def build_report(records: Iterable[dict]) -> str:
    by_boot: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        by_boot[r.get("boot", "unknown")].append(r)
    if not by_boot:
        return "No black box records found.\n"

    out: list[str] = []
    for boot, lines in sorted(by_boot.items(), key=lambda kv: min(r.get("t", 0) for r in kv[1])):
        lines.sort(key=lambda r: (r.get("up_s") is None, r.get("up_s") or 0, r.get("seq", 0)))
        offset = _clock_offset(lines)
        ups = [r["up_s"] for r in lines if r.get("up_s") is not None]
        out.append(
            f"=== boot {boot}: {len(lines)} records, uptime {min(ups, default=0):.0f}s .. {max(ups, default=0):.0f}s ==="
        )
        if offset is None:
            out.append("    clock never reported synced in this boot: wall-clock times are NOT reliable")
        app = [r for r in lines if r.get("src") == "app" and r.get("kind") in ("tick", "summary", "bus_state")]
        if app:
            last = app[-1]
            mcu = last.get("mcu") or {}
            out.append(
                f"    app: frames_total={max(r.get('frames_total', 0) for r in app)} "
                f"peak_fps={max(r.get('frames_per_s', 0) for r in app)} last_state={last.get('state')} "
                f"pgns={last.get('pgns_seen')} devices={last.get('sources_seen')} "
                f"persisted={last.get('persisted_total')} receiver_errors={last.get('receiver_errors')}"
            )
            out.append(
                f"    mcu: frames={max((r.get('mcu') or {}).get('mcu_frames', 0) for r in app)} "
                f"errors(last)={mcu.get('error_counters')} mode={mcu.get('mode')} "
                f"dropped={mcu.get('mcu_dropped')} restarts={mcu.get('mcu_restarts')}"
            )
            summaries = [r for r in app if r.get("kind") == "summary" and r.get("pgn_counts")]
            if summaries:
                counts = summaries[-1]["pgn_counts"]
                top = sorted(counts.items(), key=lambda kv: -kv[1])[:12]
                out.append("    PGNs seen: " + ", ".join(f"{p}x{n}" for p, n in top))
        else:
            out.append("    no app records in this boot (app never started, or its log was not copied)")
        for r in lines:
            text = _describe(r)
            if text:
                out.append(f"  {_stamp(r, offset)}  {text}")
        last_line = lines[-1]
        out.append(
            f"    last record: {_stamp(last_line, offset)} ({last_line.get('src')}/{last_line.get('kind')}); "
            "power was cut after this, or nothing more was written"
        )
        out.append("")
    return "\n".join(out) + "\n"


def _fetch(host: str, dest: Path) -> list[Path]:
    targets = [
        ("ArduinoApps/unoq_nmea2000_capture/.cache/blackbox", dest / "bb-app"),
        ("unoq-blackbox", dest / "bb-host"),
    ]
    got: list[Path] = []
    for remote, local in targets:
        result = subprocess.run(
            ["scp", "-q", "-r", f"{host}:{remote}", str(local)], capture_output=True, text=True, check=False
        )
        if result.returncode == 0:
            got.append(local)
        else:
            print(f"warning: could not copy {remote}: {result.stderr.strip()}", file=sys.stderr)
    return got


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("dirs", nargs="*", help="directories holding blackbox.jsonl* files")
    parser.add_argument("--fetch", metavar="USER@HOST", help="copy both black boxes from the board first")
    args = parser.parse_args(argv)
    dirs = [Path(d) for d in args.dirs]
    if args.fetch:
        tmp = Path(tempfile.mkdtemp(prefix="unoq-blackbox-"))
        dirs += _fetch(args.fetch, tmp)
        print(f"(copied to {tmp})", file=sys.stderr)
    records = [r for d in dirs for r in read_records(d)]
    sys.stdout.write(build_report(records))
    return 0


if __name__ == "__main__":
    sys.exit(main())
