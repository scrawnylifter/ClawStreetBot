---
created: 2026-05-14
updated: 2026-05-14
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

## Setup (must be met before entry)

**For Calls (bullish crossover):**
- [ ] 9 EMA crosses above 21 EMA
- [ ] Price is above VWAP (intraday) or above 50 EMA (swing)
- [ ] Volume on crossover candle ≥ 1.5x 20-day average
- [ ] RSI between 40-65 (room to run, not overbought)
- [ ] MACD histogram turning positive or already positive

**For Puts (bearish crossover):**
- [ ] 9 EMA crosses below 21 EMA
- [ ] Price is below VWAP (intraday) or below 50 EMA (swing)
- [ ] Volume on crossover candle ≥ 1.5x 20-day average
- [ ] RSI between 35-60 (room to fall, not oversold)
- [ ] MACD histogram turning negative or already negative

## Entry
- **Order type:** Limit order at or near the crossover candle close
- **Timing:** Enter on candle close confirmation (don't jump mid-candle)
- **Timeframe:** Daily chart for swing, 1-hour for tighter entries

## Position Sizing
- **Capital allocation:** ≤20% of portfolio (Law 3)
- **Formula:** Risk amount / (ATR × 1.5) = position size in shares/contracts
- **Example:** $200 risk / ($3 ATR × 1.5) = 44 shares

## Stop-Loss (defined before entry — Law 2)
- **Stop level:** Below the swing low (calls) or above swing high (puts) by 1 ATR
- **Or:** Below the 21 EMA (whichever is tighter)
- **Never move stop down** — only trail up as position moves in favor

## Take-Profit (Law 4 — realize gains)
- **Target 1 (+30%):** Sell 1/3 of position
- **Target 2 (+50%):** Sell 1/3 of position
- **Remainder:** Trail with 21 EMA as dynamic stop

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

## Risk/Reward
- **Typical R:R:** 1:2 minimum (risk $1 to make $2)
- **Win rate target:** >50% to be profitable at 1:2 R:R
- **Max loss:** Stop-loss level × position size

## Invalidation
Exit immediately if:
- Crossover reverses within 2 candles (false signal)
- Volume dries up immediately after entry
- Major macro event invalidates thesis (Fed announcement, sector rotation)
- Position is causing emotional distress (Law 2 — you're too deep)

## Backtest Results
- **Win Rate:** TBD
- **Profit Factor:** TBD
- **Max Drawdown:** TBD