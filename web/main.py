"""Web_API entrypoint: binds ONLY to loopback by default (Requirement 14.3;
steering 12). Run with `make run-api` or `python -m web.main`.
"""
from __future__ import annotations

import os
from pathlib import Path

from service.cache import LiveStateCache
from service.discovery import DiscoveryMode
from web.api import create_app

#: Never a publicly routable interface by default (Requirement 14.3).
DEFAULT_BIND_HOST = "127.0.0.1"
DEFAULT_BIND_PORT = 8000


def main() -> int:
    import uvicorn

    capture_dir = Path(os.environ.get("UNOQ_CAPTURE_DIR", "./captures"))
    capture_dir.mkdir(parents=True, exist_ok=True)

    cache = LiveStateCache()
    discovery = DiscoveryMode()
    app = create_app(cache, discovery, capture_dir)

    host = os.environ.get("UNOQ_BIND_HOST", DEFAULT_BIND_HOST)
    if host not in ("127.0.0.1", "localhost", "::1") and not host.startswith("192.168.") and not host.startswith("10."):
        # Any interface outside loopback/private-LAN ranges must be an
        # explicit, deliberate override -- never silently accepted
        # (Requirement 14.3's "no publicly routable interface by default").
        print(
            f"UNOQ_BIND_HOST={host} is outside loopback/private-LAN ranges; "
            "refusing to bind a potentially public interface by default.",
            flush=True,
        )
        return 1

    port = int(os.environ.get("UNOQ_BIND_PORT", DEFAULT_BIND_PORT))
    uvicorn.run(app, host=host, port=port)
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
