"""Stage 7.11/7.12 Gate -- display-only litres/US gallons toggle for
capacity_litres, quantity_remaining_litres, and fuel_rate (Requirement
29.11, 29.12), plus the wire-unit documentation requirement (Requirement
8.8).

The feature is entirely client-side JS (no new API endpoint, no decoder
change), so these tests statically verify the shipped markup/script per
this project's existing precedent for client-side JS behavior without a
browser test runner (see test_stage7_dashboard_gate.py's own docstring),
PLUS a direct Python port of the conversion arithmetic (same constant,
same formula) as an independently-checkable pin on the numeric behavior,
and a decode-path test proving the toggle has no decoder dependency.
"""
from __future__ import annotations

import re
from pathlib import Path

from protocol.decoders.engine import decode_engine_dynamic
from protocol.decoders.tank import decode_fluid_level
from protocol.domain import UNSET

INDEX_PATH = Path("web/static/index.html")
LITRES_PER_US_GALLON = 3.785411784


def _html() -> str:
    return INDEX_PATH.read_text()


# --- Requirement 29.11: toggle exists and is scoped to exactly the three
# litres-denominated attributes


def test_unit_toggle_control_present_with_both_options():
    html = _html()
    assert 'id="unit-toggle"' in html
    assert 'data-unit="litres"' in html
    assert 'data-unit="us_gallons"' in html


def test_conversion_scoped_to_exactly_capacity_fuel_rate_and_remaining():
    html = _html()
    m = re.search(r"VOLUME_OR_RATE_ATTRIBUTES\s*=\s*new Set\(\[([^\]]*)\]\)", html)
    assert m, "VOLUME_OR_RATE_ATTRIBUTES set not found"
    attrs = {a.strip().strip('"') for a in m.group(1).split(",")}
    assert attrs == {"capacity_litres", "quantity_remaining_litres", "fuel_rate"}


def test_gauge_and_metric_renderers_use_the_unit_aware_helpers():
    """renderGauge/renderMetricCell must route through displayValueFor /
    unitLabelFor (not the raw UNITS[attribute] lookup) so the toggle
    actually reaches both rendering paths."""
    html = _html()
    gauge_fn = re.search(r"function renderGauge\(.*?\n  \}", html, re.S).group(0)
    metric_fn = re.search(r"function renderMetricCell\(.*?\n  \}", html, re.S).group(0)
    for fn_src, name in ((gauge_fn, "renderGauge"), (metric_fn, "renderMetricCell")):
        assert "displayValueFor(" in fn_src, f"{name} must convert via displayValueFor"
        assert "unitLabelFor(" in fn_src, f"{name} must label via unitLabelFor"


def test_legacy_panel_renderer_also_uses_the_unit_aware_helpers():
    """renderEntityFields (the original fixed engine/tank panels) must also
    respect the toggle, not just the Stage 7.6 dashboard cards."""
    html = _html()
    fn_src = re.search(r"function renderEntityFields\(.*?\n  \}", html, re.S).group(0)
    assert "displayValueFor(" in fn_src
    assert "unitLabelFor(" in fn_src


# --- Requirement 29.12: persisted locally, defaults to litres


def test_display_unit_persisted_to_its_own_localstorage_key_defaulting_litres():
    html = _html()
    assert 'DISPLAY_UNIT_STORAGE_KEY = "unoq_display_unit_v1"' in html
    load_fn = re.search(r"function loadDisplayUnit\(.*?\n  \}", html, re.S).group(0)
    assert 'return raw === "us_gallons" ? "us_gallons" : "litres"' in load_fn


def test_conversion_constant_matches_standard_litres_per_us_gallon():
    html = _html()
    assert f"LITRES_PER_US_GALLON = {LITRES_PER_US_GALLON};" in html


# --- Numeric pin: Python port of the same formula shipped in JS, so the
# arithmetic itself is covered by a test runner independent of the browser


def _format_volume_or_rate(attribute: str, litres_value: float, unit: str) -> tuple[float, str]:
    """Direct port of index.html's formatVolumeOrRate, for a numeric pin."""
    if unit == "us_gallons":
        converted = litres_value / LITRES_PER_US_GALLON
        label = "gal/h" if attribute == "fuel_rate" else "gal"
        return converted, label
    label = "L/h" if attribute == "fuel_rate" else "L"
    return litres_value, label


def test_capacity_litres_converts_to_us_gallons_correctly():
    value, label = _format_volume_or_rate("capacity_litres", 189.0, "us_gallons")
    assert label == "gal"
    assert abs(value - 49.9285) < 0.001  # 189 L tank ~= 49.93 US gal


def test_fuel_rate_converts_with_distinct_per_hour_label():
    value, label = _format_volume_or_rate("fuel_rate", 18.5, "us_gallons")
    assert label == "gal/h"
    assert abs(value - 4.8865) < 0.001  # 18.5 L/h ~= 4.89 US gal/h


def test_litres_passthrough_is_exact_when_unit_is_litres():
    value, label = _format_volume_or_rate("capacity_litres", 189.0, "litres")
    assert value == 189.0
    assert label == "L"


def test_round_trip_through_gallons_and_back_restores_original_litres():
    original = 100.0
    to_gallons, _ = _format_volume_or_rate("capacity_litres", original, "us_gallons")
    back_to_litres = to_gallons * LITRES_PER_US_GALLON
    assert abs(back_to_litres - original) < 1e-9


# --- Requirement 8.8: the decode path has NO dependency on display unit --
# TankState/EngineState/Provenance stay litres-denominated regardless


def test_tank_decoder_output_is_litres_only_no_unit_parameter():
    """decode_fluid_level has no unit argument and always returns litres;
    confirms the display toggle cannot reach the decode path at all."""
    # Instance 0, fluid type 0; level 54.0% (raw 0x34E2), capacity 189.0 L (raw 1890)
    payload = bytes([0x00]) + (13500).to_bytes(2, "little", signed=True) + (1890).to_bytes(4, "little") + b"\xff"
    result = decode_fluid_level(source_address=42, data=payload)
    state = result.entity
    assert state.capacity_litres == 189.0  # litres, never gallons
    assert state.quantity_remaining_litres is not UNSET
    assert state.provenance.derivation == "(level_percent / 100) * capacity_litres"


def test_engine_decoder_fuel_rate_output_is_litres_per_hour_only():
    """decode_engine_dynamic has no unit argument; fuel_rate is always
    L/h -- confirms the display toggle cannot reach the decode path."""
    body = bytearray(26)
    body[0] = 0  # engine instance
    body[1:3] = (0xFFFF).to_bytes(2, "little")  # oil pressure N/A
    body[3:5] = (0xFFFF).to_bytes(2, "little")  # oil temp N/A
    body[5:7] = (0xFFFF).to_bytes(2, "little")  # coolant temp N/A
    body[7:9] = (0x7FFF).to_bytes(2, "little", signed=False)  # alternator N/A
    fuel_rate_raw = round(18.5 / 0.1)
    body[9:11] = fuel_rate_raw.to_bytes(2, "little", signed=True)
    body[11:15] = (0xFFFFFFFF).to_bytes(4, "little")  # engine hours N/A
    result = decode_engine_dynamic(source_address=35, data=bytes(body))
    assert result.entity.fuel_rate == 18.5  # L/h, never gal/h
