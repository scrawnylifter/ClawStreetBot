---
created: 2026-05-15
updated: 2026-05-16
tags: [risk, management, mOC]
---

# 🛡️ Risk Management — Protecting Capital

Risk management is what keeps you in the game. Strategies make you money — risk management makes sure you don't lose it all first.

**Logic flow:** [[Laws of Trading]] (rules) → [[Trade Entry Criteria]] (when/why) → [[Strategies]] (how) → **Risk Management** (how much, when to stop) → Execution

Every position must have a defined risk framework *before* entry. No exceptions.

---

## The Three Pillars

| Pillar | Document | What It Controls |
|--------|----------|-----------------|
| [[Position Sizing]] | How much capital per trade | Bet size, capital allocation, variance control |
| [[Loss Limits]] | When to exit and when to stop trading | Stop-losses, drawdown caps, circuit breakers |
| [[Correlation Risk]] | How positions relate to each other | Sector overlap, portfolio correlation, concentration risk |

---

## Core Principles

1. **Capital preservation over capital appreciation** — the first job is to not blow up
2. **Risk is defined before entry** — if you can't articulate the max loss, you don't take the trade
3. **Different strategies, different risk profiles** — day, swing, and long-term each have their own parameters
4. **No single trade matters more than the system** — a 20% max position size means nothing if five 20% positions all crash together
5. **Drawdowns kill compounding** — a 50% drawdown requires a 100% gain to recover. Avoid the deep hole.
6. **Correlation is the hidden risk** — five "different" trades in semis are really one trade

---

## Strategy Risk Profiles (Single Source of Truth)

These are the **authoritative** risk parameters for each strategy category.
All strategy playbooks reference this table — they do NOT define their own R:R, sizing, or TP.

### Day Trading

Hold: minutes to hours. In-and-out same day or next day. 30 DTE minimum on contracts is insurance, not hold time.

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Risk per trade | **5%** | Fast pace = more trades = need lower per-trade risk |
| Reward:Risk minimum | **3:1** | Same floor as swing — no exceptions |
| Stop type | ATR × 1.5 (tight) or opening range boundary | Fast exit — no room to breathe |
| Position cap | 20% (Law 3) | Hard ceiling |
| Max concurrent positions | 2-3 | Can't watch more in real-time |
| Take-profit | See [[Loss Limits#Tiered Exit — Day Trading]] | |
| Win rate to break even | **25%** | At 3:1 R:R |
| Time stop | Close or flatten before market close | No overnight risk |

> **Key difference from swing:** Day trades have no overnight gap risk. The 30 DTE contract gives you room if you're wrong on timing, but you're typically out same-day.

### Swing Trading

Hold: days to weeks. Quick in, quick out, defined risk.

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

Hold: months to years. Conviction plays — you believe in the company and are willing to sit through drawdowns.

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

## How Risk Management Connects to the Rest

```
┌──────────────────────────────────────────────────────────────┐
│                     LAWS OF TRADING                            │
│  Law 3: Max 20% per position                                  │
│  Law 4: Realize gains (30-50%)                                │
│  Law 5: No options under 30 DTE                               │
└──────────────┬───────────────────────────────────────────────┘
               │ constrains
               v
┌──────────────────────────────────────────────────────────────┐
│                   RISK MANAGEMENT                              │
├──────────────────┬──────────────────┬────────────────────────┤
│  POSITION SIZING │   LOSS LIMITS    │  CORRELATION RISK      │
│                  │                  │                        │
│  Risk per trade  │  Stop-losses     │  Sector caps           │
│  Kelly criterion │  Daily drawdown │  Pair correlations     │
│  Fixed fractional│  Weekly limits  │  Portfolio heat map    │
│  ATR-based       │  Circuit breakers│  Hedging requirements  │
└──────┬───────────┴────────┬─────────┴──────────┬─────────────┘
       │                    │                     │
       └────────────────────┼─────────────────────┘
                            │ feeds into
                            v
┌──────────────────────────────────────────────────────────────┐
│                    TRADE EXECUTION                             │
│  Alpaca API → bracket orders, stop orders, position tracking  │
└──────────────────────────────────────────────────────────────┘
```

---

## Implementation Phases

### Phase 2e: Position Sizing Engine
- [ ] Implement fixed-fractional position sizing module
- [ ] ATR-based stop distance calculation
- [ ] Kelly criterion calculator (advisory, not auto-size)
- [ ] Integration with Alpaca account equity API

### Phase 3a: Loss Limit Enforcement
- [ ] Hard stop-loss on every order (bracket order via Alpaca)
- [ ] Daily drawdown monitor (Redis real-time P&L)
- [ ] Weekly drawdown circuit breaker (halts new trades)
- [ ] Loss log and recovery tracking in Postgres

### Phase 3b: Correlation Analysis
- [ ] Rolling correlation matrix for watchlist (daily returns)
- [ ] Sector/industry exposure monitoring
- [ ] Portfolio heatmap (which positions move together)
- [ ] Auto-reject trades that push sector exposure > threshold

---

## Key Decisions & Open Questions

| Decision | Status | Notes |
|----------|--------|-------|
| Position sizing method | ✅ Strategy-specific | Day: 5% risk / 3:1, Swing: 10% risk / 3:1, Long-term: 3-tranche conviction model |
| Max risk per trade | ✅ 5% (day) / 10% (swing) / 30-40% drawdown (long-term) | Law 3 (20% cap) often reduces effective risk on small accounts |
| Daily drawdown limit | ✅ 10% daily, 20% weekly, 30% monthly | Applies across all strategy types |
| Day trading risk | ✅ 5% per trade | More trades per session = lower per-trade risk |
| Sector concentration cap | 🔜 TBD | Likely 40-50% max in one sector |
| Correlation threshold | 🔜 TBD | Reject entry if portfolio correlation > 0.7 |

---

## See Also

- [[Laws of Trading]] — Non-negotiable rules (Law 3 caps position size)
- [[Trade Entry Criteria]] — What triggers a trade
- [[Position Sizing]] — How much to bet on each trade
- [[Loss Limits]] — Stop-losses, drawdown caps, circuit breakers
- [[Correlation Risk]] — Hidden correlations, sector caps, hedging
- [[Alpaca API]] — Order types (bracket, stop, limit)