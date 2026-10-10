"""Dashboard "Shut down the board" button (Setup tab).

The app writes a request file carrying the boot id; a host systemd path unit runs deploy/unoq_shutdown.sh,
which powers off only if that id matches the CURRENT boot. A request left behind by a power cut must never
power the board off again at the next boot (that would be a boot loop on the boat).
"""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from service.cache import LiveStateCache
from service.discovery import DiscoveryMode
from service.flight_recorder import FlightRecorder, read_records
from service.shutdown_request import ShutdownUnavailable, is_pending, request_file, request_shutdown
from web.api import create_app

SCRIPT = Path("deploy/unoq_shutdown.sh")
PATH_UNIT = Path("deploy/unoq-shutdown.path")
SERVICE_UNIT = Path("deploy/unoq-shutdown.service")
HTML = Path("web/static/index.html").read_text(encoding="utf-8")


def _boot_file(tmp_path, boot="aaaa-bbbb-cccc") -> str:
    path = tmp_path / "boot_id"
    path.write_text(boot + "\n")
    return str(path)


# --- app side -------------------------------------------------------------------


def test_request_carries_the_boot_id_and_no_temp_file_is_left(tmp_path):
    target = tmp_path / "shutdown-request"
    request_shutdown(target, boot_id_path=_boot_file(tmp_path, "1234-abcd"))
    assert target.read_text().strip() == "1234-abcd"
    assert sorted(p.name for p in tmp_path.iterdir() if "shutdown" in p.name) == ["shutdown-request"]
    assert is_pending(target)


def test_no_boot_id_means_no_request_rather_than_one_the_host_would_ignore(tmp_path):
    target = tmp_path / "shutdown-request"
    with pytest.raises(ShutdownUnavailable):
        request_shutdown(target, boot_id_path=str(tmp_path / "missing"))
    assert not target.exists()


def _client(tmp_path, recorder=None):
    capture_dir = tmp_path / "cache" / "captures"
    capture_dir.mkdir(parents=True)
    app = create_app(LiveStateCache(), DiscoveryMode(), capture_dir, recorder=recorder)
    return TestClient(app), request_file(tmp_path / "cache")


def test_post_writes_the_request_beside_the_capture_dir_and_black_box_records_it(tmp_path):
    recorder = FlightRecorder(tmp_path / "blackbox")
    client, target = _client(tmp_path, recorder)
    assert client.get("/api/v1/system/shutdown").json() == {"pending": False, "queued": False, "failed": False}
    response = client.post("/api/v1/system/shutdown", json={"confirm": "shutdown"})
    if os.path.exists("/proc/sys/kernel/random/boot_id"):
        assert response.status_code == 202 and response.json() == {"status": "requested"}
        assert target.exists() and client.get("/api/v1/system/shutdown").json() == {"pending": True, "queued": False, "failed": False}
        kinds = [r["kind"] for r in read_records(tmp_path / "blackbox")]
        assert "shutdown_requested" in kinds  # a clean power-off is distinguishable from a power cut later
    else:  # non-Linux dev machine: refused, never silently accepted
        assert response.status_code == 503


@pytest.mark.parametrize("body", [{}, {"confirm": "yes"}, {"confirm": True}, {"confirm": "SHUTDOWN"}])
def test_post_without_the_exact_confirmation_does_nothing(tmp_path, body):
    client, target = _client(tmp_path)
    assert client.post("/api/v1/system/shutdown", json=body).status_code == 422
    assert not target.exists()


def test_a_plain_form_or_text_post_cannot_trigger_it(tmp_path):
    """A web page on another site can only send form/text posts without a CORS preflight."""
    client, target = _client(tmp_path)
    assert client.post("/api/v1/system/shutdown", content='{"confirm": "shutdown"}',
                       headers={"Content-Type": "text/plain"}).status_code >= 400
    assert client.post("/api/v1/system/shutdown", data={"confirm": "shutdown"}).status_code >= 400
    assert not target.exists()


def test_a_get_never_starts_a_shutdown(tmp_path):
    client, target = _client(tmp_path)
    client.get("/api/v1/system/shutdown")
    assert not target.exists()


# --- host side: run the real script with a fake systemctl -------------------------


def _run(tmp_path, request_text, boot="aaaa-bbbb-cccc", systemctl_exit=0):
    fake = tmp_path / "fake_systemctl.sh"
    fake.write_text(f'#!/bin/sh\necho "$@" > {tmp_path}/systemctl_args\nexit {systemctl_exit}\n')
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    request = tmp_path / "shutdown-request"
    if request_text is not None:
        request.write_text(request_text)
    env = {**os.environ, "UNOQ_BOOT_ID_FILE": _boot_file(tmp_path, boot),
           "UNOQ_SYSTEMCTL": str(fake), "UNOQ_POWEROFF_EXTRA": "--dry-run"}
    result = subprocess.run(["sh", str(SCRIPT), str(request)], env=env, capture_output=True, text=True)
    args = (tmp_path / "systemctl_args")
    return result, request, (args.read_text().strip() if args.exists() else None)


def test_script_powers_off_when_the_request_is_from_this_boot(tmp_path):
    result, request, args = _run(tmp_path, "aaaa-bbbb-cccc\n")
    assert result.returncode == 0 and args == "--no-block poweroff --dry-run"
    assert not request.exists()  # taken: the dashboard reads "file gone" as accepted


def test_script_ignores_and_deletes_a_request_left_by_an_earlier_boot(tmp_path):
    """The boot-loop guard: this fails (the board powers off) if the boot id is not compared."""
    result, request, args = _run(tmp_path, "OLD-BOOT-ID\n")
    assert result.returncode == 0 and args is None
    assert not request.exists()
    assert "stale" in result.stdout


def test_script_ignores_an_empty_request(tmp_path):
    result, request, args = _run(tmp_path, "")
    assert args is None and not request.exists()


def test_script_does_nothing_when_there_is_no_request(tmp_path):
    result, _request, args = _run(tmp_path, None)
    assert result.returncode == 0 and args is None


def test_failed_poweroff_is_reported_not_swallowed(tmp_path):
    """Review finding: the request was deleted before poweroff was queued, so if systemctl failed the
    dashboard saw 'file gone' and said it was shutting down. The host must leave a .failed marker."""
    result, request, args = _run(tmp_path, "aaaa-bbbb-cccc\n", systemctl_exit=1)
    assert result.returncode == 1
    assert not request.exists()  # still deleted: a failing poweroff must never loop
    assert (tmp_path / "shutdown-request.failed").read_text().startswith("systemctl poweroff exit 1")
    assert "NOT shutting down" in result.stdout


def test_successful_poweroff_writes_queued_and_no_failure_marker(tmp_path):
    """Review finding: 'request file gone' proved nothing (systemctl could still fail), so the dashboard
    needs the host's positive word. .queued is written only after systemctl accepted the power-off."""
    _run(tmp_path, "aaaa-bbbb-cccc\n")
    assert (tmp_path / "shutdown-request.queued").exists()
    assert not (tmp_path / "shutdown-request.failed").exists()


def test_failed_poweroff_never_writes_queued(tmp_path):
    _run(tmp_path, "aaaa-bbbb-cccc\n", systemctl_exit=1)
    assert not (tmp_path / "shutdown-request.queued").exists()


def test_stale_request_writes_neither_marker(tmp_path):
    _run(tmp_path, "OLD-BOOT-ID\n")
    assert not (tmp_path / "shutdown-request.queued").exists()
    assert not (tmp_path / "shutdown-request.failed").exists()


def test_api_reports_queued_and_a_restarted_app_forgets_old_outcomes(tmp_path):
    client, target = _client(tmp_path)
    queued = target.with_name(target.name + ".queued")
    queued.write_text("queued\n")
    assert client.get("/api/v1/system/shutdown").json() == {"pending": False, "queued": True, "failed": False}
    # the app restarts (or the board rebooted): an outcome from before must not read as current success
    capture_dir = tmp_path / "cache" / "captures"
    fresh = TestClient(create_app(LiveStateCache(), DiscoveryMode(), capture_dir))
    assert not queued.exists()
    assert fresh.get("/api/v1/system/shutdown").json() == {"pending": False, "queued": False, "failed": False}


def test_path_unit_does_not_watch_the_failure_marker():
    """The marker must not re-trigger the service (that would loop)."""
    assert "shutdown-request.failed" not in PATH_UNIT.read_text()
    assert "PathExists=/home/arduino/ArduinoApps/unoq_nmea2000_capture/.cache/shutdown-request\n" in PATH_UNIT.read_text()


def test_api_reports_a_host_failure_and_a_new_request_clears_it(tmp_path):
    client, target = _client(tmp_path)
    marker = target.with_name(target.name + ".failed")
    marker.write_text("systemctl poweroff exit 1\n")
    assert client.get("/api/v1/system/shutdown").json() == {"pending": False, "queued": False, "failed": True}
    if os.path.exists("/proc/sys/kernel/random/boot_id"):
        assert client.post("/api/v1/system/shutdown", json={"confirm": "shutdown"}).status_code == 202
        assert not marker.exists()  # an old failure is not mistaken for this request's result
        assert client.get("/api/v1/system/shutdown").json() == {"pending": True, "queued": False, "failed": False}


def test_script_is_valid_sh():
    assert subprocess.run(["sh", "-n", str(SCRIPT)]).returncode == 0
    assert subprocess.run(["bash", "-n", "scripts/install_shutdown.sh"]).returncode == 0


# --- units -----------------------------------------------------------------------


def test_path_unit_watches_the_apps_bind_mounted_cache_and_waits_for_home():
    text = PATH_UNIT.read_text()
    # container /app/.cache/shutdown-request is this path on the host; the app writes it via
    # request_file(capture_dir.parent) with capture_dir = /app/.cache/captures
    assert "PathExists=/home/arduino/ArduinoApps/unoq_nmea2000_capture/.cache/shutdown-request" in text
    assert "Unit=unoq-shutdown.service" in text
    assert "RequiresMountsFor=/home/arduino" in text
    assert "systemctl enable --now /home/" not in text.replace("do NOT `systemctl enable /home/...`", "")
    assert "sudo cp /home/arduino/unoq-nmea2000/deploy/unoq-shutdown.path /etc/systemd/system/" in " ".join(text.split())


def test_service_runs_the_script_on_the_same_path_and_waits_for_home():
    text = SERVICE_UNIT.read_text()
    assert "RequiresMountsFor=/home/arduino" in text
    assert "Type=oneshot" in text and "[Install]" not in text  # only the path unit starts it
    assert ("unoq_shutdown.sh /home/arduino/ArduinoApps/unoq_nmea2000_capture/.cache/shutdown-request") in text


def test_install_script_copies_units_never_links_and_preflight_checks_them():
    install = Path("scripts/install_shutdown.sh").read_text()
    assert "sudo cp " in install and "systemctl enable --now unoq-shutdown.path" in install
    assert "systemctl enable --now /home" not in install
    preflight = Path("scripts/boat_preflight.sh").read_text()
    assert "unoq-shutdown.path" in preflight and "! test -L /etc/systemd/system/unoq-shutdown.path" in preflight


# --- UI --------------------------------------------------------------------------


def test_button_is_in_the_setup_tab_only_and_needs_two_taps():
    from html.parser import HTMLParser

    class Ids(HTMLParser):
        def __init__(self):
            super().__init__()
            self.cur, self.ids = None, {}

        def handle_starttag(self, tag, attrs):
            a = dict(attrs)
            if tag == "main":
                self.cur = a["id"]
                self.ids[self.cur] = set()
            elif self.cur and "id" in a:
                self.ids[self.cur].add(a["id"])

        def handle_endtag(self, tag):
            if tag == "main":
                self.cur = None

    parser = Ids()
    parser.feed(HTML)
    assert "shutdown-button" in parser.ids["view-setup"]
    assert "shutdown-button" not in parser.ids["view-dashboard"]
    assert "Tap again to shut down" in HTML  # armed state, so one stray tap does nothing
    assert 'JSON.stringify({ confirm: "shutdown" })' in HTML
    rule = HTML.split("#shutdown-button {")[1].split("}")[0]
    assert "min-height: 44px" in rule


def test_ui_never_claims_it_is_safe_after_a_dropped_connection():
    """Review finding: a failed poll (phone wifi drop) was shown as 'Shutting down... safe to cut power'."""
    catch_branch = HTML.split("} catch (e) {\n          show(\"Lost contact")[1].split("return;")[0]
    assert "Shutdown started" not in catch_branch
    assert "did NOT shut down" in HTML          # tells the owner how to interpret the silence
    assert "state.failed" in HTML and "could not start the shutdown" in HTML
    assert HTML.count("Shutdown started") == 1  # said only on the host's explicit word
    # ...and that word is "queued". "The request file is gone" (pending=false) is NOT success: systemctl may
    # still be about to fail (review finding).
    started_branch = HTML.split("if (state.queued) {")[1].split("return;")[0]
    assert "Shutdown started" in started_branch
    assert "!state.pending" not in HTML


def test_ui_warns_when_the_host_did_not_take_the_request():
    """Without the host unit the board would NOT shut down; the UI must not imply it is safe."""
    assert "/api/v1/system/shutdown" in HTML and "pending" in HTML
    assert "NOT shutting down" in HTML
