"""29-bit NMEA 2000 CAN identifier parser.

An NMEA 2000 CAN ID packs Priority, PGN (via PDU format / PDU specific / data
page bits), Source Address and (for PDU1) a destination address into a 29-bit
extended CAN identifier. This module decodes and recomposes that packing
without touching payload bytes.

Bit layout (design.md -> 29-bit Identifier Parsing):
    bits  0-7   source address
    bits  8-15  PDU specific (PS)        -> destination (PDU1) or PGN tail (PDU2)
    bits 16-23  PDU format (PF)
    bit  24     data page (DP)
    bits 26-28  priority
"""
from __future__ import annotations

from dataclasses import dataclass


class MalformedIdentifierError(ValueError):
    """Raised when a CAN identifier is outside the valid 29-bit range."""


@dataclass(frozen=True)
class Identifier:
    priority: int  # 0-7, bits 26-28
    pgn: int  # Decoded PGN number
    source_address: int  # 0-255, bits 0-7
    dest_address: int | None = None  # PDU1 destination; None for PDU2 broadcast


def parse_identifier(can_id: int) -> Identifier:
    """Decode a 29-bit NMEA 2000 CAN identifier into its components.

    Raises MalformedIdentifierError for any can_id that does not fit in
    29 bits -- callers (Ingestion_Service) must catch this and route the
    frame to malformed-frame handling rather than let it propagate.

    PF < 240  (PDU1, addressed):  PS is the destination; PGN = (DP<<16)|(PF<<8).
    PF >= 240 (PDU2, broadcast):  PS folds into PGN; PGN = (DP<<16)|(PF<<8)|PS,
                                  no destination.
    """
    if can_id < 0 or can_id > 0x1FFFFFFF:
        raise MalformedIdentifierError(
            f"CAN identifier 0x{can_id:X} is outside the 29-bit range"
        )

    source_address = can_id & 0xFF
    pdu_specific = (can_id >> 8) & 0xFF
    pdu_format = (can_id >> 16) & 0xFF
    data_page = (can_id >> 24) & 0x1
    priority = (can_id >> 26) & 0x7

    if pdu_format < 240:
        # PDU1 (addressed): PDU specific is the destination address.
        pgn = (data_page << 16) | (pdu_format << 8)
        dest_address: int | None = pdu_specific
    else:
        # PDU2 (broadcast): PDU specific is part of the PGN, no destination.
        pgn = (data_page << 16) | (pdu_format << 8) | pdu_specific
        dest_address = None

    return Identifier(
        priority=priority,
        pgn=pgn,
        source_address=source_address,
        dest_address=dest_address,
    )


def compose_identifier(ident: Identifier) -> int:
    """Exact inverse of parse_identifier over the modelled 29-bit space.

    Reconstructs the raw 29-bit CAN id from (priority, pgn, source_address,
    dest_address). For PDU1 the PDU-specific byte is taken from dest_address;
    for PDU2 it is the low byte of the PGN. compose_identifier(parse(x)) == x
    for every valid 29-bit identifier (Property 6).
    """
    data_page = (ident.pgn >> 16) & 0x1
    pdu_format = (ident.pgn >> 8) & 0xFF

    if pdu_format < 240:
        # PDU1: PGN's low byte is 0; PDU specific carries the destination.
        pdu_specific = (ident.dest_address or 0) & 0xFF
    else:
        # PDU2: PGN's low byte IS the PDU specific; dest_address is None.
        pdu_specific = ident.pgn & 0xFF

    return (
        ((ident.priority & 0x7) << 26)
        | ((data_page & 0x1) << 24)
        | ((pdu_format & 0xFF) << 16)
        | ((pdu_specific & 0xFF) << 8)
        | (ident.source_address & 0xFF)
    )
