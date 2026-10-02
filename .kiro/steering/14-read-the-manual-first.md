---
title: Read the manual before diagnosing hardware or protocol behavior
inclusion: always
---

# RTFM before inference

For any UNO Q, Arduino App Lab, BlueZ/BLE, CAN-controller, or other
hardware/protocol diagnosis, read the relevant official vendor/manual/API
documentation *before* proposing an explanation or a fix.

Rules:

- Start with the authoritative source: Arduino UNO Q/App Lab documentation,
  the BlueZ D-Bus API documentation and upstream examples, device datasheets,
  or the protocol specification.
- Use live logs/traces to verify the manual against this exact hardware; do not
  replace reading the manual with guesswork from partial traces or screenshots.
- If documentation and observed behavior disagree, report the discrepancy
  explicitly and investigate it. Do not silently assume either is wrong.
- State what official source was checked before claiming a protocol or pairing
  behavior is understood.
