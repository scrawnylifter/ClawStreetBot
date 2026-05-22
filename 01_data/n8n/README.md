# n8n Workflows

These JSON files are checked into git as source-of-truth. n8n stores its own
copies in the `n8n_data` volume once imported.

## One-time import

```bash
docker compose up -d n8n
docker exec clawstreet-n8n n8n import:workflow --separate --input=/workflows
```

Then open <http://localhost:5678>, log in (creds in `.env.n8n`), and **activate**
each workflow individually (import leaves them inactive).

## Active Workflows (v2)

| File | Schedule (PDT) | Calls |
|------|-----------------|-------|
| `watchlist_sync.json` | every 5 min | `setup_watchlist.py` |
| `backfill_pending.json` | every 5 min | `backfill_runner.py` (per pending symbol) |
| `alpaca_ohlcv_daily.json` | Mon–Fri 15:00 | `ingest_alpaca_ohlcv.py --timeframe 1d` |
| `alpaca_ohlcv_intraday.json` | Mon–Fri hourly :05 (7–13 PDT) | `ingest_alpaca_ohlcv.py --timeframe 5m` + `15m` |
| `alpaca_options_daily.json` | Mon–Fri 14:55 | `ingest_alpaca_options.py` |
| `derived_daily.json` | Mon–Fri 15:30 | RV → IV-rank → GEX → tech → greeks → outliers |
| `trend_daily.json` | Mon–Fri 11:00 | `compute_trend.py --backfill --days 400` |
| `equity_snapshot_daily.json` | Daily | `snapshot_equity.py` |
| `setup_scanner.json` | Mon–Fri every 15min 6–13 PDT | `scan_setups.py` |
| `ema_crossover_detector.json` | Mon–Fri 7:00 | `detect_ema_crossover.py` |
| `ema_crossover_15m.json` | Mon–Fri every 15min 6–13 PDT | `detect_ema_crossover_15m.py` |
| `liquidity_sweep.json` | Mon–Fri every 5min 6–13 PDT | `detect_liquidity_sweep.py` |
| `orb_detector.json` | Mon–Fri every 5min 6–12 PDT | `detect_orb.py` |

## Archived Workflows (v1)

Moved to `archive/n8n-workflows/`. These call archived scripts or v1 pipeline code:

- `alert_dispatch.json`, `execute_trade.json`, `exit_monitor.json`
- `reconcile_orders.json`, `reconcile_exits.json` — v1 pipeline
- `fundamentals_daily.json`, `rss_news_scanner.json` — Polygon / archived scripts
- `signals_daily.json`, `intraday_signal_5m.json` — archived generators
- `regime_weekly.json` — archived backtest

## Docker socket isolation

The `docker` CLI inside the n8n container is pointed at `tcp://docker-proxy:2375` (set via `DOCKER_HOST` in `docker-compose.yml`), **not** at the host Docker socket. The `docker-proxy` service (`wollomatic/socket-proxy`) only whitelists the exec endpoints for `clawstreet-worker`:

| Method | Path |
|--------|------|
| GET | `/_ping`, `/version`, `/containers/clawstreet-worker/json`, `/exec/{hex-id}/json` |
| HEAD | `/_ping`, `/version` |
| POST | `/containers/clawstreet-worker/exec`, `/exec/{hex-id}/(start\|resize)` |

`docker ps`, `docker run`, `docker stop`, `docker rm`, host volume mounts, and exec into any other container are all denied at the proxy. If you add a workflow that needs to exec into a different container, extend the `-allowGET=`/`-allowPOST=` regex on the `docker-proxy` service in `docker-compose.yml`.

## After editing a workflow in the UI

Export it back into this directory to keep git in sync:

```bash
docker exec clawstreet-n8n n8n export:workflow --all --output=/workflows --pretty
```