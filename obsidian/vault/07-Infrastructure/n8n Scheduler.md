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

## Workflows (22 active)

The decommissioned Polygon ingestion workflows (`ohlcv_daily`, `ohlcv_intraday`, `options_daily`) have been deleted from the repo — replaced by their Alpaca equivalents listed below.

### Core Sync
| Workflow | Schedule (PDT) | Script | Purpose |
|----------|----------------|--------|---------|
| `watchlist_sync` | Every 5 min | `setup_watchlist.py` | Sync `config/watchlist.yml` → Alpaca + Postgres |
| `backfill_pending` | Every 5 min | `backfill_runner.py` | Pick up symbols with `backfill_status='pending'` and run full ingestion |

### Alpaca Data Ingestion (Primary)
| Workflow | Schedule (PDT) | Script | Purpose |
|----------|----------------|--------|---------|
| `alpaca_ohlcv_daily` | Mon–Fri 15:00 | `ingest_alpaca_ohlcv.py --timeframe 1d` | Daily OHLCV bars (1d) with trade_count + VWAP |
| `alpaca_ohlcv_intraday` | Mon–Fri hourly :05 (7–13) | `ingest_alpaca_ohlcv.py --timeframe 15m` then `--timeframe 5m` | Intraday bars (15m + 5m) |
| `alpaca_options_daily` | Mon–Fri 14:55 | `ingest_alpaca_options.py --all` | Options chains + greeks + bid/ask snapshots |

### Polygon Data (Secondary — Fundamentals Only)
| Workflow | Schedule (PDT) | Script | Purpose |
|----------|----------------|--------|---------|
| `fundamentals_daily` | Mon–Fri 16:00 | `ingest_polygon_fundamentals.py` | Quarterly financials (revenue, EPS, market cap) |

### Intraday Signal Detection
| Workflow | Schedule (PDT) | Script | Purpose |
|----------|----------------|--------|---------|
| `intraday_signal_5m` | Mon–Fri every 5 min 6–12 | `intraday_signal.py` | Re-score tech factor from 5m bars, threshold alerts |
| `ema_crossover_15m` | Mon–Fri every 15 min 6:30–13 | `detect_ema_crossover_15m.py` | 15m EMA crossover + real-time Alpaca snapshot enrichment |
| `rss_news_scanner` | Mon–Fri every 30 min 6–13 | `ingest_rss_news.py` | RSS + Reddit scraper (articles + posts) |

### Derived Compute Chain
| Workflow | Schedule (PDT) | Scripts (chained) | Purpose |
|----------|----------------|-------------------|---------|
| `derived_daily` | Mon–Fri 15:30 | `compute_realized_vol.py` → `compute_iv_rank.py` → `compute_gex_dex.py` → `compute_technical_indicators.py` → `compute_greeks_filter.py` → `compute_iv_outliers.py` | Full derived analytics pipeline |
| `trend_daily` | Mon–Fri 11:00 | `compute_trend.py` | Multi-timeframe trend detection (micro/intermediate/primary) |

### Signal Generation
| Workflow | Schedule (PDT) | Script | Purpose |
|----------|----------------|--------|---------|
| `ema_crossover_detector` | Mon–Fri 7:00 | `detect_ema_crossover.py` | Daily EMA 9/21 crossover detection → Telegram alert (supplementary) |
| `ema_crossover_15m` | Mon–Fri every 15 min 6:30–13 | `detect_ema_crossover_15m.py` | 15m EMA crossover + real-time Alpaca snapshot (supplementary) |
| **`setup_scanner`** | **Mon–Fri every 15 min 6–12** | **`scan_setups.py`** | **★ PRIMARY — 8-gate BUY signal scanner (trend, ADX, RSI, IV rank, IV-RV spread, premium cost, DTE, R:R). Silence = no signal.** |
| `signals_daily` | Mon–Fri 16:30 | `generate_signals.py` → `backtest.py` | Composite signal scoring + daily backtest |

### Alert Dispatch + Execution + Exits (Phase 5B — shipped)
| Workflow | Schedule (PDT) | Script | Purpose |
|----------|----------------|--------|---------|
| `alert_dispatch` | Mon–Fri every 1min 6–13 | `alert_telegram.py --limit 20` | Reads `signal_alerts WHERE telegram_sent=FALSE`; sends with the 4-button keyboard |
| `execute_trade` | Mon–Fri every 1min 6–13 | `execute_trade.py --confirm --limit 5` | Submits approved orders to Alpaca paper |
| `reconcile_orders` | Mon–Fri every 1min 6–14 | `reconcile_orders.py` | Polls BUY fill state → `trading.positions`; FOR UPDATE SKIP LOCKED |
| `reconcile_exits` | Mon–Fri every 1min 6–14 | `reconcile_exits.py` | Polls SELL/TP1-partial fills → close position + record P&L + `status='exited'` |
| `exit_monitor` | Mon–Fri every 5min 6–13 | `exit_monitor.py --confirm --limit 20` | TP/SL/time-stop decision tree; submits closes with client_order_id |
| `equity_snapshot_daily` | Mon–Fri 14:30 | `snapshot_equity.py` | Daily equity snapshot for drawdown halt denominator (H8) |

### Weekly
| Workflow | Schedule (PDT) | Script | Purpose |
|----------|----------------|--------|---------|
| `regime_weekly` | Sat 8:00 | `regime_backtest.py all` | Regime classification + factor analysis + weight optimization + comparison |

### Pipeline Order

The daily pipeline runs in sequence to ensure data dependencies are met:

```
14:55  alpaca_options_daily → market.options, market.greeks (with bid/ask)
15:00  alpaca_ohlcv_daily   → market.ohlcv (1d bars with trade_count, VWAP)
15:00  trend_daily           → market.trend_status
15:30  derived_daily         → market.realized_vol → market.iv_rank → market.gex_dex → technical_indicators → greeks_filter → iv_outliers
16:00  fundamentals_daily    → market.fundamentals (Polygon)
16:30  signals_daily         → trading.signals + backtest
```

Intraday bars, intraday signals, EMA crossover detection, **setup scanner**, RSS scanner, and watchlist syncs run independently in parallel.

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

- **Workflows not firing:** Check they're Active (toggle in UI or API). Verify schedule cron expressions match PDT timezone.
- **Exec command fails:** Check worker container is running (`docker ps`). Check `.env.db` is present inside worker container. Check proxy logs (`docker logs clawstreet-docker-proxy`).
- **Duplicate workflows:** Can happen from repeated imports. Delete via API or UI — keep only the active version of each.
- **n8n owner account:** Must be created via UI at `http://localhost:5678` before API access works. After that, all operations can use the API key.

## See Also

- [[Database Architecture]] — Postgres schemas, Redis usage
- [[Alpaca Data Pipeline]] — Alpaca ingestion scripts (OHLCV, options, snapshots)
- [[Polygon.io API]] — Still active for fundamentals and flat-file backfill
- [[Watchlist]] — Tracked symbols and lifecycle