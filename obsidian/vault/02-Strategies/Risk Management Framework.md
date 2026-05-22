---
title: Risk Management Framework
source: "[[Fractal Flow - The ULTIMATE Guide to Risk Management]](https://youtu.be/qN0-ltRAcV4)"
date: 2026-05-17
updated: 2026-05-22
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
| Scale out at TP1 | 50/50 TP1/TP2 split (qty//2) | All strategies — `exit_monitor.py` line 681 |
| Roll to break even | Stop → entry after TP1 hit | `exit_monitor.py` (trail logic) |
| ATR-based stops | Day: ATR×1.5, Swing: ATR×2.0, Sweep: level±ATR×0.05 | Scanners + `exit_monitor.py` |
| Trailing stop after TP2 | Trail at 2× ATR (swing mode only) | `exit_monitor.py` lines 183-202, 300-310 |
| Premium stop | 50% of entry → full close | `exit_monitor.py` OPTION_PREMIUM_STOP_FRACTION |
| Drawdown halt | 30%/40%/50% daily/weekly/monthly → binary halt | `process_approved.py` lines 96-98 |
| Time stop | 12:45 PDT (day mode only) | `exit_monitor.py` TIME_STOP_LOCAL |
| DTE exit | DTE ≤ 1 → close | `exit_monitor.py` MIN_DTE_HOLDABLE |

### ❌ Gaps to Implement

1. **Break-even + scale out combo** — After TP1, move stop to entry AND scale out → guaranteed profit position
2. **Exact scale-out formula** — `S/(S+P) × position_size` instead of fixed 50/50
3. **Stale-swing timeout** — No time stop for swing positions currently implemented
4. **Graded drawdown tiers** — Code only has binary halt, no yellow/red caution zones

## Position Sizing

### As Implemented (Code-Verified)
- **Setup scanner (swing):** Risk % = 10%, stop = ATR×2.0, TP1 = ATR×6.0, TP2 = ATR×10.0
- **ORB / intraday (day):** Risk % = 5%, stop = ATR×1.5, TP1 = ATR×4.5, TP2 = ATR×7.5
- **Liquidity sweep:** Stop = swept level ± ATR×0.05, TP1 = R:R×3.0, TP2 = R:R×5.0
- **Option premium stop:** 50% of entry (all modes, no differentiation)
- **Delta bands:** Scanner 0.50-0.70; post-approval standard 0.50-0.70, conservative 0.55-0.65, aggressive 0.40-0.80
- **TP1 exits 50%** (qty//2), NOT 1/3
- **Day TP2:** Full close (flatten). **Swing TP2:** Trail-activate at 2× ATR

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

**Code-verified drawdown halts:** Daily 30%, Weekly 40%, Monthly 50% — binary, no graded tiers.

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