---
created: 2026-05-15
updated: 2026-05-15
tags: [risk, stop-loss, drawdown, circuit-breaker, mOC]
---

# Loss Limits — When to Exit and When to Stop Trading

Loss limits are the safety net. They answer two questions:
1. **When do I exit this specific trade?** (stop-loss)
2. **When do I stop trading entirely?** (circuit breaker)

A trade without a stop-loss is gambling. A day without a loss limit is reckless.

---

## The Math of Drawdowns

Why loss limits matter — recovering from losses is exponentially harder than taking them:

| Drawdown | Recovery Gain Needed | Recovery Difficulty |
|----------|---------------------|---------------------|
| 5% | 5.3% | Easy |
| 10% | 11.1% | Manageable |
| 20% | 25.0% | Hard |
| 30% | 42.9% | Very hard |
| 50% | 100% | Devastating |

A 50% loss requires a **100% gain** just to break even. This is why [[Laws of Trading#Law 2: If It's Bothering You, You're Too Deep|Law 2]] exists — if you're emotionally affected by a position, you've already risked too much.

---

## Stop-Loss Framework

### Rule 1: Every Trade Has a Stop (No Exceptions)

Before entering any position, the stop-loss price must be defined. No stop = no trade.

**Where stops come from:**

| Method | Best For | How |
|--------|----------|-----|
| ATR stop | Swing trades | Entry price - (ATR × multiplier) |
| Technical level | All trades | Below support, below previous day low, below key moving average |
| Fixed percentage | Long-term holds | 8-12% below entry (wide for conviction positions) |
| Premium stop | Long options | If premium drops 40-50%, the thesis is wrong |
| Time stop | All trades | If trade hasn't moved in expected direction within X days, exit |

### Rule 2: Stop Types and When to Use Them

**Hard Stop (Alpaca stop order):**
- Placed at entry as part of a bracket order
- Executes automatically, no emotion involved
- Used on every trade as the baseline protection

```
Bracket Order:
  Entry:   LIMIT buy at $50.00
  TP:      LIMIT sell at $65.00 (30% gain - Law 4)
  SL:      STOP sell at $47.00 (6% risk)
```

**Mental Stop (for long-term holds only):**
- Not placed as an order — monitored manually
- Only for positions held 3+ months where temporary swings are expected
- Must be written down BEFORE entry (in trade journal)
- Converted to hard stop if price approaches within 2% of mental level

**Trailing Stop:**
- Moves up with price, never moves down
- Locks in gains as trade moves in your favor
- Alpaca supports trailing stop orders (trail_price or trail_percent)

| Trail Type | Setting | Best For |
|-----------|---------|----------|
| Fixed dollar | $2-5 below swing low | Choppy stocks |
| Percentage | 5-8% trail | Trending stocks |
| ATR-based | 2× ATR trail | Volatile stocks |

### Rule 3: Stop-Loss Placement Pitfalls

- **Too tight** → stopped out on noise, then price recovers without you
- **Too wide** → risking too much capital on a single trade
- **At obvious levels** → round numbers, previous day H/L get hit by everyone
- **Moving stops** → the cardinal sin — never widen a stop because "it'll come back"
- **Averaging down** → this is NOT the same as "buying the dip" (see [[Trade Entry Criteria]]). Averaging down into a losing position doubles your risk without a new thesis.

---

## Drawdown Limits

Drawdown limits are **portfolio-wide** circuit breakers. They stop ALL new trades, not just one.

### Daily Drawdown Limits

| Level | Drawdown | Action |
|-------|----------|--------|
| 🟢 Green | < 5% | Normal trading |
| 🟡 Yellow | 5% - 8% | Reduce position sizes by 50% |
| 🔴 Red | 8% - 10% | No new positions, manage existing only |
| ⛔ Halt | > 10% | All trading stopped until next day |

### Weekly Drawdown Limits

| Level | Drawdown | Action |
|-------|----------|--------|
| 🟢 Green | < 8% | Normal trading |
| 🟡 Yellow | 8% - 15% | Reduce all position sizes by 50% |
| 🔴 Red | 15% - 20% | No new positions, existing stops tightened |
| ⛔ Halt | > 20% | All trading stopped for the rest of the week |

### Monthly Drawdown Limits

| Level | Drawdown | Action |
|-------|----------|--------|
| 🟢 Green | < 15% | Normal trading |
| 🟡 Yellow | 15% - 20% | Position sizes halved, no new strategies |
| 🔴 Red | 20% - 25% | No new positions, liquidate worst performers |
| ⛔ Halt | > 30% | Full stop. Review and reset. |

> ⚠️ These are **initial targets** — subject to backtesting and adjustment. Paper trading in Phase 2 will validate or revise these numbers.

---

## Take-Profit Rules (Law 4: Realize Gains)

[[Laws of Trading#Law 4: Realize Gains|Law 4]] says never be afraid to take profits. Implementation:

### Tiered Exit Strategy — Day Trading

| Tier | Target | Action | Position Portion |
|------|--------|--------|-----------------|
| TP1 | +20% | Sell 1/3 of position | Quick scalp — lock in fast |
| TP2 | +40% | Sell 1/3 of position | Strong move — realize profit |
| TP3 | Let it ride | Trail stop on remaining | Let winners run, flatten before close |

> Day trades move fast — tighter TP targets than swing. If you're up 40% in 30 minutes on an ORB, take the money.

### Tiered Exit Strategy — Swing Trading

| Tier | Target | Action | Position Portion |
|------|--------|--------|-----------------|
| TP1 | +30% | Sell 1/3 of position | Lock in solid gain |
| TP2 | +50% | Sell 1/3 of position | Realize major profit |
| TP3 | Let it ride | Trail stop on remaining | Let winners run to 100%+ |

### Tiered Exit Strategy — Long-Term Holding

| Tier | Target | Action | Position Portion |
|------|--------|--------|-----------------|
| TP1 | +50% | Sell 1/3 of position | Lock in major gain |
| TP2 | +100% | Sell 1/3 of position | Doubled your money |
| TP3 | Let it ride | Trail stop on remaining | Ride to 200%+ |

**Why different structures:**
- **Swing trades** are quick — take profit at 30%/50% where you're comfortable
- **Long-term holds** need bigger targets to justify the time risk — 50%/100% minimum (but R:R is measured differently; see [[Risk Management#Long-Term Holding]] and [[Position Sizing#Long-Term Holding]])

### Options-Specific Exits

Options decay differently — take-profit targets adjust:

| Gain | Action |
|------|--------|
| +50% | Sell half (recover premium + profit) |
| +100% | Sell another quarter |
| Remaining | Trail with 25% premium stop or hold to target date |

> ⚠️ Options theta accelerates in the last 30 days. Per [[Laws of Trading#Law 5: No Short-Dated Options|Law 5]], we don't hold options under 30 DTE, but time decay still accelerates as we approach expiry.

---

## Circuit Breaker Implementation

### Real-Time Drawdown Tracking

```
Every 5 minutes:
┌─────────────────────────────────────────┐
│  Fetch current portfolio value from Alpaca│
│  Calculate: P&L = current - start_of_day │
│  Drawdown % = P&L / start_of_day equity  │
├─────────────────────────────────────────┤
│  if daily_drawdown > 10%:                 │
│    → HALT: cancel pending orders          │
│    → Alert user via Telegram             │
│    → Log halt event to Postgres          │
│  elif daily_drawdown > 8%:             │
│    → REDUCE: no new positions             │
│  elif daily_drawdown > 5%:             │
│    → CAUTION: reduce sizes by 50%        │
└─────────────────────────────────────────┘
```

### Cool-Down Periods

After a circuit breaker triggers:

| Trigger | Cool-Down | Requirements to Resume |
|---------|-----------|------------------------|
| Daily 10% | Next trading day | Review losing trades, verify system |
| Weekly 20% | Next trading week | Full journal review, strategy check |
| Monthly 30% | Until manual reset | Must document analysis before re-enabling |

---

## Loss Journal

Every stopped-out trade gets logged:

```
Trade Loss Record:
  Symbol: ____
  Entry date: ____
  Exit date: ____
  Entry price: ____  Exit price: ____
  P&L: ____ ($ / %)
  Stop type: hard / trailing / mental / time
  Stop level: ____
  Did stop fire correctly? Y / N
  Emotion: calm / frustrated / tilted / relieved
  Lesson: ____
```

This feeds back into [[Laws of Trading#Law 6: Know the Difference Between Luck and Skill|Law 6]] — are losses following a pattern (skill issue) or are they random variance?

---

## Sizing Parameters Summary

| Parameter | Value | Notes |
|-----------|-------|-------|
| Default stop method | ATR × 2.0 (swing) / 8-12% fixed (long-term) | Strategy-dependent |
| Long-term hold stop | 8-12% fixed | Only for conviction positions |
| Premium stop (options) | 40-50% premium loss | Thesis invalidation |
| Daily drawdown halt | 10% | Hard stop, next day |
| Weekly drawdown halt | 20% | Hard stop, next week |
| Monthly drawdown halt | 30% | Full system review required |
| TP1 day | +20% | Sell 1/3 |
| TP2 day | +40% | Sell 1/3 |
| TP3 day | Let it ride | Flatten before close |
| TP1 swing | +30% | Sell 1/3 |
| TP2 swing | +50% | Sell 1/3 |
| TP3 swing | Let it ride | Trail stop remaining |
| TP1 long-term | +50% | Sell 1/3 |
| TP2 long-term | +100% | Sell 1/3 |
| TP3 long-term | Let it ride | Trail stop remaining |
| TP1 (options) | +50% | Sell half |

---

## See Also

- [[Risk Management]] — Overview of all risk pillars
- [[Position Sizing]] — How position size is calculated
- [[Correlation Risk]] — Why individual stops aren't enough
- [[Laws of Trading]] — Law 2 (too deep), Law 4 (realize gains), Law 5 (no 0DTE)
- [[Alpaca API]] — Bracket orders, trailing stops