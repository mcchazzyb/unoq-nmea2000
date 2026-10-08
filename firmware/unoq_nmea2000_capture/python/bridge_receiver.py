"""
unoq-nmea2000 Stage 9.1 firmware -- Linux-side Bridge client (Requirement 24.1).

Runs on the UNO Q's Qualcomm Linux MPU side (App Lab's Python file for this
App). Polls the MCU sketch (sketch/sketch.ino) over the Bridge RPC and
implements protocol.receiver.ICanReceiver so the EXISTING Layer 2/3 stack
(Ingestion_Service, decoders, Web_API/UI) consumes real hardware frames
through the identical interface the Simulator already satisfies
(Requirements 17.9, 23, 24.6) -- no code above the Hardware Abstraction
Boundary needs to change for real hardware.

ponytail: get_frame_batch()/get_health() return a plain-text line encoding
(see sketch.ino's header comment for why) rather than a packed binary
format -- this module is the one place that parses it, so the encoding is
an internal contract between these two files, not a public wire format.

DEPLOYMENT NOTE (confirmed by an actual App Lab deploy via arduino-app-cli,
2026-10-02): App Lab's Python service runs in a Docker container with this
App's WHOLE directory (unoq_nmea2000_capture/) bind-mounted to /app --
confirmed from the generated .cache/app-compose.yaml. A host filesystem
path like /home/arduino/unoq-nmea2000 is NOT visible inside the container
(this failed with ModuleNotFoundError on the first deploy attempt). The fix
is that protocol/ is VENDORED as a sibling directory inside this App
(unoq_nmea2000_capture/protocol/, a copy of the repo's top-level protocol/),
so sys.path.insert(0, "/app") finds it inside the container. This is real,
intentional duplication, not a mistake: protocol/ has no hardware/UI
dependency (steering 05) and is small, but it DOES mean this vendored copy
can drift from the canonical repo copy if one is edited without the other.
Upgrade path: a packaging/sync step if drift becomes a real problem; out of
scope for Stage 9.1's first working deploy, which this docstring records as
genuinely proven live (CAN connected=True mode=ListenOnly on real silicon).
"""
from __future__ import annotations

import logging
import sys
import threading
import time
from dataclasses import dataclass

from arduino.app_utils import App, Bridge  # type: ignore[import-not-found]

# Make the existing, already-tested Layer 2 types importable from this
# App Lab Python file. protocol/ has no hardware/UI dependency (steering 05),
# so this import is the ONLY place Layer 1 (this file) touches Layer 2.
#
# App Lab bind-mounts this App's whole directory to /app inside its Python
# container (confirmed via .cache/app-compose.yaml), so protocol/ is vendored
# as a sibling of this file (unoq_nmea2000_capture/protocol/) rather than
# referenced by a host filesystem path -- a host path is invisible from
# inside the container (ModuleNotFoundError), confirmed by an actual deploy.
sys.path.insert(0, "/app")
from protocol.identifier import MalformedIdentifierError, parse_identifier  # noqa: E402
from protocol.receiver import CanFrame, CanHealth  # noqa: E402


logger = logging.getLogger(__name__)

# frames() (ingestion thread) and health() (health-poll thread) share one Bridge
# client. Overlapping calls on it can interleave on the router socket and leave the
# MCU's Bridge waiting on a half-sent request, so every call goes through one lock.
_BRIDGE_LOCK = threading.Lock()


def _bridge_call(method: str):
    with _BRIDGE_LOCK:
        return Bridge.call(method)


POLL_INTERVAL_S = 0.02  # 20ms -- comfortably faster than NMEA 2000's typical frame spacing
BRIDGE_CALL_TIMEOUT_S = 1.0


@dataclass
class _ParsedHealth:
    total_frames: int
    dropped_frames: int
    queue_depth: int
    controller_mode: str
    termination_off: bool
    silent_pin_high: bool
    error_counters: int
    uptime_ms: int | None = None  # absent from older sketches


def _parse_frame_line(line: str) -> CanFrame | None:
    """Parses one "<id>,<ext 0|1>,<len>,<hex bytes>,<millis_ts>" line from
    the MCU. Returns None for a malformed line rather than raising --
    capture-first/fail-safe (steering 02, 11): a single bad line from the
    Bridge must never take down the whole ingestion loop."""
    try:
        id_str, ext_str, len_str, hex_str, ts_str = line.split(",")
        can_id = int(id_str)
        ext = ext_str == "1"
        length = int(len_str)
        payload = bytes.fromhex(hex_str) if hex_str else b""
        if len(payload) != length:
            return None
        mcu_millis = int(ts_str)
    except (ValueError, IndexError):
        return None

    if not ext:
        # NMEA 2000 is 29-bit extended-frame only (Requirement 24.2); a
        # standard (11-bit) frame from this bus is unexpected wiring/noise,
        # not a frame this platform's PGN parser can interpret.
        return None

    try:
        parsed = parse_identifier(can_id)
    except MalformedIdentifierError:
        return None

    return CanFrame(
        can_id=can_id,
        priority=parsed.priority,
        pgn=parsed.pgn,
        source_address=parsed.source_address,
        dest_address=parsed.dest_address,
        dlc=length,
        payload=payload,
        capture_time=mcu_millis / 1000.0,
    )


def _parse_health_line(line: str) -> _ParsedHealth | None:
    try:
        parts = line.split(",")
        return _ParsedHealth(
            total_frames=int(parts[0]),
            dropped_frames=int(parts[1]),
            queue_depth=int(parts[2]),
            controller_mode=parts[3],
            termination_off=parts[4] == "true",
            silent_pin_high=parts[5] == "true",
            error_counters=int(parts[6]),
            uptime_ms=int(parts[7]) if len(parts) > 7 else None,
        )
    except (ValueError, IndexError):
        return None


class ArduinoBridgeCanReceiver:
    """Implements protocol.receiver.ICanReceiver (frames(), health()) against
    the real MCP2518FD hardware via the UNO Q Bridge. Receive-only by
    construction -- see steering 01: this class, like ICanReceiver itself,
    exposes no send()/transmit()/write() method, and never will."""

    def __init__(self) -> None:
        self._last_health: _ParsedHealth | None = None
        # Number of times the MCU was seen to restart (its uptime went backwards).
        # The sketch resets itself when its Bridge wedges; this makes that visible.
        self.mcu_restarts = 0
        self._can_init_ok: bool | None = None

    def _check_can_init_once(self) -> bool:
        if self._can_init_ok is None:
            self._can_init_ok = bool(_bridge_call("get_can_init_ok"))
        return self._can_init_ok

    def frames(self):
        """Yields CanFrame objects as they become available from the MCU's
        ring buffer, polling the Bridge at POLL_INTERVAL_S. A malformed line
        from the MCU is skipped, never raised (fail-safe, steering 11)."""
        self._check_can_init_once()
        while True:
            batch = _bridge_call("get_frame_batch")
            if batch:
                for line in batch.split("\n"):
                    if not line:
                        continue
                    frame = _parse_frame_line(line)
                    if frame is not None:
                        yield frame
            time.sleep(POLL_INTERVAL_S)

    def _note_restart(self, parsed: _ParsedHealth) -> None:
        prev = self._last_health
        if prev is None or prev.uptime_ms is None or parsed.uptime_ms is None:
            return
        if parsed.uptime_ms < prev.uptime_ms:
            self.mcu_restarts += 1
            logger.warning(
                "MCU restarted (uptime %d ms -> %d ms); restart #%d since the app started. "
                "The sketch resets itself when its Bridge stops answering.",
                prev.uptime_ms, parsed.uptime_ms, self.mcu_restarts,
            )

    def health(self) -> CanHealth:
        """Requirement 24.5: health metrics exposed over the Bridge. Returns
        the LAST successfully parsed health snapshot if the current Bridge
        call fails or returns a malformed line, rather than raising --
        serving stale health is better than crashing the health endpoint
        (consistent with steering 11's fail-safe/capture-first intent)."""
        raw = _bridge_call("get_health")
        parsed = _parse_health_line(raw) if raw else None
        if parsed is not None:
            self._note_restart(parsed)
            self._last_health = parsed
        h = self._last_health
        # CanHealth.termination_on MUST be False in the MVP (Requirement 1.5,
        # 24.4) -- this sketch has no runtime termination switch at all, so
        # it is hard-False here, never derived from anything the MCU reports.
        return CanHealth(
            connected=self._check_can_init_once(),
            fps=0.0,  # derived by the caller from successive health() calls, not duplicated here
            dropped_frames=h.dropped_frames if h else 0,
            error_count=h.error_counters if h else 0,
            controller_state=h.controller_mode if h else "unknown",
            termination_on=False,
        )


def loop():
    """App Lab's App.run(user_loop=loop) entry point. The real wiring into
    Ingestion_Service happens in service/ingestion.py's existing
    `receiver.frames()` consumption loop -- this stub proves the Bridge
    round-trip in isolation first (Stage 9.1), ahead of Stage 9.2's bench
    sign-off, before Ingestion_Service is pointed at this receiver for real
    on-boat capture."""
    receiver = ArduinoBridgeCanReceiver()
    health = receiver.health()
    print(
        f"CAN connected={health.connected} "
        f"mode={health.controller_state} "
        f"termination_on={health.termination_on} "
        f"dropped={health.dropped_frames} errors={health.error_count}"
    )
    time.sleep(1.0)


if __name__ == "__main__":
    App.run(user_loop=loop)
