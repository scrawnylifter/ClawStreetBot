---
created: 2026-05-17
updated: 2026-05-17
tags: [monitoring, dashboards, observability, implementation-plan, mOC]
---

# Monitoring & Dashboards — Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Give you a live window into ClawStreetBot's operations — positions, P&L, signal performance, pipeline health, and risk metrics — without needing to query the database manually.

**Architecture:** Lightweight web dashboard served from the ClawStreetBot VM, backed by Postgres views and materialized tables. Read-only queries power all views. No write access from dashboard. Optional: Grafana for advanced time-series visualization.

**Tech Stack:** Python (FastAPI or Flask), HTML/CSS/JS, Postgres materialized views, optional Grafana + InfluxDB for time-series

---

## Why This Matters

Right now, the only way to see what ClawStreetBot is doing is raw SQL queries. You should be able to glance at a screen and know:
- Are we in a trade? What's the P&L?
- Did the pipeline run today? Any failures?
- What's the PDT counter? How many day trades left?
- Which signals are actionable? Which are stale?
- Is the market regime shifting? Are trends flipping?

This is operational visibility — not a nice-to-have, a requirement for running a trading bot with real risk.

## Dashboard Views

### View 1: Positions & P&L (most important)
| Metric | Source | Refresh |
|--------|--------|---------|
| Open positions | `trading.positions WHERE status='open'` | 1 min |
| Unrealized P&L | Alpaca position data + entry price | 1 min |
| Daily P&L | Sum of realized + unrealized today | 1 min |
| Win rate (7d) | `trading.positions` closed in last 7 days | 5 min |
| Portfolio equity | Alpaca account endpoint | 1 min |
| Available capital | Equity - sum of open positions | 1 min |
| Max position % | Largest open position as % of equity | 1 min |

**Visual layout:**
```
┌─────────────────────────────────────────────────┐
│ PORTFOLIO: $1,247.32  |  Day P&L: +$23.15 (+1.9%) │
│ Available: $892.40    |  Win Rate (7d): 64%      │
├─────────────────────────────────────────────────┤
│ OPEN POSITIONS                                    │
│ OKLO  Call 45DTE  +$42.30 (+18.5%)  [TP1 hit]    │
│ RDDT  Stock       +$12.10 (+4.2%)   [trailing]   │
│ NVO   Put 60DTE   -$8.20 (-3.1%)    [above SL]   │
├─────────────────────────────────────────────────┤
│ PDT COUNTER: 1/3 | Drawdown: 2.1% daily         │
└─────────────────────────────────────────────────┘
```

### View 2: Signal Dashboard
| Metric | Source | Refresh |
|--------|--------|---------|
| Today's signals | `trading.signals WHERE signal_date=TODAY` | 5 min |
| Actionable signals | Score > 60 | 5 min |
| Top bullish / bearish | Sorted by composite score | 5 min |
| Signal history heatmap | Last 30 days, score by symbol | Daily |
| Signal accuracy | Backtested vs actual P&L for signals that traded | Daily |
| Intraday score changes | `intraday_signal.py` deltas | 1 min |

**Visual layout:**
```
┌─────────────────────────────────────────────────┐
│ TODAY'S SIGNALS (May 16)                          │
│ 🟢 OKLO  63.5  moderate | 🔴 SERV  38.2  cautious │
│ 🟢 RDDT  63.0  moderate | ⚪ ASTS  35.0  no_trade │
│ 🟢 NVO   57.8  moderate | ⚪ WDC   42.1  cautious │
├─────────────────────────────────────────────────┤
│ REGIME: transition | IV: normal | GEX: mixed     │
├─────────────────────────────────────────────────┤
│ 30-DAY ACCURACY                                   │
│ Signals > 60: 72% profitable (18/25 trades)      │
│ Signals > 70: 85% profitable (11/13 trades)      │
└─────────────────────────────────────────────────┘
```

### View 3: Market Context
| Metric | Source | Refresh |
|--------|--------|---------|
| Current regime | `market.regime` latest row | Daily |
| IV rank by symbol | `market.iv_rank` | Daily |
| GEX overview | `market.gex_dex_overview` | Daily |
| RV vs IV spread | `market.realized_vol` | Daily |
| Trend status | `market.trend_status` latest | Daily |
| Sentiment summary | `scraper.articles` recent N | Hourly |

### View 4: Pipeline Health
| Metric | Source | Refresh |
|--------|--------|---------|
| Last run per workflow | n8n execution API | 5 min |
| Data freshness per symbol | `market.ingest_state` | 5 min |
| Row counts per table | `pg_class` estimates | Hourly |
| Error count today | n8n execution API | 5 min |
| Alert delivery log | `trading.alert_history` | 1 min |

### View 5: Risk Monitor
| Metric | Source | Refresh |
|--------|--------|---------|
| PDT counter | Calculated from recent trades | Real-time |
| Drawdown levels | Calculated from portfolio P&L | Real-time |
| Position concentration | Open positions by symbol | 1 min |
| Greeks exposure | Delta/gamma/vega by position | Daily |
| Sector exposure | Positions mapped to sectors | Daily |

## Implementation Tasks

### Task 1: Create Materialized Views for Dashboard Queries
- `trading.v_portfolio_summary` — equity, available capital, daily P&L, win rate
- `trading.v_position_detail` — open positions with unrealized P&L, strategy type, exits
- `trading.v_signal_today` — today's signals with factor breakdown
- `trading.v_risk_summary` — PDT counter, drawdown levels, position concentration
- `market.v_data_freshness` — last ingestion timestamp per symbol/table
- Refresh schedule: `REFRESH MATERIALIZED VIEW` via n8n cron every 5 min

### Task 2: Create `scripts/dashboard_api.py`
- FastAPI app serving dashboard data as JSON
- Endpoints:
  - `GET /api/portfolio` — portfolio summary + open positions
  - `GET /api/signals` — today's signals + intraday changes
  - `GET /api/context` — regime, IV, GEX, trend, sentiment
  - `GET /api/pipeline` — workflow run status, data freshness
  - `GET /api/risk` — PDT, drawdown, concentration, greeks exposure
- All endpoints are read-only — no mutations through dashboard API
- CORS enabled for local network access

### Task 3: Create Dashboard Frontend
- Single HTML page with CSS grid layout
- Fetches from dashboard API every 60 seconds (auto-refresh)
- Mobile-responsive (you'll check this from your phone)
- Dark theme (trading dashboards don't have light modes)
- Sections: Positions, Signals, Context, Pipeline, Risk
- Color coding: green/red for P&L, red for violations, yellow for warnings

### Task 4: Add Docker Service for Dashboard
- Add `clawstreet-dashboard` to `docker-compose.yml`
- Build from `docker/dashboard/Dockerfile`
- Expose port 8080 (LAN: `http://192.168.1.157:8080`)
- Reads from same `.env.db` as worker
- Read-only DB user (no write access from dashboard)

### Task 5: Create Notification Integration
- Dashboard reads from `trading.alert_history` to show recent alerts
- Alert timeline widget on dashboard
- Links between positions and their triggering alerts
- Pipeline failure highlighting with direct links to n8n UI

### Task 6: Create DB Migration `13_dashboard.sql`
```sql
-- Materialized views for dashboard performance
CREATE MATERIALIZED VIEW trading.v_portfolio_summary AS
SELECT 
    SUM(CASE WHEN status = 'open' THEN quantity * entry_price ELSE 0 END) as total_invested,
    SUM(CASE WHEN status = 'closed' THEN realized_pnl ELSE 0 END) as total_realized_pnl,
    COUNT(CASE WHEN status = 'open' THEN 1 END) as open_position_count,
    COUNT(CASE WHEN status = 'closed' AND realized_pnl > 0 THEN 1 END) as winning_trades,
    COUNT(CASE WHEN status = 'closed' THEN 1 END) as total_closed_trades
FROM trading.positions;

-- Signal accuracy tracking
CREATE MATERIALIZED VIEW trading.v_signal_accuracy AS
SELECT 
    s.symbol,
    s.signal_date,
    s.signal_type,
    s.composite_score,
    p.realized_pnl,
    CASE WHEN p.realized_pnl > 0 THEN 1 ELSE 0 END as profitable
FROM trading.signals s
LEFT JOIN trading.positions p ON p.signal_id = s.id
WHERE s.signal_date > CURRENT_DATE - INTERVAL '30 days'
ORDER BY s.composite_score DESC;

-- Data freshness
CREATE MATERIALIZED VIEW market.v_data_freshness AS
SELECT 
    symbol,
    timeframe,
    last_ingested,
    NOW() - last_ingested as staleness,
    CASE WHEN NOW() - last_ingested > INTERVAL '26 hours' THEN 'stale'
         WHEN NOW() - last_ingested > INTERVAL '4 hours' THEN 'aging'
         ELSE 'fresh' END as status
FROM market.ingest_state
WHERE symbol IN (SELECT symbol FROM market.assets WHERE active = TRUE);
```

### Task 7: Optional — Grafana Integration
- If time-series visualization is needed beyond the web dashboard
- Add `clawstreet-grafana` to `docker-compose.yml`
- InfluxDB or TimescaleDB for time-series storage
- Dashboards: equity curve, drawdown history, signal score distribution, P&L over time
- Not blocking — web dashboard covers 80% of needs

## Access Points
- **Web Dashboard:** `http://192.168.1.157:8080` (LAN only, no public exposure)
- **API:** `http://192.168.1.157:8080/api/*` (same host, JSON)
- **Grafana (optional):** `http://192.168.1.157:3000`

## Security
- Dashboard is read-only — no trades can be placed from dashboard
- LAN-only access (no port forwarding, no public IP)
- No authentication needed for LAN (your home network)
- If exposing publicly later: add basic auth or OAuth

## Verification Steps
1. `python scripts/dashboard_api.py` → API returns JSON for all endpoints
2. Visit `http://192.168.1.157:8080` → dashboard loads in browser
3. Open a paper position via execution engine → dashboard updates within 60 seconds
4. Check data freshness view → all symbols show "fresh" after daily pipeline
5. Check risk view → PDT counter, drawdown levels, position concentration display
6. Mobile test: open dashboard on phone browser → responsive layout works

## See Also
- [[Telegram Alert System]] — alerts appear in dashboard timeline
- [[Order Execution Engine]] — positions feed the portfolio view
- [[n8n Scheduler]] — pipeline health data source
- [[Database Architecture]] — materialized views, read-only user setup
- [[Position Sizing]] — risk metrics displayed on dashboard