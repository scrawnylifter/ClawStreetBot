# 🦞 ClawStreetBot

Autonomous stock screening, alerts, and trading.

## Tech Stack

| Component | Technology | Purpose |
|-----------|-----------|---------|
| **Broker / Data** | Alpaca (alpaca-py) | Trading, OHLCV bars, options chains, greeks, real-time snapshots |
| **Market Data** | Polygon.io | Fundamentals, flat-file backfill (OHLCV + options decommissioned) |
| **Knowledge Base** | Obsidian (Docker) | Notes & RAG — strategies, research, API refs |
| **Database** | PostgreSQL 16 | Persistent storage — market data, scraped content, trades |
| **Cache / Queue** | Redis 7 | Real-time price cache, task queue, pub/sub alerts |
| **Language** | Python 3.11 | Bot logic, data pipeline, scrapers |
| **Version Control** | GitHub (private) | Code, config, and vault tracking |

## Architecture Overview

```
┌──────────────────────────────────────────────────────────┐
│                      ClawStreetBot                       │
├──────────┬───────────┬──────────┬──────────┬────────────┤
│ Obsidian │ PostgreSQL │  Redis   │  Alpaca  │  Scraper   │
│  :3110   │  :5432     │  :6379   │   API    │  Pipeline  │
│          │           │          │          │            │
│ Notes &  │ market.*  │  Price   │ Trading, │ RSS/News/  │
│ RAG      │ scraper.* │  cache & │ OHLCV,   │ Social     │
│          │ trading.* │  queues  │ Options, │ Media      │
│          │           │          │ Snapshots│            │
├──────────┼───────────┼──────────┼──────────┼────────────┤
│          │           │          │ Polygon  │            │
│          │           │          │   .io    │            │
│          │           │          │          │            │
│          │  Fundamentals, flat-file backfill              │
│          │  (OHLCV + options ingestion decommissioned)   │
└──────────┴───────────┴──────────┴──────────┴────────────┘
```

## Watchlist

ClawStreetBot tracks a YAML-driven watchlist (`config/watchlist.yml`) synced to Alpaca and Postgres, with sector/industry tags for heat maps. Edits to the YAML are picked up by the next `setup_watchlist.py` run (or the `watchlist_sync` n8n workflow that runs every 5 minutes).

- **Add** a symbol → row inserted into `market.assets` with `backfill_status='pending'`. The `backfill_pending` workflow picks it up and runs the full OHLCV → options → IV → RV → GEX chain.
- **Remove** a symbol → `active=false`, `deactivated_at=NOW()`. Historical rows are retained; ingestion simply stops touching it (all queries filter `WHERE active = TRUE`).
- **Re-add** a symbol → `active=true`, status re-armed to `pending`; OHLCV gap-fills incrementally from `market.ingest_state.last_timestamp`.

```bash
# Edit config/watchlist.yml, then:
python scripts/setup_watchlist.py        # diff YAML vs DB, sync both sides
python scripts/backfill_symbol.py TSLA   # one-shot full backfill for a single symbol
```

## Databases

**PostgreSQL** — two databases:
- `clawstreet` — main app (schemas: `market`, `scraper`, `trading`)
- `scraped` — content pipeline (schemas: `feeds`, `social`, `analytics`)

**Redis** — cache layer with append-only persistence

## Project Structure

```
ClawStreetBot/
├── docker-compose.yml          # All services
├── requirements.txt            # Python deps for worker image + local venv
├── .env.db                     # DB credentials (gitignored)
├── .env.alpaca                 # Alpaca API keys (gitignored)
├── .env.polygon                # Polygon.io API key (gitignored)
├── .env.obsidian               # Obsidian config (gitignored)
├── .env.n8n                    # n8n basic-auth + encryption key (gitignored)
├── .env.telegram               # Telegram bot token + chat ID (gitignored)
├── .venv/                      # Python venv (gitignored)
├── config/
│   └── watchlist.yml           # YAML source-of-truth for tracked symbols
├── db/init/                    # Postgres init scripts (lex order = run order)
│   ├── 001_init_databases.sql
│   ├── 002_create_tables.sql
│   ├── 003_polygon_tables.sql   # Options, greeks, IV rank, fundamentals, ingest_state
│   ├── 004_rv_gex_tables.sql    # Realized volatility, GEX/DEX tables
│   ├── 005_watchlist_lifecycle.sql # active/added_at/deactivated_at/backfill_status
│   ├── 006_derived_analytics.sql   # Technical indicators, greeks filter, IV outliers
│   ├── 007_signals_scoring.sql     # Signal scoring columns + unique constraint
│   ├── 008_backtest.sql           # Backtest engine tables (runs, trades, metrics)
│   ├── 009_regime.sql             # Regime classification + weights + factor analysis
│   ├── 010_trend.sql              # Trend status table (micro/intermediate/primary)
│   ├── 015_signal_alerts.sql     # Signal alerts (EMA, ORB, Dip trade plans)
│   ├── 016_signal_alerts_15m.sql # 15m intraday signal alerts
│   ├── 017_ohlcv_alpaca_columns.sql  # trade_count, vwap columns for Alpaca bars
│   ├── 018_alpaca_options_columns.sql # bid/ask columns for Alpaca options snapshots
│   ├── 019_backtest_liquidity.sql    # Liquidity sweep backtest tables
│   ├── 020_alert_lifecycle.sql       # Alert approval lifecycle (status enum)
│   ├── 021_position_exit_columns.sql # Position exit tracking (sell_order_id, tp1_hit_at)
│   ├── 022_composite_score.sql       # composite_score column on signal_alerts
│   ├── 023_risk_mode.sql             # risk_mode column on signal_alerts
│   ├── 024_position_tp1_partial.sql  # TP1 50% partial close columns
│   ├── 025_equity_snapshots.sql      # Daily equity snapshots (drawdown denominator)
│   ├── 026_signal_alerts_unique.sql  # UNIQUE on alpaca_order_id, position_id (prevent double-fill)
│   ├── 027_positions_alpaca_order_id.sql # alpaca_order_id on positions + UNIQUE partial index
│   ├── 028_error_notified.sql         # error_notified_at on signal_alerts (Telegram edit tracking)
│   ├── 029_position_trail_stop.sql    # trail_stop_price on positions (swing trailing stop after TP2)
│   └── 030_status_check_constraints.sql  # CHECK constraints on signal_alerts.status and trading.positions.status
├── docker/
│   ├── worker/Dockerfile       # Python 3.11 worker image (n8n execs into this)
│   └── n8n/Dockerfile          # n8n + docker CLI for Execute Command nodes
│   # docker-socket-proxy (wollomatic/socket-proxy) is pulled directly,
│   # configured inline in docker-compose.yml — no Dockerfile needed.
├── n8n/
│   └── workflows/              # Source-of-truth JSON — 23 active workflows
│       ├── watchlist_sync.json
│       ├── backfill_pending.json
│       ├── alpaca_ohlcv_daily.json       # 1d Alpaca bars
│       ├── alpaca_ohlcv_intraday.json    # 15m + 5m Alpaca bars
│       ├── alpaca_options_daily.json     # Alpaca options + greeks + bid/ask
│       ├── derived_daily.json
│       ├── fundamentals_daily.json       # Polygon quarterly fundamentals (last Polygon-backed cron)
│       ├── rss_news_scanner.json
│       ├── signals_daily.json
│       ├── intraday_signal_5m.json
│       ├── ema_crossover_detector.json
│       ├── ema_crossover_15m.json        # EMA crossover on 15m + realtime snapshot
│       ├── setup_scanner.json            # PRIMARY — 8-gate BUY signal scanner (every 15min market hours)
│       ├── liquidity_sweep.json          # 5m + daily liquidity sweep scanner
│       ├── orb_detector.json             # ★ ORB scanner (every 5min 6-13 PDT, opening range breakout on 5m bars)
│       ├── alert_dispatch.json           # alert_telegram.py every 1min market hours
│       ├── execute_trade.json            # Alpaca paper submit (approved → executing)
│       ├── reconcile_orders.json         # BUY fill → trading.positions
│       ├── reconcile_exits.json          # SELL fill → closed + realized_pnl + status='exited'
│       ├── exit_monitor.json             # TP/SL/time-stop decision tree
│       ├── equity_snapshot_daily.json    # Daily equity snapshot for drawdown denominator
│       ├── trend_daily.json
│       └── regime_weekly.json
├── scripts/                    # Python scripts
│   ├── alert_telegram.py         # Telegram dispatcher (entry alerts w/ Strategy: header + 4-button keyboard) + expirer + error surfacer + exit-fill push notifications ★
│   ├── backfill_historical_iv.py  # Historical IV backfill
│   ├── backfill_runner.py        # n8n wrapper: queries pending symbols, runs backfill_symbol.py
│   ├── backfill_signals.py       # Historical signal backfill across 501 days
│   ├── backfill_symbol.py        # Full ingestion chain for one symbol
│   ├── backtest.py                # Backtesting engine
│   ├── backtest_liquidity.py     # Liquidity sweep v1 backtest (superseded by v3)
│   ├── backtest_liquidity_v2.py   # Liquidity sweep v2 backtest (superseded by v3)
│   ├── backtest_liquidity_v3.py  # Liquidity sweep v3 — refinement tests (close-beyond = PF 1.56)
│   ├── compute_gex_dex.py        # GEX/DEX by strike/expiry + overview per underlying
│   ├── compute_greeks_filter.py   # IV regime + delta/theta-budget gating
│   ├── compute_iv_outliers.py     # 3σ z-score IV outlier flags
│   ├── compute_iv_rank.py        # IV rank from historical IV percentiles
│   ├── compute_realized_vol.py   # 20d/5d realized volatility + IV-RV spread
│   ├── compute_technical_indicators.py # EMA/RSI/MACD/ATR/VWAP/Bollinger
│   ├── compute_trend.py           # Multi-timeframe trend detection
│   ├── detect_ema_crossover.py    # Daily EMA 9/21 crossover + ADX>25 detector (supplementary)
│   ├── detect_ema_crossover_15m.py # 15m EMA crossover + real-time snapshot enrichment (supplementary)
│   ├── detect_liquidity_sweep.py   # ★ LIVE liquidity sweep scanner (5m + daily, close-beyond, Telegram)
│   ├── detect_orb.py               # ★ ORB scanner (opening range breakout on 5m bars, strategy='orb')
│   ├── diagnose_liquidity_backtest.py  # v1 diagnostics
│   ├── explore_data.py             # Alpaca data explorer
│   ├── explore_options.py          # Options chain explorer
│   ├── fetch_alpaca_snapshot.py     # Real-time stock price + best option at signal time; DELTA_BANDS dict (conservative 0.55–0.65, standard 0.50–0.70, aggressive 0.40–0.80) ★
│   ├── generate_signals.py         # Composite signal scoring (6-factor, 0-100)
│   ├── ingest_alpaca_ohlcv.py      # Alpaca OHLCV bars → market.ohlcv (1d/15m/5m) ★
│   ├── ingest_alpaca_options.py    # Alpaca options chains + greeks + bid/ask ★
│   ├── ingest_polygon_fundamentals.py # Polygon quarterly financials (still active)
│   ├── ingest_polygon_ohlcv.py     # DECOMMISSIONED — replaced by ingest_alpaca_ohlcv.py
│   ├── ingest_polygon_options.py    # DECOMMISSIONED — replaced by ingest_alpaca_options.py
│   ├── ingest_rss_news.py              # RSS + Reddit scraper
│   ├── intraday_signal.py             # 5-min intraday tech re-score + threshold alerts
│   ├── n8n_api.sh                     # n8n REST API helper (sources .env.n8n)
│   ├── options_analysis.py            # Options greeks/IV analysis
│   ├── regime_backtest.py            # Regime classification + dynamic weights
│   ├── scan_setups.py                # ★ PRIMARY — 8-gate BUY signal scanner
│   ├── snapshot_equity.py            # Daily Alpaca equity snapshot (drawdown denominator)
│   ├── execute_trade.py              # Alpaca paper order submission; bracket orders (BRACKET) for non-option stock entries; reselect_option_for_risk_mode() for conservative/aggressive
│   ├── exit_monitor.py              # TP/SL/trail-stop/time-stop decision tree (swing trails after TP2)
│   ├── reconcile_orders.py          # BUY fill → trading.positions; orphan executing recovery
│   ├── reconcile_exits.py          # SELL/TP1 partial fills → close position + cumulative P&L + status='exited' + Telegram exit-fill push notification
│   ├── process_approved.py          # Drawdown halts + pre-flight checks before execution
│   └── telegram_callback_listener.py # Telegram callback server for inline-approve/deny
└── obsidian/vault/             # Knowledge base
    ├── Home.md                 # Dashboard
    ├── Project Roadmap.md
    ├── 01-Fundamentals/
    │   ├── Laws of Trading.md
    │   ├── Trade Entry Criteria.md
    │   └── Unified Entry & Exit Checklist.md
    ├── 02-Strategies/
    │   ├── Strategies.md
    │   ├── Day Trading.md
    │   ├── Swing Trading.md
    │   ├── Long-Term Holding.md
    │   ├── Greeks Strategy.md
    │   ├── EMA Crossover.md
    │   ├── ORB — Opening Range Breakout.md
    │   ├── Buy the 5% Dip.md
    │   ├── Liquidity — 5m Day Trading.md
    │   └── Risk Management Framework.md
    ├── 03-Market-Research/
    │   ├── Watchlist.md
    │   └── Backtesting Architecture.md
    ├── 04-API-References/
    │   ├── Alpaca API.md
    │   ├── Alpaca Data Pipeline.md
    │   └── Polygon.io API.md
    ├── 05-Risk-Management/
    │   ├── Risk Management.md
    │   ├── Position Sizing.md
    │   ├── Loss Limits.md
    │   └── Correlation Risk.md
    ├── 06-Indicators/
    ├── 07-Infrastructure/
    │   ├── Database Architecture.md
    │   ├── n8n Scheduler.md
    │   ├── Telegram Alert System.md
    │   ├── Order Execution Engine.md
    │   └── Monitoring & Dashboards.md
    └── 08-Templates/
        ├── API Reference Template.md
        └── Strategy Template.md
```

## Quick Start

```bash
# Configure credentials
cp .env.db.example .env.db
cp .env.obsidian.example .env.obsidian
cp .env.alpaca.example .env.alpaca
cp .env.polygon.example .env.polygon
cp .env.n8n.example .env.n8n
cp .env.telegram.example .env.telegram
# Edit each with real passwords/keys.
# For .env.n8n, set DOCKER_GID to `stat -c '%g' /var/run/docker.sock`
# (used by docker-proxy, not n8n itself).

# Launch all services (Postgres, Redis, Obsidian, worker, docker-proxy, n8n)
docker compose up -d

# Install Python dependencies for the local venv
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Edit config/watchlist.yml, then sync to Alpaca + Postgres
python scripts/setup_watchlist.py

# One-off manual backfills (n8n will run these on schedule too)
python scripts/ingest_alpaca_ohlcv.py --all-timeframes
python scripts/ingest_alpaca_options.py --all
python scripts/backfill_historical_iv.py
python scripts/compute_realized_vol.py
python scripts/compute_iv_rank.py
python scripts/compute_gex_dex.py

# Run the 8-gate BUY signal scanner (PRIMARY alert mechanism)
python scripts/scan_setups.py             # scan + Telegram alert
python scripts/scan_setups.py --dry-run   # dry-run: stdout only

# Connect to Postgres
docker exec -it clawstreet-db psql -U clawstreet -d clawstreet

# Connect to Redis
docker exec -it clawstreet-redis redis-cli -a <password>
```

## Continuous Ingestion (n8n)

The full ingestion pipeline is scheduled by **n8n** (UI at <http://localhost:5678>, credentials in `.env.n8n`). Workflow JSON is checked in under `n8n/workflows/`. All cron schedules use **America/Los_Angeles (PDT)** timezone.

23 active workflows; the old Polygon ingestion JSONs (`ohlcv_daily`, `ohlcv_intraday`, `options_daily`) have been deleted from the repo — replaced by their Alpaca equivalents.

| Workflow | Schedule (PDT) | Action |
|---|---|---|
| `watchlist_sync` | every 5 min | `setup_watchlist.py` (no-op when YAML unchanged) |
| `backfill_pending` | every 5 min | `backfill_runner.py` (queries pending symbols, runs backfill) |
| `alpaca_ohlcv_daily` | Mon–Fri 15:00 | 1d OHLCV bars (trade_count, VWAP) |
| `alpaca_ohlcv_intraday` | Mon–Fri hourly :05 (7–13) | 15m + 5m OHLCV bars (trade_count, VWAP) |
| `alpaca_options_daily` | Mon–Fri 14:55 | Options chains + greeks + bid/ask snapshots |
| `derived_daily` | Mon–Fri 15:30 | RV → IV-rank → GEX → tech → greeks → outliers chain |
| `fundamentals_daily` | Mon–Fri 16:00 | Polygon quarterly financials |
| `rss_news_scanner` | Mon–Fri every 30m 6–13 | RSS + Reddit ingestion |
| `signals_daily` | Mon–Fri 16:30 | Composite signals + daily backtests |
| `intraday_signal_5m` | Mon–Fri every 5min 6–12 | 5-min intraday tech re-score + threshold alerts |
| `ema_crossover_detector` | Mon–Fri 7:00 | Daily EMA 9/21 crossover detection → DB row (supplementary) |
| `ema_crossover_15m` | Mon–Fri every 15min 6:30–13 | 15m EMA crossover + real-time Alpaca snapshot (supplementary) |
| **`setup_scanner`** | **Mon–Fri every 15min 6–12** | **★ PRIMARY — 8-gate BUY signal scanner (trend, ADX, RSI, IV rank, IV-RV spread, premium, DTE, R:R)** |
| `liquidity_sweep` | Mon–Fri every 5min 6–12 | 5m + daily liquidity sweep scanner (close-beyond confirmation) |
| **`orb_detector`** | **Mon–Fri every 5min 6–13** | **★ ORB scanner — Opening Range Breakout on 5m bars, strategy='orb'** |
| **`alert_dispatch`** | **Mon–Fri every 1min 6–13** | **`alert_telegram.py` — dispatches unsent `signal_alerts` rows with the 4-button approval keyboard** |
| `execute_trade` | Mon–Fri every 1min 6–13 | Approved → Alpaca paper submit (`status='executing'`) |
| `reconcile_orders` | Mon–Fri every 1min 6–14 | BUY fill state → `trading.positions`, `status='filled'` |
| `reconcile_exits` | Mon–Fri every 1min 6–14 | SELL / TP1-partial fills → close position + record P&L + `status='exited'` |
| `exit_monitor` | Mon–Fri every 5min 6–13 | TP/SL/trail-stop/time-stop decision tree; trail stop after TP2 for swing; submits closes with `client_order_id` |
| `equity_snapshot_daily` | Mon–Fri 14:30 | Daily equity snapshot for drawdown halt denominator |
| `trend_daily` | Mon–Fri 11:00 | Multi-timeframe trend detection + status |
| `regime_weekly` | Sat 8:00 | Classify regime + optimize weights + compare |

n8n runs scripts via `docker exec clawstreet-worker python /app/scripts/<name>.py`, so edits to scripts/config land immediately (the worker image only rebuilds when `requirements.txt` changes).

**★ Primary alert mechanism:** The `setup_scanner` workflow (`scan_setups.py`) evaluates ALL watchlist symbols against 8 buying gates (trend, ADX, RSI, IV rank, IV-RV spread, premium cost, DTE, R:R) every 15 minutes during market hours. It only sends a Telegram alert when ALL 8 gates pass — silence means no signal. EMA crossover detectors (daily + 15m) remain active as supplementary alerts.

### Docker socket isolation

n8n does **not** mount the host Docker socket. It talks to a dedicated `docker-proxy` service (`wollomatic/socket-proxy`) on `tcp://docker-proxy:2375`. The proxy uses per-endpoint regex allowlists pinned to the worker container only:

| Method | Allowed path                                                                            |
|--------|------------------------------------------------------------------------------------------|
| GET    | `/_ping`, `/version`, `/containers/clawstreet-worker/json`, `/exec/{hex-id}/json`       |
| HEAD   | `/_ping`, `/version`                                                                     |
| POST   | `/containers/clawstreet-worker/exec`, `/exec/{hex-id}/(start\|resize)`                   |

Everything else is denied — n8n cannot `docker ps`, `stop`, `rm`, `run`, mount the host filesystem, or exec into any container other than `clawstreet-worker`. The proxy itself runs read-only, with all capabilities dropped, `no-new-privileges`, and as an unprivileged user in the host's `docker` group (set `DOCKER_GID` in `.env.n8n` to `stat -c '%g' /var/run/docker.sock`).

### Importing workflows

Once the n8n owner account is set up:

```bash
docker exec clawstreet-n8n n8n import:workflow --separate --input=/workflows
# Then activate each workflow in the UI (Settings → Workflows → toggle Active)
```

## Alpaca API

Using **Paper Trading** for development. Alpaca is now the **primary data source** for OHLCV bars, options chains, greeks, and real-time snapshots (Phase 5 Alpaca migration).

- **OHLCV bars** — 1d, 15m, 5m timeframes via `StockHistoricalDataClient` (free tier, IEX 15-min delayed)
- **Options chains + greeks** — full chain snapshots with delta, gamma, theta, vega, IV, bid, ask via `OptionHistoricalDataClient`
- **Real-time snapshots** — stock price + best filtered option at signal time via `StockHistoricalDataClient.get_stock_snapshot()` + `OptionHistoricalDataClient`
- **Trading / orders** — market, limit, stop, bracket orders (paper mode)
- **WebSocket streams** — live data available (not yet integrated)

Switch to `paper=False` for live trading with real money (requires SIP data subscription).

### Data Source Comparison (Phase 5+)

| Data | Alpaca (Primary) | Polygon.io (Secondary) |
|------|-------------------|------------------------|
| Trading / orders | ✅ Broker | ❌ Data only |
| OHLCV bars | ✅ Free (1d/15m/5m) | ❌ Decommissioned for ingestion |
| Options chains + greeks | ✅ Free (snapshots) | ❌ Decommissioned for ingestion |
| Bid/ask on options | ✅ Free | ❌ Not available |
| Real-time snapshots | ✅ Free (15-min delayed) | ❌ Paid |
| Fundamentals | ❌ | ✅ Still active (Polygon) |
| Flat-file backfill | ❌ | ✅ Still active (Polygon S3) |
| Trade count + VWAP | ✅ In bars | ❌ Not in Polygon bars |

## Polygon.io Integration

**Polygon.io** is now a **secondary/legacy data source**. After the Phase 5 Alpaca migration:

- **DECOMMISSIONED** — OHLCV ingestion (`ingest_polygon_ohlcv.py`) → replaced by `ingest_alpaca_ohlcv.py`
- **DECOMMISSIONED** — Options ingestion (`ingest_polygon_options.py`) → replaced by `ingest_alpaca_options.py`
- **Still Active** — Fundamentals (`fundamentals_daily` workflow, `ingest_polygon_fundamentals.py`)
- **Still Active** — Flat Files S3 backfill (used for initial historical data loads)

## TODO

### Phase 5A — Signal Detection (in progress)
- [x] EMA crossover detector (`detect_ema_crossover.py`) — 9/21 cross + ADX>25
- [x] Signal alerts table (`015_signal_alerts.sql`) — full trade plan storage
- [x] Telegram alert sender (`alert_telegram.py`) — strategy-specific trade alerts (bid/ask/mid)
- [x] Backfill runner (`backfill_runner.py`) — n8n wrapper for pending symbol backfills
- [x] Docker proxy hardened (allowHEAD + allowGET for exec/{id}/json)
- [x] All n8n cron schedules converted from ET to PDT
- [x] **Alpaca data migration** — OHLCV + options ingestion moved from Polygon to Alpaca (free tier)
- [x] **15m EMA crossover detector** (`detect_ema_crossover_15m.py`) — intraday signals + real-time snapshot
- [x] **Real-time snapshot enrichment** (`fetch_alpaca_snapshot.py`) — stock price + best option at signal time
- [x] **Alpaca OHLCV ingestion** (`ingest_alpaca_ohlcv.py`) — 1d/15m/5m bars with trade_count + VWAP
- [x] **Alpaca options ingestion** (`ingest_alpaca_options.py`) — chains + greeks + bid/ask
- [x] **DB migrations** — `017_ohlcv_alpaca_columns.sql` (trade_count, VWAP), `018_alpaca_options_columns.sql` (bid, ask)
- [x] **n8n workflows** — `alpaca_ohlcv_daily`, `alpaca_ohlcv_intraday`, `alpaca_options_daily` (active); old Polygon workflows deactivated
- [x] **★ Setup scanner** (`scan_setups.py`) — 8-gate BUY signal scanner (trend, ADX, RSI, IV rank, IV-RV spread, premium cost, DTE, R:R); silence = no signal; PRIMARY alert mechanism
- [x] **★ n8n workflow `setup_scanner`** — runs every 15min during market hours (Mon–Fri 6–12 PDT)
- [x] **★ Liquidity sweep scanner** (`detect_liquidity_sweep.py`) — 5m + daily, close-beyond confirmation, Telegram alerts (PF 1.56)
- [x] **★ Liquidity sweep backtest** (`backtest_liquidity_v3.py`) — 6-month, 16 symbols; close-beyond = key filter
- [x] **ORB breakout detector** (`detect_orb.py`) — Opening Range on first 15m bar, 5m close-beyond breakout, external-level filter, ATR-based intraday stops
- [ ] Buy the 5% Dip detector (`detect_dip.py`)
- [ ] Options chain filter (`filter_options.py`) — DTE≥30, delta/theta budget per strategy

### Phase 5B — Exit Monitors & Alert Delivery
- [x] Exit monitor: price-based (TP1/TP2/stop) + invalidation + greeks deterioration
- [x] Alert formatting + Telegram delivery (Y/N approval flow)
- [x] Pre-flight checks — Laws, PDT, drawdown, greeks
- [x] Alpaca execution — bracket orders, tiered exits
- [x] Risk alerts — drawdown halt, PDT warning, position breach
- [x] DB migrations: alert_history, positions, pdt_status
- [x] Lifecycle hardening — expirer, error surfacing, partial fills, position→order link (#15)

### Phase 5C — Trailing Stop & Orphan Recovery
- [x] Orphan executing recovery — reap `status='executing'` rows with no `alpaca_order_id` after 5-min grace (#16)
- [x] Position→order direct link — `alpaca_order_id` on `trading.positions` with UNIQUE partial index (migration 027)
- [x] Error surfacing — `error_notified_at` on `signal_alerts`; `alert_telegram` edits original message with failure (#15, migration 028)
- [x] Trailing stop after TP2 — swing-mode positions trail instead of full-close on TP2 hit (migration 029, `exit_monitor.py`)
- [x] Partial fill handling — `reconcile_exits` accumulates P&L across partial SELL fills (#15)
- [x] Alert keyboard expiry — `clear_message_keyboard` on stale alert rows; `editMessageText` for error surfacing (#15)
- [x] **Per-risk-mode delta bands (M1)** — `DELTA_BANDS` dict in `fetch_alpaca_snapshot.py` (conservative 0.55–0.65, standard 0.50–0.70, aggressive 0.40–0.80); `reselect_option_for_risk_mode()` in `execute_trade.py` re-queries Alpaca and persists new contract (#19)
- [x] **Bracket orders for stock entries (H7)** — `order_class=BRACKET` for non-option bullish entries with stop_loss + take_profit legs; `cancel_open_orders_for_symbol` before exit_monitor closes (#19)
- [x] **Status enum CHECK constraints** — migration 030 adds `signal_alerts_status_check` (9 values) and re-states `positions_status_check`; typos now fail at INSERT/UPDATE (#19)

### Phase 5E — Notification UX (#18)
- [x] **Strategy name in entry-alert headers** — every formatter (`setup_scanner`, `ema_crossover`, `ema_crossover_15m`, `liquidity_sweep`, `intraday_signal`) now shows `Strategy: <name> | Timeframe: <tf>` so the user knows which scanner fired the alert
- [x] **Exit-fill Telegram push notifications** — `reconcile_exits` posts to Telegram after every close commit. 💰 wins (TP2 / trail_stop / TP1 partial), ⛔ losses (stop / premium_stop), 📤 mechanics (time_stop / expiry, partial-before-cancel). Includes symbol, strategy, direction, reason, entry/exit, qty, realized P&L, residual qty for partials. Fire-and-forget — Telegram failure is logged but never rolls back the DB close

### Phase 5F — ORB Scanner (#20-22)
- [x] **ORB breakout detector** (`detect_orb.py`) — Opening Range on first 15m bar after 9:30 ET, 5m close-beyond breakout after 9:45 ET, external-level filter (skip within 0.5% of prior day's high/low), ATR-based intraday stops (1.5× ATR stop, 4.5× ATR TP1, 7.5× ATR TP2)
- [x] **`orb_detector.json` n8n workflow** — every 5min, 6–13 PDT, Mon–Fri
- [x] **`format_orb_alert()`** in `alert_telegram.py` — dispatches ORB alerts with 4-button approval keyboard
- [x] **`execute_trade.py` handles `strategy='orb'`** — ATR-based intraday stops, day-trade mode (flatten before close)
- [x] **DB env-var fallback** (PR #21) — `POSTGRES_DB` sources `.env.db` with fallback to `"clawstreet"`
- [x] **Full-session scan window** (PR #22) — scans all 5m bars after 9:45 ET, not just last 3 bars
- [x] **SPY 15m bars backfilled** — 2,667 bars (~120 trading days)

### Remaining Items
- [ ] Position sizing calculator (backtest has it, no standalone tool)
- [ ] Monitoring/dashboards (no visibility beyond raw DB queries)
- [ ] Regime optimizer needs more diverse data (underperforms static with full history — not a code fix)

## Contributing

This is a private project. See the Obsidian vault for strategy docs and research.