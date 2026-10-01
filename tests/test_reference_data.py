"""Tests for the PGN reference dataset (structure/matching, not decode)."""

from protocol.reference import PGN_SIGNATURES, lookup_pgn


def test_lookup_standard_pgn_single_signature():
    sigs = lookup_pgn(127488)
    assert len(sigs) == 1
    assert sigs[0].is_proprietary is False
    assert sigs[0].field_count == 5


def test_proprietary_pgn_has_multiple_signatures():
    assert len(lookup_pgn(65280)) >= 6


def test_unknown_pgn_returns_empty_list():
    assert lookup_pgn(999999) == []


def test_all_signatures_have_valid_type():
    for sigs in PGN_SIGNATURES.values():
        for sig in sigs:
            assert sig.pgn_type in {"Single", "Fast"}
