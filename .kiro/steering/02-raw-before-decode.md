---
title: Capture-first — raw before decode
inclusion: always
---

# Raw-before-decode ordering (Requirements 3.1, 12.1; Property 1)

Every valid frame is persisted to the append-only Raw_Capture_Log **before** any
decode, filter, or classification of that frame. This ordering is unconditional
and applies to **every** frame — valid, malformed, and unknown-PGN alike.

Rules:

- In the Ingestion_Service pipeline, `raw_log.append(frame)` is **stage 1** and
  cannot be reordered after parse/classify/decode.
- No frame is ever dropped by manufacturer, source address, PGN, or frequency
  before it is persisted.
- Under `CAPTURE_HALTED_STORAGE_FULL`, decoding stops too: a frame whose raw
  source could not be persisted is **never decoded or exposed** (the
  capture-first integrity rule — Property 35).

If you add a pipeline stage, it goes **after** raw persistence, never before.
