---
created: 2026-05-14
updated: 2026-05-14
tags: [fundamentals, trade-entry, signals, mOC]
---

# Trade Entry Criteria — When & Why We Enter Trades

This is separate from [[Laws of Trading]] (the rules). Laws are **non-negotiable constraints**. This document defines **what triggers a trade consideration** — the signals and conditions that make us look at a position in the first place.

---

## Signal Categories

### 1. News & Sentiment (Qualitative → Quantitative)

**Sources:**
- RSS feeds (Seeking Alpha, MarketWatch, Bloomberg, Reuters)
- Reddit (r/wallstreetbets, r/options, r/stocks, r/investing)
- Twitter/X (financier accounts, company IR accounts)
- Alpaca News API (already connected — article headlines & sentiment)

**Triggers:**
- Earnings announcements or guidance changes
- FDA approvals / regulatory decisions (NVO — GLP-1 news)
- Analyst upgrades/downgrades
- Sector-wide news (data center buildouts → NVDA, MU, APLD, IREN)
- Unusual social media volume (WSB mentions spiking)

**Signal Output:** Sentiment score (bearish → bullish scale), confidence level, source reliability rating

### 2. Macro & Micro Events

**Macro:**
- Fed rate decisions, CPI/PPI, employment data
- Treasury yields (10Y, 2Y), yield curve inversions
- VIX & options flow (unusual activity)
- Dollar strength (DXY)

**Micro:**
- Earnings dates & expectations (we don't trade earnings, but we track sentiment around them)
- Insider buying/selling (Form 4 filings)
- Institutional 13F filings (hedge fund positions)
- Options flow unusual activity (sweep orders, large block trades)

**Signal Output:** Event type, expected impact (low/med/high), date, affected symbols

### 3. Technical Indicators

These are the *timing* signals — they don't tell us WHAT to trade, they tell us WHEN.

**Volume:**
- Unusual volume spikes (2x+ average daily volume)
- Volume profile at key price levels

**Moving Averages:**
- EMA crossover (9/21 EMA for short-term, 50/200 for trend)
- Price relative to key EMAs (above/below)

**Momentum:**
- MACD (crossovers, divergences, histogram)
- RSI (overbought >70, oversold <30, divergences)
- Stochastic oscillator

**Volatility:**
- Bollinger Bands (squeeze → breakout)
- ATR (average true range for position sizing and stop placement)
- Implied Volatility rank (IV percentile vs. 52-week range)

**Price Action:**
- Opening Range Breakout (ORB) — 15-min, 30-min, 1-hour
- Support/resistance levels (previous day H/L, weekly pivots)
- Gap analysis (gap up/down, fill probability)

**VWAP:**
- Price relative to VWAP (above = bullish, below = bearish)
- VWAP as dynamic support/resistance

**Signal Output:** Indicator name, direction (bullish/bearish/neutral), strength (1-5), timeframe

### 4. Options-Specific Signals

- **IV Rank/Percentile** — buy options when IV is low (cheap), sell when IV is high (expensive)
- **IV Crush Warning** — avoid buying options before earnings (we don't trade earnings anyway)
- **Put/Call Ratio** — extreme readings signal sentiment reversals
- **Unusual Options Activity** — large block trades, sweep orders far from current price
- **Open Interest Spikes** — new positions being built

**Signal Output:** Metric, value, percentile rank, signal direction

---

## Pipeline Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     SIGNAL COLLECTION LAYER                      │
├───────────────┬────────────────┬───────────────┬───────────────┤
│  RSS/News      │   Technical     │   Macro/      │   Options     │
│  Scraper       │   Indicators    │   Events      │   Flow        │
│  (Python)      │   (TA-Lib)      │   (Calendar)  │   (Alpaca)    │
└───────┬───────┴────────┬─────────┴───────┬───────┴───────┬───────┘
        │                │                 │               │
        v                v                 v               v
┌─────────────────────────────────────────────────────────────────┐
│                     SIGNAL PROCESSING LAYER                       │
│                                                                  │
│  Redis Streams ──→ Signal Router ──→ Scoring Engine              │
│  (real-time queue)  (classify, dedupe)  (weighted composite)     │
│                                                                  │
│  Scoring: news_sentiment × 0.2 + tech_signal × 0.4               │
│           + macro_event × 0.15 + options_flow × 0.25             │
└───────────────────────┬─────────────────────────────────────────┘
                        │
                        v
┌─────────────────────────────────────────────────────────────────┐
│                     TRADE EVALUATION LAYER                        │
│                                                                  │
│  Composite Signal Score ──→ Laws of Trading Check                │
│                            ├─ Position size ≤ 20%?               │
│                            ├─ DTE ≥ 30?                          │
│                            ├─ Stop-loss defined?                  │
│                            ├─ Research record attached?          │
│                            └─ No earnings play?                  │
│                                                                  │
│  PASS → Generate alert / paper order                             │
│  FAIL → Log reason, discard                                      │
└───────────────────────┬─────────────────────────────────────────┘
                        │
                        v
┌─────────────────────────────────────────────────────────────────┐
│                     EXECUTION LAYER                               │
│                                                                  │
│  Alpaca API ──→ Paper Orders (Phase 2) ──→ Live Orders (Phase 3) │
│                                                                  │
│  Order Types: market, limit, stop, bracket                       │
│  Exit Rules: stop-loss (mandatory), take-profit at 30%/50%       │
└─────────────────────────────────────────────────────────────────┘
```

---

## Signal Weighting (Initial)

These weights define how much each signal category influences the final trade decision. Will be backtested and adjusted.

| Signal Category | Weight | Rationale |
|----------------|--------|-----------|
| Technical Indicators | 40% | Timing is everything — entries and exits |
| Options Flow / Unusual Activity | 25% | Smart money leaves footprints |
| News / Sentiment | 20% | Narrative drives short-term moves |
| Macro / Events | 15% | Tailwinds and headwinds |

---

## Implementation Phases

### Phase 2a: Data Ingestion Pipeline (Next)
- [ ] Build RSS/news scraper (Python, scheduled via cron)
- [ ] Pull technical indicators from Alpaca bars (EMA, MACD, RSI, VWAP, volume)
- [ ] Store all signals in Postgres `scraper.articles` and `scraper.sources`
- [ ] Redis streams for real-time signal queue

### Phase 2b: Technical Analysis Engine
- [ ] Calculate EMA (9/21/50/200) from OHLCV bars
- [ ] Calculate MACD, RSI, Bollinger Bands, ATR
- [ ] Implement ORB (Opening Range Breakout) detection
- [ ] IV rank / percentile calculation from options chains
- [ ] Volume profiling (2x average = unusual)

### Phase 2c: Options Flow Scanner
- [ ] Unusual options activity detector (large block trades, OI spikes)
- [ ] Put/call ratio calculator per symbol
- [ ] IV rank heatmap across watchlist
- [ ] Alert when IV drops below 25th percentile (buy opportunities)

### Phase 2d: News Sentiment
- [ ] Alpaca News API integration (already available)
- [ ] RSS feed parser (Seeking Alpha, MarketWatch)
- [ ] Reddit scraper (r/wallstreetbets mention frequency)
- [ ] Simple sentiment classifier (bullish/bearish/neutral)

### Phase 2e: Signal Scoring & Alerting
- [ ] Composite signal score calculation
- [ ] Laws of Trading compliance check
- [ ] Alert system (Telegram notifications via bot)
- [ ] Obsidian trade journal auto-logging

---

## Key Decisions & Open Questions

| Decision | Status | Notes |
|----------|--------|-------|
| News sources | ✅ Resolved | Alpaca News + RSS + Reddit |
| Technical library | 🔜 TBD | TA-Lib vs pandas-ta vs custom |
| Sentiment model | 🔜 TBD | Simple keyword vs. LLM-based |
| Signal weights | ⚠️ Initial | 40/25/20/15 — needs backtesting |
| Puts vs. Calls | 🔜 TBD | Framework should support both — Law 5 doesn't exclude puts |
| Backtesting engine | 🔜 TBD | Need historical data + simulation |

---

## See Also

- [[Laws of Trading]] — Non-negotiable rules
- [[Trade Entry Criteria]] — What triggers every trade
- [[Watchlist]] — Current tracked assets