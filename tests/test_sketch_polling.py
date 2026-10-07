"""The sketch must POLL the CAN controller, not rely on its interrupt line.

Regression (steering 10): with INT=D2 / can.isr() the real UNO Q received zero
frames from a live bus (P7 fitted, correct clock), while the same controller and
settings polled over SPI received thousands. Static check of the shipped sketch.
"""
from __future__ import annotations

import re
from pathlib import Path

SKETCH = Path("firmware/unoq_nmea2000_capture/sketch/sketch.ino").read_text()


def test_controller_is_polled_not_interrupt_driven():
    assert re.search(r"ACAN2517 can\(MCP2518_CS, SPI, 255\)", SKETCH)
    assert "can.begin(settings, NULL)" in SKETCH
    assert "can.isr()" not in SKETCH
    loop = SKETCH[SKETCH.index("void loop()"):]
    assert not re.search(r"^\s*can\.poll\(\)", SKETCH, re.M)  # poll() disables interrupts until empty: starves the Bridge
    assert loop.index("can.isr_core()") < loop.index("can.receive(")
    assert re.search(r"pass < \d+ && can\.isr_core\(\)", loop)  # bounded per loop


def test_sketch_never_transmits():
    for token in ("tryToSend", "can.send", "transmit"):
        assert token not in SKETCH.replace("Transmit_Path", "")


def test_frame_batch_is_small_enough_for_the_router_bridge_buffers():
    """Real traffic with 16-frame (~600 char) batches coincided with the MCU going
    unresponsive; RouterBridge uses small fixed RPC buffers. Keep batches tiny."""
    m = re.search(r"FRAME_BATCH_MAX = (\d+);", SKETCH)
    assert m and int(m.group(1)) <= 4
