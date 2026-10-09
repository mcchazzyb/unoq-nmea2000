"""Host black box, journald drop-in, report script and firmware-copy sync (boat, 2026-10-09)."""
from __future__ import annotations

import filecmp
import subprocess
import sys
from pathlib import Path

from service.flight_recorder import FlightRecorder, read_records

ROOT = Path(__file__).resolve().parent.parent
FIRMWARE = ROOT / "firmware" / "unoq_nmea2000_capture"


def _load(name: str, path: Path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_firmware_copies_match_the_source_trees():
    """The board runs firmware/unoq_nmea2000_capture/{web,service,protocol,simulator}. Editing only the
    repo-root copy means the board silently runs old code."""
    stale: list[str] = []
    for tree in ("web", "service", "protocol", "simulator"):
        cmp = filecmp.dircmp(ROOT / tree, FIRMWARE / tree, ignore=["__pycache__"])

        def walk(c: filecmp.dircmp, rel: str) -> None:
            stale.extend(f"{rel}/{n}" for n in c.left_only + c.right_only + c.diff_files)
            for sub, sc in c.subdirs.items():
                walk(sc, f"{rel}/{sub}")

        walk(cmp, tree)
    assert not stale, f"firmware copy out of sync: {stale}"


def test_host_blackbox_loads_the_recorder_and_has_no_transmit_path():
    src = (ROOT / "deploy" / "unoq_blackbox.py").read_text()
    assert "flight_recorder.py" in src
    for forbidden in ("cansend", "socket.AF_CAN", "can0", "post(", "method=\"POST\""):
        assert forbidden not in src


def test_host_blackbox_reports_changes_only_for_watched_facts():
    bb = _load("unoq_blackbox_mod", ROOT / "deploy" / "unoq_blackbox.py")
    base = {
        "ntp_synced": False, "wifi": {"connection": "hoosie", "mode": "infrastructure", "ip": "192.168.2.22"},
        "services": {"docker": "active"}, "ble_clients": [], "disk_free_mb": 100, "app": None,
    }
    assert bb.changed_facts(None, base) == []
    same_but_disk = dict(base, disk_free_mb=99)
    assert bb.changed_facts(base, same_but_disk) == []
    moved = dict(base, wifi={"connection": "BoatFi", "mode": "ap", "ip": "10.42.0.1"}, ntp_synced=True)
    assert set(bb.changed_facts(base, moved)) == {"wifi", "ntp_synced"}
    up = dict(base, app={"state": "receiving"})
    assert set(bb.changed_facts(base, up)) == {"app_up", "bus_state"}


def test_host_unit_is_a_copied_file_waits_for_home_and_installer_copies_it():
    unit = (ROOT / "deploy" / "unoq-blackbox.service").read_text()
    assert "RequiresMountsFor=/home/arduino" in unit and "Restart=always" in unit
    assert "systemctl enable --now /home/" not in unit
    installer = (ROOT / "scripts" / "install_blackbox.sh").read_text()
    assert subprocess.run(["bash", "-n", str(ROOT / "scripts" / "install_blackbox.sh")]).returncode == 0
    assert 'sudo cp "$D/$unit.service" "/etc/systemd/system/$unit.service"' in installer
    assert "ln -s" not in installer


def test_journald_dropin_keeps_the_log_on_disk_and_syncs_often():
    conf = (ROOT / "deploy" / "journald-unoq.conf").read_text()
    assert "Storage=persistent" in conf and "SyncIntervalSec=10s" in conf


def test_preflight_checks_the_diagnostics():
    s = (ROOT / "scripts" / "boat_preflight.sh").read_text()
    for needle in ("unoq-blackbox", "SyncIntervalSec=10s", "blackbox.jsonl", "/api/v1/bus-status"):
        assert needle in s
    assert subprocess.run(["bash", "-n", str(ROOT / "scripts" / "boat_preflight.sh")]).returncode == 0


def test_report_orders_by_boot_and_flags_an_unsynced_clock(tmp_path):
    report = _load("blackbox_report_mod", ROOT / "scripts" / "blackbox_report.py")
    app_dir, host_dir = tmp_path / "app", tmp_path / "host"
    app, host = FlightRecorder(app_dir, source="app"), FlightRecorder(host_dir, source="host")
    app.record("start")
    app.record("bus_state", old="no_frames_yet", new="receiving", frames_total=40, frames_per_s=12.0, last_frame_age_s=0.1)
    app.record("summary", frames_total=900, frames_per_s=14.0, state="receiving", pgns_seen=3, sources_seen=2,
               persisted_total=900, receiver_errors=0, pgn_counts={"127488": 500, "127505": 400},
               mcu={"mode": "ListenOnly", "error_counters": 0, "mcu_frames": 905, "mcu_dropped": 0, "mcu_restarts": 0})
    host.record("change", changed=["wifi"], wifi={"connection": "BoatFi", "mode": "ap", "ip": "10.42.0.1"},
                ntp_synced=False)
    text = report.build_report(list(read_records(app_dir)) + list(read_records(host_dir)))
    assert "frames_total=900" in text and "mcu: frames=905" in text
    assert "BUS no_frames_yet -> receiving" in text
    assert "wifi=BoatFi mode=ap ip=10.42.0.1" in text
    assert "PGNs seen: 127488x500" in text
    assert "NOT reliable" in text  # the host never reported NTP synced


def test_report_copes_with_no_records(tmp_path):
    report = _load("blackbox_report_mod2", ROOT / "scripts" / "blackbox_report.py")
    assert "No black box records" in report.build_report([])
