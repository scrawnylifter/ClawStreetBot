---
created: 2026-05-16
updated: 2026-05-22
tags: [strategy, day-trading, mOC]
---

# Day Trading Strategies

Hold periods: minutes to hours. In-and-out same day. No overnight risk.

Fastest signal-to-execution cycle. The 30 DTE contract rule (Law 5) still applies — but that's insurance, not hold time. You might hold a 45 DTE call for 30 minutes.

## Risk Profile
→ All risk parameters at [[Position Sizing#Day Trading]] and [[Loss Limits#Tiered Exit Strategy — Day Trading (As Implemented)]]

| Parameter | Value |
|-----------|-------|
| Risk per trade | 5% |
| R:R minimum | 3:1 |
| Stop | ATR × 1.5 from entry |
| TP1 | ATR × 4.5 (3:1 R:R), sell **50%** |
| TP2 | ATR × 7.5 (5:1 R:R), **full close** (no trailing) |
| Time stop | **12:45 PDT** flatten all positions |
| Premium stop (options) | 50% of entry price |
| Max concurrent | 2-3 positions |
| Delta band (scanner) | 0.50–0.70 |
| Delta band (post-approval) | standard: 0.50–0.70, aggressive: 0.40–0.80 |

> ⚠️ Day mode TP2 is a **full close** — no trailing stop. Day trades flatten at the time stop (12:45 PDT) anyway.

**Code references:**
- Stop/TP: `02_scanner/scripts/detect_orb.py` RULES `stop_mult=1.5, tp1_mult=4.5, tp2_mult=7.5`
- TP1 = 50%: `06_exit/scripts/exit_monitor.py` line 681: `partial_qty = qty_remaining // 2`
- Time stop: `06_exit/scripts/exit_monitor.py` line 81: `TIME_STOP_LOCAL = time(12, 45)` (America/Los_Angeles)
- Premium stop: `06_exit/scripts/exit_monitor.py` line 84: `OPTION_PREMIUM_STOP_FRACTION = Decimal("0.50")`

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