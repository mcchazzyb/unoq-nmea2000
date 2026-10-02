"""unoq-nmea2000 real Web_API/Web_UI entrypoint, run inside App Lab's Python
container on the UNO Q (Requirement 24.1, 24.6: the same host-testable
Web_API/Web_UI/Ingestion_Service stack, run against the real hardware
receiver instead of the Simulator -- no code above the Hardware Abstraction
Boundary changes for real hardware, per Requirement 17.9/23).

DEPLOYMENT NOTE (same constraint as bridge_receiver.py, confirmed by an
actual App Lab deploy): this App's whole directory is bind-mounted to /app
inside the container, so protocol/, service/, web/, simulator/ are each
vendored as top-level siblings here (real, documented duplication of the
repo's own top-level packages of the same names -- see
bridge_receiver.py's DEPLOYMENT NOTE for the drift tradeoff this implies).

This file does not reimplement web/main.py's entrypoint logic -- it imports
and calls it directly, after inserting /app onto sys.path, with
UNOQ_RECEIVER defaulted to 'hardware' (this file's whole reason to exist is
running on the real board). UNOQ_IN_CONTAINER=1 and UNOQ_BIND_HOST=0.0.0.0
are the correct bind for App Lab's Docker container (see web/main.py's
CONTAINER NOTE docstring, found via an actual deploy on 2026-10-02):
binding the board's own LAN IP from INSIDE the container fails outright
('cannot assign requested address') since that address lives in the host's
network namespace, not the container's. Docker's OWN port-publish mapping
(app.yaml's `ports: [8000]`) is the real security boundary here, scoping
exposure to the host's declared port on its real interfaces -- a different
boundary than a bare-metal process binding 0.0.0.0, which the bind guard
still correctly refuses everywhere else. All three remain overridable via
app.yaml / the container's environment without editing this file.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, "/app")

os.environ.setdefault("UNOQ_RECEIVER", "hardware")
os.environ.setdefault("UNOQ_IN_CONTAINER", "1")
os.environ.setdefault("UNOQ_BIND_HOST", "0.0.0.0")
os.environ.setdefault("UNOQ_CAPTURE_DIR", "/app/.cache/captures")

from web.main import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
