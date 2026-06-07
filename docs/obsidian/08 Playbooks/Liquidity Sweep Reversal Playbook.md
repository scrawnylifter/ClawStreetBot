---
title: Liquidity Sweep Reversal Playbook
type: playbook
status: active
tags: [playbook, liquidity]
up: ["[[Liquidity Sweeps]]"]
related: ["[[Layer 04 — Approval]]", "[[Layer 05 — Execution]]", "[[Stop Loss Rules]]", "[[Position Sizing]]"]
created: 2026-06-07
---

# Liquidity Sweep Reversal Playbook

Concrete execution of the [[Liquidity Sweeps]] strategy.

## Entry checklist
- [ ] Swing high/low with resting liquidity identified
- [ ] Sweep + close back inside the level
- [ ] Displacement / reclaim confirmation
- [ ] Within session window; spread acceptable

## Trade plan
| Field | Value |
|-------|-------|
| Entry | On reclaim of the swept level |
| Stop | Beyond the sweep wick (see [[Stop Loss Rules]]) |
| Size | Per [[Position Sizing]] (risk % of equity ÷ stop distance) |
| Target | Opposing liquidity pool / prior swing |

## Lifecycle
[[Layer 03 — Alert]] → [[Layer 04 — Approval]] → [[Layer 05 — Execution]] →
[[Layer 06 — Exit]].

## Related
- [[Max Drawdown Limits]]
