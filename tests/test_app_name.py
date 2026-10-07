"""The browser tab / home-screen name is "NMEA2000 Dashboard" (owner request, 2026-10-07)."""
from pathlib import Path

HTML = Path("web/static/index.html").read_text()


def test_page_and_home_screen_name():
    assert "<title>NMEA2000 Dashboard</title>" in HTML
    assert 'name="apple-mobile-web-app-title" content="NMEA2000 Dashboard"' in HTML
    assert 'name="application-name" content="NMEA2000 Dashboard"' in HTML
