---
title: Runbook — Bring the Stack Up
type: runbook
tags: [runbook, ops]
up: ["[[Infrastructure]]"]
created: 2026-06-07
---

# Runbook — Bring the Stack Up

**Goal:** Start ClawStreetBot from a clean checkout.

**Pre-reqs:** Docker + Compose installed; `.env` filled from `.env.example`
(required secrets: `POSTGRES_PASSWORD`, `REDIS_PASSWORD`, `N8N_PASSWORD`,
`OBSIDIAN_PASSWORD`).

## Steps

1. **Configure**
   ```bash
   cp .env.example .env   # then edit secrets
   ```
2. **Launch**
   ```bash
   docker compose up -d
   ```
3. **Verify services**
   ```bash
   docker compose ps
   ```
   Expect `cbs-postgres`, `cbs-redis`, `cbs-n8n`, `cbs-obsidian`, `cbs-01-data` Up.
4. **Open the UIs**
   - Obsidian: `http://<host>:3000` (this vault)
   - n8n: `http://<host>:5678`
5. **Confirm L01 is syncing** — see [[Runbook — Watchlist Sync]].

## Rollback / reset

```bash
docker compose down            # keep volumes
docker compose down -v         # ⚠️ wipes postgres/redis/n8n data
```

## Related

- [[Infrastructure]]
- [[Runbook — Watchlist Sync]]
