---
created: 2026-05-14
updated: 2026-05-14
tags: [strategy, swing-trading, orb, mOC]
status: draft
---

# ORB — Opening Range Breakout

## Status
- [x] Draft — not tested
- [ ] Paper — live on Alpaca Paper for ≥30 days
- [ ] Validated — proven positive expected value

## Category
[[Swing Trading]] — hold hours to days (typically same-day or next-day exit)

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
- [ ] Stock not in first/last 15 minutes of trading (Law — volatility trap)
- [ ] No earnings within 5 business days

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

## Position Sizing
- **Capital allocation:** ≤20% of portfolio (Law 3)
- **Formula:** Risk amount / (ATR × 2) = position size (ORBs have wider stops)
- **Smaller than EMA crossover position** — opening range trades have higher volatility

## Stop-Loss (defined before entry — Law 2)
- **Bullish ORB stop:** Below opening range low or 1.5 ATR below entry
- **Bearish ORB stop:** Above opening range high or 1.5 ATR above entry
- **No exceptions** — if it reverses back into the range, you're out

## Take-Profit (Law 4 — realize gains)
- **Target 1 (1:1 R:R):** Sell 1/3 — covers risk
- **Target 2 (1:2 R:R):** Sell 1/3 — locks in profit
- **Remainder:** Trail with VWAP or previous day's low as stop

## DTE Requirement (options only — Law 5)
- **Minimum DTE:** 30
- **Preferred:** 45-60 DTE — ORBs can be quick but need cushion for timing
- **Strike selection:** Slightly ITM (delta 0.55-0.65) for faster move on breakout

## Research Checklist (Law 8)
- [ ] Thesis documented (e.g., "APLD breaking out of 3-day consolidation on volume")
- [ ] Pre-market action checked (gap up/down?)
- [ ] Earnings proximity checked — not within 5 days
- [ ] Sector strength assessed (is the whole sector moving?)
- [ ] Greeks analyzed for options (delta, theta, IV rank)
- [ ] IV rank checked — avoid entries when IV rank > 70 (expensive options)

## Risk/Reward
- **Typical R:R:** 1:2 minimum, ideally 1:3 (ORBs can run big)
- **Win rate target:** 40-45% is acceptable if R:R is ≥1:2.5
- **Max loss:** Stop level × position size

## Invalidation
Exit immediately if:
- Price reverses back inside the opening range (false breakout)
- Volume dies immediately after breakout
- Sector or market turns against thesis
- Trade causing emotional reaction (Law 2 — size down)

## Timeframe Notes
- **15-min ORB:** Faster, more signals, more false breakouts
- **30-min ORB:** Fewer signals, higher quality, better for beginners
- **1-hour ORB:** Most conservative, best for high-priced stocks

## Backtest Results
- **Win Rate:** TBD
- **Profit Factor:** TBD
- **Max Drawdown:** TBD