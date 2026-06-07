---
title: Layer 05 — Execution
type: layer
layer: 5
status: planned
tags: [layer, execution, alpaca]
up: ["[[System Overview]]"]
down: ["[[Layer 06 — Exit]]"]
related: ["[[Layer 04 — Approval]]", "[[Infrastructure]]"]
created: 2026-06-07
---

# Layer 05 — Execution

**Responsibility:** Place **approved** orders with Alpaca (paper) and persist the
broker order id + intended exit parameters.

**Status:** 🚧 Planned (`05_execution/`)

## Notes

- Uses the same Alpaca client family as [[Layer 01 — Data]].
- Paper endpoint by default (`ALPACA_BASE_URL`).
- Records order intent so [[Layer 07 — Reconcile]] can verify fills vs. intent.

## Hand-off

Downstream → [[Layer 06 — Exit]] owns the position once a fill is confirmed.

## Related

- [[Layer 04 — Approval]]
- [[Layer 06 — Exit]]
- [[Layer 07 — Reconcile]]
