"""Decoder_Registry (Stage 3): maps PGN -> Decoder, enforcing at-most-one
decoder per PGN (Requirement 5.1, 5.2; Property 10).

A Decoder is a callable ``(source_address: int, data: bytes) -> DecodeResult``.
``decode()`` returns ``DecodeResult.unknown()`` for a PGN with no registered
decoder -- the frame is retained in an unknown state, never dropped, and no
decoder is invoked (Requirement 5.4).

Per the no-manufacturer-classes rule (Requirement 6.2 / steering 04), this
module holds no PGN-specific logic -- only the generic registry mechanism.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

Decoder = Callable[[int, bytes], "DecodeResult"]


@dataclass(frozen=True)
class DecodeResult:
    """What a decoder call produced: a Domain_Model entity update, or the
    unknown-PGN marker. ``decoder_version`` is threaded into Provenance by the
    caller (Requirement 5.5)."""

    pgn: int
    source_address: int
    entity: Any = None  # the populated domain entity (EngineState, TankState, ...), or None
    known: bool = True
    decoder_version: str = "unknown"

    @staticmethod
    def unknown(pgn: int, source_address: int) -> "DecodeResult":
        return DecodeResult(pgn=pgn, source_address=source_address, entity=None, known=False)


class DecoderConflictError(ValueError):
    """Raised when registering a second decoder for an already-mapped PGN.
    Names the conflicting PGN (Requirement 5.2); the existing decoder is left
    registered -- this exception is raised BEFORE any mutation."""


@dataclass
class DecoderRegistry:
    """Instance-based registry (not a module-level singleton) so tests can
    build independent registries without cross-test pollution."""

    _decoders: dict[int, Decoder] = field(default_factory=dict)
    _versions: dict[int, str] = field(default_factory=dict)

    def register(self, pgn: int, decoder: Decoder, version: str = "1.0.0") -> None:
        """Register ``decoder`` for ``pgn``. Raises ``DecoderConflictError`` and
        leaves the existing decoder untouched if ``pgn`` is already mapped
        (Requirement 5.1, 5.2)."""
        if pgn in self._decoders:
            raise DecoderConflictError(
                f"Decoder for PGN {pgn} already registered; existing decoder retained"
            )
        self._decoders[pgn] = decoder
        self._versions[pgn] = version

    def is_registered(self, pgn: int) -> bool:
        return pgn in self._decoders

    def registered_pgns(self) -> frozenset[int]:
        return frozenset(self._decoders.keys())

    def decode(self, pgn: int, source_address: int, data: bytes) -> DecodeResult:
        """Invoke the registered decoder for ``pgn``, or return
        ``DecodeResult.unknown()`` without invoking anything (Requirement
        5.3, 5.4)."""
        decoder = self._decoders.get(pgn)
        if decoder is None:
            return DecodeResult.unknown(pgn, source_address)
        result = decoder(source_address, data)
        if result.decoder_version == "unknown":
            result = DecodeResult(
                pgn=result.pgn,
                source_address=result.source_address,
                entity=result.entity,
                known=result.known,
                decoder_version=self._versions[pgn],
            )
        return result


# Default process-wide registry that decoder modules register against via
# `@decoder_for(pgn)`, mirroring the Stage 1.5 prototype's decorator ergonomics
# while using the Stage 3 DecoderRegistry/DecodeResult contract underneath.
DEFAULT_REGISTRY = DecoderRegistry()


def decoder_for(pgn: int, version: str = "1.0.0") -> Callable[[Decoder], Decoder]:
    """Decorator registering a decoder function against ``DEFAULT_REGISTRY``."""

    def _wrap(fn: Decoder) -> Decoder:
        DEFAULT_REGISTRY.register(pgn, fn, version=version)
        return fn

    return _wrap
