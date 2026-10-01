---
title: No manufacturer-specific aggregate classes
inclusion: always
---

# Generic domain, no manufacturer classes (Requirement 6.2)

The Domain_Model is generic and transport-independent. There is **no monolithic
manufacturer class** — no `SuzukiData`, `YamahaStatus`, `MercuryEngineData`, or
any equivalent — anywhere in the domain model or the decoders.

Rules:

- A Suzuki SMIS engine interface, a Yamaha gauge, and a bare generic sensor all
  emit the **same standard PGNs** (127488/127489 engine, 127505 fluid level,
  129025/129026 nav, …). Decoders decode the bytes the standard defines, making
  **no manufacturer assumption**.
- Manufacturer identity, where known, lives as *data* on a `NodeRecord`
  (`manufacturer_code`, `product_info`), never as a bespoke aggregate type.
- Proprietary PGN numbers (e.g. 65280) are reused by many vendors. They are held
  as a **list of candidate reference signatures** for autodetection
  (`protocol/reference/`), not decoded as one assumed vendor.
- A manufacturer-specific decoder, if ever justified, is per-PGN and additive —
  it never introduces an aggregate "all of vendor X" class.
