"""A dashboard card must be removable from the card itself.

Regression (steering 10): switching from the simulator (engine 0:35) to real
hardware (engine 0:23) left a stale 0:35 card that could not be deleted, because
the selection panel only lists instances currently on the bus. Static check of
the shipped page, per the project's precedent for client-side JS.
"""
from __future__ import annotations

import re
from pathlib import Path

HTML = Path("web/static/index.html").read_text()


def test_every_card_has_a_remove_button_wired_to_deselect():
    card = re.search(r"function renderDashboardCard\(.*?\n  \}", HTML, re.S).group(0)
    assert "data-remove=" in card and "Remove" in card
    dash = re.search(r"function renderDashboard\(data\).*?\n  \}", HTML, re.S).group(0)
    assert re.search(r'querySelectorAll\("\[data-remove\]"\)', dash)
    assert "toggleInstanceSelected(entityType, rest.join(\"|\"), false)" in dash


def test_stale_cards_still_persist_until_removed_requirement_29_10():
    card = re.search(r"function renderDashboardCard\(.*?\n  \}", HTML, re.S).group(0)
    assert "const isStale = !entity;" in card and "card._lastEntity" in card
