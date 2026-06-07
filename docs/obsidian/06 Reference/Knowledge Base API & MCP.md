---
title: Knowledge Base API & MCP
type: reference
tags: [reference, infra, mcp, api]
up: ["[[Infrastructure]]"]
created: 2026-06-07
---

# Knowledge Base API & MCP

This vault is reachable by agents (Claude Code, Hermes) as a **structured
knowledge base** — they read/write/search notes through tool calls, not raw
filesystem writes.

## Stack

```
Agent (Claude Code / Hermes)
   │  MCP tool calls (streamable HTTP, JSON-RPC)
   ▼
Obsidian "Local REST API with MCP" plugin   ← runs inside cbs-obsidian
   │  serves /mcp  and  REST /vault, /search, /periodic, ...
   ▼
ClawStreetBot vault  (docs/obsidian)
```

The plugin (`obsidian-local-rest-api` v4.1.3) bundles **both** a REST API and a
native **MCP server**. No separate MCP process is needed.

## Endpoints (localhost only)

| URL | Use |
|-----|-----|
| `http://127.0.0.1:27123/` | REST API (HTTP) |
| `https://127.0.0.1:27124/` | REST API (HTTPS, self-signed) |
| `http://127.0.0.1:27123/mcp` | **MCP server** (streamable HTTP) |

- Bound to `127.0.0.1` on the host (never the LAN); the plugin binds `0.0.0.0`
  *inside* the container so Docker can forward the port.
- Every request needs `Authorization: Bearer $OBSIDIAN_API_KEY`.

## How agents connect

- **Claude Code / Hermes:** project `.mcp.json` registers server `obsidian`
  (`type: http`, header `Bearer ${OBSIDIAN_API_KEY}`). The key lives in
  `.claude/settings.local.json` `env` (gitignored); the server is pre-approved via
  `enabledMcpjsonServers`. `claude mcp get obsidian` → ✓ Connected.

## MCP tools (16)

`vault_list`, `vault_read`, `vault_write`, `vault_append`, `vault_patch`,
`vault_delete`, `vault_move`, `vault_get_document_map`, `active_file_get_path`,
`periodic_note_get_path`, `search_query`, `search_simple`, `tag_list`,
`command_list`, `command_execute`, `open_file`.

## Related

- [[Infrastructure]]
- [[Runbook — Bring the Stack Up]]
