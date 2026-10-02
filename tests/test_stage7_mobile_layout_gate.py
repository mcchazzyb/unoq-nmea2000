"""Stage 7.8 Gate -- mobile-first Web_UI layout (Requirement 30.1-30.3;
steering 13). Static checks mirroring test_stage7_web_ui_gate.py's own
no-horizontal-scroll check, extended with explicit mobile-first assertions:
the BASE (no media query) rule is the phone layout, and desktop/tablet is
reached only via a min-width widen-up -- never the reverse.
"""
from __future__ import annotations

import re
from pathlib import Path

INDEX_PATH = Path("web/static/index.html")


def _html() -> str:
    return INDEX_PATH.read_text()


def _rule(selector: str, html: str) -> str:
    """Return the first bare (non-media-query) CSS rule body for selector."""
    pattern = re.escape(selector) + r"\s*\{([^}]*)\}"
    match = re.search(pattern, html)
    assert match, f"CSS rule for {selector!r} not found"
    return match.group(1)


# --- Requirement 30.1: base styles target phone portrait; desktop widens up


def test_main_grid_is_single_column_at_base_not_desktop_multicolumn():
    """The un-media-queried `main` rule must be a single column (the phone
    layout) -- the multi-column auto-fit grid is the SECONDARY case, reached
    only inside a min-width media query."""
    html = _html()
    base_main_rule = _rule("main", html)
    assert "grid-template-columns: 1fr" in base_main_rule
    assert "auto-fit" not in base_main_rule, (
        "base `main` rule still has the desktop multi-column grid as its "
        "default -- it must be single-column at base, widened via a "
        "min-width media query instead"
    )


def test_desktop_widen_up_is_a_min_width_media_query_not_max_width():
    """Requirement 30.1: `@media (min-width: 768px)` widens to desktop --
    the layout is never written desktop-first and narrowed down."""
    html = _html()
    assert "@media (min-width: 768px)" in html
    widen_block = re.search(r"@media \(min-width: 768px\)\s*\{([^}]*\{[^}]*\}[^}]*)\}", html, re.DOTALL)
    assert widen_block, "min-width:768px block not found"
    assert "auto-fit" in widen_block.group(1), "desktop widen-up should restore the multi-column grid"


def test_no_desktop_first_max_width_narrowing_the_main_grid():
    """A `@media (max-width: ...)` rule narrowing an otherwise-wide base
    layout is the desktop-first anti-pattern Requirement 30.1 forbids. Any
    remaining max-width media query in this file must be a minor padding
    tweak, not the mechanism that makes the layout fit a phone."""
    html = _html()
    for block in re.finditer(r"@media \(max-width: \d+px\)\s*\{([^{}]*(?:\{[^}]*\}[^{}]*)*)\}", html, re.DOTALL):
        body = block.group(1)
        assert "grid-template-columns" not in body, (
            "a max-width media query is still doing layout-column work -- "
            "that belongs in the mobile-first base rule instead"
        )


# --- Requirement 30.2: 44x44px minimum touch targets on Requirement 29 controls


def test_selection_toggle_meets_44px_minimum():
    html = _html()
    rule = _rule("#selection-toggle", html)
    assert "min-height: 44px" in rule


def test_instance_checkbox_meets_44px_minimum():
    html = _html()
    rule = _rule(".instance-checkbox-label", html)
    assert "min-height: 44px" in rule


def test_attribute_chip_meets_44px_minimum():
    html = _html()
    rule = _rule(".attribute-chip", html)
    assert "min-height: 44px" in rule


def test_reorder_buttons_meet_44x44_minimum():
    html = _html()
    rule = _rule(".dash-card-reorder button", html)
    assert "width: 44px" in rule
    assert "height: 44px" in rule


# --- Requirement 30.3: no horizontal scroll / clipped content at 320-430px


def test_body_floor_matches_phone_portrait_lower_bound():
    html = _html()
    assert "min-width: 320px" in html


def test_no_fixed_pixel_widths_above_the_phone_floor():
    """A hardcoded `width: <N>px` wider than the 320px floor would clip or
    force horizontal scroll on a narrow phone. Flex-basis minimums
    (dash-gauge-wrap, dash-metric-wrap) are fine -- they are a FLOOR within
    a wrapping flex container, not a hard total-width constraint."""
    html = _html()
    style_block = re.search(r"<style>(.*)</style>", html, re.DOTALL).group(1)
    # Collect bare `width: Npx` (not max-width, not flex: 1 1 Npx) declarations.
    for match in re.finditer(r"(?<![-\w])width:\s*(\d+)px", style_block):
        px = int(match.group(1))
        assert px <= 320, f"fixed width:{px}px exceeds the 320px phone floor"


def test_gauge_svg_scales_to_container_not_fixed_too_wide():
    """The gauge SVG's own max-width (128px) must fit inside a single
    narrow flex item at 320px, not force overflow."""
    html = _html()
    rule = _rule(".dash-gauge-svg", html)
    assert "width: 100%" in rule  # scales down from its max-width, never forces overflow
