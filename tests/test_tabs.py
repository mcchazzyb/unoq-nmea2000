"""The page has two tabs: Dashboard (dials and data only) and Setup (bus status,
sensor selection, tank sizes, discovered devices). Regression for the owner's
request of 2026-10-10: configuration and diagnostics must not clutter the dials.
"""
from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

HTML = Path("web/static/index.html").read_text(encoding="utf-8")


class _Ids(HTMLParser):
    """Collect element ids inside each top-level <main>, plus the nav buttons."""

    def __init__(self) -> None:
        super().__init__()
        self.current: str | None = None
        self.ids: dict[str, set[str]] = {}
        self.tabs: list[dict[str, str | None]] = []
        self.main_attrs: dict[str, dict] = {}

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "main":
            self.current = a["id"]
            self.ids[self.current] = set()
            self.main_attrs[self.current] = a
        elif self.current and a.get("id"):
            self.ids[self.current].add(a["id"])
        if tag == "button" and "data-tab" in a:
            self.tabs.append(a)

    def handle_endtag(self, tag):
        if tag == "main":
            self.current = None


def _parsed() -> _Ids:
    parser = _Ids()
    parser.feed(HTML)
    return parser


def test_dashboard_view_has_only_dials_and_data():
    ids = _parsed().ids["view-dashboard"]
    assert {"dashboard-cards", "dashboard-empty", "engine-fields", "tank-fields",
            "fuel-figures", "electrical-fields", "navigation-fields"} <= ids
    configuration = {"bus-status", "selection-panel", "selection-toggle", "tank-settings",
                     "tank-setting-rows", "unit-toggle", "discovery-table", "discovery-rows"}
    assert not (configuration & ids), f"configuration shown on the dashboard: {configuration & ids}"


def test_setup_view_holds_bus_status_sensor_selection_tank_size_and_discovery():
    ids = _parsed().ids["view-setup"]
    assert {"bus-status", "selection-panel", "selection-toggle", "tank-settings",
            "tank-setting-rows", "unit-toggle", "discovery-table", "discovery-rows"} <= ids


def test_every_id_the_script_needs_exists_exactly_once():
    import re

    wanted = set(re.findall(r'getElementById\("([a-z0-9-]+)"\)', HTML))
    dynamic = {"tab-"}  # built as "tab-" + name / "view-" + name
    for element_id in sorted(wanted):
        if element_id in dynamic:
            continue
        assert HTML.count(f'id="{element_id}"') == 1, f"id {element_id!r} must appear exactly once"
    for name in ("dashboard", "setup"):
        assert HTML.count(f'id="view-{name}"') == 1 and HTML.count(f'id="tab-{name}"') == 1


def test_tabs_are_accessible_and_dashboard_is_the_default():
    parser = _parsed()
    assert [t["data-tab"] for t in parser.tabs] == ["dashboard", "setup"]
    assert all(t["role"] == "tab" and t["aria-controls"] for t in parser.tabs)
    assert parser.tabs[0]["aria-selected"] == "true" and parser.tabs[1]["aria-selected"] == "false"
    assert "hidden" not in parser.main_attrs["view-dashboard"]
    assert "hidden" in parser.main_attrs["view-setup"]


def test_tab_buttons_are_44px_touch_targets():
    assert "#tabs button {" in HTML and "min-height: 44px" in HTML.split("#tabs button {")[1].split("}")[0]


def test_a_silent_bus_still_flags_the_setup_tab():
    """The strip moved off the dashboard; a bus that is not delivering frames
    must still be visible from the Dashboard tab."""
    assert 'id="setup-alert"' in HTML
    assert 'markSetupAlert(status.state !== "receiving")' in HTML
    assert 'markSetupAlert(true)' in HTML  # board unreachable
