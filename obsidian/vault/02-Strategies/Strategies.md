---
created: 2026-05-14
updated: 2026-05-16
tags: [strategy, playbook, mOC]
---

# Strategies — Trade Playbooks

The **"how"** — step-by-step playbooks for executing each trade type.

**Logic flow:** [[Laws of Trading]] (rules) → [[Trade Entry Criteria]] (why/when) → **Strategies** (how) → [[Risk Management]] (how much, when to stop) → Execution

Every strategy must comply with the [[Laws of Trading]] and be triggered by [[Trade Entry Criteria]].
All R:R, position sizing, and take-profit parameters are defined in [[Risk Management]] — strategies do NOT define their own.

---

## Day Trading
Hold periods: minutes to hours. In-and-out same day. 30 DTE on contracts is insurance, not hold time.

| Strategy | Status | Description |
|----------|--------|-------------|
| [[ORB — Opening Range Breakout]] | Draft | 15/30-min opening range breakout on watchlist stocks |

See [[Day Trading]] for category overview.

## Swing Trading
Hold periods: days to weeks. Quick in, quick out, defined risk.

| Strategy | Status | Description |
|----------|--------|-------------|
| [[EMA Crossover]] | Draft | 9/21 EMA crossover with ADX trend filter + volume confirmation |

See [[Swing Trading]] for category overview.

## Long-Term Holding
Hold periods: months to years. Accumulate conviction stocks on weakness.

| Strategy | Status | Description |
|----------|--------|-------------|
| [[Buy the 5% Dip]] | Draft | Accumulate watchlist stocks on ≥5% pullbacks (3-tranche entry) |

See [[Long-Term Holding]] for category overview.

---

## Strategy Lifecycle

- **Draft** — Idea documented, not tested
- **Paper** — Running on Alpaca Paper Trading for ≥30 days (Law 5 — min 30 DTE applies to paper trades too)
- **Validated** — Paper results prove positive expected value, ready for live capital

## See Also

- [[Laws of Trading]] — Non-negotiable rules
- [[Trade Entry Criteria]] — What triggers a trade consideration
- [[Risk Management]] — R:R, sizing, take-profit, drawdown halts (single source of truth)
- [[Position Sizing]] — Per-strategy risk profiles
- [[Loss Limits]] — Stop-losses and tiered take-profit