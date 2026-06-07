---
title: Layer 01 — Data
type: layer
layer: 1
status: implemented
tags: [layer, data]
up: ["[[System Overview]]"]
down: ["[[Layer 02 — Scanner]]"]
related: ["[[Infrastructure]]", "[[Runbook — Watchlist Sync]]"]
created: 2026-06-07
---

# Layer 01 — Data

**Responsibility:** Mirror Alpaca state (watchlists today; account & bars next)
into Postgres so every downstream layer reads from one consistent store.

**Status:** ✅ Implemented (`01_data/`)

## Jobs

| Job | Entry | Schedule |
|-----|-------|----------|
| `watchlist_sync` | `python -m layer.main --job watchlist_sync` | n8n every 60s |

## How it works

1. `sync/alpaca_client.py` pulls watchlists (assets embedded) from Alpaca.
2. `db/watchlist.py` ensures the `market.watchlist` table (migration
   `001_watchlist.sql`) then **upserts** rows on `ON CONFLICT (id, symbol)`.
3. Stale rows (no longer on the watchlist) are deleted in the **same
   transaction** as the upsert, so the table is always internally consistent.

## Data model — `market.watchlist`

One row per `(watchlist_id, symbol)`. Denormalized: Alpaca embeds assets in
`GET /watchlists/{id}`, so we flatten into a single table. Indexed on `symbol`,
`name`, and `synced_at`.

## Orchestration

n8n workflow **"L01 Data — watchlist sync"** (`n8n/workflows/l01_watchlist_sync.json`):
`Every 60 seconds` → `Run watchlist_sync`.

## Hand-off

Downstream → [[Layer 02 — Scanner]] reads `market.watchlist` for the symbol
universe to scan.

## Related

- [[Runbook — Watchlist Sync]]
- [[Infrastructure]]
