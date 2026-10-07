"""SOG and wind speed are shown in knots (wire unit stays m/s). Static check of
the shipped page, per the project's precedent for client-side JS (see
test_stage7_unit_toggle_gate.py).
"""
from __future__ import annotations

import re
from pathlib import Path

HTML = Path("web/static/index.html").read_text()


def test_sog_and_wind_labels_are_knots():
    units = re.search(r"const UNITS = \{(.*?)\};", HTML, re.S).group(1)
    assert 'sog: "kn"' in units
    assert 'wind: "kn"' in units


def test_sog_and_wind_converted_with_exact_knot_definition():
    assert "const MPS_TO_KNOTS = 3600 / 1852" in HTML
    fn = re.search(r"function displayValueFor\(.*?\n  \}", HTML, re.S).group(0)
    assert 'attribute === "sog"' in fn and 'attribute === "wind"' in fn and "MPS_TO_KNOTS" in fn


def test_knots_arithmetic_pin():
    # 10.8 m/s is about 21 kn; 1 kn is exactly 1852/3600 m/s
    assert abs(10.8 * 3600 / 1852 - 20.993) < 0.001
    assert abs(1852 / 3600 * 3600 / 1852 - 1.0) < 1e-12
