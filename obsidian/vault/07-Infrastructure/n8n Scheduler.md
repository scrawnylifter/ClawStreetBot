---
created: 2026-05-17
updated: 2026-05-17
tags: [infrastructure, n8n, scheduler, mOC]
---

# n8n Scheduler

## Overview

ClawStreetBot uses **n8n** as its workflow scheduler, running inside Docker alongside the worker container. All Python ingestion scripts are executed via `docker exec clawstreet-worker python /app/scripts/<name>.py`.

- **UI:** `http://localhost:5678` (LAN: `http://192.168.1.157:5678`)
- **Credentials:** See `.env.n8n` (gitignored)
- **API:** REST API at `<n8n-url>/api/v1/` with `X-N8N-API-KEY` header

## Workflows

| Workflow | Schedule (ET) | Script | Purpose |
|----------|---------------|--------|---------|
| `watchlist_sync` | Every 5 min | `setup_watchlist.py` | Sync `config/watchlist.yml` → Alpaca + Postgres |
| `backfill_pending` | Every 5 min | `backfill_symbol.py` | Pick up symbols with `backfill_status='pending'` and run full ingestion |
| `ohlcv_daily` | Mon–Fri 18:00 | `ingest_polygon_ohlcv.py --timeframe 1d` | Daily OHLCV bars |
| `ohlcv_intraday` | Mon–Fri hourly :05 (09–16) | `ingest_polygon_ohlcv.py --timeframe 5m` + `--timeframe 15m` | Intraday bars |
| `options_daily` | Mon–Fri 17:55 | `ingest_polygon_options.py` | Options contracts + greeks snapshot |
| `derived_daily` | Mon–Fri 18:30 | `compute_realized_vol.py` → `compute_iv_rank.py` → `compute_gex_dex.py` | Derived analytics chain |

### Pipeline Order

The daily pipeline runs in sequence to ensure data dependencies are met:

```
17:55  options_daily    → market.options, market.greeks
18:00  ohlcv_daily      → market.ohlcv (1d bars)
18:30  derived_daily    → market.realized_vol → market.iv_rank → market.gex_dex
```

Intraday bars and watchlist syncs run independently in parallel.

### Watchlist Lifecycle

1. Add symbol to `config/watchlist.yml`
2. `watchlist_sync` picks it up → inserts into `market.assets` with `backfill_status='pending'`
3. `backfill_pending` runs `backfill_symbol.py <SYMBOL>` → full OHLCV → options → IV → RV → GEX chain
4. On completion, `backfill_status` moves to `'done'`
5. Remove symbol from YAML → `active=false`, `deactivated_at=NOW()`, ingestion stops (all queries filter `WHERE active = TRUE`)

## Security Architecture

n8n does **not** mount the host Docker socket. It communicates with Docker through a dedicated **`wollomatic/socket-proxy`** sidecar on `tcp://docker-proxy:2375`.

### Allowed endpoints (proxy allowlist)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/_ping`, `/version` | Health checks |
| GET | `/containers/clawstreet-worker/json` | Inspect worker |
| POST | `/containers/clawstreet-worker/exec` | Execute commands in worker |
| POST | `/exec/{hex-id}/start`, `/exec/{hex-id}/resize` | Start/resize exec sessions |

**Everything else is denied.** n8n cannot `docker ps`, `stop`, `rm`, `run`, mount volumes, or exec into any container other than `clawstreet-worker`.

The proxy itself runs:
- `read_only: true`
- `cap_drop: ALL`
- `security_opt: ["no-new-privileges"]`
- As an unprivileged user in the host's `docker` group

## API Management

```bash
# List all workflows
curl -s -H "X-N8N-API-KEY: $N8N_API_KEY" http://localhost:5678/api/v1/workflows | python3 -m json.tool

# Activate a workflow
curl -s -X POST -H "X-N8N-API-KEY: $N8N_API_KEY" http://localhost:5678/api/v1/workflows/<ID>/activate

# Deactivate a workflow
curl -s -X POST -H "X-N8N-API-KEY: $N8N_API_KEY" http://localhost:5678/api/v1/workflows/<ID>/deactivate

# Delete a workflow
curl -s -X DELETE -H "X-N8N-API-KEY: $N8N_API_KEY" http://localhost:5678/api/v1/workflows/<ID>

# Import workflows from JSON
docker exec clawstreet-n8n n8n import:workflow --separate --input=/workflows
```

## Troubleshooting

- **Workflows not firing:** Check they're Active (toggle in UI or API). Verify schedule cron expressions match Eastern time.
- **Exec command fails:** Check worker container is running (`docker ps`). Check `.env.db` is present inside worker container. Check proxy logs (`docker logs clawstreet-docker-proxy`).
- **Duplicate workflows:** Can happen from repeated imports. Delete via API or UI — keep only the active version of each.
- **n8n owner account:** Must be created via UI at `http://localhost:5678` before API access works. After that, all operations can use the API key.

## See Also

- [[Database Architecture]] — Postgres schemas, Redis usage
- [[Polygon.io API]] — Data ingestion scripts and API details
- [[Watchlist]] — Tracked symbols and lifecycle