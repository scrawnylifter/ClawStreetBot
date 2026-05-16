---
created: 2026-05-16
updated: 2026-05-16
tags: [strategy, day-trading, mOC]
---

# Day Trading Strategies

Hold periods: minutes to hours. In-and-out same day. No overnight risk.

Fastest signal-to-execution cycle. The 30 DTE contract rule (Law 5) still applies — but that's insurance, not hold time. You might hold a 45 DTE call for 30 minutes.

## Risk Profile
→ All risk parameters at [[Position Sizing#Day Trading]] and [[Loss Limits#Tiered Exit — Day Trading]]

| Parameter | Value |
|-----------|-------|
| Risk per trade | 5% |
| R:R minimum | 3:1 |
| Stop type | ATR × 1.5 / opening range boundary |
| Max concurrent | 2-3 positions |
| Take-profit | 20% / 40% / flatten before close |
| Time stop | Flatten before market close |

## Strategies

| Strategy | Status | Description |
|----------|--------|-------------|
| [[ORB — Opening Range Breakout]] | Draft | 15/30-min opening range breakout on watchlist stocks |

## Key Rules for Day Trading

1. **Never carry overnight** — gap risk destroys the math
2. **Smaller per-trade risk than swing** — more trades per session means lower individual risk
3. **Volume is everything** — no volume confirmation = no entry
4. **Flatten before close** — no exceptions, no "I'll hold this one overnight"

## Pattern Day Trader (PDT) Rule

Account equity under $25K → **FINRA PDT rule applies.**

**The rule:** 3 or more day trades in 5 business days = flagged as a pattern day trader. 4th day trade in that window triggers a **PDT restriction** (90-day ban on day trading or until account meets $25K minimum equity).

**Our PDT management (account < $25K):**

|| Day Trade | Usage | Policy |
|-----------|-------|--------|
| 1st | ✅ Normal | Planned trade — good setup, all entry criteria met |
| 2nd | ✅ Cautious | Only if setup is strong — don't waste it on marginal setups |
| 3rd | ⚠️ Emergency only | Reserved for: exit-an-existing-position, hedge, or truly exceptional setup. NEVER for a new speculative entry |
| 4th | 🚫 NEVER | 4th PDT triggers the ban. No exceptions |

**Implementation rules:**
- Track PDT count in a rolling 5-business-day window
- At 2 day trades used: reduce new entries — only A+ setups with all criteria met
- At 3 day trades used: **PDT lock** — no new day trades until the oldest trade in the window ages out
- If the bot encounters a 3rd day trade scenario, it must be an **exit** (closing an existing position) or a **hedge** — never a new speculative entry
- Swing positions (held overnight) and long-term holds do NOT count as day trades
- Law 5 (30 DTE) still applies — the 3 DT limit is about entries, not contract duration

**PDT counter reset logic:**
```
pdt_count = count of day trades in last 5 business days
if pdt_count >= 3:
    PDT_LOCK = True  # no new day trades until oldest ages out
elif pdt_count == 2:
    PDT_CAUTION = True  # only A+ setups
```

> ⚠️ This rule will change if/when the account crosses $25K equity. Above $25K, PDT rule no longer applies — unlimited day trades allowed (still subject to risk management rules).

---

See also: [[Laws of Trading]] | [[Trade Entry Criteria]] | [[Swing Trading]] | [[Risk Management]]