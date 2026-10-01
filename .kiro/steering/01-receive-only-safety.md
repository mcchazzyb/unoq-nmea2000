---
title: Receive-only safety is structural
inclusion: always
---

# Receive-only safety (Requirement 1, Property 28)

The MVP **never transmits** on the CAN bus. This is enforced by *structure*, not
by discipline:

- `protocol/receiver.py` `ICanReceiver` exposes **only** `frames()` and
  `health()`. There is no `send()`, `transmit()`, or `write()` method anywhere
  in the type, and none may ever be added. The absence is the enforcement — code
  above the boundary cannot transmit because the interface offers no way to.
- No module in the whole host stack may define or call a CAN transmit path.
- On hardware, the controller/transceiver runs in silent/listen-only mode with
  the transmit channel held non-driving through startup, shutdown, crash, and
  reboot.
- Enabling transmit is gated behind **four independent barriers** (separate
  approved spec, compile-time flag, runtime flag, explicit user action) and is
  **out of scope** here. Do not add any of them.

Property 28 ("the platform transmits zero frames") is proven at the type level
by a structural test asserting `ICanReceiver` has no transmit-capable member.
