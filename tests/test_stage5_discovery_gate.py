"""Stage 5.1 Gate -- Discovery_Mode: NodeKey reconciliation, message rate,
offline/online lifecycle, address conflicts.

Property tags follow .kiro/steering/09-property-test-tagging.md exactly.
"""
from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from protocol.decoders.identity import IsoName
from protocol.receiver import CanFrame
from protocol.registry import DecodeResult
from service.discovery import DiscoveryMode, PGN_ISO_ADDRESS_CLAIM, _name_key, _provisional_key


def _frame(source_address: int, pgn: int = 127488) -> CanFrame:
    return CanFrame(
        can_id=0,
        priority=6,
        pgn=pgn,
        source_address=source_address,
        dest_address=None,
        dlc=8,
        payload=b"\x00" * 8,
        capture_time=0.0,
    )


def _iso_name(unique_number: int, manufacturer_code: int = 999) -> IsoName:
    raw = (unique_number & 0x1FFFFF) | ((manufacturer_code & 0x7FF) << 21)
    return IsoName(raw)


# --- Property 17: message rate = count in trailing 60s window --------------


@given(timestamps=st.lists(st.floats(min_value=0.0, max_value=120.0), min_size=1, max_size=50))
@settings(max_examples=100)
def test_message_rate_equals_trailing_60s_count(timestamps):
    """Feature: nmea2000-diagnostic-platform, Property 17: recorded message
    rate equals the count of messages observed within the trailing 60-second
    window."""
    timestamps = sorted(timestamps)
    discovery = DiscoveryMode()
    node_key = _provisional_key(1)
    for t in timestamps:
        discovery.observe(_frame(source_address=1), decoded=None, now=t)
    last_t = timestamps[-1]
    # The window is closed/inclusive at the 60s boundary (a timestamp exactly
    # 60s before the latest frame is still "within" the trailing window),
    # matching service/discovery.py's eviction rule `timestamps[0] < cutoff`
    # (evicts strictly-older-than-cutoff, keeps anything at or after it).
    # design.md's Property 17 text does not pin a boundary convention either
    # way; this test is written to the implementation's documented rule.
    expected = sum(1 for t in timestamps if t >= last_t - 60.0)
    assert discovery.nodes[node_key].message_rate == expected


# --- Property 18: offline/online round-trip ---------------------------------


def test_offline_after_silence_exceeding_threshold_then_online_on_next_frame():
    """Feature: nmea2000-diagnostic-platform, Property 18: a node is marked
    offline exactly when silence exceeds max(3x last interval, 30s), and
    online again on its next frame."""
    discovery = DiscoveryMode()
    node_key = _provisional_key(5)
    discovery.observe(_frame(source_address=5), decoded=None, now=0.0)
    discovery.observe(_frame(source_address=5), decoded=None, now=1.0)  # interval=1s

    # Silence of 29s: below max(3*1, 30)=30s threshold -> still online.
    discovery.check_offline_transitions(now=1.0 + 29.0)
    assert discovery.nodes[node_key].online is True

    # Silence of 31s: exceeds the 30s floor -> offline.
    discovery.check_offline_transitions(now=1.0 + 31.0)
    assert discovery.nodes[node_key].online is False

    # Next frame brings it back online.
    discovery.observe(_frame(source_address=5), decoded=None, now=1.0 + 31.5)
    assert discovery.nodes[node_key].online is True


def test_offline_threshold_uses_interval_multiplier_when_larger_than_floor():
    discovery = DiscoveryMode()
    node_key = _provisional_key(6)
    discovery.observe(_frame(source_address=6), decoded=None, now=0.0)
    discovery.observe(_frame(source_address=6), decoded=None, now=20.0)  # interval=20s
    # threshold = max(3*20, 30) = 60s
    discovery.check_offline_transitions(now=20.0 + 59.0)
    assert discovery.nodes[node_key].online is True
    discovery.check_offline_transitions(now=20.0 + 61.0)
    assert discovery.nodes[node_key].online is False


# --- Property 39: NodeKey stable across source-address change --------------


def test_nodekey_retained_when_name_moves_to_new_source_address():
    """Feature: nmea2000-diagnostic-platform, Property 39: when a node's NAME
    reappears at a new source address, the NodeKey is unchanged and
    current_source_address updates in place."""
    discovery = DiscoveryMode()
    name = _iso_name(unique_number=12345)
    name_key = _name_key(name.raw)

    # First seen at SA=10, claims via Address Claim.
    discovery.observe(_frame(source_address=10), decoded=None, now=0.0)
    discovery.observe(
        _frame(source_address=10, pgn=PGN_ISO_ADDRESS_CLAIM),
        decoded=DecodeResult(pgn=PGN_ISO_ADDRESS_CLAIM, source_address=10, entity=name),
        now=1.0,
    )
    assert discovery.nodes[name_key].current_source_address == 10
    first_seen = discovery.nodes[name_key].first_seen

    # Same NAME reappears at a NEW source address (e.g. after a re-claim).
    discovery.observe(_frame(source_address=20), decoded=None, now=2.0)
    discovery.observe(
        _frame(source_address=20, pgn=PGN_ISO_ADDRESS_CLAIM),
        decoded=DecodeResult(pgn=PGN_ISO_ADDRESS_CLAIM, source_address=20, entity=name),
        now=3.0,
    )

    assert name_key in discovery.nodes  # same NodeKey retained
    assert discovery.nodes[name_key].current_source_address == 20
    assert discovery.nodes[name_key].first_seen == first_seen  # observations carried over
    assert len(discovery.nodes) == 1  # old provisional record was reconciled away, not duplicated


# --- Property 19: address conflict retains both NodeKeys --------------------


def test_two_distinct_names_claiming_same_sa_both_retained_with_conflict_recorded():
    """Feature: nmea2000-diagnostic-platform, Property 19: two distinct NAMEs
    claiming the same source address are both retained, and the conflict is
    recorded in each affected record's address_conflicts."""
    discovery = DiscoveryMode()
    name_a = _iso_name(unique_number=111, manufacturer_code=1)
    name_b = _iso_name(unique_number=222, manufacturer_code=2)
    key_a = _name_key(name_a.raw)
    key_b = _name_key(name_b.raw)

    discovery.observe(_frame(source_address=42), decoded=None, now=0.0)
    discovery.observe(
        _frame(source_address=42, pgn=PGN_ISO_ADDRESS_CLAIM),
        decoded=DecodeResult(pgn=PGN_ISO_ADDRESS_CLAIM, source_address=42, entity=name_a),
        now=1.0,
    )
    # A second, distinct NAME claims the SAME source address.
    discovery.observe(_frame(source_address=42), decoded=None, now=2.0)
    discovery.observe(
        _frame(source_address=42, pgn=PGN_ISO_ADDRESS_CLAIM),
        decoded=DecodeResult(pgn=PGN_ISO_ADDRESS_CLAIM, source_address=42, entity=name_b),
        now=3.0,
    )

    assert key_a in discovery.nodes
    assert key_b in discovery.nodes
    assert 42 in discovery.nodes[key_a].address_conflicts
    assert 42 in discovery.nodes[key_b].address_conflicts


# --- Requirement 10.12: exposure without manufacturer exclusion ------------


def test_unknown_and_known_nodes_both_exposed():
    discovery = DiscoveryMode()
    discovery.observe(_frame(source_address=1), decoded=None, now=0.0)  # stays provisional
    name = _iso_name(unique_number=99)
    discovery.observe(_frame(source_address=2), decoded=None, now=0.0)
    discovery.observe(
        _frame(source_address=2, pgn=PGN_ISO_ADDRESS_CLAIM),
        decoded=DecodeResult(pgn=PGN_ISO_ADDRESS_CLAIM, source_address=2, entity=name),
        now=1.0,
    )
    assert len(discovery.unknown_nodes()) == 1
    assert len(discovery.known_nodes()) == 1
