"""Tests for the diagnostic PGN classifier (protocol/detection.py)."""
from __future__ import annotations

from protocol.detection import best_candidate, classify_frame


def _build_can_id(priority: int, pgn: int, source_address: int) -> int:
    """PDU2 (broadcast) inverse of parse_identifier; mirrors
    simulator.scenarios._build_can_id so test IDs decode back to `pgn`.
    """
    pdu_format = (pgn >> 8) & 0xFF
    pdu_specific = pgn & 0xFF
    data_page = (pgn >> 16) & 0x1
    return (
        (priority << 26)
        | (data_page << 24)
        | (pdu_format << 16)
        | (pdu_specific << 8)
        | source_address
    )


def test_case1_single_frame_exact_match():
    can_id = _build_can_id(2, 127488, 35)
    result = classify_frame(can_id, bytes(8))
    assert result.observed_pgn == 127488
    assert result.identifier_match is True
    best = best_candidate(result)
    assert best is not None
    assert best.confidence >= 0.8


def test_case2_fast_packet_fragment_scores_lower():
    # 127489 is Fast-packet (26 bytes full); a lone 8-byte CAN frame is one
    # fragment. It should classify without crashing and score below case 1.
    can1 = _build_can_id(2, 127488, 35)
    best1 = best_candidate(classify_frame(can1, bytes(8)))

    can2 = _build_can_id(2, 127489, 35)
    result2 = classify_frame(can2, bytes(8))
    best2 = best_candidate(result2)

    if best2 is not None:
        assert best2.confidence < best1.confidence
    else:
        # Sibling stub may not include 127489 yet; absence is still "lower".
        assert result2.identifier_match is False


def test_case3_proprietary_pgn_lists_candidates():
    can_id = _build_can_id(6, 65280, 42)
    # length 8 matches the proprietary single-frame entries in the stub
    result = classify_frame(can_id, bytes(8))
    assert len(result.candidates) >= 1
    for c in result.candidates:
        assert c.signature.is_proprietary is True or c.signature.pgn == 65280


def test_case4_unknown_pgn_no_candidates():
    # 126999 has no reference signature in the stub or real dataset.
    can_id = _build_can_id(2, 126999, 35)
    result = classify_frame(can_id, bytes(8))
    assert result.candidates == []
    assert result.identifier_match is False


def test_case5_best_candidate_none_vs_first():
    empty = classify_frame(_build_can_id(2, 126999, 35), bytes(8))
    assert best_candidate(empty) is None

    nonempty = classify_frame(_build_can_id(2, 127488, 35), bytes(8))
    assert best_candidate(nonempty) is nonempty.candidates[0]
