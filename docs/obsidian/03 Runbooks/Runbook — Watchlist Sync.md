---
title: Runbook — Watchlist Sync
type: runbook
tags: [runbook, ops, data]
up: ["[[Layer 01 — Data]]"]
created: 2026-06-07
---

# Runbook — Watchlist Sync

**Goal:** Verify / manually trigger the L01 watchlist sync and inspect results.

## Run manually

```bash
docker compose run --rm 01_data python -m layer.main --job watchlist_sync
```

## Inspect the table

```bash
docker compose exec postgres \
  psql -U cbs -d clawstreetbot -c \
  "SELECT name, count(*) AS symbols, max(synced_at) AS last_sync
   FROM market.watchlist GROUP BY name ORDER BY name;"
```

## Healthy signals

- `last_sync` advances every ~60s (n8n schedule).
- Symbol counts match your Alpaca watchlists.
- Logs are structured JSON; look for `watchlist_schema_ensured` then upsert counts.

## Troubleshooting

| Symptom | Likely cause | Action |
|---------|--------------|--------|
| No rows | Alpaca creds unset/invalid | Check `ALPACA_API_KEY/SECRET` in `.env` |
| `synced_at` stale | n8n schedule off / container down | `docker compose ps`, check `cbs-n8n` |
| Missing-vars `ValueError` | required secret empty | Fill `.env` per `.env.example` |

If something breaks, capture it in an [[90 Templates/Incident|Incident]] note.

## Related

- [[Layer 01 — Data]]
- [[Runbook — Bring the Stack Up]]
