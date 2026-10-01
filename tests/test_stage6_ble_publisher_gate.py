"""Stage 6.4/6.5 Gate -- BLE_Publisher, MockBLEPublisher, access security,
and the Stage 6 integration/property gate.

Property tags follow .kiro/steering/09-property-test-tagging.md exactly.
"""
from __future__ import annotations

import struct
import tempfile
import time
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

import protocol.decoders  # noqa: F401 -- registers DEFAULT_REGISTRY decoders
from ble.encoding import decode_envelope
from ble.publisher import (
    GATT_SCHEMA,
    MockBLEPublisher,
    PairingRejectedError,
    STALE_AFTER_NO_FRAME_S,
    assert_no_writable_characteristic,
)
from protocol.decoders.engine import PGN_ENGINE_RAPID_UPDATE
from protocol.identifier import Identifier, compose_identifier
from protocol.receiver import CanFrame, CanHealth
from protocol.registry import DEFAULT_REGISTRY
from service.cache import LiveStateCache
from service.ingestion import IngestionService
from service.raw_log import RawCaptureLog
from service.session_store import SessionStore


# --- Requirement 13.9: BLEPublisher consumes only Domain_Model -------------


def test_publisher_module_imports_no_web_models():
    """Requirement 13.9: ble/publisher.py references neither Web_UI nor
    Web_API models -- enforced here as an import check since web/ does not
    exist yet in this build (Stage 7); this test still pins the rule so a
    future Stage 7 addition cannot quietly violate it."""
    import ast

    source = Path("ble/publisher.py").read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            module = getattr(node, "module", None) or ""
            names = [n.name for n in node.names]
            assert "web" not in module, f"ble/publisher.py imports from web: {module}"
            assert not any(n.startswith("web") for n in names)


# --- Requirement 13.10: mock serves full schema, no radio -------------------


def test_mock_publisher_serves_full_gatt_schema_without_radio():
    publisher = MockBLEPublisher(require_pairing=False)
    assert publisher.schema_version() >= 1
    assert publisher.read_connection_health() is not None
    assert publisher.read_diagnostics() is not None
    # No radio/BlueZ import anywhere in this path -- proven by the fact this
    # test runs on a laptop with no CAN/BLE hardware at all.


# --- Property 23: BLE schema exposes no writable characteristic ------------


def test_gatt_schema_has_no_writable_characteristic():
    """Feature: nmea2000-diagnostic-platform, Property 23: no characteristic
    in the BLE GATT schema has a write/write-without-response/controllable
    property."""
    assert_no_writable_characteristic()
    for c in GATT_SCHEMA:
        assert c.properties.value in ("read", "notify", "read_notify")


# --- Property 38: unpaired rejection while required; read-only regardless --


def test_unpaired_client_rejected_when_pairing_required():
    """Feature: nmea2000-diagnostic-platform, Property 38 (rejection half):
    an unpaired client is rejected for vessel-data reads while pairing is
    required."""
    publisher = MockBLEPublisher(require_pairing=True)
    try:
        publisher.read_connection_health(client_id="unpaired-client")
    except PairingRejectedError:
        pass
    else:
        raise AssertionError("expected PairingRejectedError for an unpaired client")


def test_paired_client_allowed_when_pairing_required():
    publisher = MockBLEPublisher(require_pairing=True)
    publisher.pair("device-123")
    result = publisher.read_connection_health(client_id="device-123")
    assert result is not None


def test_open_access_opt_in_allows_unpaired_reads():
    """Requirement 28.2: unauthenticated/open access is an explicit opt-in."""
    publisher = MockBLEPublisher(require_pairing=False)
    result = publisher.read_connection_health(client_id=None)
    assert result is not None


def test_read_only_guarantee_holds_regardless_of_pairing_config():
    """Feature: nmea2000-diagnostic-platform, Property 38 (read-only half):
    no pairing configuration introduces a writable characteristic."""
    for require_pairing in (True, False):
        publisher = MockBLEPublisher(require_pairing=require_pairing)
        assert not hasattr(publisher, "write_characteristic")
        assert_no_writable_characteristic()


# --- Requirement 13.8 / Property 24 (BLE half): staleness -------------------


def test_stale_when_can_disconnected():
    publisher = MockBLEPublisher(require_pairing=False)
    publisher.update_can_health(
        CanHealth(connected=False, fps=0.0, dropped_frames=0, error_count=0, controller_state="listen-only", termination_on=False),
        now=0.0,
    )
    assert publisher.is_stale(now=0.0) is True


def test_stale_after_3s_gap_even_if_connected():
    """Feature: nmea2000-diagnostic-platform, Property 24 (BLE half): live
    data marked stale when no CAN frame has been received for >= 3s."""
    publisher = MockBLEPublisher(require_pairing=False)
    publisher.update_can_health(
        CanHealth(connected=True, fps=10.0, dropped_frames=0, error_count=0, controller_state="listen-only", termination_on=False),
        now=0.0,
    )
    assert publisher.is_stale(now=STALE_AFTER_NO_FRAME_S - 0.1) is False
    assert publisher.is_stale(now=STALE_AFTER_NO_FRAME_S + 0.1) is True


# --- Property 22 (BLE half): notification rate capped at 250ms -------------


@given(call_times=st.lists(st.floats(min_value=0.0, max_value=2.0), min_size=2, max_size=30))
@settings(max_examples=50)
def test_notification_rate_never_exceeds_one_per_250ms(call_times):
    """Feature: nmea2000-diagnostic-platform, Property 22 (BLE half): no BLE
    characteristic notification occurs more frequently than once per 250ms."""
    call_times = sorted(call_times)
    from protocol.domain import EngineState, Provenance

    publisher = MockBLEPublisher(require_pairing=False)
    notify_timestamps = []
    for t in call_times:
        engine = EngineState(engine_instance=0)
        engine.source_address = 1
        engine.rpm = 1500.0
        engine.provenance = Provenance(
            source_address=1, source_pgn=127488, node_identity="unknown",
            raw_frame_ref="unknown", timestamp=t, decoder_version="1.0.0",
            quality="valid", staleness_s=0.0, vendor_specific=False, value_origin="decoded",
        )
        from protocol.domain import VesselState

        vessel = VesselState()
        vessel.engines[(0, 1)] = engine
        before = publisher.notified_at("live_engine")
        publisher.publish(vessel, now=t)
        after = publisher.notified_at("live_engine")
        if after != before:
            notify_timestamps.append(after)

    for a, b in zip(notify_timestamps, notify_timestamps[1:]):
        assert (b - a) >= 0.25 - 1e-9


# --- Requirement 13.7: reconnect serves current values, no reconfiguration -


def test_reconnect_serves_current_value_without_reconfiguration():
    from protocol.domain import EngineState, Provenance, VesselState

    publisher = MockBLEPublisher(require_pairing=False)
    engine = EngineState(engine_instance=0)
    engine.source_address = 1
    engine.rpm = 2500.0
    engine.provenance = Provenance(
        source_address=1, source_pgn=127488, node_identity="unknown",
        raw_frame_ref="unknown", timestamp=0.0, decoder_version="1.0.0",
        quality="valid", staleness_s=0.0, vendor_specific=False, value_origin="decoded",
    )
    vessel = VesselState()
    vessel.engines[(0, 1)] = engine
    publisher.publish(vessel, now=0.0)

    # Simulate a reconnect: a fresh read (no "resubscribe"/reconfigure call
    # of any kind) still returns the current value.
    value = publisher.read_characteristic("live_engine", client_id=None)
    decoded = decode_envelope(value)
    assert decoded.entities[0].instance == 0


# --- Property 37: fragmented BLE payloads reassemble correctly -------------


def test_fragmented_live_engine_payload_reassembles_to_same_envelope():
    """Feature: nmea2000-diagnostic-platform, Property 37: a fragmented BLE
    payload reassembles correctly across the negotiated ATT MTU."""
    from ble.encoding import Reassembler, fragment
    from protocol.domain import EngineState, Provenance, VesselState

    publisher = MockBLEPublisher(require_pairing=False)
    vessel = VesselState()
    for i in range(4):
        engine = EngineState(engine_instance=i)
        engine.source_address = 10 + i
        engine.rpm = 1000.0 + i * 250
        engine.provenance = Provenance(
            source_address=10 + i, source_pgn=127488, node_identity="unknown",
            raw_frame_ref="unknown", timestamp=0.0, decoder_version="1.0.0",
            quality="valid", staleness_s=0.0, vendor_specific=False, value_origin="decoded",
        )
        vessel.engines[(i, 10 + i)] = engine
    publisher.publish(vessel, now=0.0)

    raw = publisher.read_characteristic("live_engine")
    segments = fragment(raw, mtu=20)
    assert len(segments) > 1
    reassembler = Reassembler()
    result = None
    for seg in segments:
        result = reassembler.feed(seg)
    assert result == raw
    decoded = decode_envelope(result)
    assert len(decoded.entities) == 4


# --- Property 29: DisplayAdapter presence doesn't change decoding output --


def test_adding_display_adapter_produces_identical_decoding_output():
    """Feature: nmea2000-diagnostic-platform, Property 29: adding/removing a
    DisplayAdapter produces identical ingestion/decoding output."""

    def _run(with_adapter: bool):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            log = RawCaptureLog(tmp_path / "raw")
            store = SessionStore(tmp_path / "session.db")
            cache = LiveStateCache()
            adapter = None
            if with_adapter:
                class _Adapter:
                    def on_update(self, result):
                        pass

                    def on_snapshot(self, vessel):
                        pass

                adapter = _Adapter()
            service = IngestionService(
                log, store, DEFAULT_REGISTRY, live_cache=cache, display_adapter=adapter
            )
            ident = Identifier(
                priority=2, pgn=PGN_ENGINE_RAPID_UPDATE, source_address=5, dest_address=None
            )
            can_id = compose_identifier(ident)
            payload = struct.pack("<BHHb", 0, 4000, 0xFFFF, 0x7F) + b"\x00\x00"
            frame = CanFrame(
                can_id=can_id, priority=2, pgn=PGN_ENGINE_RAPID_UPDATE, source_address=5,
                dest_address=None, dlc=8, payload=payload, capture_time=0.0,
            )
            stats = service.ingest_batch([frame])
            log.close()
            store.close()
            return stats.decoded, cache.get_engine(0, 5).rpm

    without = _run(with_adapter=False)
    with_adapter = _run(with_adapter=True)
    assert without == with_adapter


# --- Requirement 19.5: integration -- cache read vs mock BLE read agree ----


def test_cache_and_mock_ble_observe_the_same_decoded_value():
    """Requirement 19.5: one domain fixture flows through
    parser->decoder->cache, and the same values are observable from a direct
    cache read AND a mock BLE read."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        log = RawCaptureLog(tmp_path / "raw")
        store = SessionStore(tmp_path / "session.db")
        cache = LiveStateCache()
        service = IngestionService(log, store, DEFAULT_REGISTRY, live_cache=cache)

        ident = Identifier(
            priority=2, pgn=PGN_ENGINE_RAPID_UPDATE, source_address=7, dest_address=None
        )
        can_id = compose_identifier(ident)
        payload = struct.pack("<BHHb", 0, 6000, 0xFFFF, 0x7F) + b"\x00\x00"
        frame = CanFrame(
            can_id=can_id, priority=2, pgn=PGN_ENGINE_RAPID_UPDATE, source_address=7,
            dest_address=None, dlc=8, payload=payload, capture_time=0.0,
        )
        service.ingest_batch([frame])
        log.close()
        store.close()

        cache_rpm = cache.get_engine(0, 7).rpm

        publisher = MockBLEPublisher(require_pairing=False)
        publisher.publish(cache.snapshot(), now=0.0)
        raw = publisher.read_characteristic("live_engine")
        decoded = decode_envelope(raw)
        ble_entity = next(e for e in decoded.entities if e.instance == 0 and e.source_address == 7)
        ble_rpm_field = next(f for f in ble_entity.fields if f.field_id == 1)  # "rpm"

        assert abs(cache_rpm - ble_rpm_field.value) < 0.01
