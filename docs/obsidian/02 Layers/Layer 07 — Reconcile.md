---
title: Layer 07 — Reconcile
type: layer
layer: 7
status: planned
tags: [layer, reconcile]
up: ["[[System Overview]]"]
related: ["[[Layer 05 — Execution]]", "[[Layer 06 — Exit]]"]
created: 2026-06-07
---

# Layer 07 — Reconcile

**Responsibility:** Close the loop — compare what we *intended* (signals, approved
orders, exits) against what Alpaca *actually did* (fills, positions, cash), and
true up Postgres. The integrity check for the whole pipeline.

**Status:** 🚧 Planned (`07_reconcile/`)

## Checks

- Every approved order has a matching broker order + terminal state.
- Open positions in Postgres match Alpaca positions.
- Realized P&L booked for closed positions from [[Layer 06 — Exit]].
- Orphans flagged (fills with no intent, intents with no fill).

## Related

- [[Layer 05 — Execution]]
- [[Layer 06 — Exit]]
- [[System Overview]]
