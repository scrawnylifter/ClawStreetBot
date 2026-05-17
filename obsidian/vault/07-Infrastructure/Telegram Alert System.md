---
created: 2026-05-17
updated: 2026-05-17
tags: [alerts, telegram, strategy, execution, implementation-plan, mOC]
---

# Trade Alert System — Implementation Plan (v2)

> **Replaces**: Telegram Alert System v1 (generic score alerts → strategy-specific trade setups)

**Goal:** When a strategy trigger fires, send you a complete trade setup with entry, stop, targets, and best options contract. You reply Y/N. If Y, execute on Alpaca Paper.

**Architecture:** Strategy-specific detectors run on existing data in Postgres. Each produces a structured alert with everything you need to decide: symbol, direction, strategy name, entry price, stop price, take-profit levels, R:R ratio, best options contract (filtered by DTE, delta, theta budget), and risk amount. Telegram delivers. You approve. Alpaca executes.

**Tech Stack:** Python 3.11, psycopg2 (data), alpaca-py (execution), python-telegram-bot (delivery), n8n (scheduling)

---

## Why This Redesign

The v1 plan sent generic "OKLO scored 63.5" alerts. But your composite rarely breaks 60 (0.5% of signals), and a score doesn't tell you *which strategy* applies or *how to trade it*. You want:

> "WDC hit EMA crossover — here's the entry, stop, target, and the best option contract. Y/N?"

That's a trade decision, not a notification. Each alert needs to be strategy-specific with full context.

## The Three Strategies

### Strategy 1: EMA Crossover (Swing)

**Trigger:** 9 EMA crosses above/below 21 EMA, confirmed by ADX > 25 and volume >= 1.5x average

**Data we have:** ✅ EMA-9, EMA-21, ADX, RSI, MACD, ATR, volume — all in `market.technical_indicators`

**Alert format:**
```
📈 EMA CROSSOVER — WDC

Signal: Bullish 9/21 cross confirmed
Price: $72.50 | ATR: $3.20
Stop: $66.10 (ATR × 2.0 below)
TP1: $94.25 (+30%) | TP2: $108.75 (+50%)
R:R: 3.2:1 ✅
ADX: 31 (trending ✅) | RSI: 52 (room to run)
Trend: micro↑ inter↑ primary↑ (all aligned ✅)
IV Rank: 28% (buy zone ✅)
Regime: transition

Best Option (DTE≥30, Delta 0.50-0.70):
WDC 08/21 C$72.50 — Delta 0.62, Theta 2.1%/day
Entry: ~$4.20 | Risk: 10% ($100 on $1K)

/approve WDC_EMA_0517
/reject
```

**Detection script:** `scripts/detect_ema_crossover.py`
- Query today's + yesterday's `technical_indicators` for 9/21 EMA, ADX
- Compare: was EMA-9 < EMA-21 yesterday, is EMA-9 > EMA-21 today? (bullish cross)
- Filter by ADX > 25, volume >= 1.5x 20-day average
- Cross-reference `trend_status` for trend alignment
- Get current `iv_rank` for IV check
- Query `options` + `greeks` for best contract (DTE >= 30, delta 0.50-0.70, lowest theta/premium)
- Calculate ATR-based stop (ATR × 2.0), R:R, take-profit targets

### Strategy 2: Opening Range Breakout (Day)

**Trigger:** Price breaks above/below the opening range (first 15 or 30 min), confirmed by volume >= 2x average

**Data we have:** ✅ 5m bars (back to April 16), ✅ VWAP, ✅ ATR, ✅ volume

**Alert format:**
```
🚀 ORB BREAKOUT — RKLB

Signal: Bullish breakout above 30-min opening range
Opening range: $22.10 - $22.45 → broke above $22.50
Price: $22.65 | ATR: $0.85
Stop: $21.35 (opening range low, below by ATR×1.5)
TP1: $27.18 (+20%) | TP2: $31.71 (+40%)
Flatten: Before market close (time stop)
R:R: 3.1:1 ✅
Volume: 2.4x average ✅ | VWAP: rising ✅
PDT: 0/3 used ✅
IV Rank: 34% (normal zone)

Best Option (DTE≥30, Delta 0.55-0.65):
RKLB 06/26 C$22.50 — Delta 0.58, Theta 3.8%/day
Entry: ~$1.85 | Risk: 5% ($50 on $1K)

/approve RKLB_ORB_0517
/reject
```

**Detection script:** `scripts/detect_orb.py`
- At 10:00 ET (30 min after open): query 5m bars 9:30-10:00 for opening range high/low
- At 10:15 ET (15 min after OR defined): check if current price broke range
- Filter by volume >= 2x average, VWAP confirmation, ADX trending
- Skip first/last 15 min of trading (volatility trap per strategy doc)
- Time stop: all positions flattened before close (day trade rules)
- Track PDT counter

### Strategy 3: Buy the 5% Dip (Long-Term)

**Trigger:** Watchlist stock pulls back >= 5% from local high, thesis still intact

**Data we have:** ✅ Daily OHLCV (2 years), ✅ fundamentals, ✅ sentiment

**Alert format:**
```
💰 DIP ALERT — RKLB

Signal: 5%+ pullback from local high
Local high: $28.40 (May 10) → Current: $26.85 (-5.5%)
Thesis: Space launch growth, Neutron rocket development ✅
No earnings in 5 days ✅
Fundamentals: Revenue growing, no material weakness

Scale-in plan (3 tranches, 5% each):
  Tranche 1: $26.85 (now) — 5% risk
  Tranche 2: ~$25.50 (another 5% dip) — 5% risk  
  Tranche 3: Confirmed rebound — 5% risk
Stop: Thesis-based (NOT price-based)
TP1: +50% → TP2: +100% → let it ride

Buy shares (not options — long-term accumulation)

/approve RKLB_DIP_0517
/reject
```

**Detection script:** `scripts/detect_dip.py`
- For each watchlist symbol, find the local high in last 30 days
- If current price >= 5% below local high, flag as dip candidate
- Cross-reference fundamentals (revenue, EPS direction — no thesis-breaking drops)
- Check earnings calendar (no earnings within 5 days)
- Calculate scale-in levels: entry, -5% more, confirmation rebound
- Shares only (no options for long-term holds)

## Alert Flow Architecture

```
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
│ • Format message      │
│ • Dedup check         │
└──────┬───────────────┘
       │
       ▼
┌──────────────────────┐
│ Telegram Delivery    │
│                       │
│ • Send formatted msg  │
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
│ • Submit bracket      │
│ • Log to positions    │
│ • Set SL/TP orders    │
│ • Track fill status   │
└──────────────────────┘
```

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

## Scheduling

| Detector | Schedule | n8n Workflow |
|----------|----------|-------------|
| `detect_ema_crossover.py` | Mon-Fri 18:35 ET (after derived_daily + trend_daily) | `alerts_daily` |
| `detect_dip.py` | Mon-Fri 18:40 ET (after ohlcv_daily + alerts_daily) | `alerts_daily` |
| `detect_orb.py` | Mon-Fri 10:00 ET (define opening range) + 10:15 ET (check breakout) | `alerts_orb` |
| Risk checks | After every execution + daily summary | `alerts_risk` |

## What's New vs v1

| v1 (Generic Score Alerts) | v2 (Strategy Trade Setups) |
|---------------------------|---------------------------|
| "OKLO scored 63.5" | "WDC hit EMA crossover — entry $72.50, stop $66.10, TP1 $94.25, best option Delta 0.62" |
| No strategy context | Strategy name, entry/exit rules, invalidation criteria |
| No options chain info | Best contract filtered by DTE, delta, theta budget |
| No R:R or sizing | R:R ratio, risk amount, position size calculated |
| Score threshold (arbitrary) | Strategy trigger (mechanical, testable) |
| Just notifications | Y/N approval → Alpaca execution |
| One script | Three strategy detectors + execution engine |

## Implementation Tasks

### Task 1: Strategy Detector — EMA Crossover
- Create `scripts/detect_ema_crossover.py`
- Input: today's + yesterday's technical indicators
- Output: structured alert (symbol, direction, price, stop, targets, R:R, best option, IV rank)
- Filter: EMA-9 crossed EMA-21, ADX > 25, volume >= 1.5x, RSI 40-65 (bullish) or 35-60 (bearish)
- Cross-reference: trend alignment from `market.trend_status`, regime from `market.regime`
- Must match the strategy playbook in `02-Strategies/EMA Crossover.md` exactly

### Task 2: Strategy Detector — ORB
- Create `scripts/detect_orb.py`
- Input: 5m bars from 9:30-10:00 ET (opening range), 5m bars after 10:00 (breakout check)
- Define opening range high/low from first 30 min of trading
- Filter: breakout candle closes outside range, volume >= 2x, VWAP confirmation
- Calculate: stop at opposite side of range + ATR×1.5, targets at +20%/+40%
- PDT check: how many day trades used in rolling 5-day window
- Must match the strategy playbook in `02-Strategies/ORB — Opening Range Breakout.md`

### Task 3: Strategy Detector — Buy the 5% Dip
- Create `scripts/detect_dip.py`
- Input: daily OHLCV for local high detection, fundamentals for thesis check
- Find local highs in last 30 days per symbol
- If current close >= 5% below local high, flag as dip
- Filter: no earnings within 5 days, no obvious fundamental thesis break
- Scale-in plan: 3 tranches at entry, -5%, confirmation
- Shares only (not options — long-term accumulation)
- Must match the strategy playbook in `02-Strategies/Buy the 5% Dip.md`

### Task 4: Options Chain Filter
- Create `scripts/filter_options.py`
- Input: symbol, direction (call/put), strategy_type
- Query `market.options` + `market.greeks` for contracts matching:
  - DTE >= 30 (Law 5)
  - Delta in strategy range (0.50-0.70 swing, 0.55-0.65 day, 0.60-0.80 LT)
  - Theta budget < strategy limit (5% day, 3% swing, 1% LT of premium)
  - Sort by best theta/delta ratio
- Output: best contract with full greeks breakdown

### Task 5: Alert Formatting + Telegram Delivery
- Create `scripts/alert_telegram.py`
- Format each strategy alert with the templates above
- Send via Telegram Bot API
- Receive /approve or /reject responses
- 15-minute timeout = auto-reject
- Log all alerts to `trading.alert_history`
- Dedup: don't re-send same signal for same symbol on same day

### Task 6: Pre-Flight Checks Module
- Create `scripts/preflight_checks.py`
- Laws enforcement (cannot be bypassed):
  - Law 3: position not > 20% equity
  - Law 5: option DTE >= 30
  - PDT: day trade counter < 3 in rolling 5-day window
  - Drawdown: no new trades if daily DD > 10%, weekly > 20%, monthly > 30%
- Greeks filter:
  - IV rank < 75% (reject if above)
  - Delta in range for strategy
  - Theta budget within limit
- Returns: PASS with details, or FAIL with reason

### Task 7: Alpaca Paper Execution
- Create `scripts/execute_trade.py`
- Input: approved alert with all details
- Submit bracket order to Alpaca Paper:
  - Entry: limit at signal price
  - Stop-loss: ATR-based per strategy
  - Take-profit: tiered per strategy (30%/50%/trail for swing, 20%/40%/flatten for day, 50%/100%/ride for LT)
- Log to `trading.positions` with full audit trail
- Track fill status, update on exit

### Task 8: Exit Management
- Extend `execute_trade.py` with exit monitoring
- Day trade: flatten before close (time stop)
- Swing: stop-loss hit, TP1/TP2/trail, invalidation conditions
- Long-term: thesis invalidation only (not price-based)
- Partial exit execution (sell 1/3 at TP1, 1/3 at TP2, trail remaining)

### Task 9: Risk Alert Module
- Create `scripts/alert_risk.py`
- Drawdown halt: compare portfolio P&L to thresholds
- PDT tracker: count day trades in rolling 5-day window from Alpaca
- Position size check: any position > 20% equity
- Pipeline health: check n8n execution API for failures
- Data freshness: check `market.ingest_state` for stale data
- Always sent (no dedup, no threshold filtering)

### Task 10: DB Migrations
- `db/init/11_alert_history.sql` — alert dedup and audit log
- `db/init/12_execution.sql` — extend positions table, execution_log, pdt_status

### Task 11: n8n Workflows
- `n8n/workflows/alerts_daily.json` — daily at 18:35 ET (EMA crossover + dip detection)
- `n8n/workflows/alerts_orb.json` — 10:00 + 10:15 ET (ORB range definition + breakout)
- `n8n/workflows/alerts_risk.json` — hourly during market hours (risk checks)

## Verification Steps
1. `python scripts/detect_ema_crossover.py --dry-run` → shows what crossovers would have fired today
2. `python scripts/detect_orb.py --dry-run` → shows opening range + breakout detection
3. `python scripts/detect_dip.py --dry-run` → shows current dip candidates
4. `python scripts/filter_options.py WDC call swing` → shows best option contract
5. `python scripts/alert_telegram.py --dry-run` → prints formatted messages without sending
6. Confirm Telegram delivery on your phone
7. Test Y/N approval flow: approve → confirm Alpaca Paper order placed
8. Test pre-flight rejection: simulate position > 20% → confirm blocked

## Consistency Check — Backtests Must Match

Before any alert fires, the strategy detectors MUST produce signals consistent with `backtest.py`. If the backtest uses different entry/exit rules than the detector, they'll disagree and you'll get alerts for trades that wouldn't have passed backtest.

**Verification:**
- EMA crossover detector entry rules = EMA crossover backtest rules
- ATR-based stops in alerts = ATR-based stops in `backtest.py`
- Tiered take-profit in alerts = tiered TP in `backtest.py`
- PDT tracking in alerts = PDT tracking in `backtest.py`
- Greeks filters in alerts = greeks filters in `backtest.py`

If any of these disagree, the alerts are lying to you. We'll validate against the last 30 days of backtest data before going live.

## See Also
- [[EMA Crossover]] — Strategy playbook with exact entry/exit rules
- [[ORB — Opening Range Breakout]] — Strategy playbook
- [[Buy the 5% Dip]] — Strategy playbook
- [[Order Execution Engine]] — Alpaca execution details (from v1 plan, still valid)
- [[Greeks Strategy]] — IV regime, delta, theta budget filters
- [[Laws of Trading]] — Hard constraints enforced at execution time
- [[Position Sizing]] — ATR-based sizing calculations
- [[Loss Limits]] — Stop-loss, drawdown halts, time stops
- [[Monitoring & Dashboards]] — Position tracking and P&L visibility