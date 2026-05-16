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

ClawStreetBot tracks a configurable watchlist stored in both Alpaca and Postgres (with sector/industry tags for heat maps). Run the setup script and add your own picks:

```bash
# Edit SYMBOLS in scripts/setup_watchlist.py, then:
python scripts/setup_watchlist.py
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
├── .env.db                     # DB credentials (gitignored)
├── .env.alpaca                 # Alpaca API keys (gitignored)
├── .env.polygon                # Polygon.io API key (gitignored)
├── .env.obsidian               # Obsidian config (gitignored)
├── .venv/                      # Python venv (gitignored)
├── db/init/                    # Postgres init scripts
│   ├── 01_init_databases.sql
│   ├── 02_create_tables.sql
│   └── 03_polygon_tables.sql   # Options, greeks, IV rank, fundamentals, ingest_state
├── scripts/                    # Python scripts
│   ├── explore_data.py         # Alpaca data explorer
│   ├── setup_watchlist.py      # Watchlist setup (Alpaca + Postgres)
│   ├── ingest_polygon_ohlcv.py # OHLCV bars → market.ohlcv (1d/5m/15m)
│   ├── ingest_polygon_options.py # Options contracts + greeks snapshots
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
# Edit each with real passwords/keys

# Launch all services
docker compose up -d

# Install Python dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Set up watchlist in Alpaca + Postgres
python scripts/setup_watchlist.py

# Ingest Polygon.io market data
python scripts/ingest_polygon_ohlcv.py --all-timeframes   # OHLCV bars (1d/5m/15m)
python scripts/ingest_polygon_options.py                    # Options + greeks snapshots

# Explore available data
python scripts/explore_data.py

# Connect to Postgres
docker exec -it clawstreet-db psql -U clawstreet -d clawstreet

# Connect to Redis
docker exec -it clawstreet-redis redis-cli -a <password>
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