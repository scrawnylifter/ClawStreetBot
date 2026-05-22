---
created: 2026-05-21
updated: 2026-05-21
tags: [strategy, template, data-pipeline, mOC]
---

# Strategy Template & Data Pipeline

Two things live in this note:

1. **Strategy Template** — every new strategy in ClawStreetBot must document these sections so the bot can execute it without ambiguity.
2. **Data Pipeline** — what data each strategy needs, where it comes from, and where the current pipeline falls short.

**Logic flow:** [[Laws of Trading]] (rules) → [[Trade Entry Criteria]] (why/when) → [[Strategies]] (how) → [[Risk Management]] (how much, when to stop) → Execution

---

## PART 1 — Strategy Template

Copy this skeleton into every new strategy note under `02-Strategies/`. Do **not** invent new R:R, sizing, or stop multiples per strategy — those live in [[Risk Management]] and [[Loss Limits]]. The strategy only picks **which profile** it belongs to.

---

### ENTRY PLAN

#### 1. Risk Management Profile

Pick exactly one from [[Risk Management]]: **Day Trading**, **Swing Trading**, or **Long-Term Holding**. Every parameter below is inherited from that profile and is **not** overridable by the strategy.

| Field | Day Trading | Swing Trading | Long-Term Holding |
|-------|-------------|---------------|-------------------|
| Risk per trade | **5%** | **10%** | 5–7% per tranche (max 15–20% built) |
| R:R minimum | **3:1** | **3:1** | n/a (thesis-based, upside 100%+) |
| Stop type | **ATR × 1.5** (or opening-range boundary) | **ATR × 2.0** | Thesis invalidation (mental stop) |
| TP1 | **ATR × 4.5** — sell 1/3 | **ATR × 6** — sell 1/3 | +50% — sell 1/3 |
| TP2 | **ATR × 7.5** — sell 1/3 | **ATR × 10** — sell 1/3 | +100% — sell 1/3 |
| TP3 | Trail remaining, flatten before close | Trail remaining (post-TP2) | Trail to 200%+ |
| Position cap | 20% (Law 3) | 20% (Law 3) | 20% (Law 3) |
| Max concurrent | 2–3 | 3–5 | 3 tranches per name |
| Time stop | Flatten before market close | Exit if thesis not progressing within N days | None — thesis-based |

ATR multiples are the single source of truth (see [[Unified Entry & Exit Checklist]] E4). They are **not** percentages of premium; they are multiples of the underlying's ATR.

#### 2. Entry Reasoning

Document the **science** behind the entry:

- **Primary signal** — the one event that fires the scanner (e.g. EMA9/21 crossover, ORB breakout, liquidity sweep + close-beyond, 5% pullback).
- **Confirmation signals** — what must be true on the *same* or *next* bar (volume ≥ X, ADX > Y, close beyond level, trend alignment).
- **Hard gates** — every Tier 1 gate from [[Unified Entry & Exit Checklist]] must pass. List the strategy-specific overrides here (e.g. ORB skips E1 trend gate by design).

#### 3. Liquidity & Greeks Filters

For options-leg trades, the contract must pass all of these before submission (see [[Greeks Strategy]]):

| Filter | Threshold | Source |
|--------|-----------|--------|
| Volume | ≥ 100 contracts (day) | Alpaca options snapshot |
| Open interest | ≥ 500 | Alpaca options snapshot |
| Bid-ask spread | ≤ **15%** of mid (`MAX_SPREAD_PCT`) | `scripts/constants.py` |
| DTE | **≥ 30** (Law 5) | Alpaca options chain |
| Delta | **0.50–0.70** standard; conservative 0.55–0.65; aggressive 0.40–0.80 | `DELTA_BANDS` in `fetch_alpaca_snapshot.py` |
| IV rank | **< 40** (cheap zone) | `market.iv_rank` |
| IV − RV spread | **≤ 0.05** | `market.realized_vol` |

If any gate fails the trade is rejected at scan time, again at persistence, and a third time at preflight. See [[Trade Entry Criteria]] and the three-layer enforcement in CLAUDE.md.

#### 4. Entry Pricing

- **Order type** — `LIMIT` only. Never `MARKET` on options.
- **Price reference** — `(bid + ask) / 2`, rounded to a penny. Crossing the spread leaks edge equal to `spread_pct / 2` on every entry.
- **Never pay the ask.** If the mid limit isn't filling, re-evaluate the setup rather than walking the order up.
- For stock-leg entries, use Alpaca **bracket orders** (`order_class=BRACKET`) with stop + TP2 legs attached, so the exit survives `exit_monitor` downtime.

---

### EXIT PLAN

#### 5. Risk Management Exit Rules

Exits are dictated by the profile, not the strategy:

- **Stop-loss** — hard stop at `entry − ATR × multiplier` (Day: 1.5, Swing: 2.0). For options legs, a 40–50% premium stop runs in parallel as thesis-invalidation.
- **Take-profit (tiered)** — partial close 1/3 at TP1, another 1/3 at TP2, trail the remaining 1/3. ATR multiples per the table above.
- **Shelf trailing (for the trailing 1/3)** — after TP2, trail the final 1/3 through shelf levels (prior swing points / consolidation zones on 5m). Exit when a candle closes past a shelf. See [[ORB — Opening Range Breakout]] for backtest evidence (3.84 PF vs 2.45 PF baseline).
- **Time stop** — Day: flatten before market close (12:45 PDT in `exit_monitor`). Swing: configurable per strategy, default = exit if no progress within max-hold window. Long-Term: none.

⚠️ FVG-as-entry is **rejected** (backtested -0.04R avg over 3,306 trades). FVG is only used as a **profit target or shelf overlay** — never as an entry trigger.

#### 6. Signal-Based Exit

Some strategies have a **signal reversal** that overrides time/price:

- **What counts as a reversal** — e.g. EMA9 crosses back under EMA21 for an EMA Crossover long; price closes back inside the opening range for an ORB long.
- **Confirmation required?** — default **yes**: wait for the same-timeframe close. Immediate exit only when ATR-stop is hit.

Signal-based exits never override a hard stop — they trigger *earlier* than the stop when the thesis dies.

#### 7. Selling Timing

- **Market hours only** — no extended-hours exits. Day trades must flatten in-session (Law 5 + Day Trading time stop).
- **Order type for exits** — `LIMIT` at the TP price (we set the price, MM crosses to us). On stop trigger, the bracket fires `STOP-MARKET` for guaranteed exit. Trailing stops use Alpaca `trail_percent` or `trail_price`.

#### 8. Portfolio-Level Rules

Every exit decision also checks the portfolio:

- **Drawdown circuit breaker** — from [[Loss Limits]]:
  - **Daily ≥ 30%** of latest equity snapshot → halt new entries, manage existing only.
  - **Weekly ≥ 40%** → halt new entries for the week.
  - **Monthly ≥ 50%** → full halt, manual reset required.
  - Baseline = **latest `market.equity_snapshots` row**, *not* the origin $100K. Missing snapshot = FAIL preflight (never WARN — see PR #17 C6).
- **Correlation check** — see [[Correlation Risk]]. Reject new entry if it would push sector exposure above its cap or portfolio correlation above the threshold.
- **PDT** — accounts < $25K: 3 day trades per 5-business-day rolling window. Preflight (`process_approved.py`) projects whether the new entry would be the 4th and rejects if so.

---

### TRADE LOGGING

Every entry and exit must land in `trading.positions` with the strategy-specific context:

| Column | Type | Purpose |
|--------|------|---------|
| `strategy_name` | `varchar` | Which scanner fired (e.g. `ema_crossover_15m`, `orb`, `liquidity_sweep`) |
| `entry_reason` | `text` | The specific signal that triggered (e.g. "EMA9 crossed above EMA21 at 14:35 with ADX 28, volume 1.4× avg") |
| `entry_gates_passed` | `jsonb` | Which [[Unified Entry & Exit Checklist]] gates passed (`{"E1":true,"E2":true,...,"E8":true}`) so we can audit later |
| `exit_reason` | `text` | One of: `stop_loss`, `tp1`, `tp2`, `trail_stop`, `signal_reversal`, `time_stop`, `expiry`, `manual`, `error` |
| `notes` | `text` | Free-form — context the structured columns can't capture (news catalyst, manual override rationale) |

The `entry_gates_passed` blob is the most important field for post-mortems: it lets us measure which gates correlate with winners vs. losers and prune the checklist over time per [[Laws of Trading|Law 6]] (luck vs. skill).

---

## PART 2 — Data Pipeline

### Data Requirements per Strategy

| Strategy | Stock Data | Options Data | Frequency | Min Freshness |
|----------|-----------|--------------|-----------|---------------|
| [[ORB — Opening Range Breakout\|ORB]] | 5m OHLCV + volume | Chain + greeks + bid/ask | During market hours | Near real-time (1–5 min) |
| [[Liquidity — 5m Day Trading\|Liquidity Sweep]] | 5m OHLCV + daily levels | Chain snapshot | During market hours | Near real-time (1–5 min) |
| [[EMA Crossover]] 15m | 15m OHLCV + EMA9/21 | Snapshot at signal time | Every 15 min | 15 min |
| [[EMA Crossover]] daily | Daily OHLCV + EMA9/21 | Chain + greeks | End of day | Daily batch |
| Setup Scanner | Daily OHLCV + indicators | Chain + greeks filter | End of day | Daily batch |
| Fundamentals | N/A | N/A | Weekly | N/A |

### Current Pipeline Status

| Layer | Source | Mechanism | Sink |
|-------|--------|-----------|------|
| Intraday OHLCV (15m / 5m) | Alpaca **IEX** (15-min delayed) | n8n cron every 1 hour, 6–13 PDT | `market.ohlcv` |
| Daily OHLCV | Alpaca **SIP** | n8n cron 15:00 PDT Mon–Fri | `market.ohlcv` |
| Options chains + greeks + bid/ask | Alpaca | n8n cron 14:55 PDT Mon–Fri | `market.options`, `market.greeks` |
| Fundamentals | `yfinance==0.2.55` | Weekly cron, Saturday | `market.fundamentals` |
| Derived analytics | Internal compute | `derived_daily` workflow nightly | `market.iv_rank`, `market.realized_vol`, `market.gex_dex`, `market.technical_indicators`, `market.trend_status` |

### The Gap (v2 reality check)

These are the missing rails that block the bot from acting on its own signals today. They are roughly in priority order:

1. **Alert pipeline — #1 gap.** Scanners write to `market.signal_alerts`, but **no dispatcher** is running. `alert_telegram.py` is archived. Until a dispatcher is back online, every signal is silent — the user can't see, let alone approve, what the scanners are firing.
2. **Real-time data.** We're on IEX 15-min delayed. ORB and Liquidity Sweep are pretending to be intraday but are seeing stale prints. **Alpaca Unlimited ($9/mo)** upgrades the feed to SIP and is the cheapest fix.
3. **Equity snapshots.** `snapshot_equity.py` is archived. Without daily snapshots, [[Loss Limits]] drawdown halts (30%/40%/50%) have no denominator — the bot can't tell whether it's halted or not.
4. **Execution layer.** `execute_trade.py`, `process_approved.py`, and `exit_monitor.py` are all archived. No order submission, no preflight (PDT / drawdown), no TP/SL monitoring. Approved signals would have nowhere to go even if (1) were fixed.

Until those four are restored, the v2 pipeline is **read-only**: it computes signals but cannot act on them. The strategy template above describes the contract every new strategy must honor *once those rails are reconnected*.

---

## See Also

- [[Strategies]] — Playbook index
- [[Laws of Trading]] — Non-negotiable rules
- [[Trade Entry Criteria]] — Why/when to enter
- [[Unified Entry & Exit Checklist]] — The gate list every strategy inherits
- [[Risk Management]] — Profile definitions (Day / Swing / Long-Term)
- [[Position Sizing]] — How risk-per-trade becomes share/contract count
- [[Loss Limits]] — Stop-losses, tiered TP, drawdown halts
- [[Correlation Risk]] — Portfolio-level entry vetoes
- [[ORB — Opening Range Breakout|ORB]], [[Liquidity — 5m Day Trading]], [[EMA Crossover]] — Strategies that already follow this template
