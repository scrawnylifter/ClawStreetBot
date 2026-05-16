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

**`market` schema** — Asset prices, options, and derived analytics
- `market.assets` — Watchlist symbols with lifecycle columns: `active`, `added_at`, `deactivated_at`, `backfill_status`
- `market.ohlcv` — OHLCV candle data (1d, 5m, 15m). Index on `(asset_id, timeframe, timestamp DESC)`
- `market.options` — Options contracts with strike, expiry, type, settlement
- `market.greeks` — Greeks snapshots (delta, gamma, theta, vega, IV) per contract/date
- `market.iv_rank` — IV rank percentiles per symbol/date (1,576 rows)
- `market.realized_vol` — 20-day and 5-day annualized realized volatility + IV-RV spread per symbol/date (3,465 rows)
- `market.gex_dex` — GEX/DEX per strike/expiry per symbol/date (9,350 rows)
- `market.gex_dex_overview` — Net GEX/DEX totals per underlying/date (15 rows)
- `market.fundamentals` — Revenue, EPS, P/E (currently empty, Phase 2)
- `market.ingest_state` — Tracks last-ingested timestamp per symbol/timeframe for incremental updates

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