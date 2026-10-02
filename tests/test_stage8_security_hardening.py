"""Stage 8.2 -- Security hardening pass.

Covers Requirement 20.1, 20.2, 20.3, 20.7: corrupted replay files are
quarantined and continue (not raised), hostile Web_API/BLE requests are
rejected with a named error while other clients keep being served, and the
secret-scan lint step detects a planted plaintext secret.

Each test below is a regression per steering 10: it fails on the pre-fix
code (iter_records raising on a bad line, read_characteristic raising a
bare KeyError, no secret scanner existing at all) and passes with the fix.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ble.publisher import MockBLEPublisher, UnknownCharacteristicError
from scripts.secret_scan import scan_text
from service.raw_log import CorruptLine, iter_records
from simulator.replay import load_fixture_frames


def test_corrupted_replay_line_is_quarantined_not_raised(tmp_path: Path):
    """Feature: nmea2000-diagnostic-platform, Requirement 20.1/20.3: a
    corrupted/truncated line in a replay/log file is quarantined as a
    CorruptLine, and iter_records continues past it to yield every
    subsequent valid record rather than raising."""
    path = tmp_path / "corrupted.jsonl"
    path.write_text(
        '{"capture_time": 1.0, "can_id": 1, "priority": 6, "pgn": 127488, '
        '"source": 1, "dest": null, "dlc": 8, "payload_hex": "00", "malformed": false}\n'
        "{this is not valid json at all\n"
        '{"capture_time": 2.0, "can_id": 2, "priority": 6, "pgn": 127488, '
        '"source": 2, "dest": null, "dlc": 8, "payload_hex": "00", "malformed": false}\n'
    )

    records = list(iter_records(path))

    assert len(records) == 3
    assert isinstance(records[0], dict) and records[0]["source"] == 1
    assert isinstance(records[1], CorruptLine)
    assert records[1].line_number == 2
    assert isinstance(records[2], dict) and records[2]["source"] == 2


def test_corrupted_fixture_line_is_skipped_by_load_fixture_frames(tmp_path: Path):
    """Feature: nmea2000-diagnostic-platform, Requirement 20.1/20.3: a
    CorruptLine (or a dict missing a required field) reaching
    load_fixture_frames is skipped, not raised -- replay of the surrounding
    valid frames is unaffected."""
    path = tmp_path / "corrupted.jsonl"
    path.write_text(
        '{"capture_time": 1.0, "can_id": 1, "priority": 6, "pgn": 127488, '
        '"source": 1, "dest": null, "dlc": 8, "payload_hex": "00", "malformed": false}\n'
        "not json\n"
        '{"missing": "required fields"}\n'
    )

    frames = load_fixture_frames(list(iter_records(path)))

    assert len(frames) == 1
    assert frames[0].source_address == 1


def test_log_endpoint_reports_corrupt_lines_without_failing(tmp_path: Path):
    """Feature: nmea2000-diagnostic-platform, Requirement 20.1/20.3,
    14.5-adjacent: GET /api/v1/logs/{id} on a corrupted file returns 200 with
    the valid records plus a corrupt_lines count, never a 500."""
    from fastapi.testclient import TestClient

    from protocol.domain import VesselState
    from service.cache import LiveStateCache
    from service.discovery import DiscoveryMode
    from web.api import create_app

    capture_dir = tmp_path
    log_path = capture_dir / "bad.jsonl"
    log_path.write_text(
        '{"capture_time": 1.0, "can_id": 1, "priority": 6, "pgn": 127488, '
        '"source": 1, "dest": null, "dlc": 8, "payload_hex": "00", "malformed": false}\n'
        "garbage line, not json\n"
    )

    app = create_app(LiveStateCache(VesselState()), DiscoveryMode(), capture_dir)
    client = TestClient(app)

    response = client.get("/api/v1/logs/bad.jsonl")

    assert response.status_code == 200
    body = response.json()
    assert len(body["records"]) == 1
    assert body["corrupt_lines"] == 1


def test_unknown_ble_characteristic_rejected_by_name_not_keyerror():
    """Feature: nmea2000-diagnostic-platform, Requirement 20.2: a hostile or
    malformed BLE read naming a characteristic outside GATT_SCHEMA is
    rejected with UnknownCharacteristicError (named reason), not a bare
    unhandled KeyError."""
    publisher = MockBLEPublisher(require_pairing=False)

    with pytest.raises(UnknownCharacteristicError):
        publisher.read_characteristic("not_a_real_characteristic")


def test_hostile_ble_request_does_not_affect_other_clients():
    """Feature: nmea2000-diagnostic-platform, Requirement 20.2: after a
    hostile request is rejected, a valid client reading a real
    characteristic keeps being served normally."""
    publisher = MockBLEPublisher(require_pairing=False)

    with pytest.raises(UnknownCharacteristicError):
        publisher.read_characteristic("garbage")

    # The publisher itself is unaffected -- a legitimate read still works.
    assert publisher.read_characteristic("live_engine") is None  # no data published yet, but no raise


def test_secret_scanner_detects_a_planted_plaintext_secret():
    """Feature: nmea2000-diagnostic-platform, Requirement 20.7: the
    secret-scan step actually detects a plaintext secret, proving it is not
    a no-op lint step."""
    planted = 'api_key = "sk_live_abcdef1234567890abcdef"\n'
    findings = scan_text(planted)
    assert findings, "secret scanner found nothing in a planted plaintext secret"


def test_secret_scanner_does_not_flag_a_bare_env_var_reference():
    """Feature: nmea2000-diagnostic-platform, Requirement 20.7: a reference
    to an env-var-style name with no literal value attached (the normal,
    correct pattern used throughout this codebase for config) is not a false
    positive."""
    clean = 'webhook_secret = os.environ["STRIPE_WEBHOOK_SECRET"]\n'
    findings = scan_text(clean)
    assert not findings


def test_secret_scanner_ignores_placeholder_values():
    """Feature: nmea2000-diagnostic-platform, Requirement 20.7: a documented
    placeholder like "changeme" is not treated as a real leaked secret."""
    placeholder = 'password = "changeme"\n'
    findings = scan_text(placeholder)
    assert not findings


def test_secret_scanner_does_not_flag_its_own_pattern_literals_or_planted_test_fixture(capsys):
    """Feature: nmea2000-diagnostic-platform, Requirement 20.7: running the
    secret scanner's own `main()` against the real repo must not flag the
    scanner's own source (whose pattern definitions necessarily contain the
    literal words "api_key"/"secret"/... as regex source, not real
    credentials) or this test file's own planted-secret fixture above
    (`test_secret_scanner_detects_a_planted_plaintext_secret`'s `sk_live_...`
    string, which exists specifically to prove detection works). This failed
    on a real `make lint` run before the self-exempt list was added: both
    files tripped the generic assigned-secret heuristic on themselves."""
    from scripts import secret_scan

    rc = secret_scan.main()
    captured = capsys.readouterr()
    assert rc == 0, f"make lint's secret-scan step must not false-positive on its own source/tests:\n{captured.err}"
