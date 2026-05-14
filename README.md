# 🦞 ClawStreetBot

Autonomous stock screening, alerts, and trading.

## Tech Stack

| Component | Technology | Purpose |
|-----------|-----------|---------|
| **Broker / Data** | Alpaca (alpaca-py) | Trading, market data, news, screeners |
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
└──────────┴───────────┴──────────┴──────────┴────────────┘
```

## Watchlist (15 stocks)

| Symbol | Name | Sector | Industry |
|--------|------|--------|----------|
| NVDA | NVIDIA | Technology | Semiconductors |
| AMD | AMD | Technology | Semiconductors |
| MU | Micron | Technology | Semiconductors |
| WDC | Western Digital | Technology | Data Storage |
| STX | Seagate | Technology | Data Storage |
| APLD | Applied Digital | Technology | Data Centers / Cloud |
| IREN | IREN Limited | Technology | Data Centers / Cloud |
| NBIS | Nebius Group | Technology | Data Centers / Cloud |
| CIFR | Cipher Digital | Technology | Data Centers / Cloud |
| RDDT | Reddit | Technology | Social Media |
| SERV | Serve Robotics | Technology | Robotics |
| RKLB | Rocket Lab | Industrials | Aerospace & Defense |
| ASTS | AST SpaceMobile | Comm Services | Satellite |
| OKLO | Oklo | Energy | Nuclear |
| NVO | Novo-Nordisk | Healthcare | Pharmaceuticals |

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
├── .env.obsidian               # Obsidian config (gitignored)
├── .venv/                      # Python venv (gitignored)
├── db/init/                    # Postgres init scripts
│   ├── 01_init_databases.sql
│   └── 02_create_tables.sql
├── scripts/                    # Python scripts
│   ├── explore_data.py         # Alpaca data explorer
│   └── setup_watchlist.py      # Watchlist setup (Alpaca + Postgres)
└── obsidian/vault/             # Knowledge base
    ├── Home.md                 # Dashboard
    ├── Project Roadmap.md
    ├── 01-Trading-Strategies/
    ├── 02-Market-Research/
    │   └── Watchlist.md
    ├── 03-API-References/
    │   └── Alpaca API.md
    ├── 04-Risk-Management/
    ├── 05-Indicators/
    ├── 06-Infrastructure/
    │   └── Database Architecture.md
    └── 07-Templates/
        ├── Strategy Template.md
        └── API Reference Template.md
```

## Quick Start

```bash
# Configure credentials
cp .env.db.example .env.db
cp .env.obsidian.example .env.obsidian
cp .env.alpaca.example .env.alpaca
# Edit each with real passwords/keys

# Launch all services
docker compose up -d

# Install Python dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install alpaca-py psycopg2-binary

# Set up watchlist in Alpaca + Postgres
python scripts/setup_watchlist.py

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

## Contributing

This is a private project. See the Obsidian vault for strategy docs and research.