# Tank/fuel data (PGN 127505)

Tank level and capacity are decoded from standard NMEA 2000 PGN 127505
(Fluid Level) by `protocol/decoders/tank.py`. Per steering 04, this decoder
makes no manufacturer assumption — any compliant tank sender interface (a
resistive-sender-to-NMEA2000 gateway, a dedicated tank module, or a
simulator) emits the same PGN and is decoded identically.

## Wire layout (single-frame, 8 bytes)

| Byte(s) | Field | Encoding |
|---|---|---|
| 0, bits 0-3 | Instance | u4 |
| 0, bits 4-7 | Fluid type | u4 (0=fuel, 1=water, 2=waste, 3=live well, 4=oil, 5=black water, …) |
| 1-2 | Level | i16, resolution 0.004%, `0x7FFF` = not available |
| 3-6 | Capacity | u32, resolution 0.1 L, `0xFFFFFFFF` = not available |
| 7 | Reserved | — |

## Decoding rules (Requirement 8.1-8.7)

- `tank_instance` and `source_address` together form the entity's identity
  key — two tanks with the same instance number at different source
  addresses, or vice versa, are different `TankState` entities (steering
  07, Property 15). Keying happens at the cache/registry layer, not in this
  decoder.
- `level_percent` decodes to `UNSET` (never `None`, never a clamped number)
  when the raw value is the N/A sentinel **or** the decoded percentage
  falls outside `[0, 100]` — an out-of-range raw value is rejected, not
  clamped (Requirement 8.5, Property 14).
- `capacity_litres` decodes to `UNSET` on the N/A sentinel.
- `fluid_type` decodes to `UNSET` only when fewer than 1 byte of payload is
  present (malformed/truncated frame) — a valid 0-15 type code is always
  decodable even when `level_percent`/`capacity_litres` are unavailable.

## The derived `quantity_remaining_litres` field — and its known oddity

When both `level_percent` and `capacity_litres` are present (neither
`UNSET`), the decoder derives:

```
quantity_remaining_litres = (level_percent / 100) * capacity_litres
```

and marks that value's `Provenance.value_origin = "derived"` with
`derivation = "(level_percent / 100) * capacity_litres"` (Requirement 8.6,
Property 33). When either input is `UNSET`, `quantity_remaining_litres` is
`UNSET` too — it is never computed from a partial input.

`level_percent` is a 0-100 percentage of the tank's configured
`capacity_litres` — matching both the NMEA 2000 PGN 127505 field definition
(`Level` is a percentage field; `Capacity` is the tank's own configured
volume) and real-world tank-sender configuration, where capacity is set once
in litres (e.g. 90 L, 120 L) and the sender reports level as % of that
setting. See `docs/architecture.md` for the judgment-call record (resolved
2026-10-01).

## Example

A tank at 50% full with a 200 L capacity, decoded:

```python
TankState(
    tank_instance=0,
    fluid_type=0,          # fuel
    level_percent=50.0,
    capacity_litres=200.0,
    quantity_remaining_litres=100.0,
    ...
)
```

## Wire units are litres, unconditionally — no gallons path (Requirement 8.8)

PGN 127505's `Capacity` field, and PGN 127489's `Fuel rate` field, are
defined by the NMEA 2000 standard in **litres** and **litres-per-hour**
respectively, on the wire, regardless of which country's hardware is
transmitting. A US-market tank sender or engine interface that lets its
*installer* configure the tank size in US gallons still performs the
gallon-to-litre conversion internally, inside the sender/gateway, before
the value is ever broadcast on the bus — this platform only ever sees
the already-converted litres value and performs **no unit conversion at
decode time**. `capacity_litres`, `quantity_remaining_litres`, and
`fuel_rate` are therefore always litres-denominated in `TankState` and
`EngineState`, with no ambiguity to resolve in `protocol/decoders/`.

The Web_UI's dashboard (Stage 7.11) offers a **display-only** litres/US
gallons toggle for exactly these three attributes, purely for a reader who
thinks in gallons — it converts the number and label at render time only
(`1 US gal = 3.785411784 L`) and never touches the decoded/derived value,
its `Provenance`, or the `(level_percent / 100) * capacity_litres`
derivation text above, which remain litres-denominated no matter which
display unit is selected.
