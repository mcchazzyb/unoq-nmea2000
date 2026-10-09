"""The board's systemd units must survive /home being mounted late (boat, 2026-10-09).

/home/arduino is a separate partition. A unit installed as a symlink into it dangles when systemd
reads /etc/systemd/system, so unoq-mcu-recover never started on either boat boot. These checks
pin the install instructions (copy, do not link) and the mount ordering.
"""
from __future__ import annotations

from pathlib import Path

UNIT = Path("deploy/unoq-mcu-recover.service")
HARDWARE_DOC = Path("docs/hardware.md")
PREFLIGHT = Path("scripts/boat_preflight.sh")


def test_recover_unit_waits_for_the_home_mount():
    assert "RequiresMountsFor=/home/arduino" in UNIT.read_text()


def test_recover_unit_install_comment_copies_not_links():
    text = UNIT.read_text()
    assert "sudo cp /home/arduino/unoq-nmea2000/deploy/unoq-mcu-recover.service /etc/systemd/system/" in text
    assert "systemctl enable --now /home/" not in text


def test_hardware_doc_does_not_tell_the_owner_to_link_the_unit():
    assert "systemctl enable --now /home/" not in HARDWARE_DOC.read_text()


def test_preflight_rejects_a_symlinked_unit():
    s = PREFLIGHT.read_text()
    assert "! test -L /etc/systemd/system/unoq-mcu-recover.service" in s
