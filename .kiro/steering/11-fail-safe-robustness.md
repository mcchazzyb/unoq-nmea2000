---
title: Fail-safe robustness
inclusion: always
---

# No single fault terminates the pipeline (Requirements 2, 4.5, 12.6, 20; Properties 3, 4, 9, 35)

The unifying rule is capture-first, fail-safe: a raw frame is persisted before
anything can fail, and no single fault — a bad frame, a bad decoder, a full
disk, a corrupt replay, a hostile client — may terminate the pipeline or lose
previously persisted data.

Rules:

- **Malformed frame:** mark `malformed=True`, still persist it with a malformed
  indication, and return without raising so the next frame proceeds (Property 4).
- **Decoder that raises:** caught in Ingestion; the raw frame is already safe, a
  decode-failure indication is recorded, processing continues.
- **Unknown PGN:** retained in an unknown state, no decoder invoked, never
  dropped, still replayable (Property 3).
- **Fast-packet corruption:** a missing/out-of-order counter, or a >5 s gap since
  the first frame, **discards** the buffer without emitting a partial or wrong
  message; subsequent frames continue (Property 9).
- **Disk full (<50 MB free):** enter `CAPTURE_HALTED_STORAGE_FULL` — stop
  persisting **and** stop decoding, keep serving the last state marked **stale**,
  resume automatically when capacity returns. Never decode or expose a value
  lacking a persisted raw source frame (Property 35).
- **Append-only log:** earlier lines are never rewritten; frames written before a
  fault remain byte-identical afterward (Property 5).
