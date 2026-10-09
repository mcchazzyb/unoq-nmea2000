"""The dashboard must stay reachable from a phone on the iPhone hotspot (boat, 2026-10-09).

iOS always uses 172.20.10.0/28; Docker's default pool put the app network on 172.20.0.0/16, so the container
treated the phone as on-link and the dashboard never answered. The fix moves Docker's pool to 198.18.0.0/16.
"""
from __future__ import annotations

import ipaddress
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IPHONE_HOTSPOT = ipaddress.ip_network("172.20.10.0/28")
BOATFI = ipaddress.ip_network("10.42.0.0/24")


def test_fix_script_moves_docker_to_a_pool_that_cannot_overlap_a_hotspot():
    script = (ROOT / "scripts" / "fix_docker_subnet.sh").read_text()
    assert subprocess.run(["bash", "-n", str(ROOT / "scripts" / "fix_docker_subnet.sh")]).returncode == 0
    pool = ipaddress.ip_network("198.18.0.0/16")
    assert '"base": "198.18.0.0/16"' in script
    assert not pool.overlaps(IPHONE_HOTSPOT) and not pool.overlaps(BOATFI)
    assert not pool.overlaps(ipaddress.ip_network("192.168.0.0/16"))  # home and most hotspots
    # the stopped container pins the old network id, so it must be removed before the network
    assert script.index("docker rm -f") < script.index("docker network rm")


def test_reachability_check_covers_the_iphone_range_and_cleans_up():
    script = (ROOT / "scripts" / "check_hotspot_reachability.sh").read_text()
    assert subprocess.run(["bash", "-n", str(ROOT / "scripts" / "check_hotspot_reachability.sh")]).returncode == 0
    assert "172.20.10.2/28" in script and "10.42.0.1/24" in script
    assert "trap cleanup EXIT" in script


def test_preflight_rejects_a_docker_network_in_172_16_12():
    s = (ROOT / "scripts" / "boat_preflight.sh").read_text()
    assert "unoq_nmea2000_capture_default" in s and "172.16.0.0/12" in s
    assert subprocess.run(["bash", "-n", str(ROOT / "scripts" / "boat_preflight.sh")]).returncode == 0


def test_the_old_docker_default_really_did_overlap():
    assert ipaddress.ip_network("172.20.0.0/16").overlaps(IPHONE_HOTSPOT)
