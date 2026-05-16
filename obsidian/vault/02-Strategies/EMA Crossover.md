---
created: 2026-05-14
updated: 2026-05-16
tags: [strategy, swing-trading, ema, mOC]
status: draft
---

# EMA Crossover

## Status
- [x] Draft — not tested
- [ ] Paper — live on Alpaca Paper for ≥30 days
- [ ] Validated — proven positive expected value

## Category
[[Swing Trading]] — hold days to weeks

## Trigger
From [[Trade Entry Criteria]]:
- Short-term EMA crosses above long-term EMA (bullish) or below (bearish)
- Confirmed by volume spike (≥1.5x average)
- RSI not overbought (>70) or oversold (<30) — avoid chasing

## Trend Filter (Required — prevents whipsaws)

9/21 EMA crosses fire constantly in choppy/sideways markets. Most are false signals.
**Before acting on any crossover, confirm the trend:**

**ADX (Average Directional Index):**
- ADX > 25 = trending market → crossover is valid
- ADX < 20 = choppy/ranging → ignore crossover, likely whipsaw
- ADX 20-25 = weak trend → proceed with caution, smaller position

**Higher-Timeframe Trend Check:**
- For daily 9/21 crosses: check that price is above the weekly 50 EMA (bullish) or below (bearish)
- Don't take a bullish daily cross if the weekly trend is against you

> This filter is what makes 3:1 R:R achievable. Without it, you'll get stopped out repeatedly on false crosses.

## Setup (must be met before entry)

**For Calls (bullish crossover):**
- [ ] 9 EMA crosses above 21 EMA
- [ ] ADX > 25 (trending, not chopping)
- [ ] Price is above VWAP (intraday) or above 50 EMA (swing)
- [ ] Volume on crossover candle ≥ 1.5x 20-day average
- [ ] RSI between 40-65 (room to run, not overbought)
- [ ] MACD histogram turning positive or already positive
- [ ] Higher-timeframe trend agrees (if daily cross, weekly trend bullish)

**For Puts (bearish crossover):**
- [ ] 9 EMA crosses below 21 EMA
- [ ] ADX > 25 (trending, not chopping)
- [ ] Price is below VWAP (intraday) or below 50 EMA (swing)
- [ ] Volume on crossover candle ≥ 1.5x 20-day average
- [ ] RSI between 35-60 (room to fall, not oversold)
- [ ] MACD histogram turning negative or already negative
- [ ] Higher-timeframe trend agrees (if daily cross, weekly trend bearish)

## Entry
- **Order type:** Limit order at or near the crossover candle close
- **Timing:** Enter on candle close confirmation (don't jump mid-candle)
- **Timeframe:** Daily chart for swing, 1-hour for tighter entries

## Stop-Loss (defined before entry — Law 2)
- **Stop level:** Below the swing low (calls) or above swing high (puts) by 1 ATR
- **Or:** Below the 21 EMA (whichever is tighter)
- **ATR multiplier for sizing:** 2.0× → see [[Position Sizing#ATR-Based]]
- **Never move stop down** — only trail up as position moves in favor

## Risk Parameters
→ All R:R, position sizing, take-profit, and drawdown limits are defined in [[Risk Management]] and [[Position Sizing#Swing Trading]]
- This strategy uses the **Swing Trading** risk profile
- R:R minimum: **3:1** (see [[Position Sizing]])
- Position sizing: **ATR × 2.0** stop method (see [[Position Sizing#ATR-Based]])
- Take-profit tiers: **30% / 50% / trail** (see [[Loss Limits#Tiered Exit — Swing Trading]])

## DTE Requirement (options only — Law 5)
- **Minimum DTE:** 30
- **Preferred:** 45-90 DTE for swing trades (gives time to develop)
- **Strike selection:** Near ATM (delta 0.40-0.60 for options)

## Research Checklist (Law 8)
- [ ] Thesis documented (e.g., "NVDA momentum breakout after consolidation")
- [ ] Data sources cited (chart timeframe, volume data, earnings proximity)
- [ ] Greeks analyzed (delta, theta, IV rank for options)
- [ ] IV rank checked — prefer entries when IV rank < 50th percentile
- [ ] No earnings within 5 business days (Law 5)
- [ ] ADX checked — must be >25 to confirm trend
- [ ] Correlation check — is another semi stock already signaling the same cross? (see [[Correlation Risk]])

## Invalidation
Exit immediately if:
- Crossover reverses within 2 candles (false signal)
- ADX drops below 20 (trend dying)
- Volume dries up immediately after entry
- Major macro event invalidates thesis (Fed announcement, sector rotation)
- Position is causing emotional distress (Law 2 — you're too deep)

## Backtest Results
- **Win Rate:** TBD
- **Profit Factor:** TBD
- **Max Drawdown:** TBD