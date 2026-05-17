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

## Workflows

| File                            | Schedule (PDT)                | Calls                                           |
|---------------------------------|-------------------------------|--------------------------------------------------|
| `watchlist_sync.json`           | every 5 min                   | `setup_watchlist.py`                             |
| `backfill_pending.json`         | every 5 min                   | `backfill_runner.py` (per pending symbol)       |
| `ohlcv_daily.json`              | Mon–Fri 15:00                 | `ingest_polygon_ohlcv.py --timeframe 1d`        |
| `ohlcv_intraday.json`           | Mon–Fri hourly :05 (7–13 PDT) | `ingest_polygon_ohlcv.py --timeframe 5m` + `15m` |
| `options_daily.json`             | Mon–Fri 14:55                 | `ingest_polygon_options.py`                      |
| `derived_daily.json`             | Mon–Fri 15:30                 | RV → IV-rank → GEX → tech → greeks → outliers  |
| `fundamentals_daily.json`        | Mon–Fri 16:00                 | `ingest_polygon_fundamentals.py`                |
| `rss_news_scanner.json`          | Mon–Fri every 30m 6–13 PDT   | `ingest_rss_news.py`                             |
| `signals_daily.json`             | Mon–Fri 16:30                 | `generate_signals.py --all` + daily backtests   |
| `intraday_signal_5m.json`        | Mon–Fri every 5min 6–12 PDT  | `intraday_signal.py --threshold 60`              |
| `trend_daily.json`               | Mon–Fri 11:00                 | `compute_trend.py --backfill --days 400`        |
| `regime_weekly.json`             | Sat 8:00                      | `regime_backtest.py all — classify + optimize`  |
| `ema_crossover_detector.json`    | Mon–Fri 7:00                  | `detect_ema_crossover.py` → `alert_telegram.py` |

All execute via `docker exec clawstreet-worker python /app/scripts/<name>.py …`.
All cron schedules use `America/Los_Angeles` (PDT) timezone.

## Docker socket isolation

The `docker` CLI inside the n8n container is pointed at `tcp://docker-proxy:2375` (set via `DOCKER_HOST` in `docker-compose.yml`), **not** at the host Docker socket. The `docker-proxy` service (`wollomatic/socket-proxy`) only whitelists the exec endpoints for `clawstreet-worker`:

| Method | Path                                                                                    |
|--------|------------------------------------------------------------------------------------------|
| GET    | `/_ping`, `/version`, `/containers/clawstreet-worker/json`, `/exec/{hex-id}/json`       |
| HEAD   | `/_ping`, `/version`                                                                     |
| POST   | `/containers/clawstreet-worker/exec`, `/exec/{hex-id}/(start\|resize)`                    |

`docker ps`, `docker run`, `docker stop`, `docker rm`, host volume mounts, and exec into any other container are all denied at the proxy. If you add a workflow that needs to exec into a different container, extend the `-allowGET=`/`-allowPOST=` regex on the `docker-proxy` service in `docker-compose.yml`.

## After editing a workflow in the UI

Export it back into this directory to keep git in sync:

```bash
docker exec clawstreet-n8n n8n export:workflow --all --output=/workflows --pretty
```