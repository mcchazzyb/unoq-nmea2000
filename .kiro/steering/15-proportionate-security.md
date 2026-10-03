---
title: Proportionate security, read-only hardware
inclusion: always
---

# Security is proportionate: this is a read-only boat gauge

The platform only listens to the CAN bus and shows data (engine, fuel, nav) to the
owner. It transmits nothing onto the vessel network. The owner has said they do not
care who can see fuel consumption or similar readings, so confidentiality of that
data is NOT a design driver.

Rules:

- Apply core security hygiene and stop there: no secrets in source, validated input at
  trust boundaries, safe path resolution, and the existing bind-host guard (steering 12
  and 13). Do not add more layers on top.
- Do NOT propose or build extra authentication, encryption-at-rest, MITM-protected BLE
  pairing, TLS, rate limiting, or audit logging unless the owner asks. Just Works BLE
  pairing with auto-accept is an accepted trade-off on this hardware.
- Do NOT spend review time hardening against "someone can read the dashboard". Flag a
  security issue only when it could change what the device does on the CAN bus,
  damage data, or expose credentials.
- The things that DO stay strict are safety, not security: receive-only structure
  (steering 01), the silent-mode transmit fail-safe (Stage 9.2), and fail-safe
  robustness (steering 11). Those are not relaxed by this note.
- Document known limits in one line (e.g. in `docs/can-safety.md`) rather than
  engineering around them.
