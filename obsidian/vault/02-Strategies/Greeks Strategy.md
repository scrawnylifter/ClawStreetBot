---
created: 2026-05-16
updated: 2026-05-16
tags: [strategy, options, greeks, delta, theta, IV, mOC]
---

# Greeks Strategy — Using Options Greeks for Entry, Exit, and Risk Management

> Greeks are not just data — they're the **lens** through which we evaluate every options trade. A trade that looks good on price alone can be fatal on greeks.

This document defines **how we use greeks** to filter, qualify, size, and manage options positions. It complements [[Position Sizing]] and [[Loss Limits]] — greeks override sizing when they conflict.

---

## Data Sources

| Greek | Alpaca (Snapshots) | Polygon.io (Historical + Snapshots) |
|-------|--------------------|--------------------------------------|
| Delta | ✅ Current snapshot | ✅ Full historical |
| Gamma | ✅ Current snapshot | ✅ Full historical |
| Theta | ✅ Current snapshot | ✅ Full historical |
| Vega | ✅ Current snapshot | ✅ Full historical |
| Rho | ✅ Current snapshot | ✅ Full historical |
| Vanna | ❌ | ✅ (Massive/Polygon only) |
| IV | ✅ Current snapshot | ✅ Full historical + rank/percentile |
| Open Interest | ⚠️ None on Paper tier | ✅ Full historical |
| Break-even Price | ✅ | ✅ |

**Rule:** Use Polygon.io for **historical greeks, IV rank, and backtesting**. Use Alpaca for **real-time snapshots at execution time**.

---

## 1. IV Regime → Strategy Selection

IV Rank tells us whether options are cheap or expensive relative to their own history. This is the **first filter** — it determines *what kind* of play we consider.

**IV Rank = (Current IV - 52-week Low) / (52-week High - 52-week Low) × 100**

| IV Rank | Regime | Play Type | Rationale |
|---------|--------|-----------|-----------|
| 0–25% | **Low IV** | Buy calls/puts (premium is cheap) | Options are cheap — pay less for directional exposure |
| 25–50% | **Normal** | Directional plays with tighter strikes | Fair pricing — focus on directional conviction |
| 50–75% | **Elevated** | Cautious buying, consider spreads | Premium is expensive — use spreads to reduce cost |
| 75–100% | **High IV** | Avoid naked buying; consider credit spreads or skip | Options are expensive — selling premium is favored, but we don't sell naked |

### Hard Rules

- **Never buy naked options when IV Rank > 75%** — premium is too expensive, theta decay will crush you
- **IV Rank < 25% = buying opportunity** — this is where we take our best swing trades
- **IV Crush Warning:** If earnings are within 5 trading days, mark the trade with an **IV crush flag** even though we don't trade earnings (Law 7). IV drops 30-50% post-earnings and nearby expirations get slaughtered
- **IV Percentile vs IV Rank:** Use **IV Rank** (relative position) over IV Percentile (% of time below current). Rank is more actionable for trading decisions

---

## 2. Delta-Based Entry Qualification

Delta tells us how much the option moves per $1 move in the stock. It's also a rough probability-of-profit proxy.

### Delta Targets by Strategy

| Strategy | Target Delta | Rationale |
|----------|-------------|-----------|
| **Day Trading (Calls)** | 0.70–0.80 | Need stock-like movement for scalp; less extrinsic value |
| **Day Trading (Puts)** | -0.70 to -0.80 | Same — deep ITM for immediate directional exposure |
| **Swing Trading (Calls)** | 0.50–0.70 | Balance between leverage and cost; tracks stock decently |
| **Swing Trading (Puts)** | -0.50 to -0.70 | Same — directional conviction with manageable premium |
| **Long-Term (Calls)** | 0.60–0.80 | Deep ITM for leverage; mostly intrinsic value, less theta risk |

### Delta Rules

- **Minimum delta: |0.50|** — below this, the option is too far OTM and behaves like a lottery ticket
- **Maximum delta: |0.90|** — above this, you're better off buying shares (stock substitution, no leverage benefit)
- **Delta must align with directional conviction** — don't buy a 0.40 delta call if you're "sure" about the move; go deeper ITM
- **Position delta equivalent** — know how many shares your position replicates:
  ```
  Position Delta = Contract Delta × 100 × Number of Contracts
  ```
  Example: 3 contracts at 0.60 delta = 180 share equivalent. Make sure this aligns with your stock position limits.

---

## 3. Theta Budget (Time Decay Acceptance)

Theta tells us how much premium we lose per day. Every options trade has a **daily theta cost** — we need to budget for it.

### Theta Rules

| Strategy | Max Daily Theta | Rationale |
|----------|----------------|-----------|
| **Day Trading** | < 5% of premium | Must move today — can't afford to pay theta |
| **Swing Trading** | < 3% of premium per day | 5-10 day hold; theta compounds fast |
| **Long-Term** | < 1% of premium per day | Holding 30-90 days; theta must be negligible |

**Formula:**
```
Theta Budget = Daily Theta / Option Premium
```

**Example:**
- Option premium: $3.50
- Daily theta: -$0.05
- Theta Budget: 0.05 / 3.50 = **1.4%** ✅ (passes all strategies)
- If theta were -$0.12: 0.12 / 3.50 = **3.4%** ❌ (fails long-term, passes swing only)

### Hard Rules

- **Reject any trade where daily theta > 5% of premium** — you're paying too much for time
- **Law 5 (30 DTE minimum) exists partly for theta** — options < 30 DTE suffer accelerating time decay
- **Theta accelerates in the last 2 weeks** — even at 30 DTE, if you're holding and theta exceeds your budget, roll or close

---

## 4. Gamma Risk Assessment

Gamma measures how fast delta changes — it's the **acceleration** of your position. High gamma means delta moves fast, which is great when you're right and devastating when you're wrong.

### Gamma Rules by Strategy

| Strategy | Gamma Guidance | Rationale |
|----------|---------------|-----------|
| **Day Trading** | High gamma is OK (short hold) | Delta moves fast = profit faster, but cut losses immediately |
| **Swing Trading** | Moderate gamma preferred | Don't want delta to shift dramatically overnight |
| **Long-Term** | Low gamma preferred | Slow delta change = stable position, thesis has time to play out |

### Gamma Warning Zones

- **Near-the-money options near expiration** → gamma spikes. This is why Law 5 exists (30 DTE minimum)
- **Gamma ≥ 0.10 per contract** → position delta can shift 10+ shares per $1 move. Know your exposure
- **Earnings week gamma** → Even though we don't trade earnings, positions held through earnings face extreme gamma. Check the calendar

---

## 5. Vega Sensitivity (IV Impact)

Vega tells us how much the option price changes per 1% change in IV.

### Vega Rules

- **When buying options and IV Rank < 25%:** Vega is your **friend** — IV expanding increases your premium
- **When buying options and IV Rank > 50%:** Vega is your **enemy** — IV contraction destroys premium even if direction is right
- **Vega crush around earnings:** IV drops 30-50% after earnings. If you hold options through earnings, vega loss can exceed delta gain

**Vega-to-Theta Ratio:**
```
Vega/Theta = IV sensitivity vs. time decay
```
- **Ratio > 2.0:** The option is more sensitive to IV changes than time decay (IV regime matters most)
- **Ratio < 0.5:** The option is more sensitive to time decay than IV (time is the enemy)
- **Ideal buying zone:** Vega/Theta ratio between 0.5–2.0 with IV Rank < 50%

---

## 6. Vanna (Delta-Vol Sensitivity)

**Vanna** is available from Polygon.io/Massive and tells us how delta shifts when IV changes. This is the most underutilized greek that matters most around IV regime changes.

### When Vanna Matters

- **Pre/post earnings:** IV crush shifts delta on every strike. Vanna predicts the shift
- **Regime changes:** When IV crosses from low to high (or vice versa), delta changes on all options
- **Cross-asset vol events:** VIX spikes affect all equity options — vanna quantifies the impact

### Vanna Rules

- **|Vanna| > 0.05 and IV Rank changing regime** → expect delta shift, adjust stops or exit
- **Negative vanna on long calls during IV crush** → delta decreases, position becomes less responsive. Consider rolling
- **Positive vanna on long puts during IV spike** → delta becomes more negative, position overshoots target. Consider taking profit early

---

## 7. Greeks Integration into Trade Flow

```
Signal received
      │
      v
Check IV Rank (first filter)
      ├── IV Rank > 75% → SKIP or use spreads (premium too expensive)
      ├── IV Rank 50-75% → Cautious, use tighter strikes, consider debit spreads
      └── IV Rank < 50% → Proceed to delta check
      │
      v
Delta check (entry qualification)
      ├── |Delta| < 0.50 → SKIP (too far OTM = lottery ticket)
      ├── |Delta| > 0.90 → Consider shares instead (no leverage benefit)
      └── |Delta| in target range → Proceed to theta check
      │
      v
Theta budget check
      ├── Daily theta > 5% of premium → SKIP (time decay too expensive)
      ├── Daily theta > 3% of premium → Swing trade only, NOT long-term
      └── Daily theta < threshold → Proceed to position sizing
      │
      v
Position sizing (from Position Sizing doc)
      ├── Calculate max premium (Equity × Risk% strategy)
      ├── Calculate contracts = Max Premium / (Premium × 100)
      ├── Check delta equivalent ≤ position limits
      └── Check Gamma exposure
      │
      v
Greeks-based stops (in addition to price stops)
      ├── Stop-loss price OR stop-loss delta shift (gamma-aware)
      ├── Theta budget threshold → close if daily theta exceeds budget
      └── Vanna regime change → close or roll if IV regime shift invalidates delta
```

---

## 8. Greeks-Based Exit Rules

These supplement the price-based exits in [[Loss Limits]].

| Exit Trigger | Action | Rationale |
|-------------|--------|-----------|
| Theta exceeds daily budget | **Close or roll** | Paying too much for time |
| IV Rank crosses 75% (while holding) | **Tighten stops** | IV expansion is over; contraction will hurt |
| |Delta| drops below 0.30 | **Close or roll** | No longer tracking stock meaningfully |
| Gamma acceleration near expiration | **Roll to next month** | Law 5 — always have ≥30 DTE remaining |
| Vanna regime shift (IV crosses zone) | **Re-evaluate** | Delta assumptions may no longer hold |
| Profit target reached but IV is falling | **Take profit early** | IV contraction can eat gains even as stock moves your way |

---

## 9. Implementation Phases

### Phase 2a: Data Collection (with Polygon.io Ingestion)
- [ ] Create `market.options_greeks` table in Postgres
- [ ] Build Polygon.io greeks ingestion script (daily snapshot per symbol per contract)
- [ ] Calculate IV Rank from historical IV data (52-week high/low)
- [ ] Store greeks snapshots alongside OHLCV bars

### Phase 2b: Greeks Filtering Engine
- [ ] IV Rank calculator (current IV vs 52-week range)
- [ ] Delta filter (min 0.50, max 0.90, strategy-specific targets)
- [ ] Theta budget calculator (daily theta / premium)
- [ ] Vanna regime detector (IV Rank zone transitions)

### Phase 2c: Backtesting with Greeks
- [ ] Backtest IV regime filter against historical data
- [ ] Backtest delta ranges per strategy (which delta range is most profitable?)
- [ ] Validate theta budget thresholds (are 3%/5% cutoffs optimal?)
- [ ] Vanna impact study (how much does vanna affect P&L around regime changes?)

### Phase 2d: Live Greeks Scanner
- [ ] Real-time IV Rank heatmap across watchlist
- [ ] Delta-qualified opportunity scanner (signals that pass greeks filters)
- [ ] Theta decay monitor (positions approaching theta budget limit)
- [ ] Vanna alert on IV regime transitions

---

## 10. Greeks Quick Reference

| Greek | What It Measures | Directional Call | Directional Put | Good Range (Buying) | Bad Range (Buying) |
|-------|-----------------|-----------------|-----------------|---------------------|---------------------|
| **Delta** | Price sensitivity | 0 to 1.0 | 0 to -1.0 | 0.50–0.80 | < 0.30 or > 0.90 |
| **Gamma** | Delta acceleration | Positive | Positive | Low-moderate | Very high near expiry |
| **Theta** | Time decay | Negative | Negative | < 3%/day of premium | > 5%/day |
| **Vega** | IV sensitivity | Positive | Positive | Friend when IV low | Enemy when IV high |
| **Vanna** | Delta-Vol interaction | Variable | Variable | Monitor around regime shifts | High near earnings |
| **IV Rank** | IV relative to 52-week range | N/A | N/A | 0–25% (cheap) | 75–100% (expensive) |

---

## See Also

- [[Trade Entry Criteria]] — Signal categories and weighting (greeks are 25% of composite signal)
- [[Position Sizing]] — How much to bet (greeks override when they conflict)
- [[Loss Limits]] — Price-based and time-based exits
- [[Alpaca API]] — Real-time options snapshots with greeks
- [[Polygon.io API]] — Historical greeks, IV rank, full chain data