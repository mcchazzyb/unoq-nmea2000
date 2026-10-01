---
title: Instance keying
inclusion: always
---

# Engines and tanks key on (instance, source_address) (Requirements 7.2, 8.2; Property 15)

`EngineState` and `TankState` live in dicts keyed by the tuple
`(instance, source_address)` — never by instance alone and never by source
address alone.

Rules:

- Two distinct instances never share one domain entity. A second engine at the
  same source address, or the same instance number at a different source
  address, is a **different** key and a different entity.
- `VesselState.engines` and `VesselState.tanks` are `dict`s with this tuple key.
- `updated_at` on each entity records the decode time of its most recent field
  values (Requirement 6.5).

Distinct-instances-never-collide is Property 15; the Stage 3 gate proves it.
