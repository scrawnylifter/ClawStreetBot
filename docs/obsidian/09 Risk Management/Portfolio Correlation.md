---
title: Portfolio Correlation
type: risk-rule
tags: [risk, correlation]
up: ["[[Home]]"]
related: ["[[Position Sizing]]", "[[Layer 02 — Scanner]]"]
created: 2026-06-07
---

# Portfolio Correlation

**Rule:** Cap aggregate exposure to correlated names so several positions don't
collapse into one bet.

- Treat highly correlated symbols (ρ > 0.7) as a single risk unit.
- Combined risk across a correlated cluster ≤ the single-idea cap (2%).
- [[Layer 02 — Scanner]] should down-weight signals that duplicate open exposure.

## Related
- [[Max Drawdown Limits]]
