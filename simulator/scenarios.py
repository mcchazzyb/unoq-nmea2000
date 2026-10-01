"""Synthetic NMEA 2000 traffic generator (Stage 5 Simulator).

Produces realistic frame sequences for golden-fixture testing. Scenarios
are named after the real-world equipment they model (per user request,
grounded in web research of actual Suzuki DF140 / SMIS behavior -- see
docs/fixtures.md) but every frame emitted is a STANDARD NMEA 2000 PGN.
There is no manufacturer-specific decoding path: the Suzuki SMIS Engine
Interface is itself a protocol translator that emits standard PGNs
127488/127489 (confirmed via public documentation and installer manuals),
so the scenario below only needs to pick plausible standard-PGN values.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

from protocol.receiver import CanFrame

# Suzuki uses manufacturer code 586 (confirmed via CANboat canboat.json,
# PGN 65298-65304 "Suzuki: Engine Data A..E"); not used for decoding here
# since this scenario only emits standard PGNs, but recorded for provenance.
SUZUKI_MANUFACTURER_CODE = 586

_ENGINE_SOURCE_ADDRESS = 35  # arbitrary plausible bus address
_GPS_SOURCE_ADDRESS = 20
_TANK_SOURCE_ADDRESS = 42


def _build_can_id(priority: int, pgn: int, source_address: int) -> int:
    """Inverse of protocol.identifier.parse_identifier, PDU2 (broadcast) form."""
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


def _make_frame(
    priority: int, pgn: int, source_address: int, data: bytes, timestamp: float
) -> CanFrame:
    """Build a full CanFrame, deriving dest/dlc from the canonical fields.

    These scenario PGNs are all PDU2 (broadcast), so dest_address is None.
    """
    can_id = _build_can_id(priority, pgn, source_address)
    return CanFrame(
        can_id=can_id,
        priority=priority,
        pgn=pgn,
        source_address=source_address,
        dest_address=None,
        dlc=len(data),
        payload=data,
        capture_time=timestamp,
    )


@dataclass(frozen=True)
class DF140SmokeTestScenario:
    """A fixed snapshot of a Suzuki DF140 outboard underway: cruising RPM,
    normal oil pressure/temp, GPS fix with forward motion, half-full fuel
    tank. Values are plausible for a 140hp 4-stroke outboard at cruise,
    not telemetry from a real logged session.
    """

    engine_rpm: float = 3800.0
    oil_pressure_kpa: float = 310.0
    coolant_temp_c: float = 71.0
    alternator_voltage: float = 13.8
    fuel_rate_lph: float = 18.5
    engine_hours_s: int = 452_000
    tank_level_percent: float = 54.0
    tank_capacity_l: float = 189.0  # ~50 US gal, a common SMIS-era tank size
    latitude_deg: float = 50.8198  # Poole Harbour approx, arbitrary UK coast
    longitude_deg: float = -1.9870
    sog_knots: float = 22.4
    cog_deg: float = 95.0

    def frames(self) -> list[CanFrame]:
        """Return the raw CAN frames for one reporting cycle of this scenario.

        127489 (Engine Dynamic) is a fast-packet PGN and is therefore split
        across two 8-byte CAN frames per the NMEA 2000 fast-packet framing
        rule (frame 0: counter+length+6 bytes; frame 1: counter+7 bytes).
        """
        t = 0.0
        frames: list[CanFrame] = []

        # PGN 127488 Engine Rapid Update (single frame, engine source)
        rpm_raw = round(self.engine_rpm / 0.25)
        boost_raw = 0xFFFF  # not available on an outboard without a turbo
        trim_raw = 0x7F  # tilt/trim not available in this scenario
        payload_488 = bytes(
            [0x00]
        ) + struct.pack("<HHb", rpm_raw, boost_raw, trim_raw) + bytes([0xFF, 0xFF])
        frames.append(
            _make_frame(2, 127488, _ENGINE_SOURCE_ADDRESS, payload_488, t)
        )
        t += 0.01

        # PGN 127489 Engine Dynamic (fast-packet, 2 frames)
        oil_pressure_pa_raw = round((self.oil_pressure_kpa * 1000) / 100)
        oil_temp_raw = 0xFFFF  # not separately reported in this scenario
        coolant_temp_raw = round((self.coolant_temp_c + 273.15) / 0.01)
        alt_v_raw = round(self.alternator_voltage / 0.01)
        fuel_rate_raw = round(self.fuel_rate_lph / 0.1)
        total_hours_raw = self.engine_hours_s
        coolant_pressure_raw = 0xFFFF
        fuel_pressure_raw = 0xFFFF
        reserved = 0xFF
        discrete_status_1 = 0x0000
        discrete_status_2 = 0x0000
        engine_load_raw = 0x7F
        engine_torque_raw = 0x7F

        body = bytes([0x00])  # engine instance
        body += struct.pack("<H", oil_pressure_pa_raw)
        body += struct.pack("<H", oil_temp_raw)
        body += struct.pack("<H", coolant_temp_raw)
        body += struct.pack("<h", alt_v_raw)
        body += struct.pack("<h", fuel_rate_raw)
        body += struct.pack("<I", total_hours_raw)
        body += struct.pack("<H", coolant_pressure_raw)
        body += struct.pack("<H", fuel_pressure_raw)
        body += bytes([reserved])
        body += struct.pack("<H", discrete_status_1)
        body += struct.pack("<H", discrete_status_2)
        body += bytes([engine_load_raw, engine_torque_raw])
        assert len(body) == 26, f"expected 26-byte 127489 body, got {len(body)}"

        frame0 = bytes([0x00, len(body)]) + body[:6]
        frame1 = bytes([0x01]) + body[6:13]
        frame2 = bytes([0x02]) + body[13:20]
        frame3 = bytes([0x03]) + body[20:26] + bytes([0xFF])
        for frame_payload in (frame0, frame1, frame2, frame3):
            frames.append(
                _make_frame(2, 127489, _ENGINE_SOURCE_ADDRESS, frame_payload, t)
            )
            t += 0.01

        # PGN 127505 Fluid Level (single frame, tank sender)
        tank_type_fuel = 0
        instance_type_byte = (tank_type_fuel << 4) | 0x00
        level_raw = round(self.tank_level_percent / 0.004)
        capacity_raw = round(self.tank_capacity_l / 0.1)
        payload_505 = (
            bytes([instance_type_byte])
            + struct.pack("<h", level_raw)
            + struct.pack("<I", capacity_raw)
            + bytes([0xFF])
        )
        frames.append(
            _make_frame(6, 127505, _TANK_SOURCE_ADDRESS, payload_505, t)
        )
        t += 0.01

        # PGN 129025 Position Rapid Update (single frame, GPS source)
        lat_raw = round(self.latitude_deg / 1e-7)
        lon_raw = round(self.longitude_deg / 1e-7)
        payload_025 = struct.pack("<ii", lat_raw, lon_raw)
        frames.append(
            _make_frame(2, 129025, _GPS_SOURCE_ADDRESS, payload_025, t)
        )
        t += 0.01

        # PGN 129026 COG & SOG Rapid Update (single frame, GPS source)
        import math

        cog_rad_raw = round(math.radians(self.cog_deg) / 0.0001)
        sog_mps_raw = round((self.sog_knots / 1.9438444924) / 0.01)
        payload_026 = (
            bytes([0x00, 0x00])  # SID, COG reference = true (bits 0-1 = 0)
            + struct.pack("<H", cog_rad_raw)
            + struct.pack("<H", sog_mps_raw)
            + bytes([0xFF, 0xFF])
        )
        frames.append(
            _make_frame(2, 129026, _GPS_SOURCE_ADDRESS, payload_026, t)
        )

        return frames


def main() -> int:
    """Emit one reporting cycle of the DF140 scenario to stdout (make run-sim).

    A standing streaming generator lands in Stage 5 (simulator/generator.py);
    for now this prints the synthetic frames and exits 0 on success.
    """
    scenario = DF140SmokeTestScenario()
    frames = scenario.frames()
    for f in frames:
        print(
            f"pgn={f.pgn} sa={f.source_address} dlc={f.dlc} "
            f"payload={f.payload.hex()} t={f.capture_time:.3f}"
        )
    print(f"# {len(frames)} frames emitted")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
