---
created: 2026-05-14
updated: 2026-05-16
tags: [strategy, day-trading, orb, mOC]
status: draft
---

# ORB — Opening Range Breakout

## Status
- [x] Draft — not tested
- [ ] Paper — live on Alpaca Paper for ≥30 days
- [ ] Validated — proven positive expected value

## Category
[[Day Trading]] — hold minutes to hours (same-day or next-day exit)

## Trigger
From [[Trade Entry Criteria]]:
- Price breaks above or below the opening range (first 15 or 30 minutes of market open)
- Volume on breakout candle ≥ 2x average
- Stock is on watchlist with a thesis or catalyst

## Setup (must be met before entry)

**For Long (bullish breakout):**
- [ ] Opening range high defined (15-min or 30-min after market open)
- [ ] Price breaks above opening range high with volume ≥ 2x average
- [ ] VWAP is rising after breakout
- [ ] Stock not in first/last 15 minutes of trading (volatility trap)
- [ ] No earnings within 5 business days
- [ ] Pre-market action checked (gap up/down?)

**For Short / Puts (bearish breakdown):**
- [ ] Opening range low defined
- [ ] Price breaks below opening range low with volume ≥ 2x average
- [ ] VWAP is falling after breakdown
- [ ] No trading in first/last 15 minutes

## Entry
- **Order type:** Stop-limit order at opening range high (long) or low (short)
- **Timing:** Enter on the breakout, not the pullback
- **Confirmation:** Wait for candle close outside the range

**The Trap:** False breakouts are common. Only enter if:
1. Volume confirms (≥2x)
2. Candle closes outside range (not just a wick)
3. No immediate reversal

## Stop-Loss (defined before entry — Law 2)
- **Bullish ORB stop:** Below opening range low or 1.5 ATR below entry
- **Bearish ORB stop:** Above opening range high or 1.5 ATR above entry
- **ATR multiplier for sizing:** 1.5× → see [[Position Sizing#ATR-Based]]
- **No exceptions** — if it reverses back into the range, you're out

## Risk Parameters
→ All R:R, position sizing, take-profit, and drawdown limits are defined in [[Risk Management]] and [[Position Sizing#Day Trading]]
- This strategy uses the **Day Trading** risk profile
- R:R minimum: **3:1** (see [[Position Sizing]])
- Position sizing: **5% risk / ATR × 1.5** stop method (see [[Position Sizing#Day Trading]])
- Take-profit tiers: **20% / 40% / flatten before close** (see [[Loss Limits#Tiered Exit — Day Trading]])
- **Time stop:** Flatten all positions before market close — no overnight gap risk

## DTE Requirement (options only — Law 5)
- **Minimum DTE:** 30 (insurance for timing, not hold time)
- **Preferred:** 45-60 DTE — gives cushion, but you're typically out same-day
- **Strike selection:** Slightly ITM (delta 0.55-0.65) for faster move on breakout

## Research Checklist (Law 8)
- [ ] Thesis documented (e.g., "APLD breaking out of 3-day consolidation on volume")
- [ ] Pre-market action checked (gap up/down?)
- [ ] Earnings proximity checked — not within 5 days
- [ ] Sector strength assessed (is the whole sector moving?)
- [ ] Greeks analyzed for options (delta, theta, IV rank)
- [ ] IV rank checked — avoid entries when IV rank > 70 (expensive options)
- [ ] Correlation check — ORB on NVDA + AMD + MU same morning = one trade (see [[Correlation Risk]])

## Exit Plan

### Hard Stop (safety net)
- ATR × 1.5 below entry (long) / above entry (short) — **always in place**, never removed
- Price reverses back inside opening range → immediate exit (false breakout)

### Take-Profit (tiered — from [[Risk Management]])
- **TP1:** ATR × 4.5 (3:1 R:R) → sell 1/3
- **TP2:** ATR × 7.5 (5:1 R:R) → sell 1/3
- **Trail remaining 1/3 through shelf levels** (see Shelf Trailing below)

### Shelf Trailing (for the final 1/3)
After TP2 exits the first 2/3, trail the remaining position through **shelf levels** — prior swing points or consolidation zones where price retests to continue the trend.
- **Shelf** = a minor swing high/low (3-bar minimum) or consolidation zone on 5m
- Stay in the trade while price **respects** shelves (touches and bounces)
- Exit when a 5m candle **closes past** a shelf level (structure broken)
- If a shelf overlaps with an FVG, it's a **confluent** level — stronger, but still trail through it the same way
- **Backtest evidence** (22 signals): shelf trail produced 3.84 PF vs 2.45 PF baseline, 0.52R avg vs 0.17R avg, lower drawdown (-7.22R vs -9.72R)
- **Caveat:** 22 signals is thin (Law 6 — luck vs. skill). Needs more data. Shelf detection currently on 5m proxy — real 1m bars would surface more micro-shelves.

⚠️ FVG-as-entry is **rejected** (backtested -0.04R avg over 3,306 trades). FVG is a **profit target only**, never an entry trigger.

### Time Stop
- **Flatten all positions before market close** — no overnight gap risk

## Invalidation
Exit immediately if:
- Price reverses back inside the opening range (false breakout)
- Volume dies immediately after breakout
- Shelf level disrespected (5m candle closes past it) while holding trailing 1/3
- Sector or market turns against thesis
- Trade causing emotional reaction (Law 2 — size down)
- Time stop hit — flatten before close, no exceptions

## Timeframe Notes
- **15-min ORB:** Faster, more signals, more false breakouts
- **30-min ORB:** Fewer signals, higher quality, better for newer traders
- **1-hour ORB:** Most conservative, best for high-priced stocks

## Backtest Results

### Exit Method Comparison (22 ORB + liquidity sweep signals)

| Method | Win Rate | Avg R | Profit Factor | Max DD | Total R |
|--------|----------|-------|--------------|--------|---------|
| Baseline (ATR stops only) | 40.9% | 0.17R | 2.45 | -9.72R | 3.63R |
| **Shelf trail** | 40.9% | **0.52R** | **3.84** | **-7.22R** | **11.35R** |
| Shelf + FVG partials | 40.9% | 0.13R | 2.43 | -6.95R | 2.80R |
| Confluent only (shelf+FVG overlap) | 40.9% | 0.39R | 2.88 | -8.64R | 8.52R |

- **Backtest script:** `scripts/backtest_fvg_shelf.py` (branch `feature/fvg-shelf-backtest`)
- **Caveat:** 22 signals is thin. Needs more data before graduating from Draft. Shelf detection on 5m proxy — 1m bars would sharpen.
- **FVG-as-entry:** ❌ Rejected (-0.04R avg, 3,306 trades in prior liquidity backtest)