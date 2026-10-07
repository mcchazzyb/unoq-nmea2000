# Security model

The platform is a **passive local diagnostic tool**. It talks to the vessel
network and the local operator, and to nothing on the internet (steering
12). This document covers the receive-only guarantee, the threat model for
hostile/adverse input, and secret handling.

## Receive-only is structural, not configured (Requirement 1)

The platform never transmits on the CAN bus. This is enforced by the
**absence of a method**, not a runtime flag: `protocol/receiver.py`'s
`ICanReceiver` Protocol exposes only `frames()` and `health()` — there is
no `send()`, `transmit()`, or `write()` anywhere in the type, and none may
ever be added (steering 01). Code above this boundary cannot transmit
because the interface offers no way to; it is not "transmit is disabled",
it is "transmit does not exist as a callable". No module anywhere in the
host stack defines or calls a CAN transmit path.

On hardware (Stage 9-10, not yet built), this is backed by the physical
layer too: the controller/transceiver runs in silent/listen-only mode with
the transmit channel held non-driving through startup, shutdown, crash, and
reboot, and enabling transmit at all is gated behind four independent
barriers (a separate approved spec, a compile-time flag, a runtime flag,
and an explicit user action) that are explicitly out of scope for this
project.

**Known limit (measured 2026-10-03):** the transceiver's silent pin (D4) is held
HIGH by firmware only. Before the sketch starts (power-up, MCU reset, app stopped)
D4 reads about 0 V, so silent mode is not a hardware default. Accepted by the
owner: no code path exists that can send a frame, and the CAN controller is not
configured until the sketch starts, so nothing transmits in that window. In situ the
board is wired to the Backbone at power-on, so this window occurs on every boot (Linux
boot plus app start; length not yet measured). A 10k pull-up from D4 to 3.3 V would
close it in hardware if that reasoning is ever not enough.

## Local-only operation (Requirement 14.3, 14.9, 15.6)

- The Web_API binds to loopback and configured local interfaces only —
  never a publicly routable interface by default.
- No module in capture, decode, persistence, or serving issues an outbound
  internet request.
- The Web_UI makes no outbound request after its initial page load.

## Hostile/malformed input handling (Requirement 20.1, 20.2, 20.3)

Every untrusted input source is treated as hostile, quarantined/rejected
without terminating the process, and never allowed to block service to
other clients:

| Input | Handling |
|---|---|
| Malformed CAN frame | Marked `malformed=True`, still persisted, decode skipped — the pipeline continues with the next frame (steering 11) |
| Decoder that raises | Caught in Ingestion; the raw frame is already safely persisted; a decode-failure indication is recorded; processing continues |
| Unknown PGN | Retained, no decoder invoked, never dropped, replayable |
| Fast-packet corruption (missing/out-of-order counter, >5s gap) | The buffer is discarded — no partial or wrong message is ever emitted |
| Corrupted replay/log line | Quarantined as a `CorruptLine` (`service/raw_log.py`) rather than raised; every subsequent line is still processed (see `docs/log-format.md`) |
| Malformed Web_API request | Rejected with a named parameter/limit in the error body (`docs/api.md`'s Error shape); valid clients unaffected |
| Path traversal (`/logs/{id}`, `/replay`) | Rejected via `Path.resolve()` + `is_relative_to(capture_dir)` **before** any filesystem access — the escaping path is never opened (Property 26) |
| Unknown BLE characteristic name | Rejected with `UnknownCharacteristicError` naming the characteristic; the publisher's state and other clients are unaffected |

## Storage exhaustion (Requirement 12.6, 20.4)

Below 50 MB free, the platform enters `CAPTURE_HALTED_STORAGE_FULL`: it
stops **both** persisting and decoding — a value is never decoded or
exposed without a persisted raw source frame (Property 35) — and keeps
serving the last known state marked **stale**, resuming automatically once
capacity returns. See `docs/architecture.md` for the subscriber-behavior
decision during this transition (a connected client is not disconnected;
it keeps receiving the frozen state, correctly flagged stale).

## No secrets in source (Requirement 20.7)

`make lint` runs `scripts/secret_scan.py`, a small stdlib-only scanner, over
every git-tracked file. It checks known credential/key formats (PEM private
key blocks, AWS/GitHub/Slack token shapes) and a generic
"secret-sounding-name assigned a plaintext literal" heuristic, with a
placeholder allowlist (`changeme`, `<...>`, etc.) to avoid false positives on
normal config patterns like `os.environ["SOME_SECRET"]`. It is deliberately
narrow rather than a general entropy analyzer (ponytail: no new dependency
like `detect-secrets` for a surface this small) — the documented ceiling is
that it will miss a secret in an unrecognized format or split across lines;
`detect-secrets`/`gitleaks` are the named upgrade path if that ever matters.

The project's own local configuration file (`.env`) is git-ignored
(`.gitignore`) and therefore never read by the scanner or committed —
secrets belong there, not in tracked source, and the scanner's job is to
catch the case where one leaks into tracked source anyway.

## Data quality and staleness are never hidden (Requirement 20.6)

Every served value — over BLE, REST, WebSocket, or the UI — carries its
`quality` (`valid`/`stale`/`invalid`/`unknown`) and `staleness_s` from its
`Provenance`, so a stale or spoofed-looking value is distinguishable from
current valid data at the transport interface itself, not just internally.
See `docs/ble-protocol.md` and `docs/architecture.md` for the cross-transport
consistency details (including the BLE schema v2 bump that closed the one
real gap found here).
