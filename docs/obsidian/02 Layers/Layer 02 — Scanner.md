---
title: Layer 02 — Scanner
type: layer
layer: 2
status: planned
tags: [layer, scanner]
up: ["[[System Overview]]"]
down: ["[[Layer 03 — Alert]]"]
related: ["[[Layer 01 — Data]]"]
created: 2026-06-07
---

# Layer 02 — Scanner

**Responsibility:** Walk the symbol universe from [[Layer 01 — Data]], evaluate
strategy rules, and emit **candidate signals** (symbol, side, rationale, score).

**Status:** 🚧 Planned (`02_scanner/`)

## Inputs

- `market.watchlist` (symbol universe)
- Market bars / quotes (to be synced by L01)

## Outputs

- Candidate signals written to Postgres and/or Redis with a TTL
  (`SIGNAL_TTL_MINUTES`, `daily_signal` = 60 min).

## Hand-off

Downstream → [[Layer 03 — Alert]] consumes fresh signals and notifies Telegram.

## Open questions

- [ ] Which indicators / strategy define a "signal"?
- [ ] Signal dedup window vs. Redis TTL.

## Related

- [[Layer 01 — Data]]
- [[Layer 03 — Alert]]
