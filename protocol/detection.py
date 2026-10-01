"""Diagnostic PGN classifier.

This is a DIAGNOSTIC classifier, not a decoder: given a raw CAN frame it
reports which reference PGN signature(s) the frame is consistent with, each
with a confidence score and human-readable reasoning. It never assumes a
single manufacturer -- for ambiguous or proprietary PGNs (where one PGN number
maps to several manufacturer-specific signatures) it surfaces every candidate
so an operator can see the competing interpretations rather than a false
certainty.

Scoring heuristic (classify_frame):
  Every candidate signature starts at a base confidence of 0.5 (the identifier
  claimed this PGN, so it is plausible). Length evidence then adjusts it:
    +0.3  Single-frame PGN whose declared length matches the frame exactly.
    +0.3  Fast-packet PGN with a declared length matching the frame exactly.
    +0.15 Fast-packet PGN with the frame meeting its declared min_length
          (a partial/first fragment of a multi-frame message is expected to
          be shorter than the full assembled length, so a positive but weaker
          signal).
    -0.4  Single-frame PGN whose declared length is known but does NOT match
          the frame -- a single frame has a fixed size, so a mismatch is
          strong evidence against this interpretation.
  The result is clamped to [0.0, 1.0]. When several signatures tie on
  confidence, non-proprietary signatures are ordered before proprietary ones
  (a standard interpretation is the more likely default).
"""
from __future__ import annotations

from dataclasses import dataclass

from protocol.identifier import parse_identifier
from protocol.reference.canboat_pgns import PGNSignature, lookup_pgn


@dataclass(frozen=True)
class ClassificationCandidate:
    signature: PGNSignature
    confidence: float
    reasoning: str


@dataclass(frozen=True)
class ClassificationResult:
    observed_pgn: int
    observed_length: int
    candidates: list[ClassificationCandidate]  # sorted desc by confidence
    identifier_match: bool  # True iff candidates is non-empty


def _score(sig: PGNSignature, data_len: int) -> tuple[float, str]:
    confidence = 0.5
    reasons = [f"base 0.50 (identifier claimed PGN {sig.pgn})"]

    if sig.pgn_type == "Single":
        if sig.length is not None and data_len == sig.length:
            confidence += 0.3
            reasons.append(f"+0.30 single-frame length matches ({data_len}=={sig.length})")
        elif sig.length is not None and data_len != sig.length:
            confidence -= 0.4
            reasons.append(
                f"-0.40 single-frame length mismatch ({data_len}!={sig.length})"
            )
    elif sig.pgn_type == "Fast":
        if sig.length is not None and data_len == sig.length:
            confidence += 0.3
            reasons.append(f"+0.30 fast-packet full length matches ({data_len}=={sig.length})")
        elif sig.min_length is not None and data_len >= sig.min_length:
            confidence += 0.15
            reasons.append(
                f"+0.15 fast-packet meets min_length ({data_len}>={sig.min_length}), "
                "likely a fragment"
            )

    confidence = max(0.0, min(1.0, confidence))
    return confidence, "; ".join(reasons)


def classify_frame(can_id: int, data: bytes) -> ClassificationResult:
    """Classify one raw CAN frame against the reference PGN set.

    See the module docstring for the full scoring heuristic. Returns a
    ClassificationResult whose candidates are sorted by descending confidence
    (ties broken non-proprietary before proprietary); identifier_match is True
    exactly when at least one reference signature exists for the claimed PGN.
    """
    identifier = parse_identifier(can_id)
    claimed_pgn = identifier.pgn
    data_len = len(data)

    signatures = lookup_pgn(claimed_pgn)
    candidates = [
        ClassificationCandidate(signature=sig, confidence=conf, reasoning=reason)
        for sig in signatures
        for conf, reason in (_score(sig, data_len),)
    ]

    candidates.sort(
        key=lambda c: (-c.confidence, c.signature.is_proprietary)
    )

    return ClassificationResult(
        observed_pgn=claimed_pgn,
        observed_length=data_len,
        candidates=candidates,
        identifier_match=bool(candidates),
    )


def best_candidate(result: ClassificationResult) -> ClassificationCandidate | None:
    """Return the highest-confidence candidate, or None if there are none."""
    return result.candidates[0] if result.candidates else None
