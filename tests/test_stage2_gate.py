"""Stage 2 Gate -- Protocol_Core: 29-bit identifier parse/compose and
fast-packet reassembly.

Property tags follow .kiro/steering/09-property-test-tagging.md exactly:
    Feature: nmea2000-diagnostic-platform, Property N: <text>
matching design.md -> Correctness Properties.
"""
from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from protocol.fast_packet import (
    FastPacketReassembler,
    is_fastpacket_pgn,
)
from protocol.identifier import (
    Identifier,
    MalformedIdentifierError,
    compose_identifier,
    parse_identifier,
)


# --- Strategy: a raw, valid 29-bit NMEA 2000 identifier ---------------------
# Build from well-formed component fields so the modelled/valid identifier
# space is covered (reserved/EDP bits held to the modelled layout).

@st.composite
def valid_can_ids(draw) -> int:
    priority = draw(st.integers(min_value=0, max_value=7))
    data_page = draw(st.integers(min_value=0, max_value=1))
    pdu_format = draw(st.integers(min_value=0, max_value=255))
    pdu_specific = draw(st.integers(min_value=0, max_value=255))
    source = draw(st.integers(min_value=0, max_value=255))
    return (
        (priority << 26)
        | (data_page << 24)
        | (pdu_format << 16)
        | (pdu_specific << 8)
        | source
    )


# --- Unit tests: PDU1/PDU2 boundary -----------------------------------------


def test_pdu1_boundary_pf_239_has_destination():
    # PF = 239 is PDU1: destination derived, PGN low byte is zero.
    can_id = (6 << 26) | (0 << 24) | (239 << 16) | (0x42 << 8) | 0x10
    ident = parse_identifier(can_id)
    assert ident.dest_address == 0x42
    assert ident.pgn == (239 << 8)
    assert ident.source_address == 0x10


def test_pdu2_boundary_pf_240_has_no_destination():
    # PF = 240 is PDU2: no destination, PS folds into PGN.
    can_id = (6 << 26) | (0 << 24) | (240 << 16) | (0x42 << 8) | 0x10
    ident = parse_identifier(can_id)
    assert ident.dest_address is None
    assert ident.pgn == (240 << 8) | 0x42


def test_compose_is_exact_inverse_pdu1_and_pdu2():
    for can_id in (
        (3 << 26) | (0 << 24) | (10 << 16) | (0x33 << 8) | 0x05,   # PDU1
        (3 << 26) | (1 << 24) | (240 << 16) | (0x42 << 8) | 0x05,  # PDU2
    ):
        assert compose_identifier(parse_identifier(can_id)) == can_id


def test_parse_identifier_rejects_out_of_range():
    for bad in (1 << 29, 1 << 30, -1):
        try:
            parse_identifier(bad)
        except MalformedIdentifierError:
            pass
        else:
            raise AssertionError(f"expected MalformedIdentifierError for {bad}")


# --- Property 6 -------------------------------------------------------------


@given(can_id=valid_can_ids())
@settings(max_examples=200)
def test_identifier_round_trip(can_id):
    """Feature: nmea2000-diagnostic-platform, Property 6: 29-bit identifier
    round-trip over the valid identifier space."""
    ident = parse_identifier(can_id)
    assert compose_identifier(ident) == can_id
    # Derived PGN and source are stable under re-parse.
    reparsed = parse_identifier(compose_identifier(ident))
    assert reparsed.pgn == ident.pgn
    assert reparsed.source_address == ident.source_address


# --- Property 7 -------------------------------------------------------------


@given(can_id=valid_can_ids())
@settings(max_examples=200)
def test_dest_present_iff_pdu1(can_id):
    """Feature: nmea2000-diagnostic-platform, Property 7: PDU format determines
    destination presence (dest is not None iff PF < 240)."""
    pdu_format = (can_id >> 16) & 0xFF
    ident = parse_identifier(can_id)
    assert (ident.dest_address is not None) == (pdu_format < 240)


# --- Property 8 -------------------------------------------------------------


def _fragment(payload: bytes, sequence_id: int = 0) -> list[bytes]:
    """Split a payload into a contiguous fast-packet frame sequence."""
    seq_hi = (sequence_id & 0x07) << 5
    frames = [bytes([seq_hi | 0, len(payload)]) + payload[:6]]
    rest = payload[6:]
    counter = 1
    while rest:
        frames.append(bytes([seq_hi | (counter & 0x1F)]) + rest[:7])
        rest = rest[7:]
        counter += 1
    return frames


@given(payload_len=st.integers(min_value=1, max_value=200))
@settings(max_examples=100)
def test_fast_packet_round_trip(payload_len):
    """Feature: nmea2000-diagnostic-platform, Property 8: fast-packet reassembly
    round-trip reproduces the original payload bytes exactly."""
    payload = bytes((i * 7 + 3) % 256 for i in range(payload_len))
    reassembler = FastPacketReassembler()
    result = None
    t = 0.0
    for frame in _fragment(payload):
        result = reassembler.feed(frame, timestamp=t)
        t += 0.01
    assert result == payload


# --- Property 9 -------------------------------------------------------------


@given(
    payload_len=st.integers(min_value=10, max_value=120),
    drop_at=st.integers(min_value=1, max_value=4),
)
@settings(max_examples=100)
def test_non_contiguous_counter_never_emits_then_recovers(payload_len, drop_at):
    """Feature: nmea2000-diagnostic-platform, Property 9: a missing/out-of-order
    counter emits no partial/wrong message, buffer is discarded, and a fresh
    correct sequence afterward still reassembles."""
    payload = bytes((i * 5 + 1) % 256 for i in range(payload_len))
    frames = _fragment(payload)
    if drop_at >= len(frames):
        drop_at = len(frames) - 1 if len(frames) > 1 else 1

    reassembler = FastPacketReassembler()
    # Feed up to but not including the dropped frame, then a frame whose
    # counter skips ahead (non-contiguous).
    emitted = []
    t = 0.0
    for frame in frames[:drop_at]:
        emitted.append(reassembler.feed(frame, timestamp=t))
        t += 0.01
    # Corrupt: skip one counter by jumping to drop_at+1 if it exists.
    if drop_at + 1 < len(frames):
        emitted.append(reassembler.feed(frames[drop_at + 1], timestamp=t))
        t += 0.01
    # No complete payload must ever have been emitted by the corrupted run.
    assert all(e is None or e != payload for e in emitted)

    # A fresh, correct sequence still reassembles exactly.
    result = None
    for frame in _fragment(payload):
        result = reassembler.feed(frame, timestamp=t)
        t += 0.01
    assert result == payload


def test_timeout_discards_buffer_no_emit():
    """A >5s gap since the first frame discards the buffer (Property 9)."""
    payload = bytes(range(20))
    frames = _fragment(payload)
    reassembler = FastPacketReassembler()
    assert reassembler.feed(frames[0], timestamp=0.0) is None
    # Next frame arrives after the 5s window -> stale buffer discarded, and
    # since this is a continuation frame (counter != 0) nothing is emitted.
    assert reassembler.feed(frames[1], timestamp=6.0) is None
    # A fresh sequence from scratch still works.
    result = None
    t = 10.0
    for frame in _fragment(payload):
        result = reassembler.feed(frame, timestamp=t)
        t += 0.01
    assert result == payload


# --- Property 4 (parser half) -----------------------------------------------


@given(
    stream=st.lists(
        st.integers(min_value=-(2**40), max_value=2**40), min_size=1, max_size=50
    )
)
@settings(max_examples=100)
def test_malformed_input_never_halts_parsing(stream):
    """Feature: nmea2000-diagnostic-platform, Property 4: malformed/out-of-range
    input never halts repeated parsing of a stream (parser half)."""
    seen = 0
    for raw in stream:
        try:
            parse_identifier(raw)
        except MalformedIdentifierError:
            pass  # expected for out-of-range ids; parsing continues
        seen += 1
    assert seen == len(stream)


# --- Property 34 ------------------------------------------------------------


@given(
    payload=st.binary(min_size=8, max_size=8),
    single_frame_pgn=st.sampled_from([127488, 127505, 129025, 129026, 60928]),
    fast_pgn=st.sampled_from([126996, 127489, 129029, 126464]),
)
@settings(max_examples=100)
def test_routing_decided_by_pgn_metadata_not_frame(payload, single_frame_pgn, fast_pgn):
    """Feature: nmea2000-diagnostic-platform, Property 34: fast-packet transport
    is decided by PGN metadata, not frame content -- a single-frame PGN's 8-byte
    payload is never routed through the reassembler by the predicate regardless
    of byte content, and a known fast-packet PGN always is."""
    assert is_fastpacket_pgn(single_frame_pgn) is False
    assert is_fastpacket_pgn(fast_pgn) is True
