---
created: 2026-05-14
updated: 2026-05-14
tags: [home, mOC]
---

# 🦞 ClawStreetBot — Knowledge Base

Welcome to the ClawStreetBot knowledge base. This vault serves as the central brain for our trading assistant — strategies, research, API references, risk management, and infrastructure docs all live here.

## Tech Stack

| Component | Technology | Purpose |
|-----------|-----------|---------|
| Broker / Data | Alpaca (alpaca-py) | Trading, market data, news, screeners |
| Database | PostgreSQL 16 | Market data, scraped content, trades |
| Cache / Queue | Redis 7 | Price cache, task queue, pub/sub |
| Knowledge Base | Obsidian (Docker) | Notes, RAG, documentation |
| Language | Python 3.11 | Bot logic, data pipeline, scrapers |

## Navigation

| Folder | Purpose |
|--------|---------|
| [[01-Fundamentals]] | Trading rules, **[[Laws of Trading]]**, **[[Trade Entry Criteria]]** |
| [[02-Market-Research]] | Market research, asset analysis, **[[Watchlist]]** |
| [[03-API-References]] | Broker/exchange API docs — **[[Alpaca API]]** |
| [[04-Risk-Management]] | Position sizing, stop-loss rules, risk frameworks |
| [[05-Indicators]] | Technical indicators, calculations, usage notes |
| [[06-Infrastructure]] | Deployment, monitoring, **[[Database Architecture]]** |
| [[07-Templates]] | Reusable note templates |

## Quick Links

- [[Project Roadmap]]
- [[Watchlist]] — 15 stocks with sector/industry breakdown
- [[Alpaca API]] — Full API reference with quirks & gotchas
- [[Database Architecture]] — Postgres schemas, Redis usage
- [[Architecture Overview]] — System design (in README)

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