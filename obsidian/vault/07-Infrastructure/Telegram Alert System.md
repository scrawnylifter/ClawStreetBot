---
created: 2026-05-17
updated: 2026-05-17
tags: [alerts, telegram, notifications, implementation-plan, mOC]
---

# Telegram Alert System — Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Push real-time trade signals and system alerts to your phone via Telegram, so you never miss an actionable setup.

**Architecture:** Python script reads from `trading.signals` + `trading.positions` + regime/trend tables, formats rich messages, and pushes via Telegram Bot API. Runs as part of the existing n8n pipeline after signal generation.

**Tech Stack:** Python 3.11, `requests` (HTTP to Telegram Bot API), `psycopg2`, n8n cron trigger

---

## Why This Matters

Right now, ClawStreetBot computes 7,968+ signals and runs 12 workflows daily — but none of that reaches you without manually querying the DB. By the time you check, the move might be over. This bridges the **last mile**: from computed signal → actionable notification on your phone.

## Alert Types

### Priority 1 — Trade Signals (highest urgency)
| Alert | Trigger | Example |
|-------|---------|---------|
| New bullish signal | `composite_score > 60` on daily signal | "BULL OKLO score=63.5 (moderate) — IV-RV spread=-0.35, neg GEX" |
| New bearish signal | `composite_score > 60` AND `signal_type='bearish'` | "BEAR SERV score=62.0 (moderate) — full bear trend, IV rich" |
| Intraday signal upgrade | Intraday composite crosses above threshold during market hours | "INTRADAY UPGRADE: RDDT 55→63 (+8) — tech momentum surge" |
| Intraday signal downgrade | Intraday composite drops sharply | "INTRADAY DOWNGRADE: NVDA 55→22 (-33) — RSI crash" |

### Priority 2 — Risk Alerts (safety-critical)
| Alert | Trigger | Example |
|-------|---------|---------|
| Drawdown halt | Portfolio daily DD > 10%, weekly > 20%, monthly > 30% | "HALT: Daily drawdown 12.3% — new trades blocked until tomorrow" |
| PDT warning | 2nd day trade used in rolling window | "PDT: 2/3 day trades used — cautious mode, A+ setups only" |
| PDT lock | 3rd day trade used | "PDT LOCK: 3/3 day trades — emergency only for rest of window" |
| Position size breach | Any position > 20% of portfolio | "BREACH: NVDA position at 24% equity — exceeds 20% Law 3 cap" |

### Priority 3 — Pipeline Health (operational awareness)
| Alert | Trigger | Example |
|-------|---------|---------|
| Daily pipeline complete | All 12 workflows finished for the day | "Pipeline complete — 16 signals scored, 3 actionable" |
| Pipeline failure | Any n8n workflow execution fails | "FAIL: derived_daily workflow error — check n8n UI" |
| Data freshness warning | No new OHLCV data for 24h on any active symbol | "STALE: No OHLCV update for MU in 26h" |

### Priority 4 — Regime & Trend (context shifts)
| Alert | Trigger | Example |
|-------|---------|---------|
| Regime change | Today's regime differs from yesterday's | "REGIME SHIFT: transition → bull" |
| Trend flip | Symbol trend_status primary direction changes | "TREND FLIP: OKLO primary bear → bull" |
| IV regime boundary | IV rank crosses 25%/50%/75% thresholds | "IV THRESHOLD: NVDA IV rank 23% → 26% (enter normal zone)" |

## Message Format

### Trade Signal Message
```
🟢 BULL OKLO | 63.5/100 | moderate
IV-RV: -0.35 (cheap premium)
GEX: -1.2M (neg, amplified moves)
Trend: micro↑ inter↑ primary↓ (trap risk!)
Regime: transition
Strategy: swing call, delta 0.50-0.70
```

### Risk Alert Message
```
🛑 PDT WARNING | 2/3 day trades used
Window resets: May 21
Next trade: cautious mode — A+ setups only
3rd trade = emergency-only (exit/hedge)
```

### Pipeline Health Message
```
✅ PIPELINE COMPLETE | May 16, 2026
Signals: 16 generated, 3 actionable (score > 60)
Actionable: OKLO 63.5, RDDT 63.0, NVO 57.8
Top bearish: SERV 38.2
Next regime update: Saturday
```

## Implementation Tasks

### Task 1: Create Telegram Bot + Store Credentials
- Create bot via @BotFather on Telegram → get API token
- Get chat ID (message the bot, then hit `getUpdates` endpoint)
- Store in `.env.telegram`: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
- Add `.env.telegram.example` to repo

### Task 2: Create `scripts/alert_telegram.py`
- Read latest signals from `trading.signals` (today's date)
- Read regime from `market.regime`, trend from `market.trend_status`
- Format messages per templates above
- Send via `requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={...})`
- Support `--mode signal` (trade signals), `--mode risk` (risk alerts), `--mode health` (pipeline), `--mode all`
- Support `--threshold 60` to filter by composite score
- Support `--dry-run` to print messages without sending

### Task 3: Add Alert Deduplication
- Track last alert per symbol in `trading.alert_history` (new table)
- Columns: `id, symbol, alert_type, message_hash, sent_at`
- Only send if: (a) new signal for symbol today, OR (b) composite score changed by >10 points since last alert, OR (c) risk/pipeline alert (no dedup — always send)
- Prevents spam on re-runs or unchanged signals

### Task 4: Create n8n Workflow `alerts_daily.json`
- Triggers after `signals_daily` completes (chain dependency)
- Runs `alert_telegram.py --mode all`
- Mon-Fri 19:35 ET (5 min after signals)
- Add to n8n workflows list

### Task 5: Create n8n Workflow `alerts_intraday.json`
- Triggers from `intraday_signal_5m` output
- Only sends if intraday composite crosses threshold OR changes >10 points from daily
- Mon-Fri during market hours
- Separate workflow to control rate limiting

### Task 6: Add Risk Alert Hooks
- Modify `backtest.py` to emit risk events on drawdown halts / PDT violations
- Modify `intraday_signal.py` to emit PDT counter changes
- Alert script reads these events and formats risk messages
- Risk alerts are ALWAYS sent (no dedup, no threshold filtering)

### Task 7: Create DB Migration `11_alert_history.sql`
```sql
CREATE TABLE trading.alert_history (
    id          SERIAL PRIMARY KEY,
    symbol      VARCHAR(10),
    alert_type  VARCHAR(20) NOT NULL,  -- signal, risk, health, regime, trend
    message_hash VARCHAR(64),
    alert_data  JSONB,                 -- full alert payload for debugging
    sent_at     TIMESTAMPTZ DEFAULT NOW(),
    delivered   BOOLEAN DEFAULT TRUE
);
CREATE INDEX idx_alert_history_symbol ON trading.alert_history(symbol);
CREATE INDEX idx_alert_history_sent ON trading.alert_history(sent_at);
```

### Task 8: Rate Limiting
- Telegram Bot API: max 30 messages/second, 20 messages/minute to same group
- Our volume: at most 16 signals + 1 pipeline + occasional risk alerts = ~20 messages/day
- Not a concern at daily scale, but intraday alerts could burst
- Implement: `time.sleep(1)` between messages in a batch, max 5 alerts per 60-second window

## Verification Steps
1. `python scripts/alert_telegram.py --dry-run --mode all` → prints all formatted messages
2. `python scripts/alert_telegram.py --mode health` → sends pipeline health to your phone
3. Confirm delivery on Telegram app
4. Test dedup: run signal alerts twice → second run should skip unchanged symbols
5. Test risk: simulate drawdown halt → confirm alert arrives immediately

## Security
- Bot token in `.env.telegram` (gitignored)
- `.env.telegram.example` committed with placeholder
- Bot can only SEND messages to your chat ID — it cannot read your messages or access any other chats
- No webhook needed (polling/push from our side only)

## See Also
- [[Order Execution Engine]] — alerts trigger execution in Phase 5
- [[Monitoring & Dashboards]] — alert history feeds into dashboards
- [[n8n Scheduler]] — workflow chaining for alert delivery
- [[Laws of Trading]] — risk alerts enforce Law 2, Law 3, PDT rules