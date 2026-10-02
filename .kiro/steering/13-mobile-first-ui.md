---
title: Mobile-first UI, LAN testing is opt-in and private-only
inclusion: always
---

# The Web_UI is a phone surface first (Requirement 30)

The real usage pattern is a boat owner holding a phone in a cockpit, not a
desktop browser. Every Web_UI change designs for that first and treats wider
viewports as the secondary case, not the other way around.

Rules:

- **Base styles target a phone portrait viewport** (320-430px) directly; a
  `@media (min-width: 768px)` rule then widens to desktop/tablet. Never write
  a desktop-first layout and rely on reflow to make it work on a phone.
- **Every interactive control is a real touch target** — minimum 44x44 CSS
  pixels (WCAG 2.5.5 / Apple HIG), especially the Requirement 29 selection
  checkboxes, attribute checklist, and reorder controls.
- **Gauges and text are sized for arm's-length outdoor reading** — a cockpit
  in daylight, not a desk under even lighting.

## Local-network testing access is explicit, named, and never a wildcard

The Web_API (Requirement 14) has **no authentication**. Steering 12's
loopback-only default stands; what Requirement 30 adds is a narrow, explicit
override for loading the Web_UI from a phone on the same trusted LAN during
development or on-boat use — never a general network exposure.

- The bind-host guard (`web/main.py`) accepts loopback or a **named RFC 1918
  private address** (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16) that the
  caller supplies explicitly via `UNOQ_BIND_HOST`. It is never auto-detected
  and never defaulted to anything but loopback.
- **`0.0.0.0` and `::` (every interface) are refused by default, with no
  configuration path to override that refusal for a bare-metal process.**
  The ONE documented exception is the container case below
  (`UNOQ_IN_CONTAINER=1`), which is narrow, explicit, and never inferred.
  Binding "every interface" is categorically different from binding "my
  own LAN IP so my phone can reach it" — the former is an unauthenticated
  service reachable by anything that can route a packet to the host (open
  marina WiFi, a compromised device on the same network, etc.); the latter
  is still scoped to one host's one private address.
- Any change to the bind guard is a security-relevant change: it needs a
  test proving the wildcard/non-private refusal still holds (Property 41),
  not just a test proving the new private-address path works.

## Container deploys: the wildcard bind is sometimes correct, never by default

Found via an actual App Lab deploy to the UNO Q (Requirement 30.7): when
`web/main.py` runs inside a container whose own network boundary (Docker's
port-publish mapping in App Lab's case) is the real exposure control, the
board's own LAN IP is **unreachable as a bind target from inside the
container** (`cannot assign requested address` — that address lives in the
host's network namespace, not the container's). The only valid in-container
bind target is the container's own `0.0.0.0`.

This does NOT mean the bind guard's wildcard refusal is wrong or should be
loosened generally — it means the container case needs its own narrow,
explicit, never-inferred escape:

- `UNOQ_IN_CONTAINER=1` is the ONLY way `is_permitted_bind_host` permits a
  wildcard, and it is opt-in, never a default, never detected automatically
  from the runtime environment.
- It widens ONLY the wildcard case. A literal public address is still
  refused with `UNOQ_IN_CONTAINER=1` set.
- The deploy entrypoint (not this code) is responsible for setting it only
  when genuinely running in the documented container case.
