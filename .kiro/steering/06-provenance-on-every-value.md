---
title: Provenance on every value
inclusion: always
---

# No number is exposed without its origin (Requirement 11; Property 11)

Every decoded or derived value carries a `Provenance` (`protocol/domain.py`). A
value is never served over any transport without it.

A complete `Provenance` records: `source_address`, `source_pgn`,
`node_identity`, `raw_frame_ref` (reference into the Raw_Capture_Log),
`timestamp`, `decoder_version`, `quality` (`valid`/`stale`/`invalid`/`unknown`),
`staleness_s`, `vendor_specific`, `value_origin` (`decoded`/`derived`), and
`derivation` (for derived values, the inputs used).

Rules:

- An undeterminable field is recorded as the string `"unknown"`, **never
  omitted** (Requirement 11.2).
- `value_origin` distinguishes a directly-decoded value (`"decoded"`) from a
  computed one (`"derived"`). A derived value names its inputs in `derivation`
  (e.g. `quantity_remaining_litres` → `"level_percent * capacity_litres"`,
  Property 33).
- `decoder.version` is threaded into Provenance on every produced value
  (Requirement 5.5).
- Provenance survives persistence and replay unchanged (Property 12).
