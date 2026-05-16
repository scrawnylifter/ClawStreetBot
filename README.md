# 🦞 ClawStreetBot

Autonomous stock screening, alerts, and trading.

## Tech Stack

| Component | Technology | Purpose |
|-----------|-----------|---------|
| **Broker / Data** | Alpaca (alpaca-py) | Trading, market data, news, screeners |
| **Market Data** | Polygon.io | Historical OHLCV, options chains, fundamentals, real-time feeds |
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
│ Obsidian │ PostgreSQL │  Redis  │  Alpaca  │  Scraper   │
│  :3110   │  :5432     │  :6379  │   API    │  Pipeline  │
│          │           │          │          │            │
│ Notes &  │ market.*  │  Price   │ Trading  │ RSS/News/  │
│ RAG      │ scraper.* │  cache & │ Data &  │ Social     │
│          │ trading.* │  queues  │ Orders   │ Media      │
├──────────┼───────────┼──────────┼──────────┼────────────┤
│          │           │          │ Polygon  │            │
│          │           │          │   .io    │            │
│          │           │          │          │            │
│          │  Historical OHLCV, options, fundamentals      │
│          │  → Postgres market.* tables                  │
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
│   └── 05_watchlist_lifecycle.sql # active/added_at/deactivated_at/backfill_status
├── docker/
│   ├── worker/Dockerfile       # Python 3.11 worker image (n8n execs into this)
│   └── n8n/Dockerfile          # n8n + docker CLI for Execute Command nodes
│   # docker-socket-proxy (wollomatic/socket-proxy) is pulled directly,
│   # configured inline in docker-compose.yml — no Dockerfile needed.
├── n8n/
│   └── workflows/              # Source-of-truth JSON for n8n workflows
│       ├── watchlist_sync.json
│       ├── backfill_pending.json
│       ├── ohlcv_daily.json
│       ├── ohlcv_intraday.json
│       ├── options_daily.json
│       └── derived_daily.json
├── scripts/                    # Python scripts
│   ├── explore_data.py         # Alpaca data explorer
│   ├── setup_watchlist.py      # Sync config/watchlist.yml → Alpaca + Postgres
│   ├── backfill_symbol.py      # Full ingestion chain for one symbol
│   ├── ingest_polygon_ohlcv.py # OHLCV bars → market.ohlcv (1d/5m/15m)
│   ├── ingest_polygon_options.py # Options contracts + greeks snapshots
│   ├── compute_iv_rank.py        # IV rank from historical IV percentiles
│   ├── compute_realized_vol.py   # 20d/5d realized volatility + IV-RV spread
│   ├── compute_gex_dex.py        # GEX/DEX by strike/expiry + overview per underlying
│   └── backfill_historical_iv.py # Historical IV backfill
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
python scripts/ingest_polygon_ohlcv.py --all-timeframes
python scripts/ingest_polygon_options.py
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

The full ingestion pipeline is scheduled by **n8n** (UI at <http://localhost:5678>, credentials in `.env.n8n`). Workflow JSON is checked in under `n8n/workflows/`.

| Workflow            | Schedule (ET)              | Action                                                       |
|---------------------|----------------------------|--------------------------------------------------------------|
| `watchlist_sync`    | every 5 min                | `setup_watchlist.py` (no-op when YAML unchanged)             |
| `backfill_pending`  | every 5 min                | Picks `backfill_status='pending'` symbols → `backfill_symbol.py` |
| `ohlcv_daily`       | Mon–Fri 18:00              | 1d OHLCV bars                                                |
| `ohlcv_intraday`    | Mon–Fri hourly :05 (09–16) | 5m + 15m OHLCV bars                                          |
| `options_daily`     | Mon–Fri 17:55              | Options contracts + greeks snapshot                          |
| `derived_daily`     | Mon–Fri 18:30              | RV → IV-rank → GEX chain                                     |

n8n runs scripts via `docker exec clawstreet-worker python /app/scripts/<name>.py`, so edits to scripts/config land immediately (the worker image only rebuilds when `requirements.txt` changes).

### Docker socket isolation

n8n does **not** mount the host Docker socket. It talks to a dedicated `docker-proxy` service (`wollomatic/socket-proxy`) on `tcp://docker-proxy:2375`. The proxy uses per-endpoint regex allowlists pinned to the worker container only:

| Method | Allowed path                                                            |
|--------|--------------------------------------------------------------------------|
| GET    | `/_ping`, `/version`, `/containers/clawstreet-worker/json`               |
| POST   | `/containers/clawstreet-worker/exec`, `/exec/{hex-id}/(start\|resize)`   |

Everything else is denied — n8n cannot `docker ps`, `stop`, `rm`, `run`, mount the host filesystem, or exec into any container other than `clawstreet-worker`. The proxy itself runs read-only, with all capabilities dropped, `no-new-privileges`, and as an unprivileged user in the host's `docker` group (set `DOCKER_GID` in `.env.n8n` to `stat -c '%g' /var/run/docker.sock`).

### Importing workflows

Once the n8n owner account is set up:

```bash
docker exec clawstreet-n8n n8n import:workflow --separate --input=/workflows
# Then activate each workflow in the UI (Settings → Workflows → toggle Active)
```

## Alpaca API

Using **Paper Trading** for development. The free tier provides:
- IEX data feed (15-min delayed stocks, real-time crypto)
- Market movers / screeners
- News articles
- Full order types (market, limit, stop, bracket)
- WebSocket streams for live data

Switch to `paper=False` for live trading with real money (requires SIP data subscription).

## Polygon.io Integration

**Polygon.io** provides historical and real-time market data that complements Alpaca's trading API:

- **Historical OHLCV** — daily, hourly, minute bars for all US stocks (goes back decades)
- **Options chains** — full historical options data with greeks, IV, OI
- **Fundamentals** — financial statements, earnings, dividends
- **Real-time feeds** — WebSocket streaming for trades, quotes, aggregates

### Why Polygon.io alongside Alpaca?

| Data | Alpaca | Polygon.io |
|------|--------|-----------|
| Trading / orders | ✅ Broker | ❌ Data only |
| Real-time quotes | 15-min delayed (free) | ✅ Real-time (paid) |
| Historical bars | Limited | ✅ Full history |
| Options greeks | ✅ Snapshots | ✅ Full historical |
| Fundamentals | ❌ | ✅ Financials, earnings |
| News | ✅ Basic | ✅ Full news feed |

We use **Alpaca for execution** and **Polygon.io for deep historical data and analysis**.

## TODO

### Phase 2 — Data Ingestion & Signals (in progress)
- [x] Connect Polygon.io API (historical OHLCV, options, fundamentals)
- [x] Create `.env.polygon` with API key + Flat Files credentials
- [x] Build OHLCV ingestion script (`ingest_polygon_ohlcv.py` — 1d/5m/15m)
- [x] Build options + greeks ingestion script (`ingest_polygon_options.py`)
- [x] Store Polygon data in `market.*` Postgres tables (ohlcv, options, greeks, iv_rank, fundamentals)
- [x] Backfill historical data for 15 watchlist symbols
- [x] Claude Code + Postgres MCP — direct DB access for research & analysis
- [x] IV rank computation (`compute_iv_rank.py` — 1,576 rows in market.iv_rank)
- [x] Realized volatility computation (`compute_realized_vol.py` — 3,465 rows in market.realized_vol)
- [x] GEX/DEX computation (`compute_gex_dex.py` — 9,350 rows in market.gex_dex, 15 rows in market.gex_dex_overview)
- [x] Watchlist lifecycle (`config/watchlist.yml`, `market.assets.active/backfill_status`, soft-deactivate on remove)
- [x] Per-symbol backfill orchestrator (`scripts/backfill_symbol.py`)
- [x] n8n scheduler with worker container (6 workflows: watchlist_sync, backfill_pending, ohlcv_daily, ohlcv_intraday, options_daily, derived_daily)
- [ ] Historical IV backfill for IV rank calculation
- [ ] Greeks filtering engine — IV regime, delta entry, theta budget
- [ ] Technical analysis engine (EMA, MACD, RSI, VWAP, ATR, ORB)
- [ ] Options flow scanner (unusual activity, IV rank)
- [ ] RSS/News + Reddit scraper pipeline
- [ ] Composite signal scoring & Laws compliance check

### Phase 3 — Strategy & Backtesting
- [ ] Backtesting engine (historical data + simulation)
- [ ] Validate greeks filters against historical data (IV regime, delta ranges)
- [ ] Paper trading mode (Alpaca Paper, 30-day minimum)
- [ ] Position sizing & stop-loss automation (swing: 10%/3:1, long-term: 3-tranche)
- [ ] Correlation analysis & sector exposure monitoring
- [ ] Drawdown circuit breakers (10% daily, 20% weekly, 30% monthly)

## Contributing

This is a private project. See the Obsidian vault for strategy docs and research.