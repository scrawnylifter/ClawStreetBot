---
title: Infrastructure
type: architecture
tags: [architecture, infra]
up: ["[[System Overview]]"]
related: ["[[Layer 01 — Data]]"]
created: 2026-06-07
---

# Infrastructure

All services run under `docker-compose.yml`. Config and secrets come from `.env`
(template: `.env.example`) and are surfaced to code only via `config/settings.py`.

## Services

| Service | Image | Role | Port(s) |
|---------|-------|------|---------|
| `cbs-postgres` | postgres | System of record (`market`, `n8n` schemas) | 5432 |
| `cbs-redis` | redis | Live signals, TTLs (`SIGNAL_TTL_MINUTES`) | 6379 |
| `cbs-n8n` | n8nio/n8n | Scheduling & orchestration | 5678 |
| `cbs-obsidian` | lscr.io/linuxserver/obsidian | This knowledge base (web UI + REST/MCP) | 3000 / 3001 / 27123 / 27124 |
| `cbs-01-data` | built from `docker/Dockerfile.layer` | L01 data jobs | — |

## Externals

- **Alpaca** — brokerage + market data. Paper API by default
  (`ALPACA_BASE_URL=https://paper-api.alpaca.markets`).
- **Telegram** — alert delivery and approval inbox (`03 Alert`, `04 Approval`).

## Obsidian (this vault)

- Vault path in container: `/config/obsidian/vaults/ClawStreetBot`
- Mounted from host: `./docs/obsidian`
- Web UI: `http://<host>:3000` (HTTPS on 3001). Credentials via
  `OBSIDIAN_USER` / `OBSIDIAN_PASSWORD`.
- Community plugins: **Excalidraw**, **ExcaliBrain**, and **Dataview**
  (pre-installed in `.obsidian/plugins/`). Dataview is a hard dependency of
  ExcaliBrain — without it ExcaliBrain disables itself.

### REST API + MCP

The container also runs the **Local REST API with MCP** plugin so agents (Claude
Code, Hermes) read/write/search notes via tool calls. Ports 27123/27124 are bound
to `127.0.0.1` only and gated by `OBSIDIAN_API_KEY`. See
[[Knowledge Base API & MCP]].

> ⚠️ The Obsidian container must **not** be exposed to the internet without a
> reverse proxy enforcing auth.

## Related

- [[System Overview]]
- [[Runbook — Bring the Stack Up]]
- [[ADR-0001 — Single Source of Truth Config]]
