---
title: Local-only, no outbound, no secrets
inclusion: always
---

# Local-only operation and no secrets (Requirements 14.3, 14.9, 15.6, 20.7; Property 26)

The platform is a passive local diagnostic tool. It talks to the vessel network
and the local operator, and to nothing on the internet.

Rules:

- **Local-only binding:** the Web_API binds to loopback and configured local
  interfaces only — **never** a publicly routable interface by default
  (Requirement 14.3).
- **No outbound internet:** capture, decode, persistence, and serving issue **no**
  outbound internet requests (Requirement 14.9). The Web_UI makes no outbound
  request after initial load (Requirement 15.6).
- **Safe path resolution:** log/replay file paths resolve with `Path.resolve()` +
  `is_relative_to(capture_dir)`; any path outside the configured capture
  directory is rejected without access (Property 26).
- **Hostile input is untrusted:** all CAN traffic, replay files, and client
  requests are untrusted; malformed/out-of-range requests are rejected with an
  error naming the failed parameter, while valid clients keep being served.
- **No secrets in source:** no plaintext secrets, credentials, or private keys in
  tracked source (Requirement 20.7) — enforced by a secret-scan lint step. The
  `.env` file holding any local config is git-ignored and never committed.
