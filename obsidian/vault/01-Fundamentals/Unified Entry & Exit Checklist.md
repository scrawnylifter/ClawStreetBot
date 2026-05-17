---
created: 2026-05-17
updated: 2026-05-17
tags: [fundamentals, entry, exit, checklist, strategy-synthesis]
sources:
  - "[[EMA Crossover]]"
  - "[[Liquidity — 5m Day Trading]]"
  - "[[Greeks Strategy]]"
  - "[[Swing Trading]]"
  - "[[Day Trading]]"
---

# Unified Entry & Exit Checklist

Every strategy contributes criteria based on what **backtesting proves works**, not what sounds good on YouTube. Items are ranked by weight — how much they contribute to signal quality. Items with ❌ are things we tested and discarded.

This is a living document. When a new strategy is synthesized and backtested, its proven criteria get added here. Discarded criteria get marked and explained.

---

## Entry Checklist (Ranked by Weight)

### Tier 1 — Hard Gates (ALL must pass, otherwise NO trade)

These are non-negotiable. A trade must pass every Tier 1 gate. If any fails, the answer is "don't take this trade."

| # | Criterion | Source Strategy | Backtest Verdict | Weight |
|---|-----------|----------------|-------------------|--------|
| E1 | **Trend alignment** — EMA9 > EMA21 (bullish) or EMA9 < EMA21 (bearish), gap > 0.5% | [[EMA Crossover]] | Proven (whipsaw filter) | 🔴 Required |
| E2 | **Trend strength** — ADX > 20 (daily) for setups, ADX > 25 for pure EMA crosses | [[EMA Crossover]] | Proven (only 5.6% of crosses pass ADX>25 — most are noise without it) | 🔴 Required |
| E3 | **Close-beyond confirmation** — next bar closes past the swept level | [[Liquidity — 5m Day Trading]] | Proven (PF 1.24 → 1.56, +0.20R) | 🔴 Required |
| E4 | **R:R ≥ 3:1** — ATR-based stops and targets (Day: ATR×1.5/4.5/7.5, Swing: ATR×2/6/10) | [[Risk Management]] | Proven (mathematical edge) | 🔴 Required |
| E5 | **DTE ≥ 30** — no short-dated options, no 0DTE | Laws of Trading #5 | Proven (theta decay kills) | 🔴 Required |
| E6 | **IV rank < 40** — premium in cheap zone | [[Greeks Strategy]] | Proven (high IV = overpaying for options) | 🔴 Required |
| E7 | **IV-RV spread ≤ 0.05** — not paying more than realized vol warrants | [[Greeks Strategy]] | Proven (spread > 0.05 = overpriced premium) | 🔴 Required |
| E8 | **Budget fit** — option mid price ≤ risk budget per trade | [[Risk Management]] | Proven (Law 3: max 20% capital per position) | 🔴 Required |

### Tier 2 — Strong Boosters (passing these significantly improves odds)

These aren't hard blockers, but when they align, the trade is materially stronger.

| # | Criterion | Source Strategy | Backtest Verdict | Contribution |
|---|-----------|----------------|-------------------|-------------|
| E9 | **RSI not overbought/oversold** — RSI < 70 for buys, > 30 for shorts | [[EMA Crossover]] (scanner) | Proven | Avoids chasing |
| E10 | **Session timing** — London/NY open (9:30-10:30 ET) for day trades | [[Liquidity — 5m Day Trading]] | Observed (all 3 sources) | Most stop hunts happen in first hour |
| E11 | **Daily swing level present** — HTF zone marked before LTF entry | [[Liquidity — 5m Day Trading]] | Proven (external sweep is the core edge) | Aligns with where stops accumulate |
| E12 | **Regime alignment** — trade direction matches current market regime (bull/bear/transition) | [[EMA Crossover]] (regime filter) | Proven (static weights actually outperformed conditional for now, but direction matters) | Avoids fighting the tide |
| E13 | **Negative GEX + IV << RV** — dealer amplification + cheap premium | [[Greeks Strategy]] (combined) | Proven (best buying setup per IV-RV-GEX analysis) | Doubles down when conditions align |
| E14 | **Equal highs/lows nearby** — engineered liquidity (too clean to be real) | [[Liquidity — 5m Day Trading]] | Observed (Tom Crown + TradingLab) | Predicts where sweeps will target |

### Tier 3 — Nice-to-Have (marginal improvement, don't wait for these)

| # | Criterion | Source Strategy | Backtest Verdict | Contribution |
|---|-----------|----------------|-------------------|-------------|
| E15 | **MACD histogram declining** — tightening stop signal, NOT entry | [[EMA Crossover]] | Proven (as EXIT signal, not entry) | See Exit Checklist |
| E16 | **RV compression** — 5d RV < 20d RV (vol squeeze building) | [[Greeks Strategy]] | Observational | Flags potential breakout but not timing |
| E17 | **Rejection candle at zone** — engulfing, long wick, volume divergence | [[Liquidity — 5m Day Trading]] | Proven (confirms sweep) | Reduces false entries at sweeps |

---

## Exit Checklist (Ranked by Weight)

Exits are where most traders lose. Every strategy agrees: **you need exit rules BEFORE you enter.**

### Tier 1 — Hard Exit Rules (mandatory, no overrides)

| # | Criterion | Source Strategy | Verdict | Weight |
|---|-----------|----------------|---------|--------|
| X1 | **Stop-loss: ATR-based** — Day ATR×1.5, Swing ATR×2.0 from entry | [[Risk Management]] | Proven | 🔴 Required |
| X2 | **TP1: ATR-based** — Day ATR×4.5 (3:1 R:R), Swing ATR×6.0 (3:1 R:R) | [[Risk Management]] | Proven | 🔴 Required |
| X3 | **TP2: ATR-based** — Day ATR×7.5 (5:1 R:R), Swing ATR×10.0 (5:1 R:R) | [[Risk Management]] | Proven | 🔴 Required |
| X4 | **TP1 exit = sell 1/3** — guaranteed profit, not a full exit | [[Risk Management]], [[Liquidity — 5m Day Trading]] | Proven (Law 4: realize gains) | 🔴 Required |
| X5 | **Time stop (day trades)** — flatten before close, no overnight risk | [[Day Trading]] | Proven | 🔴 Required |
| X6 | **Thesis invalidation stop** — for long-term holds, exit when the reason you bought is no longer true | [[Long-Term Holding]] | Proven | 🔴 Required |

### Tier 2 — Early Exit Signals (tighten or exit before stop hits)

| # | Criterion | Source Strategy | Verdict | Contribution |
|---|-----------|----------------|---------|-------------|
| X7 | **MACD histogram declining 3+ consecutive candles** — tighten stop to breakeven | [[EMA Crossover]] | Proven (92% of EMA crosses confirmed by MACD — histogram flip = momentum fading) | Early warning |
| X8 | **MACD histogram crosses zero against position** — take TP1 or close | [[EMA Crossover]] | Proven | Momentum reversal |
| X9 | **MACD bearish divergence** (price higher high, MACD lower high) — consider TP1 exit | [[EMA Crossover]] | Proven | Exhaustion signal |
| X10 | **Sweep target hit** — if you entered on a liquidity sweep, the FVG / opposite level is your target | [[Liquidity — 5m Day Trading]] | Proven (PF 1.56) | Take profit at the logical target |
| X11 | **Vanna shift** — \|delta-vol sensitivity\| > 0.05 with IV regime change | [[Greeks Strategy]] | Observational | IV crush shifts delta on every strike |
| X12 | **Break-even + scale out combo** — after TP1, move stop to breakeven, then trail | [[Risk Management]] (Fractal Flow) | Proven (guarantees profit position) | Locks in gains |

---

## ❌ Discarded Criteria (tested and failed)

These came from YouTube strategies or common trader wisdom. We backtested them and they don't work on our data. **Do NOT add these to your checklist.**

| # | Criterion | Source | Backtest Result | Why It Failed |
|---|-----------|--------|----------------|---------------|
| ❌ D1 | **FVG-as-entry trigger** (enter on FVG retrace) | Tom Crown, TradingLab | 3,306 trades, -0.04R avg | FVGs on 5m are noise, not entry signals |
| ❌ D2 | **Volume spike > 1.5x avg** as entry filter | Tom Crown | Slightly higher WR but fewer trades → PF drops to 1.07 | Over-constrains, eliminates good setups |
| ❌ D3 | **Minimum penetration depth** (0.5 ATR past level) | Diagnostic | Improves WR but PF drops to 1.40 | Too few trades survive the filter |
| ❌ D4 | **MACD as entry confirmation** | Common wisdom | 92% overlap with EMA9/21 cross (redundant) | MACD = EMA12 minus EMA26 — it just echoes the EMA relationship |
| ❌ D5 | **Percentage-based targets** (e.g., +30% for TP) | Common | Produces absurd targets on large caps (NVDA $220 → $286) | ATR-based targets scale with actual volatility |

---

## How to Use This Checklist

**For a BUY signal, ALL Tier 1 entry gates must pass (E1-E8).** This is what the 8-gate scanner already checks. Tier 2 and 3 items boost confidence but don't block a trade.

**For exits, X1-X6 are mandatory.** X7-X12 are decision points — they tell you to tighten, scale, or take early profit. They don't override your stop, they tell you something is changing.

**When a new strategy is synthesized and backtested:**
1. Proven criteria → add to the appropriate tier
2. Marginal criteria → add to Tier 3
3. Discarded criteria → add to the ❌ section with results
4. Update this document's `updated` date and re-rank

This checklist is the **single source of truth** for what makes a trade worth taking. Strategy playbooks reference this, not the other way around.