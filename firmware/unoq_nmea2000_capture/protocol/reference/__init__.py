"""PGN reference data for cross-manufacturer autodetection and matching."""

from .canboat_pgns import PGN_SIGNATURES, PGNSignature, lookup_pgn

__all__ = ["PGN_SIGNATURES", "PGNSignature", "lookup_pgn"]
