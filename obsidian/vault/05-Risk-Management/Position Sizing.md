---
created: 2026-05-15
updated: 2026-05-15
tags: [risk, position-sizing, capital, mOC]
---

# Position Sizing — How Much to Bet

> "The most important thing is to never go bust. If you never go bust, you always have a chance." — Ed Seykota

Position sizing answers one question: **how much capital do I put on this trade?**

It's not about conviction — it's about math. Every position size is calculated from account equity, risk tolerance, and the trade's stop distance. And different strategies need different sizing.

---

## Hard Cap: Law 3

[[Laws of Trading#Law 3: Never More Than 20% in a Single Position|Law 3]] sets the **absolute maximum**:

> No more than 20% of capital in any single position.

This is a hard ceiling, not a target. Most positions should be well below it.

---

## Strategy-Specific Risk Profiles

Day trades, swing trades, and long-term holds have fundamentally different risk profiles. They need different sizing.
**This is the single source of truth — strategy playbooks reference these, they do NOT define their own.**

### Day Trading

In-and-out same day or next day. Fast pace, more trades per session, tight stops.

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Risk per trade | **5%** | More trades per session = lower per-trade risk |
| Reward:Risk minimum | **3:1** | Same floor — no exceptions |
| Stop type | ATR × 1.5 (tight) or opening range boundary | Fast exit — no room to breathe |
| Position cap | 20% (Law 3) | Hard ceiling |
| Max concurrent positions | 2-3 | Can't watch more in real-time |
| Take-profit | See [[Loss Limits#Tiered Exit — Day Trading]] | |
| Win rate to break even | **25%** | At 3:1 R:R |
| Time stop | Flatten before market close | No overnight gap risk |

> **The 30 DTE rule still applies** (Law 5) — contracts must have ≥30 DTE, but that's insurance, not hold time. You might be in the trade for 30 minutes.

### Swing Trading

Quick in, quick out. Higher risk per trade, tighter stops, defined targets.

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Risk per trade | **10%** | Aggressive — each winner pays for ~3 losers at 3:1 |
| Reward:Risk minimum | **3:1** | Risk $1 to make $3 |
| Stop type | ATR × 2.0 (standard) or strategy-specific | Quick exit if thesis invalid |
| Position cap | 20% (Law 3) | Hard ceiling |
| Max concurrent positions | 3-5 | Limited focus |
| Take-profit | See [[Loss Limits#Tiered Exit — Swing Trading]] | |
| Win rate to break even | **25%** | At 3:1 R:R |

### Long-Term Holding

Months to years. Conviction plays — you believe in the company and are willing to sit through drawdowns.

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Risk tolerance | **30-40% drawdown** per position | Conviction trades — temporary swings are noise |
| Entry method | 3-tranche scale-in on dips | Buy the 5% Dip — average into positions |
| Max position (full build) | 15-20% (3 tranches × 5-7%) | Conviction justifies concentration |
| Hard stop | Thesis invalidation | Not a % number — sell when the reason you bought is gone |
| Take-profit | See [[Loss Limits#Tiered Exit — Long-Term Holding]] | |
| R:R | Not measured the same way | Upside is 100%+, downside tolerance is 30-40% |

**Why long-term is different:**
- You're **buying on pullbacks** — entering at a discount, not chasing
- **Time is on your side** — a 30% drawdown on NVDA over 3 months doesn't matter if your thesis is 12+ months
- **"Buying the dip" ≠ "averaging down"** — you have conviction, a 3-tranche entry plan, and patience
- The stop isn't a price level, it's a **thesis level**: sell when the reason you bought is no longer true

---

## The Math: Why Risk ≠ Position Size

**Risk** and **position size** are two different numbers connected by your stop distance:

```
Position Size = (Account Equity × Risk%) / Stop Distance ($)
```

10% risk does **NOT** mean 10% of your account goes into the trade. It means you **lose** 10% if your stop hits.

### Example: $1,000 Account, Swing Trade

Stock at $50, ATR×2.0 stop at $47 (6% stop distance, $3 risk/share):

```
Risk budget: $1,000 × 10% = $100
Position size: $100 / $3 = 33 shares = $1,650

❌ EXCEEDS Law 3 cap → capped to 4 shares = $200 (20%)
Actual risk: 4 × $3 = $12 (1.2% of equity)
```

**The 20% cap overrides the 10% risk target on small accounts.** This is intentional — you grow into the full risk level as your account grows.

### Example: $5,000 Account, Swing Trade

Same stock, same stop:

```
Risk budget: $5,000 × 10% = $500
Law 3 cap: $5,000 × 20% = $1,000 max position
Position size: 20 shares = $1,000 (at cap)
Actual risk: 20 × $3 = $60 (1.2% of equity)
```

Still cap-constrained, but actual risk is meaningful.

### Example: $10,000 Account, Swing Trade

```
Risk budget: $10,000 × 10% = $1,000
Position size: $1,000 / $3 = 33 shares = $1,650

✅ Law 3 cap = $2,000 — position fits!
Actual risk: $1,000 (10% of equity) — full risk target achieved
```

**Account size where 10% risk and 20% cap align:**
```
Break-even: when (Equity × 10%) / StopDistance × Price ≤ Equity × 20%
Requires: StopDistance / Price ≥ 50% ... which never happens for normal stops

Reality: On small accounts, Law 3 caps actual risk below 10%. This protects you.
As the account grows, you naturally grow into the full 10% risk level.
```

---

## Position Sizing Methods

### Method 1: Fixed-Fractional (Primary)

The default method. Risk a fixed percentage of total equity per trade, adjusted by strategy.

**Swing Trading:**
```
Position Size = (Account Equity × 10%) / (Entry Price - Stop-Loss Price)
```

**Long-Term Holding (3-Tranche Scale-In):**
```
Tranche 1: (Account Equity × 5%) / Entry Price → Initial position on dip
Tranche 2: Add on further dip (scale in)
Tranche 3: Add on deeper dip (scale in)
Max total: 15-20% of equity across all tranches
```

Long-term holds don't use a traditional stop — the stop is **thesis invalidation**, not a price level.

| Strategy | Risk/Tolerance | R:R | Max Consecutive Losses to 30% Drawdown |
|----------|---------------|-----|------------------------------------------|
| Swing | 10% per trade | 3:1 | 3 losses |
| Long-term | 30-40% drawdown tolerance | Not rigid — hold until thesis plays out | N/A (conviction model) |

### Method 2: ATR-Based (For Swing Trades)

Use Average True Range (ATR) to set stop distance dynamically.

```
Swing:  Position Size = (Equity × 10%) / (ATR × 2.0)
```

| Strategy | ATR Multiplier | Stop Distance | R:R Target |
|----------|---------------|---------------|------------|
| Swing (tight) | 1.5× | Narrow | 3:1 |
| Swing (normal) | 2.0× | Standard | 3:1 |

> Long-term holds don't use ATR stops — they use 3-tranche dip buying with thesis invalidation as the stop.

**Example (Swing, $10K account):**
- ATR(14) on NVDA: $5.20
- Multiplier: 2.0 → stop distance = $10.40
- Position size: $1,000 / $10.40 = 96 shares ($48,000) → **capped by Law 3 to 4 shares** at $500/share
- Actual risk at 4 shares: 4 × $10.40 = $41.60 (0.4%)

> ⚠️ On expensive stocks with wide ATR, the 20% cap severely limits position size. This is working as designed — expensive + volatile = smaller position.

### Method 3: Kelly Criterion (Advisory Only)

Kelly gives the mathematically optimal bet size. **Advisory only — we never size at full Kelly.**

```
Kelly % = W - [(1 - W) / R]

Where:
  W = win rate (historical)
  R = average win / average loss (reward-to-risk ratio)
```

**Swing example (3:1 R:R, 35% win rate):**
- Kelly = 0.35 - [(1 - 0.35) / 3.0] = 0.35 - 0.217 = **13.3%** → half-Kelly = 6.7%

**Long-term example (conviction model, no rigid R:R):**
- Kelly doesn't apply cleanly — you're holding based on thesis, not a fixed R:R target

**Usage:**
- If Kelly < 5%, the trade has no edge — skip it
- Use **half-Kelly** as a sanity check against our fixed-fractional size
- Full Kelly is too aggressive for real trading

### Method 4: Options-Specific Adjustments

Options have different risk characteristics:
- **Max loss = full premium paid** (for long options)
- **IV impact on stop placement** — traditional stops don't work well
- **Delta and gamma** — position delta equivalents, not just share count

**Swing (options):**
```
Max Premium = Equity × 10%
Max Contracts = Max Premium / (Premium × 100)
```

**Long-term (options):**
```
Max Premium = Equity × 5%
Max Contracts = Max Premium / (Premium × 100)
```

**Overrides (both strategies):**
- Never exceed Law 3's 20% cap on total premium paid
- Maximum notional value (strike × 100 × contracts) shouldn't exceed 2× account equity
- Per [[Laws of Trading#Law 5: No Short-Dated Options|Law 5]], minimum 30 DTE

---

## Sizing Decision Flow

```
Signal received
      │
      v
Identify strategy type
      ├── Day → 5% risk, 3:1 R:R, ATR × 1.5 stop
      ├── Swing → 10% risk, 3:1 R:R, ATR × 2.0 stop
      └── Long-term → 3-tranche scale-in, thesis-based stop, 30-40% drawdown tolerance
      │
      v
Calculate position size
      ├── Swing: (Equity × 10%) / Stop Distance
      └── Long-term: Equity × 5-7% per tranche (3 tranches max)
      │
      v
Check against hard limits
      ├── > 20% of equity? → cap at 20% (Law 3)
      ├── Exceeds max shares/contracts? → reduce
      └── Sector concentration > cap? → reduce or reject
      │
      v
Check R:R ratio (swing only)
      ├── Potential profit / risk ≥ 3:1?
      └── If insufficient R:R → SKIP
      │
      v
Verify Kelly sanity check (swing only)
      ├── Kelly < 5%? → SKIP (no edge)
      └── Kelly OK? → PROCEED with fixed-fractional size
      │
      v
Submit bracket order with stop-loss (swing) or scale-in order (long-term)
```

---

## Sizing Parameters Summary

| Parameter | Day Trading | Swing Trading | Long-Term Holding |
|-----------|-------------|---------------|-------------------|
| Risk per trade | 5% | 10% | 30-40% drawdown tolerance |
| R:R minimum | 3:1 | 3:1 | Not rigid — hold until thesis plays out |
| Win rate (breakeven) | 25% | 25% | N/A (conviction model) |
| Stop method | ATR × 1.5 / opening range | ATR × 2.0 | Thesis invalidation only |
| Entry method | Single entry | Single entry | 3-tranche scale-in on dips |
| Max position | 20% (Law 3) | 20% (Law 3) | 15-20% (full build across tranches) |
| Max concurrent | 2-3 positions | 3-5 positions | 2-3 positions |
| TP structure | See [[Loss Limits]] | See [[Loss Limits]] | See [[Loss Limits]] |
| Kelly usage | Half-Kelly sanity check | Half-Kelly sanity check | Not applicable |
| Options max premium | 5% of equity | 10% of equity | 5-7% per tranche |
| Options max notional | 2× account equity | 2× account equity | 2× account equity |
| Time stop | Flatten before close | 5-10 trading days (close if thesis not playing out) | N/A |

---

## Implementation

### Alpaca Integration

```python
def calculate_position_size(
    account_equity: float,
    strategy: str,            # "day", "swing", or "long_term"
    entry_price: float,
    stop_price: float,
    max_pct: float = 0.20,    # Law 3 cap
) -> dict:
    """Calculate position size using strategy-specific risk profiles.
    
    Day: 5% risk, 3:1 R:R, tight stops
    Swing: 10% risk, 3:1 R:R, single entry
    Long-term: 3-tranche scale-in, thesis-based stop, 30-40% drawdown tolerance
    """
    
    if strategy == "day":
        risk_pct = 0.05
        min_rr = 3.0
        dollar_risk = account_equity * risk_pct
        risk_per_share = entry_price - stop_price
        
        if risk_per_share <= 0:
            return {"error": "Stop price must be below entry price"}
        
        raw_shares = dollar_risk / risk_per_share
        raw_value = raw_shares * entry_price
        
        # Apply Law 3 cap
        max_value = account_equity * max_pct
        max_shares = max_value / entry_price
        
        final_shares = int(min(raw_shares, max_shares))
        final_value = final_shares * entry_price
        actual_risk = final_shares * risk_per_share
        
        return {
            "strategy": "day",
            "shares": final_shares,
            "dollar_value": round(final_value, 2),
            "pct_of_equity": round(final_value / account_equity * 100, 2),
            "risk_dollars": round(actual_risk, 2),
            "risk_pct_actual": round(actual_risk / account_equity * 100, 2),
            "min_rr": min_rr,
            "law3_capped": raw_shares > max_shares,
        }
    
    elif strategy == "swing":
        risk_pct = 0.10
        min_rr = 3.0
        dollar_risk = account_equity * risk_pct
        risk_per_share = entry_price - stop_price
        
        if risk_per_share <= 0:
            return {"error": "Stop price must be below entry price"}
        
        raw_shares = dollar_risk / risk_per_share
        raw_value = raw_shares * entry_price
        
        # Apply Law 3 cap
        max_value = account_equity * max_pct
        max_shares = max_value / entry_price
        
        final_shares = int(min(raw_shares, max_shares))
        final_value = final_shares * entry_price
        actual_risk = final_shares * risk_per_share
        
        return {
            "strategy": "swing",
            "shares": final_shares,
            "dollar_value": round(final_value, 2),
            "pct_of_equity": round(final_value / account_equity * 100, 2),
            "risk_dollars": round(actual_risk, 2),
            "risk_pct_actual": round(actual_risk / account_equity * 100, 2),
            "min_rr": min_rr,
            "law3_capped": raw_shares > max_shares,
        }
    
    elif strategy == "long_term":
        # 3-tranche scale-in model
        tranche_pct = 0.07  # 5-7% per tranche
        max_tranches = 3
        total_max_pct = 0.20  # Law 3 absolute max
        
        tranche_size = account_equity * tranche_pct
        total_build = min(account_equity * total_max_pct, tranche_size * max_tranches)
        
        return {
            "strategy": "long_term",
            "tranche_size": round(tranche_size, 2),
            "tranches": max_tranches,
            "total_build": round(total_build, 2),
            "pct_per_tranche": round(tranche_pct * 100, 1),
            "total_pct": round(total_build / account_equity * 100, 1),
            "stop_type": "thesis_invalidation",
            "drawdown_tolerance": "30-40%",
        }
```

---

## See Also

- [[Risk Management]] — Overview of all risk pillars
- [[Laws of Trading]] — Law 3 (20% cap), Law 4 (realize gains)
- [[Loss Limits]] — Stop-losses and drawdown caps (where stops come from)
- [[Correlation Risk]] — Why sizing in isolation isn't enough
- [[Swing Trading]] — Quick in/out strategy details
- [[Long-Term Holding]] — Buy the dip, hold for months
- [[Alpaca API]] — Bracket orders, position tracking