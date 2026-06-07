---
title: Glossary
type: reference
tags: [reference, glossary]
up: ["[[Home]]"]
created: 2026-06-07
---

# Glossary

| Term | Meaning |
|------|---------|
| **Watchlist** | Alpaca-managed list of symbols; mirrored into `market.watchlist` by [[Layer 01 — Data]]. |
| **Signal** | A scanner-produced trade candidate (symbol, side, score, rationale). |
| **Approval** | Human Telegram yes/no gating execution. See [[ADR-0002 — Human-in-the-Loop Approval]]. |
| **TTL** | Redis expiry on signals (`SIGNAL_TTL_MINUTES`; `daily_signal` = 60 min). |
| **Paper API** | Alpaca's simulated brokerage (`paper-api.alpaca.markets`). No real money. |
| **SSOT** | Single Source of Truth — `config/settings.py`. See [[ADR-0001 — Single Source of Truth Config]]. |
| **Reconcile** | Intent-vs-actual integrity pass. See [[Layer 07 — Reconcile]]. |

→ Back to [[Home]]
