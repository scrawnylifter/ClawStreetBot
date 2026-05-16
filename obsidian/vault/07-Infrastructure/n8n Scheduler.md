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

## Workflows (12 total)

### Core Sync
| Workflow | Schedule (ET) | Script | Purpose |
|----------|---------------|--------|---------|
| `watchlist_sync` | Every 5 min | `setup_watchlist.py` | Sync `config/watchlist.yml` → Alpaca + Postgres |
| `backfill_pending` | Every 5 min | `backfill_symbol.py` | Pick up symbols with `backfill_status='pending'` and run full ingestion |

### Daily Data Ingestion
| Workflow | Schedule (ET) | Script | Purpose |
|----------|---------------|--------|---------|
| `options_daily` | Mon–Fri 17:55 | `ingest_polygon_options.py` | Options contracts + greeks snapshot |
| `ohlcv_daily` | Mon–Fri 18:00 | `ingest_polygon_ohlcv.py --timeframe 1d` | Daily OHLCV bars |
| `fundamentals_daily` | Mon–Fri 19:00 | `ingest_polygon_fundamentals.py` | Quarterly financials (revenue, EPS, market cap) |

### Intraday
| Workflow | Schedule (ET) | Script | Purpose |
|----------|---------------|--------|---------|
| `ohlcv_intraday` | Mon–Fri hourly :05 (09–16) | `ingest_polygon_ohlcv.py --timeframe 5m` + `--timeframe 15m` | Intraday bars |
| `rss_news_scanner` | Mon–Fri every 30m 9:30–16:00 | `ingest_rss_news.py` | RSS + Reddit scraper (articles + posts) |
| `intraday_signal_5m` | Mon–Fri every 5 min 9:30–16:00 | `intraday_signal.py` | Re-score tech factor from 5m bars, threshold alerts |

### Derived Compute Chain
| Workflow | Schedule (ET) | Scripts (chained) | Purpose |
|----------|---------------|-------------------|---------|
| `derived_daily` | Mon–Fri 18:30 | `compute_realized_vol.py` → `compute_iv_rank.py` → `compute_gex_dex.py` → `compute_technical_indicators.py` → `compute_greeks_filter.py` → `compute_iv_outliers.py` | Full derived analytics pipeline |
| `trend_daily` | Mon–Fri 18:00 | `compute_trend.py` | Multi-timeframe trend detection (micro/intermediate/primary) |

### Signal Generation
| Workflow | Schedule (ET) | Script | Purpose |
|----------|---------------|--------|---------|
| `signals_daily` | Mon–Fri 19:30 | `generate_signals.py` → `backtest.py` | Composite signal scoring + daily backtest |

### Weekly
| Workflow | Schedule (ET) | Script | Purpose |
|----------|---------------|--------|---------|
| `regime_weekly` | Sat 11:00 | `regime_backtest.py all` | Regime classification + factor analysis + weight optimization + comparison |

### Pipeline Order

The daily pipeline runs in sequence to ensure data dependencies are met:

```
17:55  options_daily      → market.options, market.greeks
18:00  ohlcv_daily        → market.ohlcv (1d bars)
18:00  trend_daily        → market.trend_status
18:30  derived_daily      → market.realized_vol → market.iv_rank → market.gex_dex → technical_indicators → greeks_filter → iv_outliers
19:00  fundamentals_daily → market.fundamentals
19:30  signals_daily      → trading.signals + backtest
```

Intraday bars, intraday signals, RSS scanner, and watchlist syncs run independently in parallel.

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
./scripts/n8n_api.sh list

# Activate a workflow
./scripts/n8n_api.sh activate <ID>

# Deactivate a workflow
./scripts/n8n_api.sh deactivate <ID>

# Delete a workflow
./scripts/n8n_api.sh delete <ID>
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