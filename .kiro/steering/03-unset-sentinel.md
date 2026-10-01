---
title: UNSET sentinel discipline
inclusion: always
---

# UNSET is distinct from None and 0 (Property 13)

`protocol/domain.py` defines `UNSET`, a falsy singleton sentinel. It marks a
field that is structurally present in a PGN but whose value is **absent,
invalid, or reserved** on the wire (NMEA 2000's `0xFF…` "data not available"
convention).

Rules:

- A field with no source value is `UNSET`, **never** `None` and **never** `0`.
  `0` is a valid reading (0 RPM, 0% tank level); `None` conflates "not
  applicable" with "not yet decoded".
- There is exactly **one** sentinel across the whole protocol core. Import it
  from `protocol.domain` (the decoder envelope re-exports the same object). Never
  create a second sentinel.
- A decoder populates a field only where the frame provides it; absent / invalid
  / reserved values are left `UNSET` (Requirements 7.1/7.4, 8.1/8.3/8.5, 9.3).
- `TankState.level_percent` is `UNSET` or within `0..100` inclusive — an
  out-of-range raw value decodes to `UNSET`, never a clamped number (Property 14).

Every optional field on every entity must be `UNSET`-distinguishable from both
`None` and `0`; the Stage 1 gate proves this field-by-field.
