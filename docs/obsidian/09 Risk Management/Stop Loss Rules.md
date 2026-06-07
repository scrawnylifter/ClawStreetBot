---
title: Stop Loss Rules
type: risk-rule
tags: [risk, stops]
up: ["[[Home]]"]
related: ["[[Liquidity Sweeps]]", "[[Layer 06 — Exit]]"]
created: 2026-06-07
---

# Stop Loss Rules

**Rule:** Every position has a hard stop set *at entry*. No discretionary widening.

- Structural stop: just beyond the invalidation level (e.g., the sweep wick).
- The stop distance caps position size via [[Position Sizing]].
- Time stop: exit if the thesis hasn't resolved within the signal TTL.
- Stops are managed by [[Layer 06 — Exit]].

## Related
- [[Max Drawdown Limits]]
