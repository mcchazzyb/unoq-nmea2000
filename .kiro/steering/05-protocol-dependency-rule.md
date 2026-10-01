---
title: Layer-2 dependency rule
inclusion: always
---

# /protocol imports nothing from /service /ble /web (Requirement 21.2)

`protocol/` is Layer 2: pure, platform-independent, no hardware or UI
dependency. It is the single Domain_Model that many transports consume.

Rules:

- No module under `protocol/` may import from `service/`, `ble/`, or `web/`.
  Transports depend on the Domain_Model; the Domain_Model never depends on a
  transport.
- `protocol/` also carries no hardware or UI import — it must run and be tested
  on a laptop with no CAN hardware, radio, or vessel.
- The boundary is enforced by `tests/test_import_boundary.py` (an AST check run
  by both `make lint` and `make test`). A new forbidden import fails the build;
  do not weaken or skip that test to land code.
- The only OS-specific dependency in the whole platform is BlueZ (Linux-only),
  and it lives behind the `BLE_Publisher` abstraction in `ble/`, never in
  `protocol/`.
