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
| ATR stop (day/intraday) | Day trades, ORB, liquidity sweep (intraday) | Entry price - (ATR × 1.5) |
| ATR stop (swing) | Swing trades, EMA crossover | Entry price - (ATR × 2.0) |
| Swept level stop | Liquidity sweep | Swept level ± (ATR × 0.05) buffer |
| Technical level | All trades | Below support, below previous day low, below key moving average |
| Fixed percentage | Long-term holds (aspirational) | 8-12% below entry (wide for conviction positions) |
| Premium stop | All options | If premium drops to 50% of entry, thesis is wrong |
| Time stop | Day trades | Flatten at 12:45 PDT — no overnight gap risk |
| Time stop | Swing trades | ⚠️ Not implemented — no stale-swing timeout in code |
| DTE stop | All options | Exit when DTE ≤ 1 (Law 5 enforcement) |

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

### As Implemented (Code-Verified)

The live system uses **binary halt thresholds** — there are no graded yellow/red tiers. If a threshold is breached, all trading halts; otherwise it doesn't.

|| Threshold | Action |
|-----------|--------|
| Daily drawdown ≥ 30% | ⛔ Halt — all trading stopped until next day |
| Weekly drawdown ≥ 40% | ⛔ Halt — all trading stopped for the rest of the week |
| Monthly drawdown ≥ 50% | ⛔ Halt — full stop, manual reset required |

**Code reference:** `04_approval/scripts/process_approved.py` lines 96-98:
```python
DRAWDOWN_DAILY_PCT   = Decimal("0.30")   # 30% daily → halt
DRAWDOWN_WEEKLY_PCT  = Decimal("0.40")   # 40% weekly → halt
DRAWDOWN_MONTHLY_PCT = Decimal("0.50")   # 50% monthly → halt
```

The thresholds are wider than typical retail (10/20/30) because options premium can swing 50%+ per contract — the old thresholds would halt on every normal losing day.

### Aspirational: Graded Tiers (Not Yet Implemented)

> ⚠️ The graded system below is **not implemented in code**. It exists as a design goal for when the account grows large enough that 30%-daily halts feel too loose. Currently the code only does binary halt/don't-halt.

**Proposed (future):**

|| Level | Drawdown | Action ||
||-------|----------|--------|
|| 🟢 Green | < 5% | Normal trading ||
|| 🟡 Yellow | 5% - 8% | Reduce position sizes by 50% ||
|| 🔴 Red | 8% - 10% | No new positions, manage existing only ||
|| ⛔ Halt | > 10% | All trading stopped until next day ||

(And similar graded tiers for weekly/monthly.)

---

## Take-Profit Rules (Law 4: Realize Gains)

[[Laws of Trading#Law 4: Realize Gains|Law 4]] says never be afraid to take profits. Implementation:

### Tiered Exit Strategy — Day Trading (As Implemented)

Code uses **ATR multipliers**, not percentage targets.

| Tier | Target | Action | Position Portion |
|------|--------|--------|-------------------|
| Stop | ATR × 1.5 from entry | Hard stop — exit immediately | 100% |
| TP1 | ATR × 4.5 (3:1 R:R) | Sell **50%** of position (qty//2) | 50% |
| TP2 | ATR × 7.5 (5:1 R:R) | **Full close** (day mode flattens, no trailing) | Remaining 50% |
| Time stop | 12:45 PDT | Flatten everything | 100% |

**Code reference:**
- Scanner ATR multipliers: `02_scanner/scripts/detect_orb.py` RULES `stop_mult=1.5, tp1_mult=4.5, tp2_mult=7.5`
- Scanner (setup/EMA): `02_scanner/scripts/scan_setups.py` `ATR_STOP_MULT=2.0, ATR_TP1_MULT=6.0, ATR_TP2_MULT=10.0`
- TP1 exits 50%: `06_exit/scripts/exit_monitor.py` line 681: `partial_qty = qty_remaining // 2`
- Day time stop: `06_exit/scripts/exit_monitor.py` line 81: `TIME_STOP_LOCAL = time(12, 45)` (America/Los_Angeles)

### Tiered Exit Strategy — Swing Trading (As Implemented)

| Tier | Target | Action | Position Portion |
|------|--------|--------|-------------------|
| Stop | ATR × 2.0 from entry | Hard stop — exit immediately | 100% |
| TP1 | ATR × 6.0 (3:1 R:R) | Sell **50%** of position (qty//2) | 50% |
| TP2 | ATR × 10.0 (5:1 R:R) | **Activate trailing stop** at 2× ATR | Remaining 50% trails |
| Trailing stop | 2× ATR from price | Trail moves with price; close on break | Remainder |

**Code reference:**
- Swing TP2 → trail-activate: `06_exit/scripts/exit_monitor.py` line 300-310
- Trail distance = 2× ATR(14): `06_exit/scripts/exit_monitor.py` lines 183-202

### Tiered Exit Strategy — Liquidity Sweep (As Implemented)

| Tier | Target | Action | Position Portion |
|------|--------|--------|-------------------|
| Stop | Swept level ± ATR × 0.05 (buffer) | Hard stop just beyond swept level | 100% |
| TP1 | Risk × 3.0 (R:R 3:1) | Sell 50% (tp1_size=0.50) | 50% |
| TP2 | Risk × 5.0 (R:R 5:1) | Activate trail (swing) or full close (day) | Remaining 50% |

**Code reference:** `02_scanner/scripts/detect_liquidity_sweep.py` lines 49-56 and line 261.

### Tiered Exit Strategy — Long-Term Holding (Aspirational Only)

> ⚠️ **Not implemented in code.** `infer_trade_mode()` never returns `long_term` — no scanner produces long-term signals. The parameters below are design targets only.

| Tier | Target | Action | Position Portion |
|------|--------|--------|-------------------|
| Stop | Thesis invalidation (not a price level) | Manual exit | 100% |
| TP1 | +50% | Sell 1/3 of position | Aspirational |
| TP2 | +100% | Sell 1/3 of position | Aspirational |
| TP3 | Let it ride | Trail stop on remaining | Aspirational |

### Options-Specific Exits

Options decay differently — take-profit targets adjust per strategy (see "As Implemented" tables above). The one universal options exit is the **premium stop**:

| Condition | Action |
|-----------|--------|
| Option premium drops to 50% of entry price | **Full close** — thesis is wrong |

This is hardcoded at 50% for all modes (day/swing/long-term). No differentiation by strategy.

**Code reference:** `06_exit/scripts/exit_monitor.py` line 84: `OPTION_PREMIUM_STOP_FRACTION = Decimal("0.50")`

> ⚠️ Options theta accelerates in the last 30 days. Per [[Laws of Trading#Law 5: No Short-Dated Options|Law 5]], we don't hold options under 30 DTE, but time decay still accelerates as we approach expiry. Code enforces exit at DTE ≤ 1 (`MIN_DTE_HOLDABLE = 1`).

---

## Circuit Breaker Implementation

### Real-Time Drawdown Tracking

```
Every 5 minutes:
┌─────────────────────────────────────────────────────────────────┐
│  Fetch current portfolio value from Alpaca                       │
│  Calculate: P&L = current - start_of_day                         │
│  Drawdown % = P&L / start_of_day equity                         │
├─────────────────────────────────────────────────────────────────┤
│  if daily_drawdown ≥ 30%:                                       │
│    → HALT: cancel pending orders, no new trades                  │
│    → Alert user via Telegram                                     │
│    → Log halt event to Postgres                                   │
│  elif daily_drawdown ≥ 40% weekly:                               │
│    → HALT: no new trades for remainder of week                   │
│  elif daily_drawdown ≥ 50% monthly:                              │
│    → HALT: full system stop, manual reset required               │
│  else:                                                            │
│    → Normal trading                                               │
└─────────────────────────────────────────────────────────────────┘
```

> ⚠️ Code implements **binary halt only** — there are no graded caution/reduce tiers. See "As Implemented" drawdown section above for details.

### Cool-Down Periods

After a circuit breaker triggers:

| Trigger | Cool-Down | Requirements to Resume |
|---------|-----------|------------------------|
| Daily 30% | Next trading day | Bot auto-resumes next session |
| Weekly 40% | Next trading week | Manual review recommended |
| Monthly 50% | Until manual reset | Must document analysis before re-enabling |

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

| Parameter | As Implemented | Notes |
|-----------|----------------|-------|
| Day stop method | ATR × 1.5 | `detect_orb.py` RULES, liquidity sweep intraday |
| Swing stop method | ATR × 2.0 | `scan_setups.py` ATR_STOP_MULT |
| Liquidity sweep stop | Swept level ± ATR × 0.05 | `detect_liquidity_sweep.py` line 261 |
| Long-term hold stop | 8-12% fixed (aspirational) | No scanner produces long_term mode |
| Premium stop (options) | 50% of entry price, all modes | `exit_monitor.py` OPTION_PREMIUM_STOP_FRACTION |
| Day time stop | 12:45 PDT (America/Los_Angeles) | `exit_monitor.py` TIME_STOP_LOCAL |
| Swing time stop | ⚠️ Not implemented | No stale-swing timeout in code |
| DTE entry minimum | 30 DTE | `process_approved.py` MIN_DTE |
| DTE exit threshold | DTE ≤ 1 | `exit_monitor.py` MIN_DTE_HOLDABLE |
| Daily drawdown halt | 30% | Binary halt, no graded tiers |
| Weekly drawdown halt | 40% | Binary halt, no graded tiers |
| Monthly drawdown halt | 50% | Binary halt, no graded tiers |
| TP1 day | ATR × 4.5 (3:1 R:R) | Sell 50% (qty//2), NOT 1/3 |
| TP2 day | ATR × 7.5 (5:1 R:R) | Full close (day mode — no trailing) |
| TP1 swing | ATR × 6.0 (3:1 R:R) | Sell 50% (qty//2) |
| TP2 swing | ATR × 10.0 (5:1 R:R) | Activate trail at 2× ATR (NOT full close) |
| TP1 liquidity sweep | Risk × 3.0 R:R | Sell 50% |
| TP2 liquidity sweep | Risk × 5.0 R:R | Trail (swing) or full close (day) |
| TP1 (options) | Per strategy ATR target | Sell 50% |
| Delta band (scanner) | 0.50–0.70 all strategies | `scan_setups.py` DELTA_MIN/DELTA_MAX |
| Delta band (post-approval) | standard 0.50–0.70, conservative 0.55–0.65, aggressive 0.40–0.80 | `fetch_alpaca_snapshot.py` DELTA_BANDS |

---

## See Also

- [[Risk Management]] — Overview of all risk pillars
- [[Position Sizing]] — How position size is calculated
- [[Correlation Risk]] — Why individual stops aren't enough
- [[Laws of Trading]] — Law 2 (too deep), Law 4 (realize gains), Law 5 (no 0DTE)
- [[Alpaca API]] — Bracket orders, trailing stops