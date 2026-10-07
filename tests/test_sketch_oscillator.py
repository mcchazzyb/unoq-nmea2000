"""The sketch must configure the shield's real 40 MHz MCP2518FD oscillator.

Regression (steering 10): with the old unverified OSC_20MHz the controller ran
at the wrong bit rate and received nothing on a 250 kbit/s bus, while
begin() still reported success. Static check, as for other firmware text.
"""
from __future__ import annotations

import re
from pathlib import Path

SKETCH = Path("firmware/unoq_nmea2000_capture/sketch/sketch.ino").read_text()


def test_sketch_uses_40mhz_oscillator_at_250kbit():
    m = re.search(r"ACAN2517Settings settings\((ACAN2517Settings::\w+),\s*(\d+)UL \* 1000UL\)", SKETCH)
    assert m, "ACAN2517Settings construction not found"
    assert m.group(1) == "ACAN2517Settings::OSC_40MHz"
    assert m.group(2) == "250"
