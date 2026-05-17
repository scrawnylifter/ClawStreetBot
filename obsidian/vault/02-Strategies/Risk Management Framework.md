---
title: Risk Management Framework
source: "[[Fractal Flow - The ULTIMATE Guide to Risk Management]](https://youtu.be/qN0-ltRAcV4)"
date: 2026-05-17
tags: [risk-management, position-sizing, exit-management, fractal-flow]
status: active
applies_to: [setup_scanner, liquidity_sweep, all_strategies]
---

# Risk Management Framework

Synthesized from Fractal Flow's 80-minute Ultimate Guide. Applies to **every ClawStreetBot strategy**.

## Core Principle: Risk Conservation

**You cannot eliminate risk — only transform it.** Decreasing one type increases another.

### Three Risk Types

1. **Capital risk** — money you lose if stopped out
2. **Positional risk** — probability of being stopped out (stop proximity)
3. **Target risk** — probability of making less than optimal profit

### Trade-offs (Thomas Sowell: "No solutions, only trade-offs")

| Transformation | Capital Risk | Positional Risk | Target Risk |
|---------------|-------------|-----------------|-------------|
| Tighter stop | ↓ | ↑↑ | — |
| Scale out at TP1 | ↓↓ | — | ↑ |
| Move stop to break even | ↓↓ | ↑ | — |
| Break even + scale out | ↓↓↓ | ↑ | ↑↑ |

## Risk Transformation Techniques

### ✅ Already in ClawStreetBot

| Technique | Implementation | Where |
|-----------|---------------|-------|
| Scale out at TP1 | 50/50 TP1/TP2 split | All strategies |
| Roll to break even | Stop → entry after TP1 hit | detect_liquidity_sweep.py |

### ❌ Gaps to Implement

1. **Break-even + scale out combo** — After TP1, move stop to entry AND scale out → guaranteed profit position
2. **Exact scale-out formula** — `S/(S+P) × position_size` instead of fixed 50/50
3. **Trailing stop after free trade** — ATR-based trailing to maximize final R:R

## Position Sizing

### Current: Fixed Dollar Budget
- Setup scanner: $2,000 max premium
- Liquidity sweep: implied from option price

### Upgrade: Account-Equity-Based (% of equity)
- Adapts automatically to drawdowns
- Half-Kelly for liquidity sweep: ~5% (WR 32.4%, R:R 3:1)
- Half-Kelly for setup scanner: ~6.5% (WR ~35%, R:R 3:1)

### Streak-Based Sizing
- After 3 consecutive losses → halve position size
- After 3 consecutive wins → increase by 25%
- Rationale: Trades are NOT independent (behavior + regime shifts)

## Asymmetry Guard

| Loss | Recovery Gain Needed |
|------|---------------------|
| 10% | 11.1% |
| 25% | 33.3% |
| 50% | 100% |

**Daily loss limit**: Max 3× single trade risk per day. Halt if hit.

## What We're Already Doing Right

- **Ship analogy**: 10% max risk, 30 DTE, IV rank <40% = large ship, sustainable
- **Minefield analogy**: Skip list (RKLB/RDDT/OKLO) = avoiding known mines
- **High R:R = low WR by design**: 3:1 with ~30-35% WR (video confirms this is correct)
- **Consistency of behavior**: Automated scanners = no emotional deviation
- **Scale out**: 50/50 TP1/TP2 = risk transformation (capital → target)

## Antifragility Path

Future: Delta-neutral strategies when IV rank >80% (trade volatility, not direction).
Current: IV rank <40% filter already exploits low-vol → high-vol transitions.

---

See also: [[Liquidity — 5m Day Trading]] | [[ClawStreetBot Trading Laws]] | [[Setup Scanner]]