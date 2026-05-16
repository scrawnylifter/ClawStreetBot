---
created: 2026-05-14
updated: 2026-05-16
tags: [strategy, long-term, accumulation, mOC]
status: draft
---

# Buy the 5% Dip

## Status
- [x] Draft — not tested
- [ ] Paper — live on Alpaca Paper for ≥30 days
- [ ] Validated — proven positive expected value

## Category
[[Long-Term Holding]] — accumulate over months to years

## Trigger
From [[Trade Entry Criteria]]:
- A conviction stock on the watchlist pulls back ≥5% from a recent high
- No fundamental thesis change (this is NOT averaging down on a broken trade)

**The key distinction:**
- ❌ Averaging down = your trade thesis was wrong, stock keeps falling, you keep buying = Law 7 violation
- ✅ Buy the dip = your thesis is intact, the market is offering a discount = deliberate accumulation

## Setup (must be met before entry)

- [ ] Stock is on the watchlist with a documented long-term thesis
- [ ] Pulled back ≥5% from a recent local high (not all-time high necessarily)
- [ ] No fundamental reason for the drop (no earnings miss, no FDA rejection, no scandal)
- [ ] Thesis is still intact — the investment case hasn't changed
- [ ] Volume on the dip is normal or elevated (not panic selling in an unbroken stock)

## Entry
- **Order type:** Limit order at current price or slightly below
- **Timing:** Anytime — not time-sensitive like swing/day trades
- **Scale approach:** Don't buy the full position at once. Buy in 3rds:
  - 1/3 on the 5% dip
  - 1/3 on a further 5% dip (5% below your first entry)
  - 1/3 on confirmation of a rebound (price starts recovering)

## Stop-Loss (Law 2 — even long-term holds need one)
- **Stop type:** Thesis-based (not price-based)
- **Ask:** "Is the reason I bought this stock still true?"
- **If no → sell. If yes → hold or add.**
- See [[Position Sizing#Long-Term Holding]] for thesis invalidation model
- See [[Loss Limits]] for trailing stop after position is profitable

## Risk Parameters
→ All R:R, position sizing, take-profit, and drawdown limits are defined in [[Risk Management]] and [[Position Sizing#Long-Term Holding]]
- This strategy uses the **Long-Term Holding** risk profile
- Position sizing: **3-tranche scale-in** (~5-7% per tranche, max 15-20% total) (see [[Position Sizing]])
- Take-profit tiers: **50% / 100% / let it ride** (see [[Loss Limits#Tiered Exit — Long-Term Holding]])
- Drawdown tolerance: **30-40%** (see [[Position Sizing]])

## No DTE Requirement
This strategy is for shares, not options. Long-term equity accumulation.

## Research Checklist (Law 8)
- [ ] Long-term thesis documented (why do I believe in this stock?)
- [ ] Fundamentals checked: revenue growth, margins, competitive moat
- [ ] Sector outlook assessed
- [ ] Not averaging down on a broken thesis — thesis is STILL valid
- [ ] No earnings within 5 business days (Law 5 variation — don't buy right before earnings)

## Invalidation
Stop accumulating (and consider exiting) if:
- The fundamental thesis changes (e.g., company loses a major contract)
- Sector rotation away from your stock's industry
- You're buying out of emotion, not conviction (Law 1)

## Watchlist Stocks Best Suited For This

These have strong long-term theses that support accumulation:

| Symbol | Thesis | Approx. Buy Zone |
|--------|--------|-------------------|
| NVDA | AI/GPU dominance, data center buildout | 5%+ dips from highs |
| AMD | AI chip competition, MI300 ramp | 5%+ dips from highs |
| RKLB | Space launch growth, Neutron rocket | 5%+ dips from highs |
| IREN | Data center + Bitcoin mining infrastructure | 5%+ dips from highs |
| MU | Memory cycle upswing, HBM demand | 5%+ dips from highs |

## Backtest Results
- **Win Rate:** TBD
- **Profit Factor:** TBD
- **Max Drawdown:** TBD