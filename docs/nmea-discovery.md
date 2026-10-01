# NMEA 2000 node discovery and PGN classification

Two separate mechanisms make up "discovery": `service/discovery.py`'s
`DiscoveryMode` builds a live node inventory from decoded identity traffic,
and `protocol/detection.py`'s `classify_frame()` is a diagnostic classifier
that guesses which reference PGN signature(s) an unknown or proprietary
frame is consistent with. Neither assumes a specific manufacturer anywhere
(steering 04).

## Node identity: NodeKey, not source address

A device's identity on the bus is its 64-bit NMEA NAME (from PGN 60928, ISO
Address Claim) — **never** its source address, which NMEA 2000 can
reassign at any time via Address Claim (steering 08). `DiscoveryMode` keys
every node by a `NodeKey`:

- `"name:<64-bit-nmea-name-hex>"` once a NAME is known.
- `"provisional:sa<N>"` for a device observed only via non-identity traffic
  (any PGN other than 60928), until its first Address Claim arrives.

### Reconciliation

When a provisional record's device sends its first Address Claim, that
record is **reconciled in place**: its key changes from `provisional:sa<N>`
to `name:<...>`, carrying over `first_seen`, `observed_pgns`, and all other
accumulated observations (Requirement 10.1). The record is never discarded
and re-created — a reader following `DiscoveryMode.nodes` across a
reconciliation event sees the same logical node gain an identity, not a new
node appear.

### Reappearing at a new source address

When an already-named node's NAME reappears at a different source address
(the vessel's device reassigned it, or two devices briefly contended),
`current_source_address` is updated **in place** on the existing
`NodeRecord` — the `NodeKey` itself never changes (Property 39). Any lookup
or cache keyed by `NodeKey` keeps working across the move with no special
handling.

### Address conflicts

If two **distinct** NAMEs ever claim the same source address at the same
time, both NodeKeys and both records are retained — neither is discarded —
and the conflict is recorded in `address_conflicts` on **both** affected
records (Requirement 10.11, Property 19). `DiscoveryMode._observe_address_claim`
checks for this conflict *before* deciding whether to reconcile a
provisional record, specifically so a conflicting claim creates its own
record rather than overwriting/renaming the node already at that address.

## Online/offline lifecycle

- A node is marked **online** on every frame it sends, regardless of its
  prior state (Requirement 10.10).
- A node is marked **offline** only by `check_offline_transitions()`,
  called periodically by the ingestion loop — not implicitly by `observe()`
  — when its silence exceeds `max(3 × its own last observed interval, 30s)`
  (Requirement 10.9, Property 18). A node with a fast natural cadence (e.g.
  10 Hz) goes offline quickly after it actually stops; a node with a slow
  natural cadence (e.g. once every 10s) is given a longer grace period
  before being flagged, rather than a single fixed timeout misjudging both.

## Message rate

`NodeRecord.message_rate` is the count of that node's frames observed
within the trailing 60-second window (Requirement 10.3, Property 17),
recomputed on every `observe()` call by evicting timestamps older than
`now - 60.0`. The window is **closed/inclusive at the boundary** — a
timestamp exactly 60.000s before the latest frame still counts (see
`docs/architecture.md`'s entry on this for why).

## PGN classification (`protocol/detection.py`)

`classify_frame(can_id, data)` is **not a decoder** — it never populates a
Domain_Model entity. It reports which reference PGN signature(s) (from
`protocol/reference/canboat_pgns.py`) a raw frame's claimed PGN and length
are consistent with, each with a confidence score in `[0.0, 1.0]` and a
human-readable `reasoning` string. This exists specifically for **unknown
or proprietary** PGNs, where one PGN number can be reused by several
unrelated manufacturers (steering 04) — the classifier surfaces every
candidate signature rather than assuming a single interpretation.

Scoring starts every candidate at a base confidence of 0.5 (the frame's
identifier claimed this PGN), then adjusts for length evidence:

- Single-frame PGN, length matches exactly: **+0.3**
- Single-frame PGN, length does **not** match: **−0.4** (a single frame has
  a fixed size, so a mismatch is strong evidence against)
- Fast-packet PGN, full assembled length matches: **+0.3**
- Fast-packet PGN, frame meets the declared `min_length` (consistent with
  being a first/partial fragment): **+0.15**

Confidence is clamped to `[0.0, 1.0]`; ties are broken non-proprietary
signatures before proprietary ones. `best_candidate()` returns the
top-ranked candidate, or `None` if no reference signature exists for the
claimed PGN at all (a genuinely unrecognized PGN).

This is read-only diagnostic output for an operator reviewing an unknown
node — it never drives a decoder choice or populates `VesselState`.
