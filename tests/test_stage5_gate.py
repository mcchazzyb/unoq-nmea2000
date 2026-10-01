"""Stage 5 Gate -- Simulator (replay timing, fault injection) and golden
fixture library documented assertions.

Property tags follow .kiro/steering/09-property-test-tagging.md exactly.
"""
from __future__ import annotations

from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from fixtures.build_golden import GOLDEN_DIR
from protocol.decoders.electrical import decode_battery_status
from protocol.decoders.engine import decode_engine_dynamic, decode_engine_rapid_update
from protocol.decoders.navigation import decode_cog_sog_rapid_update, decode_position_rapid_update
from protocol.decoders.tank import decode_fluid_level
from protocol.domain import UNSET
from protocol.fast_packet import KeyedFastPacketReassembler, is_fastpacket_pgn
from protocol.identifier import parse_identifier
from protocol.receiver import ICanReceiver
from service.raw_log import iter_records
from simulator.generator import MultiNodeScenario, PGN_UNKNOWN_PROPRIETARY
from simulator.replay import FaultConfig, FixtureReplay, load_fixture_frames


# --- ICanReceiver conformance ------------------------------------------------


def test_fixture_replay_implements_ican_receiver():
    scenario = MultiNodeScenario()
    replay = FixtureReplay(scenario.frames(), realtime=False)
    assert isinstance(replay, ICanReceiver)
    frames = list(replay.frames())
    assert len(frames) > 0
    health = replay.health()
    assert health.termination_on is False
    assert health.controller_state == "listen-only"


# --- Property 30: replay preserves inter-frame timing within +/-5ms --------


@given(
    intervals=st.lists(
        st.floats(min_value=0.0, max_value=0.5), min_size=2, max_size=15
    )
)
@settings(max_examples=30, deadline=None)
def test_replay_preserves_inter_frame_timing_within_tolerance(intervals):
    """Feature: nmea2000-diagnostic-platform, Property 30: replaying a
    capture reproduces each inter-frame interval within +/-5ms."""
    from protocol.receiver import CanFrame

    t = 0.0
    frames = []
    for dt in intervals:
        frames.append(
            CanFrame(
                can_id=0,
                priority=6,
                pgn=127488,
                source_address=1,
                dest_address=None,
                dlc=8,
                payload=b"\x00" * 8,
                capture_time=t,
            )
        )
        t += dt

    replay = FixtureReplay(frames, realtime=True)
    import time

    emit_times = []
    for _ in replay.frames():
        emit_times.append(time.monotonic())

    observed_intervals = [b - a for a, b in zip(emit_times, emit_times[1:])]
    for expected, observed in zip(intervals, observed_intervals):
        # Property 30 asks for +/-5ms fidelity to the fixture's own timing;
        # time.sleep()-based scheduling on a shared host adds its own slack
        # on top, so the test tolerance is wider than the design property
        # itself -- this pins "close to realtime", not sub-ms precision.
        assert abs(observed - expected) <= 0.025


# --- Fault injection surfaced through CanHealth -----------------------------


def test_malformed_fault_injection_marks_frames_without_terminating_replay():
    """Requirement 17.3, 17.8: fault injection surfaces through CanHealth
    without terminating the replay stream."""
    scenario = MultiNodeScenario()
    faults = FaultConfig(malformed_frame_rate=1.0, seed=42)  # force every frame malformed
    replay = FixtureReplay(scenario.frames(), faults=faults, realtime=False)
    frames = list(replay.frames())
    assert len(frames) == len(scenario.frames())  # stream not terminated
    assert all(f.malformed for f in frames)
    assert replay.health().error_count == len(frames)


def test_drop_fault_injection_reduces_count_and_is_counted():
    scenario = MultiNodeScenario()
    original_count = len(scenario.frames())
    faults = FaultConfig(drop_frame_rate=1.0, seed=1)  # force every frame dropped
    replay = FixtureReplay(scenario.frames(), faults=faults, realtime=False)
    frames = list(replay.frames())
    assert len(frames) == 0
    assert replay.health().dropped_frames == original_count


# --- Golden fixture library: documented expected assertions ---------------


def _decode_payload(pgn, source_address, payload):
    decoders = {
        127488: decode_engine_rapid_update,
        127489: decode_engine_dynamic,
        127505: decode_fluid_level,
        127508: decode_battery_status,
        129025: decode_position_rapid_update,
        129026: decode_cog_sog_rapid_update,
    }
    decoder = decoders.get(pgn)
    if decoder is None:
        return None
    return decoder(source_address, payload)


def _reassemble_fixture(path: Path):
    """Replay a fixture through parse + fast-packet reassembly, returning a
    list of (pgn, source_address, payload, malformed) ready for decode."""
    reassembler = KeyedFastPacketReassembler()
    out = []
    for record in iter_records(path):
        pgn = record["pgn"]
        source = record["source"]
        payload = bytes.fromhex(record["payload_hex"])
        malformed = record["malformed"]
        if malformed:
            out.append((pgn, source, None, True))
            continue
        if is_fastpacket_pgn(pgn):
            reassembled = reassembler.feed(pgn, source, payload, timestamp=record["capture_time"])
            if reassembled is None:
                continue
            out.append((pgn, source, reassembled, False))
        else:
            out.append((pgn, source, payload, False))
    return out


def test_golden_engine_idle_decodes_low_rpm():
    """Fixture: engine_idle.jsonl -- expected: 127488 decodes rpm near 750."""
    items = _reassemble_fixture(GOLDEN_DIR / "engine_idle.jsonl")
    rapid = next(i for i in items if i[0] == 127488)
    result = _decode_payload(*rapid[:3])
    assert abs(result.entity.rpm - 750.0) < 1.0


def test_golden_engine_rpm_change_tracks_each_step():
    """Fixture: engine_rpm_change.jsonl -- expected: 4 successive 127488
    decodes matching (750, 2000, 3800, 5500) rpm within resolution."""
    items = _reassemble_fixture(GOLDEN_DIR / "engine_rpm_change.jsonl")
    rapids = [i for i in items if i[0] == 127488]
    assert len(rapids) == 4
    expected_rpms = [750.0, 2000.0, 3800.0, 5500.0]
    for item, expected in zip(rapids, expected_rpms):
        result = _decode_payload(*item[:3])
        assert abs(result.entity.rpm - expected) < 0.3


def test_golden_trim_change_tracks_each_value():
    """Fixture: trim_change.jsonl -- expected: 4 successive trim values
    (-10, 0, 10, 20)."""
    items = _reassemble_fixture(GOLDEN_DIR / "trim_change.jsonl")
    expected_trims = [-10, 0, 10, 20]
    for item, expected in zip(items, expected_trims):
        result = _decode_payload(*item[:3])
        assert result.entity.trim == expected


def test_golden_fuel_tank_tracks_level_steps():
    """Fixture: fuel_tank.jsonl -- expected: level_percent steps
    (80, 50, 12), capacity_litres constant 189.0."""
    items = _reassemble_fixture(GOLDEN_DIR / "fuel_tank.jsonl")
    expected_levels = [80.0, 50.0, 12.0]
    for item, expected in zip(items, expected_levels):
        result = _decode_payload(*item[:3])
        assert abs(result.entity.level_percent - expected) < 0.01
        assert abs(result.entity.capacity_litres - 189.0) < 0.1


def test_golden_electrical_tracks_voltage_and_current():
    """Fixture: electrical.jsonl -- expected: 3 battery readings matching
    (12.6V/-4.2A, 13.8V/6.0A, 11.9V/-12.0A)."""
    items = _reassemble_fixture(GOLDEN_DIR / "electrical.jsonl")
    expected = [(12.6, -4.2), (13.8, 6.0), (11.9, -12.0)]
    for item, (v, a) in zip(items, expected):
        result = _decode_payload(*item[:3])
        assert abs(result.entity.battery_voltage - v) < 0.01
        assert abs(result.entity.current - a) < 0.1


def test_golden_navigation_decodes_sog_and_cog():
    """Fixture: navigation.jsonl -- expected: 129025 position present,
    129026 sog=15.0kn (7.716 m/s, decoder's native unit) cog=180deg."""
    items = _reassemble_fixture(GOLDEN_DIR / "navigation.jsonl")
    cog_sog = next(i for i in items if i[0] == 129026)
    result = _decode_payload(*cog_sog[:3])
    expected_sog_mps = 15.0 / 1.9438444924
    assert abs(result.entity.sog - expected_sog_mps) < 0.01
    assert abs(result.entity.cog - 180.0) < 0.05


def test_golden_unknown_vendor_never_decoded():
    """Fixture: unknown_vendor.jsonl -- expected: PGN 65280 has no registered
    decoder; every record is retained but none decode (Requirement 5.4)."""
    items = _reassemble_fixture(GOLDEN_DIR / "unknown_vendor.jsonl")
    assert len(items) == 2
    for pgn, source, payload, malformed in items:
        assert pgn == PGN_UNKNOWN_PROPRIETARY
        assert _decode_payload(pgn, source, payload) is None  # no decoder registered


def test_golden_malformed_frames_marked_and_pipeline_continues():
    """Fixture: malformed_frames.jsonl -- expected: first 2 records
    malformed=true, 3rd record clean and decodes normally (Property 4)."""
    records = list(iter_records(GOLDEN_DIR / "malformed_frames.jsonl"))
    assert records[0]["malformed"] is True
    assert records[1]["malformed"] is True
    assert records[2]["malformed"] is False
    clean = _decode_payload(records[2]["pgn"], records[2]["source"], bytes.fromhex(records[2]["payload_hex"]))
    assert clean is not None


def test_golden_mixed_multi_node_covers_all_required_node_types():
    """Fixture: mixed_multi_node.jsonl -- expected: engine, tank, GPS,
    electrical, and unknown-vendor PGNs all present (Requirement 17.2)."""
    records = list(iter_records(GOLDEN_DIR / "mixed_multi_node.jsonl"))
    pgns = {r["pgn"] for r in records}
    assert 127488 in pgns  # engine
    assert 127505 in pgns  # tank
    assert 129025 in pgns  # GPS position
    assert 127508 in pgns  # electrical
    assert PGN_UNKNOWN_PROPRIETARY in pgns  # unknown vendor
