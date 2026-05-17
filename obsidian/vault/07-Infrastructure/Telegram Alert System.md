---
created: 2026-05-17
updated: 2026-05-17
tags: [alerts, telegram, strategy, execution, implementation-plan, mOC]
---

# Trade Alert System — Implementation Plan (v2)

> **Replaces**: Telegram Alert System v1 (generic score alerts → strategy-specific trade setups)

**Goal:** Every alert is a complete trade decision — entry *and* exit rules, with full context. You approve or reject. Approved trades execute on Alpaca Paper with stops and targets already placed. Exit alerts fire when the trade needs action (TP hit, stop approaching, invalidation triggered, time stop).

**Architecture:** Strategy detectors find setups → Telegram sends entry alert with full trade plan → you approve → Alpaca executes with bracket order. *Then* the system monitors open positions and sends exit alerts when action is needed (TP levels hit, stops threatened, invalidation conditions, greeks deterioration, time stops). You decide to act or let the system manage.

**Tech Stack:** Python 3.11, psycopg2 (data), alpaca-py (execution), python-telegram-bot (delivery), n8n (scheduling)

---

## Why This Redesign

The v1 plan sent generic "OKLO scored 63.5" alerts. But your composite rarely breaks 60 (0.5% of signals), and a score doesn't tell you *which strategy* applies or *how to trade it*. You want:

> "WDC hit EMA crossover — here's the entry, stop, target, and the best option contract. Y/N?"

That's a trade decision, not a notification. And just as important:

> "WDC TP1 hit at $94.25 (+30%) — sell 1/3 or let it ride?"

Every entry needs an exit plan. The system monitors and alerts you when exits need attention.

---

## Alert Types

### Entry Alerts (Strategy Triggers)
These fire when a setup meets all criteria. You approve or reject.

### Exit Alerts (Position Monitoring)
These fire when an open position needs action. No approval needed for hard stops (they execute automatically), but TP levels and invalidations need your decision.

---

## Strategy 1: EMA Crossover (Swing)

### Entry Alert

**Trigger:** 9 EMA crosses above/below 21 EMA, confirmed by ADX > 25 and volume >= 1.5x average

**Data we have:** ✅ EMA-9, EMA-21 in `market.technical_indicators`; ✅ ADX in `market.trend_status`; ✅ RSI, MACD, ATR, volume in `technical_indicators`

```
📈 EMA CROSSOVER — WDC

Signal: Bullish 9/21 cross confirmed
Price: $72.50 | ATR(14): $3.20
Stop: $66.10 (ATR × 2.0 below)
TP1: $94.25 (+30%) | TP2: $108.75 (+50%) | Trail after TP2
R:R: 3.2:1 ✅
ADX: 31 (trending ✅) | RSI: 52 (room to run)
Trend: micro↑ inter↑ primary↑ (all aligned ✅)
IV Rank: 28% (buy zone ✅)
Regime: transition

Best Option (DTE≥30, Delta 0.50-0.70):
WDC 08/21 C$72.50 — Delta 0.62, Theta 2.1%/day
Entry: ~$4.20 | Risk: 10% ($100 on $1K)

Invalidation conditions:
  • Cross reverses within 2 candles → EXIT
  • ADX drops below 20 → EXIT
  • Volume dries up after entry → caution

/approve WDC_EMA_0517
/reject
```

### Exit Alerts (monitoring open EMA crossover position)

**TP1 Hit — sell 1/3:**
```
🎯 WDC EMA — TP1 HIT

WDC hit +30% at $94.25
Action: Sell 1/3 of position (lock in gains)
Stop moves to breakeven ($72.50)

Open remainder: let it ride to TP2 ($108.75, +50%)
Or: /close_all to exit full position
```

**TP2 Hit — sell 1/3, trail rest:**
```
🎯 WDC EMA — TP2 HIT

WDC hit +50% at $108.75
Action: Sell 1/3 of position (realize major profit)
Trail stop: ATR × 2.0 trailing (currently ~$104)

Or: /close_all to exit full position
```

**Stop threatened — hard exit:**
```
🛑 WDC EMA — STOP THREATENED

WDC at $66.50, stop at $66.10
Only $0.40 from stop (0.6% away)
No action needed — bracket order will execute automatically

Monitoring closely
```

**Invalidation triggered:**
```
⚠️ WDC EMA — INVALIDATION

Crossover reversed within 2 candles (9 EMA crossed back below 21 EMA)
Reason: False signal — ADX dropped to 18 (trend dying)

Recommendation: /close position immediately
Or: /override to hold (not recommended per strategy rules)
```

**Greeks deterioration (if options position):**
```
⚠️ WDC EMA — GREEKS DETERIORATION

Position: WDC 08/21 C$72.50 (Delta 0.62)
Delta shifted: 0.62 → 0.42 (now too far OTM)
Theta budget exceeded: 4.2%/day (limit: 3% for swing)
IV Rank crossed 75% — premium is now expensive

Recommendation: Close or roll to further expiration
/roll WDC_0918 C$72.50 — rolls to September
/close — close position
```

**Earnings approaching (Law 5):**
```
📅 WDC EMA — EARNINGS WARNING

WDC earnings in 4 business days
Law 5: No holding options through earnings
Position: WDC 08/21 C$72.50

Action: Close before earnings date
/close_now — exit immediately
/close_pre_earnings — set auto-close 1 day before earnings
```

---

## Strategy 2: Opening Range Breakout (Day)

### Entry Alert

**Trigger:** Price breaks above/below opening range (first 15 or 30 min), confirmed by volume >= 2x average

**Data we have:** ✅ 5m bars (back to April 16), ✅ VWAP, ✅ ATR, ✅ volume

```
🚀 ORB BREAKOUT — RKLB

Signal: Bullish breakout above 30-min opening range
Opening range: $22.10 - $22.45 → broke above $22.50
Price: $22.65 | ATR: $0.85
Stop: $21.35 (opening range low + ATR×1.5)
TP1: $27.18 (+20%) | TP2: $31.71 (+40%)
Flatten: Before market close (time stop)
R:R: 3.1:1 ✅
Volume: 2.4x average ✅ | VWAP: rising ✅
PDT: 0/3 used today ✅
IV Rank: 34% (normal zone)

Best Option (DTE≥30, Delta 0.55-0.65):
RKLB 06/26 C$22.50 — Delta 0.58, Theta 3.8%/day
Entry: ~$1.85 | Risk: 5% ($50 on $1K)

Invalidation conditions:
  • Price reverses back inside opening range → EXIT immediately
  • Volume dies after breakout → EXIT
  • Time stop: flatten before market close → NO EXCEPTIONS

/approve RKLB_ORB_0517
/reject
```

### Exit Alerts (day trade — time-critical)

**TP1 Hit — sell 1/3:**
```
🎯 RKLB ORB — TP1 HIT

RKLB hit +20% at $27.18
Action: Sell 1/3 of position (quick scalp)

Remainder targets: TP2 $31.71 (+40%), flatten before close
Time remaining: 2h 15m until market close
/close_partial 1/3
```

**TP2 Hit — sell 1/3, flatten rest before close:**
```
🎯 RKLB ORB — TP2 HIT

RKLB hit +40% at $31.71
Action: Sell 1/3 of position (strong move realized)

⚠️ Day trade: flatten remaining 1/3 before close
/close_rest — exit remaining position now
Or flatten at 15:45 ET automatically
```

**False breakout — invalidation:**
```
🛑 RKLB ORB — FALSE BREAKOUT

RKLB reversed back inside opening range ($22.10 - $22.45)
Current: $22.30 (back inside range)
Reason: Volume dropped to 0.8x average after breakout

Stop at $21.35 should trigger automatically
NO manual override — this is a failed setup per strategy rules
```

**Time stop warning (30 min before close):**
```
⏰ RKLB ORB — TIME STOP

RKLB is still open. 30 minutes until market close.
Current P&L: +12% (above entry, no TP hit yet)
Day trade rule: flatten all positions before close.

/auto_flatten — close at market now
/hold_until_close — close at 15:55 ET automatically
```

**PDT warning:**
```
⚠️ PDT WARNING

You've used 2 of 3 day trades this week.
If you approve RKLB ORB, that's trade #3 (emergency only).

Recommendation: /reject — save the last trade for an emergency
/override — approve anyway (uses final day trade slot)
```

---

## Strategy 3: Buy the 5% Dip (Long-Term)

### Entry Alert — Tranche 1

**Trigger:** Watchlist stock pulls back >= 5% from local high, thesis still intact

**Data we have:** ✅ Daily OHLCV (2 years), ✅ fundamentals (revenue/EPS)

```
💰 DIP ALERT — RKLB

Signal: 5%+ pullback from local high
Local high: $28.40 (May 10) → Current: $26.85 (-5.5%)
Thesis: Space launch growth, Neutron rocket development ✅
No earnings in 5 days ✅
Fundamentals: Revenue growing, no material weakness ✅

Tranche 1 of 3 (buy now):
  Shares: ~5% of equity at $26.85
  Next scale-in: ~$25.50 (another 5% dip) — Tranche 2
  Confirmation rebound: price starts recovering — Tranche 3
Stop: THESIS-BASED (not price-based)
TP1: +50% → TP2: +100% → let it ride

Buy shares (not options — long-term accumulation)

/approve RKLB_DIP_0517_1
/reject
```

### Entry Alert — Tranche 2 (deeper dip)

```
💰 RKLB DIP — TRANCHE 2 OPPORTUNITY

RKLB has dipped further from $26.85 → $25.50 (-10.3% from local high)
Original thesis still intact ✅

Tranche 2 of 3 (scale in deeper):
  Shares: ~5% of equity at ~$25.50
  Average cost after 2 tranches: ~$26.18
  Tranche 3: confirmed rebound / reversal candle

/approve RKLB_DIP_0517_2
/reject (wait for rebound confirmation)
```

### Entry Alert — Tranche 3 (rebound confirmation)

```
💰 RKLB DIP — TRANCHE 3 (REBOUND CONFIRMED)

RKLB showing recovery: $26.20 (+2.7% from Tranche 2 entry)
Reversal pattern: higher low, volume picking up
Average cost: ~$26.18 across 2 tranches

Tranche 3 of 3 (final scale-in):
  Shares: ~5% of equity at $26.20
  Total position: ~15% of equity (avg cost ~$26.18)
  Now fully built — shift to hold mode

TP1: $39.27 (+50%) | TP2: $52.36 (+100%) | Trail after TP2

/approve RKLB_DIP_0517_3
/reject (position large enough)
```

### Exit Alerts (long-term — thesis-based, not price-based)

**TP1 Hit — sell 1/3:**
```
🎯 RKLB DIP — TP1 HIT

RKLB hit +50% at $39.27
Average cost: $26.18 | Current: $39.27

Action: Sell 1/3 of position (lock in major gain)
Remaining position: let it ride to TP2 ($52.36, +100%)

/sell_1_3 — sell 1/3 now
/hold_all — hold everything (thesis still strong)
```

**TP2 Hit — sell 1/3, trail rest:**
```
🎯 RKLB DIP — TP2 HIT

RKLB doubled at $52.36! 🎉
Average cost: $26.18 | Current: $52.36

Action: Sell 1/3 (doubled your money), trail the rest
Trail stop: ATR-based trailing, move up only

/sell_1_3 — take half the remaining profit
/close_all — exit full position
```

**Thesis invalidation — the ONLY sell signal for LT holds:**
```
🚨 RKLB DIP — THESIS INVALIDATION

RKLB thesis may be broken:
  • Revenue declined 15% QoQ (was growing)
  • Sector rotation: space stocks losing institutional support
  • Major contract loss reported

This is NOT a price stop — it's a thesis stop.
RKLB current: $21.50 (-17.8% from avg cost)

/review_thesis — show full thesis vs current data
/sell_all — exit full position (thesis broken)
/hold — thesis still intact, continue holding
```

**Drawdown warning (not a sell signal, just awareness):**
```
⚠️ RKLB DIP — DRAWDOWN NOTICE

RKLB position is -18% from average cost
Within long-term tolerance (30-40% drawdown acceptable)
Thesis status: still intact ✅

No action needed — this is normal volatility for conviction holds.
Reminding you: long-term holds use thesis-based stops, not price stops.

If thesis breaks: /sell_all
If you're worried: /reduce_1_3 (sell 1/3 to reduce stress — Law 2)
```

---

## Risk Alerts (Always On, No Approval Needed)

These are NOT trade decisions — they're safety system alerts:

| Alert | Trigger | Always Sent? |
|-------|---------|-------------|
| Drawdown halt | Daily DD > 10%, weekly > 20%, monthly > 30% | Yes |
| PDT warning | 2nd day trade used | Yes |
| PDT lock | 3rd day trade used | Yes |
| Position breach | Any position > 20% equity | Yes |
| Pipeline failure | n8n workflow error | Yes |
| Data stale | No OHLCV update for 26h+ | Yes |

---

## Exit Alert Types Summary

Every open position has exit conditions defined at entry time. The system monitors and sends alerts when these conditions are approached or triggered:

| Exit Type | Trigger | Response | Auto-Execute? |
|-----------|---------|----------|--------------|
| **Hard stop** | Price hits ATR-based stop level | Bracket order executes automatically | Yes — no alert needed, just confirmation |
| **TP1** | Price hits +30%/+20%/+50% (strategy-dependent) | Sell 1/3, move stop to breakeven | Semi-automatic (bracket order) |
| **TP2** | Price hits +50%/+40%/+100% (strategy-dependent) | Sell 1/3, start trailing | Semi-automatic (bracket order) |
| **Trail stop** | After TP2, ATR-based trailing | Sell remaining if trail hit | Automatic (trailing stop order) |
| **Time stop** | End of day (ORB) or 5-10 days (EMA) | Flatten position | ORB: automatic at 15:55 ET; EMA: alert |
| **Invalidation** | Strategy-specific condition breaks | You decide: close or override | No — requires your judgment |
| **Greeks deterioration** | Delta drops below 0.30, theta exceeds budget, IV rank > 75% | Close, roll, or override | No — requires your judgment |
| **Earnings approaching** | Within 5 business days (Law 5) | Close options position | No — but recommended strongly |
| **Thesis break** | Fundamental reason for holding changes | Exit full position | No — requires your judgment |
| **Drawdown limit** | Portfolio DD exceeds threshold | Halt all new trades | Automatic |

---

## Alert Flow Architecture (Entry + Exit)

```
ENTRY FLOW:
Strategy Detectors (run on schedule)
    │
    ├── detect_ema_crossover.py  (daily, after technical_indicators)
    ├── detect_orb.py             (intraday, 10:00 ET and 10:15 ET)
    └── detect_dip.py             (daily, after ohlcv_daily)
    │
    ▼
┌──────────────────────┐
│ Alert Formatting      │
│                       │
│ • Pull options chain  │
│ • Calculate SL/TP/RR │
│ • Define exit plan    │
│ • Format message      │
│ • Dedup check         │
└──────┬───────────────┘
       │
       ▼
┌──────────────────────┐
│ Telegram Delivery    │
│                       │
│ • Send entry alert    │
│ • Wait for /approve   │
│   or /reject          │
│ • 15-min timeout      │
└──────┬───────────────┘
       │ /approve
       ▼
┌──────────────────────┐
│ Pre-Flight Checks    │
│                       │
│ • Law 3: pos < 20%   │
│ • Law 5: DTE >= 30   │
│ • PDT counter < 3    │
│ • Drawdown halt?     │
│ • Greeks filter pass │
└──────┬───────────────┘
       │ PASS
       ▼
┌──────────────────────┐
│ Alpaca Execution     │
│                       │
│ • Bracket order w/    │
│   SL + TP1 + TP2     │
│ • Log to positions    │
│ • Register exits      │
└──────┬───────────────┘
       │
       ▼
EXIT MONITORING (runs while position is open)
    │
    ├── Price monitor (check every 5 min during market)
    │   ├── Stop hit? → auto-execute, send confirmation
    │   ├── TP1 hit? → sell 1/3, move stop to breakeven, alert
    │   └── TP2 hit? → sell 1/3, start trail, alert
    │
    ├── Invalidation monitor (strategy-specific)
    │   ├── EMA crossover reversed? → invalidation alert
    │   ├── ORB reversed into range? → invalidation alert
    │   └── Thesis broken? → thesis invalidation alert
    │
    ├── Greeks monitor (for options positions)
    │   ├── Delta dropped below 0.30? → deterioration alert
    │   ├── Theta exceeds budget? → close or roll alert
    │   └── IV rank crossed 75%? → premium expensive alert
    │
    ├── Time monitor
    │   ├── ORB: 30 min before close? → time stop warning
    │   ├── EMA: held > 10 days? → time stop review
    │   └── Earnings within 5 days? → Law 5 warning
    │
    └── Drawdown monitor (portfolio-level)
        └── Check thresholds: 5%/8%/10% daily, 8%/15%/20% weekly
```

---

## Scheduling

| Script | Schedule | n8n Workflow |
|--------|----------|-------------|
| `detect_ema_crossover.py` | Mon-Fri 18:35 ET (after derived_daily + trend_daily) | `alerts_daily` |
| `detect_dip.py` | Mon-Fri 18:40 ET (after alerts_daily) | `alerts_daily` |
| `detect_orb.py` | Mon-Fri 10:00 ET (define range) + 10:15 ET (check breakout) | `alerts_orb` |
| `monitor_exits.py` | Every 5 min during market hours (9:30-16:00 ET) | `monitor_exits` |
| `monitor_greeks.py` | Every 15 min during market hours | `monitor_greeks` |
| `monitor_risk.py` | Every 30 min during market hours | `alerts_risk` |

---

## Implementation Tasks

### Task 1: Strategy Detector — EMA Crossover (Entry)
- Create `scripts/detect_ema_crossover.py`
- Input: today's + yesterday's technical indicators, trend_status
- Filter: EMA-9 crossed EMA-21, ADX > 25, volume >= 1.5x, RSI 40-65 (bullish) or 35-60 (bearish)
- Cross-reference trend alignment, regime, iv_rank
- Calculate: ATR×2.0 stop, 30%/50%/trail TP, R:R ratio
- Output: structured entry alert with full trade plan + invalidation conditions
- Must match `02-Strategies/EMA Crossover.md` exactly

### Task 2: Strategy Detector — ORB (Entry)
- Create `scripts/detect_orb.py`
- Input: 5m bars 9:30-10:00 ET for opening range, then breakout check
- Filter: candle closes outside range, volume >= 2x, VWAP confirmation
- Calculate: opening range stop, 20%/40%/flatten TP, R:R ratio
- PDT counter check
- Output: structured entry alert with full trade plan + invalidation conditions
- Must match `02-Strategies/ORB — Opening Range Breakout.md` exactly

### Task 3: Strategy Detector — Buy the 5% Dip (Entry)
- Create `scripts/detect_dip.py`
- Input: daily OHLCV for local high, fundamentals for thesis check
- Find local highs in last 30 days, flag if >= 5% pullback
- Track: which tranche (1/2/3) the dip is in
- Calculate: scale-in levels, thesis check, earnings proximity
- Output: structured entry alert with 3-tranche plan
- Must match `02-Strategies/Buy the 5% Dip.md` exactly

### Task 4: Exit Monitor — Positions
- Create `scripts/monitor_exits.py`
- Runs every 5 min during market hours
- For each open position in Alpaca:
  - Check if stop price approached (within 1% → warning alert)
  - Check if TP1 hit → sell 1/3, move stop to breakeven, alert
  - Check if TP2 hit → sell 1/3, start trailing, alert
  - Check time stop (ORB flatten before close, EMA 5-10 day review)
- Different logic per strategy type:
  - **EMA (swing):** trailing after TP2, invalidation if crossover reverses or ADX < 20
  - **ORB (day):** flatten before close hard rule, false breakout invalidation
  - **Dip (LT):** thesis-based exit only, drawdown awareness alerts, tranche completion reminders

### Task 5: Exit Monitor — Invalidation Conditions
- **EMA crossover invalidation:**
  - 9 EMA crosses back below 21 EMA within 2 candles → `INVALIDATION` alert
  - ADX drops below 20 → `INVALIDATION` alert
  - Volume dries up (below 0.5x average after entry) → `CAUTION` alert
- **ORB invalidation:**
  - Price reverses back inside opening range → `FALSE BREAKOUT` alert
  - Volume dies below 1x average → `CAUTION` alert
- **Dip thesis invalidation:**
  - Revenue decline, earnings miss, sector rotation alerts from fundamentals data
  - NOT price-based — only fundamental triggers

### Task 6: Exit Monitor — Greeks Deterioration (Options)
- Create `scripts/monitor_greeks.py`
- Runs every 15 min during market hours for open options positions
- Check current greeks against entry greeks:
  - Delta dropped below 0.30 → position no longer tracking stock meaningfully
  - Theta exceeds daily budget (day 5%, swing 3%, LT 1%) → time decay killing position
  - IV rank crossed 75% → premium is now expensive
  - DTE approaching 30 → Law 5 warning, consider rolling
- Output: `Greeks Deterioration` alert with close/roll options
- Cross-reference: `02-Strategies/Greeks Strategy.md` delta ranges and theta budgets

### Task 7: Options Chain Filter
- Create `scripts/filter_options.py`
- Input: symbol, direction (call/put), strategy_type
- Query `market.options` + `market.greeks` for contracts matching:
  - DTE >= 30 (Law 5)
  - Delta in strategy range (0.50-0.70 swing, 0.55-0.65 day, 0.60-0.80 LT)
  - Theta budget < strategy limit (5% day, 3% swing, 1% LT of premium)
  - IV rank < 75% (skip if above)
- Sort by: best theta/delta ratio (lowest theta per unit of delta)
- Output: best contract with full greeks breakdown + entry price estimate

### Task 8: Alert Formatting + Telegram Delivery
- Create `scripts/alert_telegram.py`
- Format entry alerts (3 templates above) and exit alerts (all types above)
- Send via Telegram Bot API
- Receive /approve, /reject, /close, /roll, /close_all, /hold commands
- 15-minute timeout on entry alerts = auto-reject
- Log all alerts and responses to `trading.alert_history`
- Dedup: don't re-send same signal/same symbol/same day

### Task 9: Pre-Flight Checks Module
- Create `scripts/preflight_checks.py`
- Laws enforcement (cannot be bypassed):
  - Law 3: current position + new position not > 20% equity
  - Law 5: option DTE >= 30
  - PDT: day trade counter < 3 in rolling 5-day window
  - Drawdown: no new trades if daily DD > 10%, weekly > 20%, monthly > 30%
- Greeks filter:
  - IV rank < 75% (reject if above for buying options)
  - Delta in range for strategy
  - Theta budget within limit
- Correlation check: are 2+ correlated symbols signaling same thing? (per `05-Risk-Management/Correlation Risk.md`)
- Returns: PASS with details, or FAIL with reason

### Task 10: Alpaca Paper Execution
- Create `scripts/execute_trade.py`
- Input: approved alert with all details
- Submit bracket order to Alpaca Paper:
  - Entry: limit at signal price
  - Stop-loss: ATR-based per strategy
  - Take-profit: tiered per strategy (TP1/TP2/trail)
- For long-term (shares): simple buy order with thesis-based stop (not brackets)
- Log to `trading.positions` with full audit trail including:
  - Entry alert ID
  - Strategy type
  - Entry price, stop price, TP1, TP2
  - Invalidation conditions specific to this trade
  - Greeks at entry (for options)
- Register position in exit monitoring

### Task 11: Risk Alert Module
- Create `scripts/alert_risk.py`
- Drawdown halt: compare Alpaca portfolio P&L to thresholds (5/8/10% daily, 8/15/20% weekly, 15/20/30% monthly)
- PDT tracker: count day trades in rolling 5-day window from Alpaca
- Position size check: any position > 20% equity
- Pipeline health: check n8n execution API for failures
- Data freshness: check `market.ingest_state` for stale data
- Always sent (no dedup, no threshold filtering)

### Task 12: DB Migrations
- `db/init/11_alert_history.sql` — alert dedup and audit log
  - Fields: alert_id, strategy, symbol, direction, entry_alert, exit_alerts, status, created_at, resolved_at
- `db/init/12_positions.sql` — position tracking with exit conditions
  - Fields: position_id, strategy, symbol, direction, entry_price, stop_price, tp1, tp2, invalidation_conditions, entry_alert_id, current_status, greeks_at_entry, greeks_current
  - Links to alert_history for full audit trail
- `db/init/13_pdt_status.sql` — PDT day trade counter (rolling 5-day window)

### Task 13: n8n Workflows
- `n8n/workflows/alerts_daily.json` — daily at 18:35 ET (EMA crossover + dip detection)
- `n8n/workflows/alerts_orb.json` — 10:00 + 10:15 ET (ORB range definition + breakout)
- `n8n/workflows/monitor_exits.json` — every 5 min during market hours (price-based exits)
- `n8n/workflows/monitor_greeks.json` — every 15 min during market hours (greeks deterioration)
- `n8n/workflows/alerts_risk.json` — every 30 min during market hours (risk checks)

---

## What's New vs v1

| v1 (Generic Score Alerts) | v2 (Strategy Trade Setups) |
|---------------------------|---------------------------|
| "OKLO scored 63.5" | "WDC hit EMA crossover — entry $72.50, stop $66.10, TP1 $94.25, best option Delta 0.62" |
| No strategy context | Strategy name, entry/exit rules, invalidation criteria |
| No options chain info | Best contract filtered by DTE, delta, theta budget |
| No R:R or sizing | R:R ratio, risk amount, position size calculated |
| No exit plan at all | Entry + full exit plan defined upfront |
| No monitoring after entry | Exit alerts: TP1/TP2 hits, invalidation, greeks deterioration, time stops |
| Score threshold (arbitrary) | Strategy trigger (mechanical, testable) |
| Just notifications | Y/N approval → Alpaca execution with brackets |
| One script | 3 detectors + 3 monitors + execution engine |

---

## Verification Steps

1. `python scripts/detect_ema_crossover.py --dry-run` → shows what crossovers would have fired today
2. `python scripts/detect_orb.py --dry-run` → shows opening range + breakout detection
3. `python scripts/detect_dip.py --dry-run` → shows current dip candidates with tranche context
4. `python scripts/filter_options.py WDC call swing` → shows best option contract
5. `python scripts/monitor_exits.py --dry-run` → shows what exit conditions would trigger now
6. `python scripts/alert_telegram.py --dry-run` → prints all formatted messages without sending
7. Test entry approval: approve → confirm Alpaca Paper bracket order placed correctly
8. Test exit flow: simulate TP1 hit → confirm 1/3 sold, stop moved to breakeven
9. Test invalidation: simulate EMA reversal → confirm alert sent with correct data
10. Test pre-flight rejection: simulate position > 20% → confirm blocked
11. Test greeks deterioration: simulate delta below 0.30 → confirm deterioration alert

---

## Consistency Check — Backtests Must Match

Before any alert fires, the strategy detectors MUST produce signals consistent with `backtest.py`. If the backtest uses different entry/exit rules than the detector, they'll disagree and you'll get alerts for trades that wouldn't have passed backtest.

**Verification:**
- EMA crossover detector entry rules = EMA crossover backtest rules
- ATR-based stops in alerts = ATR-based stops in `backtest.py`
- Tiered take-profit in alerts = tiered TP in `backtest.py`
- PDT tracking in alerts = PDT tracking in `backtest.py`
- Greeks filters in alerts = greeks filters in `backtest.py`
- **Exit conditions in alerts = exit conditions in strategy playbooks**

If any of these disagree, the alerts are lying to you. We'll validate against the last 30 days of backtest data before going live.

---

## See Also
- [[EMA Crossover]] — Strategy playbook with exact entry/exit rules
- [[ORB — Opening Range Breakout]] — Strategy playbook
- [[Buy the 5% Dip]] — Strategy playbook (3-tranche entry, thesis-based exit)
- [[Order Execution Engine]] — Alpaca execution details (from v1 plan, still valid)
- [[Greeks Strategy]] — IV regime, delta, theta budget filters
- [[Laws of Trading]] — Hard constraints enforced at execution time
- [[Position Sizing]] — ATR-based sizing calculations (5% day, 10% swing, 5-7%/tranche LT)
- [[Loss Limits]] — Stop-loss, drawdown halts, time stops, tiered TP (20%/40%/flatten day, 30%/50%/trail swing, 50%/100%/ride LT)
- [[Monitoring & Dashboards]] — Position tracking and P&L visibility