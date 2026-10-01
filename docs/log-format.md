# Raw_Capture_Log format

The Raw_Capture_Log (`service/raw_log.py`) is an append-only JSONL file: one
JSON object per line, one line per captured frame, flushed and `fsync`'d
before the write is considered durable. Earlier lines are never rewritten
(Property 5) — a rotation closes the current file and opens a new one; a
rotated file is never reopened for writing.

## Record shape

```json
{
  "capture_time": 123.456,
  "can_id": 234881280,
  "priority": 6,
  "pgn": 127488,
  "source": 1,
  "dest": null,
  "dlc": 8,
  "payload_hex": "0010270000ffff00",
  "malformed": false
}
```

| Field | Type | Meaning |
|---|---|---|
| `capture_time` | float | Monotonic/relative capture timestamp (seconds) |
| `can_id` | int | The raw 29-bit extended CAN identifier |
| `priority` | int | Decoded priority field (3 bits) |
| `pgn` | int | Decoded PGN |
| `source` | int | Source address (0-255) |
| `dest` | int \| null | Destination address, or `null` for a broadcast/PGN with no addressed destination |
| `dlc` | int | Data length code (bytes in payload) |
| `payload_hex` | string | Raw payload bytes, hex-encoded |
| `malformed` | bool | True if ingestion flagged this frame malformed (still persisted — Requirement 2.1) |

Two optional fields appear only when relevant, never as an explicit `null`:

- `wall_clock_time` — present when a real wall-clock timestamp was available
  at capture time (absent for a pure simulator run with no wall clock).
- `decode_failure` — present and `true` only on a **separate** record
  appended by `mark_decode_failure()` after the original raw record; the
  original frame's own line is never mutated (append-only, Property 5).

## Reading records: `iter_records()`

`service.raw_log.iter_records(path)` yields each line as a parsed `dict`, in
file order, including malformed/unknown-PGN/decode-failure records — nothing
is filtered out (Property 3). A line that fails to parse as JSON (a
corrupted or truncated file) is **not** raised: it is yielded as a
`CorruptLine(line_number, raw, error)` instead, and iteration continues past
it (Requirement 20.1, 20.3). Callers that build typed objects from records
(e.g. `simulator.replay.load_fixture_frames`) must skip a `CorruptLine`
rather than treat it as a dict.

## Rotation

A capture file rotates (closes, opens a new timestamped file) when it
exceeds `DEFAULT_MAX_BYTES` (100 MB) or `DEFAULT_MAX_AGE_S` (24 hours),
whichever comes first. Replaying a capture session that spans a rotation
means reading each `capture-<timestamp_ns>.jsonl` file in the directory in
filename order — the timestamp in the filename is the file's creation time.

## Write failure handling

A write failure (disk error, closed file handle) is recorded in
`RawCaptureLog.last_write_failure` and the method returns `False` — it never
raises, and previously persisted bytes are left untouched (Requirement
12.8). See `docs/architecture.md`'s `CAPTURE_HALTED_STORAGE_FULL` entry for
the storage-exhaustion case specifically.
