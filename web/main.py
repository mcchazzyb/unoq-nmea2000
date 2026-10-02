"""Web_API entrypoint: binds ONLY to loopback by default (Requirement 14.3;
steering 12). An explicit, named private-LAN address is accepted for phone
testing (Requirement 30.4-30.6; steering 13) -- never a wildcard. Run with
`make run-api` or `python -m web.main`.
"""
from __future__ import annotations

import ipaddress
import os
from pathlib import Path

from service.cache import LiveStateCache
from service.discovery import DiscoveryMode
from web.api import create_app

#: Never a publicly routable interface by default (Requirement 14.3).
DEFAULT_BIND_HOST = "127.0.0.1"
DEFAULT_BIND_PORT = 8000


def is_permitted_bind_host(host: str) -> bool:
    """Requirement 14.3, 30.4, 30.5; Property 41: accept loopback or a named
    RFC 1918 private address (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16 --
    the last of these is both the common home/marina-router range and what
    the Arduino UNO Q's own networking commonly advertises on). ALWAYS
    refuse a wildcard (0.0.0.0, ::), any public address, and any malformed
    string -- there is no configuration path around this refusal.
    """
    if host in ("127.0.0.1", "localhost", "::1"):
        return True
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False  # not a valid literal address -- refuse, don't guess
    if addr.is_unspecified:  # 0.0.0.0 / :: -- "every interface", never permitted
        return False
    if addr.is_loopback:  # ::1 handled above as a literal; 127.x handled via is_loopback too
        return True
    return addr.is_private


def main() -> int:
    import uvicorn

    capture_dir = Path(os.environ.get("UNOQ_CAPTURE_DIR", "./captures"))
    capture_dir.mkdir(parents=True, exist_ok=True)

    cache = LiveStateCache()
    discovery = DiscoveryMode()
    app = create_app(cache, discovery, capture_dir)

    host = os.environ.get("UNOQ_BIND_HOST", DEFAULT_BIND_HOST)
    if not is_permitted_bind_host(host):
        # Any interface outside loopback/private-LAN ranges must be an
        # explicit, deliberate override -- never silently accepted
        # (Requirement 14.3's "no publicly routable interface by default";
        # Requirement 30.5's explicit wildcard refusal).
        print(
            f"UNOQ_BIND_HOST={host} is not loopback or a private RFC 1918 address; "
            "refusing to bind a wildcard or potentially public interface.",
            flush=True,
        )
        return 1

    port = int(os.environ.get("UNOQ_BIND_PORT", DEFAULT_BIND_PORT))
    uvicorn.run(app, host=host, port=port)
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
