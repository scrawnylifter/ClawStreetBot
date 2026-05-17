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
├── .venv/                      # Python venv (gitignored)
├── config/
│   └── watchlist.yml           # YAML source-of-truth for tracked symbols
├── db/init/                    # Postgres init scripts
│   ├── 01_init_databases.sql
│   ├── 02_create_tables.sql
│   ├── 03_polygon_tables.sql   # Options, greeks, IV rank, fundamentals, ingest_state
│   ├── 04_rv_gex_tables.sql    # Realized volatility, GEX/DEX tables
│   ├── 05_watchlist_lifecycle.sql # active/added_at/deactivated_at/backfill_status
│   ├── 06_derived_analytics.sql   # Technical indicators, greeks filter, IV outliers
│   ├── 07_signals_scoring.sql     # Signal scoring columns + unique constraint
│   ├── 08_backtest.sql           # Backtest engine tables (runs, trades, metrics)
│   ├── 09_regime.sql             # Regime classification + weights + factor analysis
│   ├── 10_trend.sql              # Trend status table (micro/intermediate/primary)
│   ├── 015_signal_alerts.sql     # Signal alerts (EMA, ORB, Dip trade plans)
│   ├── 016_signal_alerts_15m.sql # 15m intraday signal alerts
│   ├── 017_ohlcv_alpaca_columns.sql  # trade_count, vwap columns for Alpaca bars
│   └── 018_alpaca_options_columns.sql # bid/ask columns for Alpaca options snapshots
├── docker/
│   ├── worker/Dockerfile       # Python 3.11 worker image (n8n execs into this)
│   └── n8n/Dockerfile          # n8n + docker CLI for Execute Command nodes
│   # docker-socket-proxy (wollomatic/socket-proxy) is pulled directly,
│   # configured inline in docker-compose.yml — no Dockerfile needed.
├── n8n/
│   └── workflows/              # Source-of-truth JSON for n8n workflows
│       ├── watchlist_sync.json
│       ├── backfill_pending.json
│       ├── alpaca_ohlcv_daily.json      # Active — Alpaca 1d bars
│       ├── alpaca_ohlcv_intraday.json   # Active — Alpaca 15m + 5m bars
│       ├── alpaca_options_daily.json     # Active — Alpaca options + greeks + bid/ask
│       ├── ohlcv_daily.json             # DEACTIVATED (replaced by alpaca_ohlcv_daily)
│       ├── ohlcv_intraday.json          # DEACTIVATED (replaced by alpaca_ohlcv_intraday)
│       ├── options_daily.json           # DEACTIVATED (replaced by alpaca_options_daily)
│       ├── derived_daily.json
│       ├── fundamentals_daily.json
│       ├── rss_news_scanner.json
│       ├── signals_daily.json
│       ├── intraday_signal_5m.json
│       ├── ema_crossover_detector.json
│       ├── ema_crossover_15m.json        # EMA crossover on 15m + realtime snapshot
│       ├── trend_daily.json
│       └── regime_weekly.json
├── scripts/                    # Python scripts
│   ├── explore_data.py         # Alpaca data explorer
│   ├── setup_watchlist.py      # Sync config/watchlist.yml → Alpaca + Postgres
│   ├── backfill_symbol.py      # Full ingestion chain for one symbol
│   ├── backfill_runner.py      # n8n wrapper: queries pending symbols, runs backfill_symbol.py
│   ├── ingest_alpaca_ohlcv.py  # Alpaca OHLCV bars → market.ohlcv (1d/15m/5m) ★
│   ├── ingest_alpaca_options.py # Alpaca options chains + greeks + bid/ask ★
│   ├── fetch_alpaca_snapshot.py # Real-time stock price + best option at signal time ★
│   ├── ingest_polygon_ohlcv.py # DECOMMISSIONED — replaced by ingest_alpaca_ohlcv.py
│   ├── ingest_polygon_options.py # DECOMMISSIONED — replaced by ingest_alpaca_options.py
│   ├── ingest_polygon_fundamentals.py # Polygon quarterly financials (still active)
│   ├── ingest_rss_news.py              # RSS + Reddit scraper
│   ├── compute_iv_rank.py        # IV rank from historical IV percentiles
│   ├── compute_realized_vol.py   # 20d/5d realized volatility + IV-RV spread
│   ├── compute_gex_dex.py        # GEX/DEX by strike/expiry + overview per underlying
│   ├── compute_technical_indicators.py # EMA/RSI/MACD/ATR/VWAP/Bollinger
│   ├── compute_greeks_filter.py   # IV regime + delta/theta-budget gating
│   ├── compute_trend.py           # Multi-timeframe trend detection
│   ├── generate_signals.py        # Composite signal scoring (6-factor, 0-100)
│   ├── intraday_signal.py         # 5-min intraday tech re-score + threshold alerts
│   ├── detect_ema_crossover.py    # Daily EMA 9/21 crossover + ADX>25 detector
│   ├── detect_ema_crossover_15m.py # 15m EMA crossover + real-time snapshot enrichment ★
│   ├── alert_telegram.py          # Telegram alert sender (shows bid/ask/mid) ★
│   ├── backfill_historical_iv.py  # Historical IV backfill
│   ├── backtest.py                 # Backtesting engine
│   └── regime_backtest.py          # Regime classification + dynamic weights
└── obsidian/vault/             # Knowledge base
    ├── Home.md                 # Dashboard
    ├── Project Roadmap.md
    ├── 01-Fundamentals/
    │   ├── Laws of Trading.md
    │   └── Trade Entry Criteria.md
    ├── 02-Strategies/
    │   ├── Strategies.md
    │   ├── Day Trading.md
    │   ├── Swing Trading.md
    │   ├── Long-Term Holding.md
    │   ├── Greeks Strategy.md
    │   ├── EMA Crossover.md
    │   ├── ORB — Opening Range Breakout.md
    │   └── Buy the 5% Dip.md
    ├── 03-Market-Research/
    │   ├── Watchlist.md
    │   └── Backtesting Architecture.md
    ├── 04-API-References/
    │   ├── Alpaca API.md
    │   └── Polygon.io API.md
    ├── 05-Risk-Management/
    │   ├── Risk Management.md
    │   ├── Position Sizing.md
    │   ├── Loss Limits.md
    │   └── Correlation Risk.md
    ├── 06-Indicators/
    ├── 07-Infrastructure/
    │   └── Database Architecture.md
    └── 08-Templates/
```

## Quick Start

```bash
# Configure credentials
cp .env.db.example .env.db
cp .env.obsidian.example .env.obsidian
cp .env.alpaca.example .env.alpaca
cp .env.polygon.example .env.polygon
cp .env.n8n.example .env.n8n
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

# Connect to Postgres
docker exec -it clawstreet-db psql -U clawstreet -d clawstreet

# Connect to Redis
docker exec -it clawstreet-redis redis-cli -a <password>
```

## Continuous Ingestion (n8n)

The full ingestion pipeline is scheduled by **n8n** (UI at <http://localhost:5678>, credentials in `.env.n8n`). Workflow JSON is checked in under `n8n/workflows/`. All cron schedules use **America/Los_Angeles (PDT)** timezone.

| Workflow | Schedule (PDT) | Action |
|---------------------|-----------------------------|--------------------------------------------------------------|
| `watchlist_sync` | every 5 min | `setup_watchlist.py` (no-op when YAML unchanged) |
| `backfill_pending` | every 5 min | `backfill_runner.py` (queries pending symbols, runs backfill) |
| `alpaca_ohlcv_daily` | Mon–Fri 15:00 | 1d OHLCV bars (trade_count, VWAP) |
| `alpaca_ohlcv_intraday` | Mon–Fri hourly :05 (7–13) | 15m + 5m OHLCV bars (trade_count, VWAP) |
| `alpaca_options_daily` | Mon–Fri 14:55 | Options chains + greeks + bid/ask snapshots |
| ~~ohlcv_daily~~ | ~~Mon–Fri 15:00~~ | ~~DEACTIVATED — replaced by alpaca_ohlcv_daily~~ |
| ~~ohlcv_intraday~~ | ~~Mon–Fri hourly :05~~ | ~~DEACTIVATED — replaced by alpaca_ohlcv_intraday~~ |
| ~~options_daily~~ | ~~Mon–Fri 14:55~~ | ~~DEACTIVATED — replaced by alpaca_options_daily~~ |
| `derived_daily` | Mon–Fri 15:30 | RV → IV-rank → GEX → tech → greeks → outliers chain |
| `fundamentals_daily` | Mon–Fri 16:00 | Polygon quarterly financials |
| `rss_news_scanner` | Mon–Fri every 30m 6–13 | RSS + Reddit ingestion |
| `signals_daily` | Mon–Fri 16:30 | Composite signals + daily backtests |
| `intraday_signal_5m` | Mon–Fri every 5min 6–12 | 5-min intraday tech re-score + threshold alerts |
| `ema_crossover_detector` | Mon–Fri 7:00 | Daily EMA 9/21 crossover detection → Telegram alert |
| `ema_crossover_15m` | Mon–Fri every 15min 6:30–13 | 15m EMA crossover + real-time Alpaca snapshot |
| `trend_daily` | Mon–Fri 11:00 | Multi-timeframe trend detection + status |
| `regime_weekly` | Sat 8:00 | Classify regime + optimize weights + compare |

n8n runs scripts via `docker exec clawstreet-worker python /app/scripts/<name>.py`, so edits to scripts/config land immediately (the worker image only rebuilds when `requirements.txt` changes).

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
- [ ] ORB breakout detector (`detect_orb.py`)
- [ ] Buy the 5% Dip detector (`detect_dip.py`)
- [ ] Options chain filter (`filter_options.py`) — DTE≥30, delta/theta budget per strategy

### Phase 5B — Exit Monitors & Alert Delivery
- [ ] Exit monitor: price-based (TP1/TP2/stop) + invalidation + greeks deterioration
- [ ] Alert formatting + Telegram delivery (Y/N approval flow)
- [ ] Pre-flight checks — Laws, PDT, drawdown, greeks
- [ ] Alpaca execution — bracket orders, tiered exits
- [ ] Risk alerts — drawdown halt, PDT warning, position breach
- [ ] DB migrations: alert_history, positions, pdt_status

### Remaining Items
- [ ] Position sizing calculator (backtest has it, no standalone tool)
- [ ] Monitoring/dashboards (no visibility beyond raw DB queries)
- [ ] Regime optimizer needs more diverse data (underperforms static with full history — not a code fix)

## Contributing

This is a private project. See the Obsidian vault for strategy docs and research.