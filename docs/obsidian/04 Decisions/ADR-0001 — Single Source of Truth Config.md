---
title: ADR-0001 — Single Source of Truth Config
type: adr
status: accepted
date: 2026-06-07
tags: [adr, config]
up: ["[[Decisions]]"]
related: ["[[Infrastructure]]"]
---

# ADR-0001 — Single Source of Truth Config

**Status:** Accepted

## Context

Config and secrets were at risk of being read ad-hoc from `os.environ` across many
modules, making it impossible to validate completeness or rotate safely.

## Decision

All configuration lives in `config/settings.py` as a frozen `Settings` dataclass,
loaded once via `Settings.from_env()` and exported as the `settings` singleton.
**No other module reads `os.environ` directly.** Missing required secrets raise a
`ValueError` listing exactly what's absent. New settings must be added in both
`settings.py` and `.env.example`.

## Consequences

- ✅ Fail-fast on misconfiguration; one place to audit secrets.
- ✅ Immutable config — no spooky action at a distance.
- ⚠️ Every new env var touches two files by design (no shortcuts).

## Related

- [[Infrastructure]]
- [[System Overview]]
