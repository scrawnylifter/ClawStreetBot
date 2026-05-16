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

| File                          | Schedule (ET)            | Calls                                    |
|-------------------------------|--------------------------|------------------------------------------|
| `watchlist_sync.json`         | every 5 min              | `setup_watchlist.py`                     |
| `backfill_pending.json`       | every 5 min              | `backfill_symbol.py $SYMBOL` per pending |
| `ohlcv_daily.json`            | 18:00 Mon–Fri            | `ingest_polygon_ohlcv.py --timeframe 1d` |
| `ohlcv_intraday.json`         | :05 hourly 09–16 Mon–Fri | `ingest_polygon_ohlcv.py --timeframe 5m` + `15m` |
| `options_daily.json`          | 17:55 Mon–Fri            | `ingest_polygon_options.py`              |
| `derived_daily.json`          | 18:30 Mon–Fri            | `compute_realized_vol.py` → `compute_iv_rank.py` → `compute_gex_dex.py` |

All execute via `docker exec clawstreet-worker python /app/scripts/<name>.py …`.

## Docker socket isolation

The `docker` CLI inside the n8n container is pointed at `tcp://docker-proxy:2375` (set via `DOCKER_HOST` in `docker-compose.yml`), **not** at the host Docker socket. The `docker-proxy` service (`wollomatic/socket-proxy`) only whitelists the exec endpoints for `clawstreet-worker`:

| Method | Path                                                                    |
|--------|--------------------------------------------------------------------------|
| GET    | `/_ping`, `/version`, `/containers/clawstreet-worker/json`               |
| POST   | `/containers/clawstreet-worker/exec`, `/exec/{hex-id}/(start\|resize)`   |

`docker ps`, `docker run`, `docker stop`, `docker rm`, host volume mounts, and exec into any other container are all denied at the proxy. If you add a workflow that needs to exec into a different container, extend the `-allowGET=`/`-allowPOST=` regex on the `docker-proxy` service in `docker-compose.yml`.

## After editing a workflow in the UI

Export it back into this directory to keep git in sync:

```bash
docker exec clawstreet-n8n n8n export:workflow --all --output=/workflows --pretty
```
