"""Web_API entrypoint: binds ONLY to loopback by default (Requirement 14.3;
steering 12). An explicit, named private-LAN address is accepted for phone
testing (Requirement 30.4-30.6; steering 13) -- never a wildcard. Run with
`make run-api` or `python -m web.main`.

CONTAINER NOTE (found via an actual App Lab deploy, 2026-10-02): when this
process runs inside App Lab's Docker container on the UNO Q, the real
host-facing security boundary is Docker's OWN port-publish mapping
(`ports: ["8000:8000"]` in app-compose.yaml) -- NOT this process's bind
address. Binding the container's own copy of the host's LAN IP
(e.g. 192.168.201.129) from INSIDE the container fails outright
(`cannot assign requested address`): that address belongs to the host's
network namespace, not the container's. Docker's publish mapping itself
binds 0.0.0.0 on the HOST side regardless of what the container's app binds
internally, so from inside the container the only correct bind target is
the container's own `0.0.0.0` (every container-internal interface) --
exposure is scoped by Docker's publish mapping to exactly the declared
port on the host's real interfaces, which is a DIFFERENT trust boundary
than bare-metal `0.0.0.0` (which `is_permitted_bind_host` correctly refuses
everywhere else, and must keep refusing by default). `UNOQ_IN_CONTAINER=1`
is the narrow, explicit, named escape for exactly this case -- it is never
inferred, never a default, and does not relax the refusal for a bare-metal
process that sets it without actually being containerized (the container
op guide, not this code, is the thing that must get this right).
"""
from __future__ import annotations

import ipaddress
import logging
import os
from pathlib import Path

from service.bus_stats import BusStats
from service.cache import LiveStateCache
from service.discovery import DiscoveryMode
from service.flight_recorder import FlightRecorder, RecorderLogHandler
from web.api import create_app
from web.runtime import LiveIngestionRuntime, build_receiver

#: Never a publicly routable interface by default (Requirement 14.3).
DEFAULT_BIND_HOST = "127.0.0.1"
DEFAULT_BIND_PORT = 8000


def is_permitted_bind_host(host: str, in_container: bool = False) -> bool:
    """Requirement 14.3, 30.4, 30.5; Property 41: accept loopback or a named
    RFC 1918 private address (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16 --
    the last of these is both the common home/marina-router range and what
    the Arduino UNO Q's own networking commonly advertises on). ALWAYS
    refuse a wildcard (0.0.0.0, ::), any public address, and any malformed
    string -- there is no configuration path around this refusal for a
    bare-metal process.

    `in_container=True` is the ONE narrow exception (see module docstring's
    CONTAINER NOTE): it permits 0.0.0.0/:: ONLY, because inside App Lab's
    Docker container that address means "every container-internal
    interface," and the real host-facing exposure boundary is Docker's own
    port-publish mapping, not this process's bind address. It does NOT
    relax the public-address refusal below -- a literal public IP is still
    refused even with `in_container=True`.
    """
    if host in ("127.0.0.1", "localhost", "::1"):
        return True
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False  # not a valid literal address -- refuse, don't guess
    if addr.is_unspecified:  # 0.0.0.0 / :: -- "every interface"
        return in_container  # permitted ONLY for the documented container case
    if addr.is_loopback:  # ::1 handled above as a literal; 127.x handled via is_loopback too
        return True
    return addr.is_private


def main() -> int:
    import uvicorn

    capture_dir = Path(os.environ.get("UNOQ_CAPTURE_DIR", "./captures"))
    capture_dir.mkdir(parents=True, exist_ok=True)

    cache = LiveStateCache()
    discovery = DiscoveryMode()
    bus_stats = BusStats()
    app = create_app(cache, discovery, capture_dir, bus_stats=bus_stats)

    # Flight recorder: fsynced diagnostics that survive a dirty power-off (service/flight_recorder.py).
    # Lives beside the capture directory, not inside it, so clearing captures never clears the evidence.
    recorder = FlightRecorder(Path(os.environ.get("UNOQ_BLACKBOX_DIR", str(capture_dir.parent / "blackbox"))))
    logging.getLogger().addHandler(RecorderLogHandler(recorder))

    # Requirement 14.10, 14.11: a continuous receiver loop feeds the SAME
    # cache/discovery the Web_API reads, selected by configuration only
    # (never a code change) -- 'simulator' (default, matches existing
    # host-testable behavior) or 'hardware' (Stage 9's real UNO Q receiver).
    receiver_kind = os.environ.get("UNOQ_RECEIVER", "simulator")
    receiver = build_receiver(receiver_kind)
    runtime = LiveIngestionRuntime(cache, discovery, capture_dir, receiver, bus_stats, recorder)
    runtime.start()

    in_container = os.environ.get("UNOQ_IN_CONTAINER") == "1"
    # Inside the container, the host's own LAN IP (what a phone connects
    # to) is unreachable as a BIND target from this process -- Docker's
    # port-publish mapping is what exposes it. Default to the container's
    # own wildcard in that case; UNOQ_BIND_HOST can still override it
    # explicitly (e.g. to bind only one container-internal interface).
    default_host = "0.0.0.0" if in_container else DEFAULT_BIND_HOST
    host = os.environ.get("UNOQ_BIND_HOST", default_host)
    if not is_permitted_bind_host(host, in_container=in_container):
        # Any interface outside loopback/private-LAN ranges (or, inside a
        # container, outside the documented wildcard case) must be an
        # explicit, deliberate override -- never silently accepted
        # (Requirement 14.3's "no publicly routable interface by default";
        # Requirement 30.5's explicit wildcard refusal).
        print(
            f"UNOQ_BIND_HOST={host} is not loopback, a private RFC 1918 address, or "
            "(with UNOQ_IN_CONTAINER=1) the documented container wildcard case; "
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
