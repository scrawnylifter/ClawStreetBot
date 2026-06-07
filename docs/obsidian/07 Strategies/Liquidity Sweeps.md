---
title: Liquidity Sweeps
type: strategy
status: active
tags: [strategy, liquidity, smc]
up: ["[[System Overview]]"]
down: ["[[Liquidity Sweep Reversal Playbook]]"]
related: ["[[Layer 02 — Scanner]]", "[[Layer 06 — Exit]]"]
risk_rules: ["[[Position Sizing]]", "[[Stop Loss Rules]]", "[[Portfolio Correlation]]", "[[Max Drawdown Limits]]"]
created: 2026-06-07
---

# Liquidity Sweeps

**Thesis:** Price hunts resting liquidity above swing highs / below swing lows
(stop clusters), then reverses. We *fade the sweep* — enter after price takes out a
prior extreme and reclaims the level.

## Signal definition
- Identify a recent swing high/low with obvious resting liquidity.
- Wait for a **sweep**: price wicks beyond the level and closes back inside.
- Confirmation: a displacement candle and reclaim of the swept level.

## Where it runs
- [[Layer 02 — Scanner]] flags sweep candidates from the watchlist universe.
- Entry and management follow the [[Liquidity Sweep Reversal Playbook]].

## Risk
Bound by every rule in the `risk_rules` property: [[Position Sizing]],
[[Stop Loss Rules]], [[Portfolio Correlation]], [[Max Drawdown Limits]].

## Related
- [[Layer 06 — Exit]]
