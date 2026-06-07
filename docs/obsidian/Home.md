---
title: Home
type: moc
tags: [moc, home]
created: 2026-06-07
---

# 🦞 ClawStreetBot — Knowledge Base

> Automated paper-trading pipeline. Seven layers, one source of truth.
> This vault is the living documentation for the system. Start here.

## 🗺️ Maps of Content

- [[System Overview]] — the big picture and data flow
- [[Infrastructure]] — Postgres, Redis, n8n, Obsidian, Alpaca, Telegram

## 🧱 The Seven Layers

| # | Layer | Responsibility |
|---|-------|----------------|
| 01 | [[Layer 01 — Data]] | Sync market/account data into Postgres |
| 02 | [[Layer 02 — Scanner]] | Scan watchlist, generate candidate signals |
| 03 | [[Layer 03 — Alert]] | Push signals to Telegram |
| 04 | [[Layer 04 — Approval]] | Human-in-the-loop approve/reject |
| 05 | [[Layer 05 — Execution]] | Place orders via Alpaca |
| 06 | [[Layer 06 — Exit]] | Manage exits (stops, targets, TTL) |
| 07 | [[Layer 07 — Reconcile]] | Reconcile fills vs. intent, true up state |

## 📊 Trading Logic

- **Strategies** (`07 Strategies/`) — [[Liquidity Sweeps]]
- **Playbooks** (`08 Playbooks/`) — [[Liquidity Sweep Reversal Playbook]]
- **Risk Management** (`09 Risk Management/`) — [[Position Sizing]],
  [[Stop Loss Rules]], [[Portfolio Correlation]], [[Max Drawdown Limits]]

## 📒 Operations

- [[Runbook — Bring the Stack Up]]
- [[Runbook — Watchlist Sync]]
- [[Decisions]] — Architecture Decision Records (ADRs)
- [[05 Daily/2026-06-07|Today's Daily Note]]

## 🧠 Visual Thinking

- [[99 Diagrams/System Architecture.excalidraw|System Architecture (Excalidraw)]]
- ExcaliBrain: open the command palette → **ExcaliBrain: Open** to explore the
  link graph driven by the `up` / `down` / `related` frontmatter fields.

## ✍️ Templates

New notes scaffold from [[90 Templates/Layer Note|Layer Note]],
[[90 Templates/Runbook|Runbook]], [[90 Templates/ADR|ADR]],
[[90 Templates/Daily Note|Daily Note]], [[90 Templates/Incident|Incident]], [[90 Templates/Strategy|Strategy]], [[90 Templates/Playbook|Playbook]], and [[90 Templates/Risk Rule|Risk Rule]].
