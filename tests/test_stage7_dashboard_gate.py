"""Stage 7.6 Gate -- user-configurable multi-instance dashboard (Requirement
29). The feature is entirely client-side JS (no new API endpoint), so these
tests statically verify the shipped markup/script carries the required
behavior, mirroring test_stage7_web_ui_gate.py's own static-check pattern
for the parts that cannot be exercised through the Python TestClient.

A full DOM-level smoke test (selection add/remove, attribute toggling, the
gauge/metric split, reorder, localStorage round-trip) was run manually with
a minimal Node.js DOM stub during development and passed all 7 assertions;
it is not checked in as a Node test here since this project has no
JS-test-runner dependency (ponytail: avoid a new dependency -- ADR already
accepted elsewhere in this codebase for the same reason). The static checks
below are the project's existing precedent for verifying client-side JS
behavior without a browser test runner.
"""
from __future__ import annotations

import re
from pathlib import Path

INDEX_PATH = Path("web/static/index.html")


def _html() -> str:
    return INDEX_PATH.read_text()


# --- Requirement 29.1: every discovered instance is individually selectable


def test_selection_panel_lists_every_entity_instance():
    html = _html()
    assert 'id="selection-panel"' in html
    assert "listDiscoveredInstances" in html
    assert "data-instance-toggle" in html


def test_instances_distinguished_by_instance_key_not_just_entity_type():
    """Two engines must appear as two distinct selectable rows -- the
    instance list is built from the live payload's own instance:source
    keys, not a fixed single-instance assumption."""
    html = _html()
    assert "Object.keys(data.engines" in html
    assert "Object.keys(data.tanks" in html


# --- Requirement 29.2, 29.3: subset selection, per-attribute selection -----


def test_dashboard_config_model_shape():
    html = _html()
    assert "schema_version" in html
    assert "dashboardConfig" in html
    assert "unoq_dashboard_config_v1" in html


def test_attribute_checklist_present_per_entity_type():
    html = _html()
    assert "ENTITY_ATTRIBUTES" in html
    for entity_type in ("engine", "tank", "electrical", "navigation"):
        assert f'"{entity_type}"' in html or f"{entity_type}:" in html
    assert "attribute-checklist" in html
    assert "data-attr-toggle" in html


def test_only_selected_instances_render_as_cards():
    html = _html()
    assert "renderDashboard" in html
    assert "dashboardConfig.cards" in html


# --- Requirement 29.4, 29.5: gauge vs metric split --------------------------


def test_gauge_range_table_covers_the_documented_attributes():
    """Design doc's attribute-range table: these specific attributes get a
    dial; everything else falls back to a metric row."""
    html = _html()
    assert "GAUGE_RANGES" in html
    for attr in (
        "level_percent", "rpm", "coolant_temp", "oil_pressure",
        "fuel_rate", "battery_voltage", "charging_voltage", "depth",
    ):
        assert f"{attr}:" in html, f"{attr} missing from GAUGE_RANGES"


def test_gauge_rendered_as_inline_svg_not_canvas_library():
    """No Chart.js/canvas library -- avoids the self-sizing canvas feedback
    loop hazard; dials are plain inline SVG with a fixed viewBox."""
    html = _html()
    assert "dash-gauge-svg" in html
    assert "<svg" in html
    # No actual Chart.js script reference or <canvas> element -- a comment
    # merely mentioning why Chart.js was avoided is fine; a real dependency
    # is not.
    assert not re.search(r'<script[^>]+src="[^"]*chart\.js', html, re.IGNORECASE)
    assert "<canvas" not in html.lower()


def test_metric_fallback_exists_for_attributes_outside_the_range_table():
    html = _html()
    assert "renderMetricCell" in html
    assert "renderGauge" in html


# --- Requirement 29.6, 29.7: reorder + localStorage persistence -----------


def test_reorder_controls_present():
    html = _html()
    assert "data-move" in html
    assert "moveCard" in html
    assert "dash-card-reorder" in html


def test_config_persisted_to_localstorage_on_every_change():
    html = _html()
    assert "localStorage.setItem" in html
    assert "localStorage.getItem" in html
    # Persist is called from every mutating path, not just once at unload.
    assert html.count("saveDashboardConfig()") >= 3


def test_reorder_touch_targets_meet_44px_minimum():
    """Requirement 30.2: reorder buttons (and other Requirement 29 controls)
    get a real 44x44 CSS pixel touch target."""
    html = _html()
    reorder_button_css = re.search(r"\.dash-card-reorder button\s*\{([^}]*)\}", html)
    assert reorder_button_css, "reorder button CSS rule not found"
    rule_body = reorder_button_css.group(1)
    assert "width: 44px" in rule_body
    assert "height: 44px" in rule_body


def test_instance_checkbox_and_attribute_chip_meet_44px_minimum():
    html = _html()
    checkbox_css = re.search(r"\.instance-checkbox-label\s*\{([^}]*)\}", html)
    assert checkbox_css and "min-height: 44px" in checkbox_css.group(1)
    chip_css = re.search(r"\.attribute-chip\s*\{([^}]*)\}", html)
    assert chip_css and "min-height: 44px" in chip_css.group(1)


# --- Requirement 29.8: reuses the existing live payload, no second poll ---


def test_dashboard_render_is_driven_by_the_existing_renderlive_payload():
    """No second WebSocket, no second poll loop -- renderDashboard is called
    from inside the existing renderLive(), consuming the same payload."""
    html = _html()
    render_live_body = re.search(r"function renderLive\(data\)\s*\{(.*?)\n  \}", html, re.DOTALL)
    assert render_live_body, "renderLive() function not found"
    assert "renderDashboard(data)" in render_live_body.group(1)


# --- Requirement 29.9: dashboard-area empty state, distinct from discovery


def test_dashboard_empty_state_is_distinct_from_discovery_empty_state():
    html = _html()
    assert 'id="dashboard-empty"' in html
    assert 'id="discovery-empty"' in html
    assert html.index('id="dashboard-empty"') != html.index('id="discovery-empty"')
    assert "Select sensors to monitor" in html


# --- Requirement 29.10: a vanished instance keeps its last-known values ---


def test_stale_instance_keeps_rendering_last_known_values():
    html = _html()
    assert "_lastEntity" in html
    assert "card-is-stale" in html
    render_card_body = re.search(r"function renderDashboardCard\(card, data\)\s*\{(.*?)\n  \}", html, re.DOTALL)
    assert render_card_body
    body = render_card_body.group(1)
    # The card must fall back to the last-known entity rather than only the
    # current (possibly absent) one.
    assert "card._lastEntity" in body
