"""Identity decoders: PGN 60928 (ISO Address Claim) and PGN 126996 (Product
Information), both standard NMEA 2000 / ISO 11783 messages used by
Discovery_Mode to resolve node identity (Requirement 10.5, 10.6, 10.7).

These populate ``protocol.domain.NodeRecord`` fields directly; callers
(Discovery_Mode) own looking up/creating the NodeRecord by NodeKey and
assigning the result in (steering rule 08 -- NodeKey is the NMEA NAME, never
source address).

No manufacturer assumption is made (Requirement 6.2 / steering rule 04): both
PGNs are generic ISO/NMEA2000 identity messages, not vendor-specific.
"""
from __future__ import annotations

import struct

from protocol.registry import DecodeResult, decoder_for

DECODER_VERSION = "1.0.0"

PGN_ISO_ADDRESS_CLAIM = 60928
PGN_PRODUCT_INFORMATION = 126996

# ISO 11783 / NMEA 2000 64-bit NAME bitfield widths (standard, unanimous across
# every NMEA2000 implementation -- canboat, SignalK, J1939). Packed
# little-endian across 8 bytes; unpacked here as a single u64 then masked.
_UNIQUE_NUMBER_BITS = 21
_MANUFACTURER_CODE_BITS = 11
_DEVICE_INSTANCE_LOWER_BITS = 3
_DEVICE_INSTANCE_UPPER_BITS = 5
_DEVICE_FUNCTION_BITS = 8
_RESERVED_BITS = 1
_DEVICE_CLASS_BITS = 7
_SYSTEM_INSTANCE_BITS = 4
_INDUSTRY_GROUP_BITS = 3
_ARBITRARY_ADDRESS_CAPABLE_BITS = 1


class IsoName:
    """Decoded fields of a 64-bit ISO/NMEA2000 NAME. Not a domain entity --
    the caller copies the fields it needs onto a NodeRecord."""

    __slots__ = (
        "raw",
        "unique_number",
        "manufacturer_code",
        "device_instance_lower",
        "device_instance_upper",
        "device_function",
        "device_class",
        "system_instance",
        "industry_group",
        "arbitrary_address_capable",
    )

    def __init__(self, raw: int) -> None:
        self.raw = raw
        bit = 0

        def take(width: int) -> int:
            nonlocal bit
            value = (raw >> bit) & ((1 << width) - 1)
            bit += width
            return value

        self.unique_number = take(_UNIQUE_NUMBER_BITS)
        self.manufacturer_code = take(_MANUFACTURER_CODE_BITS)
        self.device_instance_lower = take(_DEVICE_INSTANCE_LOWER_BITS)
        self.device_instance_upper = take(_DEVICE_INSTANCE_UPPER_BITS)
        self.device_function = take(_DEVICE_FUNCTION_BITS)
        take(_RESERVED_BITS)
        self.device_class = take(_DEVICE_CLASS_BITS)
        self.system_instance = take(_SYSTEM_INSTANCE_BITS)
        self.industry_group = take(_INDUSTRY_GROUP_BITS)
        self.arbitrary_address_capable = take(_ARBITRARY_ADDRESS_CAPABLE_BITS)


@decoder_for(PGN_ISO_ADDRESS_CLAIM, version=DECODER_VERSION)
def decode_iso_address_claim(source_address: int, data: bytes) -> DecodeResult:
    """PGN 60928, single-frame, 8 bytes: the raw 64-bit NAME, little-endian.

    Returns an ``IsoName`` as the result entity (not a domain-model dataclass,
    since NAME fields map onto NodeRecord, which Discovery_Mode owns).
    """
    if len(data) < 8:
        return DecodeResult(
            pgn=PGN_ISO_ADDRESS_CLAIM, source_address=source_address, entity=None
        )
    raw_name = struct.unpack_from("<Q", data, 0)[0]
    return DecodeResult(
        pgn=PGN_ISO_ADDRESS_CLAIM,
        source_address=source_address,
        entity=IsoName(raw_name),
    )


class ProductInformation:
    """Decoded fields of PGN 126996 Product Information (reassembled
    fast-packet payload). Not a domain entity -- Discovery_Mode copies these
    onto the NodeRecord's ``product_info``."""

    __slots__ = (
        "nmea_2000_version",
        "product_code",
        "model_id",
        "software_version_code",
        "model_version",
        "model_serial_code",
        "certification_level",
        "load_equivalency",
    )

    def __init__(
        self,
        nmea_2000_version: int,
        product_code: int,
        model_id: str,
        software_version_code: str,
        model_version: str,
        model_serial_code: str,
        certification_level: int,
        load_equivalency: int,
    ) -> None:
        self.nmea_2000_version = nmea_2000_version
        self.product_code = product_code
        self.model_id = model_id
        self.software_version_code = software_version_code
        self.model_version = model_version
        self.model_serial_code = model_serial_code
        self.certification_level = certification_level
        self.load_equivalency = load_equivalency


def _decode_padded_ascii(raw: bytes) -> str:
    """Strip trailing NUL/space padding from a fixed-width ASCII field."""
    return raw.split(b"\x00", 1)[0].rstrip().decode("ascii", errors="replace")


@decoder_for(PGN_PRODUCT_INFORMATION, version=DECODER_VERSION)
def decode_product_information(source_address: int, data: bytes) -> DecodeResult:
    """PGN 126996, fast-packet, 134 bytes once reassembled (Stage 2 reassembler
    hands this decoder the complete payload; Requirement 10.6, 10.7 -- an
    incomplete reassembly within 5s never reaches this decoder at all).

    Byte layout:
      0-1:    NMEA 2000 Version (u16)
      2-3:    Product Code (u16)
      4-35:   Model ID (32-byte fixed ASCII, NUL/space padded)
      36-67:  Software Version Code (32-byte fixed ASCII)
      68-99:  Model Version (32-byte fixed ASCII)
      100-131: Model Serial Code (32-byte fixed ASCII)
      132:    Certification Level (u8)
      133:    Load Equivalency (u8)
    """
    if len(data) < 134:
        return DecodeResult(
            pgn=PGN_PRODUCT_INFORMATION, source_address=source_address, entity=None
        )

    nmea_2000_version, product_code = struct.unpack_from("<HH", data, 0)
    model_id = _decode_padded_ascii(data[4:36])
    software_version_code = _decode_padded_ascii(data[36:68])
    model_version = _decode_padded_ascii(data[68:100])
    model_serial_code = _decode_padded_ascii(data[100:132])
    certification_level, load_equivalency = data[132], data[133]

    entity = ProductInformation(
        nmea_2000_version=nmea_2000_version,
        product_code=product_code,
        model_id=model_id,
        software_version_code=software_version_code,
        model_version=model_version,
        model_serial_code=model_serial_code,
        certification_level=certification_level,
        load_equivalency=load_equivalency,
    )
    return DecodeResult(
        pgn=PGN_PRODUCT_INFORMATION, source_address=source_address, entity=entity
    )
