---
created: 2025-04-28
updated: 2026-05-17
tags: [strategy, liquidity, day-trading, 5m-chart, stop-hunts, smart-money, reversal, scalp]
source: YouTube — The Moving Average
video: https://www.youtube.com/watch?v=LD1FEbwXU4o
---

# Liquidity Trading on the 5-Minute Chart

> "Find where the obvious trades are sitting and do the opposite."

Market makers push price into **liquidity zones** (where retail stops sit) to fill large orders. This strategy identifies those zones on HTF, then exploits the traps on 5m/1m for surgical reversal entries.

---

## Core Principle

Liquidity on the 5m chart = areas where traders get **stopped out** or **trapped**: wicks, swing highs, equal highs, clean S/R levels. These are magnets for price. Your job: find where the obvious trades are sitting and **fade them**.

"Clean price action that's just a little too clean — equal highs, trend lines, zones that look perfect — **that's the bait**."

---

## 4-Step Process: Spotting Lower Timeframe Liquidity

### 1. Mark HTF Zones First

- Start on **daily or 4H chart**
- Mark the most recent swing highs/lows and clean **untapped** levels
- These untapped areas = where liquidity should be living
- Use horizontal lines for S/R visual

### 2. Drop to 5m/1m and Wait for the Tap

- Zoom in on lower timeframe
- Watch for price **rapidly approaching** a marked zone
- Look for: **volume spike** → **quick tap** → **rejection wick**
- Entry = after the rejection confirms (**not before the tap**)
- Target: **1:2 risk-to-reward** minimum
- Double rejection at same zone → potential **full reversal** (double bottom/top)

### 3. Look for Traps

| Pattern | Signal | Action |
|---------|--------|--------|
| Wick above resistance → pause → dump | Bearish trap | Short the rejection |
| Wick below support → reverse | Bullish trap | Buy the bounce |
| Double rejection at same zone | Full reversal | Wider target |

**Traps = confirmation.** Price faked out retail, now reverses.

### 4. Enter on Volume/Candle Signals

- **Exhaustion candles** — long wicks = rejection at the zone
- **Engulfing candles** — bearish or bullish engulfing at S/R = strong reversal signal
- **Volume divergence** — price hits zone on declining volume = weak move, likely to reverse

---

## Real Trade Example (AUD/USD)

1. Marked HTF support on daily
2. Weekend gap left price below support
3. London session: price came down to the zone
4. Saw **engulfing candle** with wicks at support + rejection up
5. **Entry: 61,408** (AUD/USD)
6. Stop: below current London low
7. Target: **1:2 R:R** (anticipation of gap fill upward)

---

## Bonus Rules

1. **Never enter before the price taps the zone** — wait for confirmation
2. **Use session times** — London/NY open is when most stop hunts happen (9:30-10:30 ET, 15:00-16:00 ET)
3. **Don't chase breakouts** — wait for price to trap traders, then fade the move

---

## Integration with ClawStreetBot Scanner

| Strategy Element | Scanner Component | Data Source |
|-----------------|-------------------|-------------|
| HTF levels (daily swing H/L) | `market.technical_indicators` EMA/RSI | Alpaca daily bars |
| 5m zone approach detection | `market.ohlcv` 5m timeframe | Alpaca intraday |
| Rejection candles | Candle pattern scanner (future) | 5m OHLCV |
| Volume spike | `trade_count` + `vwap` from 5m bars | Alpaca |
| Session filtering | Time-of-day gates | Exchange calendar |

---

## Key Quotes

- "Find where the obvious trades are sitting and do the opposite"
- "Clean price action that's just a little too clean — equal highs, trend lines, zones that look perfect — that's the bait"
- "Market makers push price into liquidity to fill large orders, and they do it fast"
- "If price rejects the same area twice, it could be a full reversal — double bottom with the bar not closing below the S/R area"
- "Do not chase breakouts — wait for price to trap traders and fade the move"

---

## See Also

- [[Day Trading]] — Day trading rules and risk management
- [[Swing Trading]] — Swing trading rules and R:R targets
- [[Strategies]] — Strategy index