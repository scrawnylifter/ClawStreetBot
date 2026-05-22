---
created: 2026-05-14
updated: 2026-05-22
tags: [strategy, swing-trading, mOC]
---

# Swing Trading Strategies

Hold periods: days to weeks. Capitalizing on short- to medium-term price movements.

These are my bread and butter. Quick in, quick out, defined risk.

## Risk Profile
→ All risk parameters at [[Position Sizing#Swing Trading]] and [[Loss Limits#Tiered Exit Strategy — Swing Trading (As Implemented)]]

| Parameter | Value |
|-----------|-------|
| Risk per trade | 10% |
| R:R minimum | 3:1 |
| Stop | ATR × 2.0 from entry |
| TP1 | ATR × 6.0 (3:1 R:R), sell **50%** |
| TP2 | ATR × 10.0 (5:1 R:R), then **trail at 2× ATR** |
| Time stop | ⚠️ Not implemented — no stale-swing timeout in code |
| Premium stop (options) | 50% of entry price |
| Max concurrent | 3-5 positions |
| Delta band (scanner) | 0.50–0.70 |
| Delta band (post-approval) | standard: 0.50–0.70, conservative: 0.55–0.65, aggressive: 0.40–0.80 |

> ⚠️ Swing mode TP2 **activates a trailing stop** at 2× ATR — it does NOT fully close. This is different from day mode which flattens at TP2.

**Code references:**
- Stop/TP: `02_scanner/scripts/scan_setups.py` ATR_STOP_MULT=2.0, ATR_TP1_MULT=6.0, ATR_TP2_MULT=10.0
- TP1 = 50%: `06_exit/scripts/exit_monitor.py` line 681: `partial_qty = qty_remaining // 2`
- TP2 trail-activate: `06_exit/scripts/exit_monitor.py` lines 300-310
- Trail distance = 2× ATR: `06_exit/scripts/exit_monitor.py` lines 183-202

## Strategies

| Strategy | Status | Description |
|----------|--------|-------------|
| [[EMA Crossover]] | Draft | 9/21 EMA crossover with ADX trend filter + volume confirmation |

---

See also: [[Laws of Trading]] | [[Trade Entry Criteria]] | [[Day Trading]] | [[Long-Term Holding]] | [[Risk Management]]