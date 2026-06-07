---
title: Position Sizing
type: risk-rule
tags: [risk, sizing]
up: ["[[Home]]"]
related: ["[[Liquidity Sweeps]]", "[[Layer 05 — Execution]]"]
created: 2026-06-07
---

# Position Sizing

**Rule:** Risk a fixed fraction of account equity per trade.
`size = (equity × risk%) ÷ stop_distance`.

- Default risk per trade: **1%** of equity.
- Hard cap: never risk more than **2%** on a single idea.
- Size is *derived from the stop*, not chosen first — see [[Stop Loss Rules]].
- Enforced at [[Layer 05 — Execution]] before order submission.

## Related
- [[Portfolio Correlation]]
- [[Max Drawdown Limits]]
