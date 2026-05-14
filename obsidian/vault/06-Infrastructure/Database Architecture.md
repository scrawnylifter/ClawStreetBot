---
created: 2026-05-14
updated: 2026-05-14
tags: [infrastructure, database, mOC]
---

# Database Architecture

## Overview

ClawStreetBot uses a **PostgreSQL + Redis** stack, both running in Docker.

## Connection Details

> ⚠️ Real credentials live in `.env.db` (gitignored). See `.env.db.example` for the template.

| Service | Host | Port | Database |
|---------|------|------|----------|
| PostgreSQL | localhost | 5432 | `clawstreet` (app) / `scraped` (content) |
| Redis | localhost | 6379 | — (password-protected) |

## PostgreSQL Schema Design

### `clawstreet` database

**`market` schema** — Asset prices and metadata
- `market.assets` — Stocks, crypto, forex definitions
- `market.ohlcv` — OHLCV candle data (all timeframes)
- Index on `(asset_id, timeframe, timestamp DESC)` for fast lookups

**`scraper` schema** — Scraped content from the internet
- `scraper.sources` — RSS feeds, Twitter, Reddit, etc.
- `scraper.articles` — News articles with sentiment + ticker tags
- `scraper.posts` — Social media posts with engagement data (JSONB)
- GIN indexes on `symbols[]` for fast ticker lookups
- Chronological indexes on publish dates

**`trading` schema** — Signal generation and position management
- `trading.signals` — Buy/sell/hold signals with confidence scores
- `trading.positions` — Open/closed positions with P&L tracking

### `scraped` database
- `feeds` schema
- `social` schema
- `analytics` schema

## Redis Usage

Redis serves as a fast cache layer:
- Real-time price caching (latest tick for each symbol)
- Task queue for the scraping pipeline
- Pub/sub for live signal alerts
- Session/rate-limit tracking for API calls

## Docker Commands

```bash
# Start all services
docker compose up -d

# Stop all services
docker compose down

# View logs
docker compose logs -f postgres
docker compose logs -f redis

# Connect to Postgres
docker exec -it clawstreet-db psql -U clawstreet -d clawstreet

# Connect to Redis
docker exec -it clawstreet-redis redis-cli -a <password>
```

## Future Extensions
- **TimescaleDB** extension for efficient time-series storage of OHLCV data
- **pgvector** extension for embedding-based similarity search on articles
- Redis Streams for event-driven architecture