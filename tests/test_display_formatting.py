"""RPM and tank level show as whole numbers; fluid type shows its name.

Regression (steering 10): the page showed rpm 4208.25, level 98.992 and a bare
fluid type "0". Static check of the shipped page, per the project's precedent.
"""
from __future__ import annotations

import re
from pathlib import Path

HTML = Path("web/static/index.html").read_text()


def test_whole_number_keys_and_fluid_names():
    keys = re.search(r"WHOLE_NUMBER_KEYS = new Set\(\[([^\]]*)\]\)", HTML).group(1)
    assert {k.strip().strip('"') for k in keys.split(",")} == {"rpm", "level_percent"}
    names = re.search(r"FLUID_TYPE_NAMES = \{([^}]*)\}", HTML).group(1)
    assert '0: "Fuel"' in names


def test_format_value_applies_them_before_the_generic_decimal_rule():
    fn = re.search(r"function formatValue\(.*?\n  \}", HTML, re.S).group(0)
    assert fn.index('key === "fluid_type"') < fn.index("WHOLE_NUMBER_KEYS.has") < fn.index("toFixed(2)")
    assert "Math.round(value)" in fn
