---
title: NodeKey stability
inclusion: always
---

# Node identity is the NMEA NAME, not the source address (Requirements 10.1, 10.2, 10.11; Properties 19, 39)

`NetworkState.nodes` is keyed by a stable **NodeKey**, derived from the 64-bit
NMEA NAME (PGN 60928 ISO Address Claim). Source address is **not** identity — on
NMEA 2000 it can be reassigned via Address Claim.

Rules:

- `NodeKey` is `"name:<64-bit-nmea-name-hex>"` once a NAME is known, else a
  **provisional** key `"provisional:sa<N>"`. On the first Address Claim, the
  provisional record is **reconciled** to the NAME-based key and its prior
  observations are carried over.
- `current_source_address` is a **mutable attribute** of a `NodeRecord`. When the
  same NAME reappears at a new source address, the **NodeKey is retained** and
  only `current_source_address` is updated in place (Property 39).
- When two distinct NAMEs claim the same source address, **both** NodeKeys and
  their observations are retained, neither discarded, and the conflict is
  recorded in each affected record's `address_conflicts` (Property 19).
- Never key a node, or a lookup, by source address.
