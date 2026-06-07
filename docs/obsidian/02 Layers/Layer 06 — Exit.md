---
title: Layer 06 — Exit
type: layer
layer: 6
status: planned
tags: [layer, exit]
up: ["[[System Overview]]"]
down: ["[[Layer 07 — Reconcile]]"]
related: ["[[Layer 05 — Execution]]", "[[Layer 03 — Alert]]"]
created: 2026-06-07
---

# Layer 06 — Exit

**Responsibility:** Manage open positions to close — stop-losses, profit targets,
and time-based exits — and fire exit alerts via [[Layer 03 — Alert]].

**Status:** 🚧 Planned (`06_exit/`)

## Notes

- Exit notifications already wired: `format_exit_notification` /
  `send_exit_notification` in `alert_telegram`.
- Time-based exits respect Redis `SIGNAL_TTL_MINUTES`.

## Hand-off

Downstream → [[Layer 07 — Reconcile]] confirms the position is flat and books P&L.

## Related

- [[Layer 05 — Execution]]
- [[Layer 07 — Reconcile]]
