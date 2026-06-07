---
title: Max Drawdown Limits
type: risk-rule
tags: [risk, drawdown, kill-switch]
up: ["[[Home]]"]
related: ["[[Stop Loss Rules]]", "[[Layer 07 — Reconcile]]"]
created: 2026-06-07
---

# Max Drawdown Limits

**Rule:** Hard equity-drawdown thresholds that halt new entries (kill switch).

- Daily loss limit: **−3%** of equity → stop opening new positions for the day.
- Peak-to-trough limit: **−10%** → halt the strategy, require manual review.
- Drawdown is tracked from [[Layer 07 — Reconcile]] booked P&L.
- Halting is fail-safe: when in doubt, don't trade.

## Related
- [[Position Sizing]]
