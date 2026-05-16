---
created: 2026-05-15
updated: 2026-05-15
tags: [risk, correlation, diversification, sector, mOC]
---

# Correlation Risk — The Hidden Danger

> "Diversification is the only free lunch in finance." — Harry Markowitz

Individual position sizing and stop-losses protect you from **one trade going wrong**. Correlation risk is about **multiple trades going wrong at the same time** — because they're secretly the same trade.

---

## What Is Correlation Risk?

If you hold 5 semiconductor stocks, you don't have 5 positions. You have **one bet on semiconductors** with extra steps. When the sector drops, all 5 drop together.

**Correlation risk** is the risk that seemingly separate positions move together, amplifying drawdowns beyond what position sizing alone would allow.

---

## Our Watchlist Exposure

Looking at the [[Watchlist|current watchlist]] through a correlation lens:

### High Correlation Clusters

| Cluster | Stocks | Correlation Risk |
|---------|--------|-----------------|
| AI/GPU Semis | NVDA, AMD, MU | **VERY HIGH** — all move on same AI/GPU news |
| Data Center | NVDA, MU, IREN, APLD | **HIGH** — overlap with semi cluster |
| Space/Defense | RKLB, ASTS | **MODERATE** — different end markets, both growth |
| Nuclear/Energy | OKLO | **LOW** — unique thesis, limited overlap |
| Pharma | NVO | **LOW** — completely different sector |
| Social/Consumer | RDDT, SERV | **LOW-MODERATE** — consumer tech, different drivers |
| Storage/Hardware | WDC, STX | **HIGH** — storage cycle moves together |

### Sector Breakdown

| Sector | Stocks | % of Watchlist | Exposure Concern |
|--------|--------|---------------|-----------------|
| Technology / Semiconductors | NVDA, AMD, MU, APLD | 27% | ⛔ Overweight |
| Data Center / Infrastructure | NVDA, MU, IREN, APLD, NBIS, CIFR | 40% | ⛔ Extreme overlap |
| Space / Launch | RKLB, ASTS | 13% | 🟡 Moderate |
| Storage | WDC, STX | 13% | 🟡 Moderate |
| Nuclear / Energy | OKLO | 7% | 🟢 Diversified |
| Healthcare | NVO | 7% | 🟢 Diversified |
| Social / Consumer Tech | RDDT, SERV | 13% | 🟡 Moderate but differentiated |

> 40% of the watchlist is in data center / infrastructure. This is a **concentration time bomb** if AI capex slows.

---

## Correlation Limits

### Sector Concentration Cap

| Metric | Limit | Rationale |
|--------|-------|-----------|
| Single stock | 20% (Law 3) | Already defined |
| Single sector | 40% max | Prevents sector wipeout |
| Correlated cluster | 30% max | Limits "same trade" risk |
| Single country (non-US) | 20% max | Regulatory/political risk |

**How to check before entering a trade:**

```
Before new position:
  1. Identify sector of the stock
  2. Calculate current sector exposure (% of equity)
  3. If sector exposure + new position > 40% → REJECT or reduce
  4. Calculate pairwise correlation with existing positions
  5. If avg correlation > 0.7 with any cluster → flag for review
  6. If portfolio heat (sum of correlated risk) > limit → REJECT
```

### Pairwise Correlation Thresholds

| Correlation | Interpretation | Action |
|-------------|---------------|--------|
| 0.0 - 0.3 | Low correlation | Good diversifier — add if thesis supports |
| 0.3 - 0.5 | Moderate correlation | Acceptable — factor into sizing |
| 0.5 - 0.7 | High correlation | Caution — reduce size or skip |
| 0.7 - 1.0 | Very high correlation | Treat as same position — strict sector cap applies |

---

## Rolling Correlation Matrix

Maintain a **daily rolling correlation matrix** for all watchlist and held stocks using 60-day returns:

```python
import pandas as pd
import numpy as np

def correlation_matrix(returns_df: pd.DataFrame, window: int = 60) -> pd.DataFrame:
    """Calculate rolling 60-day pairwise correlation for all symbols."""
    return returns_df.rolling(window).corr().iloc[-1]

def portfolio_heat_map(corr_matrix: pd.DataFrame, positions: dict) -> pd.DataFrame:
    """Calculate correlated risk for each position pair."""
    heat = {}
    for sym_a in positions:
        for sym_b in positions:
            if sym_a >= sym_b:  # upper triangle only
                continue
            corr = corr_matrix.loc[sym_a, sym_b]
            if corr > 0.7:
                heat[(sym_a, sym_b)] = {
                    'correlation': round(corr, 3),
                    'combined_risk': positions[sym_a]['risk_pct'] + positions[sym_b]['risk_pct'],
                    'warning': 'HIGH' if corr > 0.8 else 'MODERATE'
                }
    return heat
```

### Visualization (Future)

```
Correlation Heatmap (sample — current watchlist):

         NVDA   AMD    MU    APLD  IREN  NBIS  RKLB  WDC   NVO   RDDT
NVDA    [1.00] [.88]  [.82]  [.78]  [.70]  [.65]  [.30]  [.55]  [.10]  [.25]
AMD     [.88]  [1.00]  [.80]  [.72]  [.62]  [.58]  [.28]  [.50]  [.08]  [.22]
MU      [.82]  [.80]  [1.00]  [.71]  [.68]  [.60]  [.25]  [.65]  [.12]  [.20]
APLD    [.78]  [.72]  [.71]  [1.00]  [.75]  [.62]  [.20]  [.55]  [.10]  [.18]
IREN    [.70]  [.62]  [.68]  [.75]  [1.00]  [.70]  [.18]  [.45]  [.08]  [.15]
NBIS    [.65]  [.58]  [.60]  [.62]  [.70]  [1.00]  [.15]  [.40]  [.05]  [.12]
RKLB    [.30]  [.28]  [.25]  [.20]  [.18]  [.15]  [1.00] [.20]  [.05]  [.30]
WDC     [.55]  [.50]  [.65]  [.55]  [.45]  [.40]  [.20]  [1.00] [.08]  [.15]
NVO     [.10]  [.08]  [.12]  [.10]  [.08]  [.05]  [.05]  [.08]  [1.00] [.05]
RDDT    [.25]  [.22]  [.20]  [.18]  [.15]  [.12]  [.30]  [.15]  [.05]  [1.00]

⛔  NVDA↔AMD = .88  (same trade)
⛔  NVDA↔MU = .82  (same trade)
⚠️  APLD↔IREN = .75  (data center cluster)
⚠️  MU↔WDC = .65  (semi→storage spillover)
```

This heatmap should be generated daily and stored in Postgres for trend analysis.

---

## Hedging Requirements

When correlation risk is unavoidable (e.g., strong thesis on semis), use hedges:

### Sector Hedge Strategies

| Strategy | Instrument | When to Use |
|----------|-----------|-------------|
| Sector ETF put | SMH, SOXX puts | High semi exposure, want tail protection |
| Index put | SPY or QQQ puts | Broad portfolio, want market crash protection |
| Inverse ETF | SH, SDS (short-term only) | Sharp sector decline expected |
| Pairs trade | Long strongest, short weakest | Within-correlated-cluster hedging |
| Cash raise | Sell partial positions | When correlation risk exceeds 40% cap |

### Hedging Rules

1. **Never hedge with more leverage** — a hedge reduces risk, not increases it
2. **Hedge cost ≤ 2% of portfolio per quarter** — if hedging costs more, reduce the position instead
3. **Hedges must have a defined exit** — same as any trade
4. **Prefer defined-risk hedges** — long puts over short calls for portfolio protection
5. **Review hedges weekly** — adjust or remove when correlation risk decreases

---

## Portfolio Risk Metrics

Monitor these in real-time (Redis → dashboard):

| Metric | Calculation | Alert Threshold |
|--------|------------|-----------------|
| **Portfolio Heat** | Σ(position_risk × correlation_adj) | > 15% of equity |
| **Max Sector Exposure** | Sum of same-sector positions | > 40% |
| **Avg Pairwise Correlation** | Mean of held-positions correlation | > 0.5 |
| **Concentration Index** | HHI of position weights | > 0.25 |
| **Beta to SPY** | Weighted portfolio beta | > 1.5 |

**Herfindahl-Hirschman Index (HHI):**
```
HHI = Σ(weight_i²)

Example: 5 equal positions (20% each)
HHI = 0.2² × 5 = 0.20  (moderate concentration)

Example: 1 position at 20%, 4 at 5% each, rest cash
HHI = 0.2² + 4×(0.05²) = 0.04 + 0.01 = 0.05  (well-diversified)
```

---

## Implementation Roadmap

### Phase 3b: Correlation Analysis
- [ ] Build daily correlation matrix from Alpaca bars (60-day rolling)
- [ ] Sector classification for watchlist stocks (GICS or manual)
- [ ] Portfolio heatmap generator (ASCII + future dashboard)
- [ ] Pre-trade correlation check (reject if sector > 40%)
- [ ] HHI and portfolio heat tracking in Postgres

### Phase 3c: Hedging Engine
- [ ] Sector ETF mapping (SMH for semis, FXI for China, etc.)
- [ ] Hedge cost calculator (put premiums vs. risk reduction)
- [ ] Auto-suggest hedging when sector exposure > 30%
- [ ] Hedge P&L tracking (separate from directional positions)

---

## Key Decisions & Open Questions

| Decision | Status | Notes |
|----------|--------|-------|
| Sector concentration cap | ⚠️ Initial | 40% — needs backtesting to confirm |
| Correlation threshold | ⚠️ Initial | 0.7 = very high, treat as same trade |
| Hedging approach | 🔜 TBD | Long puts vs. pairs trades vs. cash |
| Correlation window | ⚠️ Initial | 60-day rolling — may need 20/60/120 multi-window |
| Data center exposure | 🔜 TBD | 40% of watchlist in data center/AI infra — biggest concentration risk |

---

## See Also

- [[Risk Management]] — Overview of all risk pillars
- [[Position Sizing]] — Can't size correctly without accounting for correlation
- [[Loss Limits]] — Individual stops + portfolio circuit breakers
- [[Laws of Trading]] — Law 3 (20% cap), Law 7 (another opportunity always exists)
- [[Watchlist]] — Current tracked assets and their sectors