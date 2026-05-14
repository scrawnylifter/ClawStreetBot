# 🦞 ClawStreetBot

Autonomous stock screening, alerts, and trading.

## Tech Stack

| Component | Technology | Purpose |
|-----------|-----------|---------|
| **Knowledge Base** | Obsidian (Docker) | Notes & RAG — strategies, research, API refs |
| **Database** | PostgreSQL 16 | Persistent storage — market data, scraped content, trades |
| **Cache / Queue** | Redis 7 | Real-time price cache, task queue, pub/sub alerts |
| **Version Control** | GitHub (private) | Code, config, and vault tracking |

## Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│                    ClawStreetBot                     │
├──────────┬──────────────┬───────────┬───────────────┤
│ Obsidian │  PostgreSQL   │   Redis   │  Trading Bot  │
│  :3110   │   :5432       │   :6379   │               │
│          │              │           │               │
│ Notes &  │  market.*    │  Price    │  Signals &    │
│ RAG      │  scraper.*   │  cache &  │  Execution   │
│          │  trading.*   │  queues   │               │
└──────────┴──────────────┴───────────┴───────────────┘
```

## Databases

**PostgreSQL** — two databases:
- `clawstreet` — main app (schemas: `market`, `scraper`, `trading`)
- `scraped` — content pipeline (schemas: `feeds`, `social`, `analytics`)

**Redis** — cache layer with append-only persistence

## Project Structure

```
ClawStreetBot/
├── docker-compose.yml        # All services
├── .env.db                   # DB credentials (gitignored)
├── .env.obsidian             # Obsidian config (gitignored)
├── db/init/                  # Postgres init scripts
└── obsidian/vault/           # Knowledge base
    ├── Home.md               # Dashboard
    ├── Project Roadmap.md
    ├── 01-Trading-Strategies/
    ├── 02-Market-Research/
    ├── 03-API-References/
    ├── 04-Risk-Management/
    ├── 05-Indicators/
    ├── 06-Infrastructure/
    └── 07-Templates/
```

## Quick Start

```bash
# Configure credentials
cp .env.db.example .env.db
cp .env.obsidian.example .env.obsidian
# Edit with real passwords

# Launch everything
docker compose up -d

# Connect to Postgres
docker exec -it clawstreet-db psql -U clawstreet -d clawstreet

# Connect to Redis
docker exec -it clawstreet-redis redis-cli -a <password>
```

## Contributing

This is a private project. See the Obsidian vault for strategy docs and research.