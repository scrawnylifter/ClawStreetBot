---
created: 2026-05-14
updated: 2026-05-14
tags: [home, mOC]
---

# 🦞 ClawStreetBot — Knowledge Base

Welcome to the ClawStreetBot knowledge base. This vault serves as the central brain for our trading assistant — fundamentals, strategies, research, and infrastructure all live here.

## Logic Flow

Every trade follows this chain:

**Laws** (01-Fundamentals) → **Entry Criteria** (01-Fundamentals) → **Strategy** (02-Strategies) → **Execution**

Rules constrain *whether* you trade. Criteria trigger *when* to look. Strategies define *how* to act.

## Navigation

| Folder | Purpose |
|--------|---------|
| [[01-Fundamentals]] | **[[Laws of Trading]]** + **[[Trade Entry Criteria]]** — rules & triggers |
| [[02-Strategies]] | **[[Strategies]]** — step-by-step playbooks (the "how") |
| [[03-Market-Research]] | Market research, asset analysis, **[[Watchlist]]** |
| [[04-API-References]] | Broker/exchange API docs — **[[Alpaca API]]** |
| [[05-Risk-Management]] | Position sizing, stop-loss rules, risk frameworks |
| [[06-Indicators]] | Technical indicators, calculations, usage notes |
| [[07-Infrastructure]] | Deployment, monitoring, **[[Database Architecture]]** |
| [[08-Templates]] | Reusable note templates |

## Quick Links

- [[Project Roadmap]]
- [[Laws of Trading]] — 8 non-negotiable rules
- [[Trade Entry Criteria]] — When & why we enter trades
- [[Watchlist]] — 15 stocks with sector/industry breakdown
- [[Alpaca API]] — Full API reference with quirks & gotchas
- [[Database Architecture]] — Postgres schemas, Redis usage

## Current Status

**Phase 1 — Foundation** ✅
- [x] Project setup (repo, gitignore, Obsidian vault)
- [x] Docker stack (Postgres, Redis, Obsidian)
- [x] Alpaca Paper Trading connected
- [x] Watchlist configured (15 stocks)
- [x] Data explorer working (stocks, options, news)
- [x] Laws of Trading documented
- [x] Trade Entry Criteria documented

**Phase 2 — Data Ingestion & Signals** 🔜
- [ ] RSS/News + Reddit scraper pipeline
- [ ] Technical analysis engine (EMA, MACD, RSI, VWAP, ATR, ORB)
- [ ] Options flow scanner (unusual activity, IV rank)
- [ ] Composite signal scoring & Laws compliance check
- [ ] Telegram alerts + Obsidian trade journal

**Phase 3 — Strategy & Backtesting** 🔜
- [ ] Backtesting engine + 30-day paper trading
- [ ] Position sizing & stop-loss automation