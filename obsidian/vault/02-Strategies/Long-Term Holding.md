---
created: 2026-05-14
updated: 2026-05-22
tags: [strategy, long-term, mOC]
---

# Long-Term Holding Strategies

> ⚠️ **Not implemented in code.** `infer_trade_mode()` in `process_approved.py` never returns `long_term` — no scanner produces long-term signals. RISK_PCT defines `long_term: 0.05` per-tranche, but it's unreachable. Everything below is a design target, not a live system.

Hold periods: months to years. Building positions over time.

These are my conviction plays. I believe in the thesis, so I accumulate on weakness.

## Risk Profile
→ All risk parameters at [[Position Sizing#Long-Term Holding]] and [[Loss Limits#Tiered Exit Strategy — Long-Term Holding (Aspirational Only)]]

| Parameter | Value | Status |
|-----------|-------|--------|
| Risk tolerance | 30-40% drawdown per position | Aspirational |
| R:R | Not measured the same way — hold until thesis plays out | Aspirational |
| Stop type | Thesis invalidation ("is my reason for buying still true?") | Aspirational |
| Entry method | 3-tranche scale-in on dips (~5-7% per tranche) | Aspirational |
| Max position (full build) | 15-20% | Aspirational |
| Take-profit | 50% / 100% / ride to 200%+ | Aspirational |
| Time stop | N/A — thesis-based exit, not time-based | Aspirational |

## Core Rule

**Buy the 5% dip.** Every time a conviction stock pulls back 5% from a recent high, add to the position. This is not averaging down on a losing trade (Law 7 forbids that for swing trades) — this is deliberate accumulation of a long-term holding you already believe in.

The distinction matters:
- **Averaging down** = adding to a swing trade that's going against you = ❌ Law 7 violation
- **Buying the 5% dip** = accumulating a long-term position at better prices = ✅ different strategy entirely

## Strategies

| Strategy | Status | Description |
|----------|--------|-------------|
| [[Buy the 5% Dip]] | Draft | Accumulate conviction stocks on 5% pullbacks |

---

See also: [[Laws of Trading]] | [[Trade Entry Criteria]] | [[Swing Trading]]