---
title: Property-test tagging convention
inclusion: always
---

# Tag every property test with its Feature + Property (Requirement 19)

Each Hypothesis property test runs **≥100 iterations** and is tagged with a
docstring/marker line in exactly this form:

```
Feature: nmea2000-diagnostic-platform, Property N: <text>
```

where `N` and `<text>` match the numbered property in `design.md` → *Correctness
Properties* (Properties 1–39).

Rules:

- Put the tag in the test's docstring (first line) so it is greppable, and run
  the test with `@settings(max_examples=100)` or more.
- Each stage's Stage Gate names the properties it proves; a property is "proven"
  only when a tagged test for it passes at the gate.
- Do not renumber or rename properties; the number is the stable contract
  between `design.md` and the test suite.
