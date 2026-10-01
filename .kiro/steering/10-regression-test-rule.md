---
title: Every feature or fix adds a regression test
inclusion: always
---

# Ongoing regression discipline (Requirement 19.6)

Every feature or fix added **after** the implementation plan must ship with at
least one test that **fails without the change and passes with it**.

Rules:

- This is a standing rule, not a one-time task. It applies to every PR, every bug
  fix, and every behaviour change, for the life of the project.
- A trivial one-liner may be covered by an existing test, but a behaviour change
  with no failing-then-passing test is incomplete.
- Prefer the smallest test that pins the behaviour: a focused unit test, or a
  Hypothesis property test (tagged per the property-tagging rule) when the change
  touches a correctness property.
- `make test` must be green before the change lands; a red suite is never
  "pre-existing" without first proving it fails on the prior commit too.
