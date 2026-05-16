---
created: 2026-05-14
updated: 2026-05-16
tags: [home, mOC]
---

# 🦞 ClawStreetBot — Knowledge Base

Welcome to the ClawStreetBot knowledge base. This vault serves as the central brain for our trading assistant — fundamentals, strategies, research, and infrastructure all live here.

## Logic Flow

Every trade follows this chain:

**Laws** (01-Fundamentals) → **Entry Criteria** (01-Fundamentals) → **Strategy** (02-Strategies) → **Risk Management** (05-Risk-Management) → **Execution**

Rules constrain *whether* you trade. Criteria trigger *when* to look. Strategies define *how* to act.

## Navigation

| Folder | Purpose |
|--------|---------|
| [[01-Fundamentals]] | **[[Laws of Trading]]** + **[[Trade Entry Criteria]]** — rules & triggers |
|| [[02-Strategies]] | **[[Swing Trading]]** · **[[Long-Term Holding]]** · [[EMA Crossover]] · [[ORB]] · [[Buy the 5% Dip]] · **[[Greeks Strategy]]** |
|| [[03-Market-Research]] | Market research, asset analysis, **[[Watchlist]]** · **[[Backtesting Architecture]]** |
|| [[04-API-References]] | Broker/exchange API docs — **[[Alpaca API]]** · **[[Polygon.io API]]** |
| [[05-Risk-Management]] | **[[Risk Management]]** · **[[Position Sizing]]** · **[[Loss Limits]]** · **[[Correlation Risk]]** |
| [[06-Indicators]] | Technical indicators, calculations, usage notes |
| [[07-Infrastructure]] | Deployment, monitoring, **[[Database Architecture]]** |
| [[08-Templates]] | Reusable note templates |

## Quick Links

- [[Project Roadmap]]
- [[Laws of Trading]] — 8 non-negotiable rules
- [[Trade Entry Criteria]] — When & why we enter trades
- [[Watchlist]] — 15 stocks with sector/industry breakdown
- [[Risk Management]] — Position sizing, loss limits, correlation risk
- [[Alpaca API]] — Trading execution, orders, positions
- [[Polygon.io API]] — Historical data, fundamentals, options chains
- [[Greeks Strategy]] — IV regime, delta entry/exit, theta budgets, vanna risk
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

**Phase 2 — Data Ingestion & Signals** 🔄
- [x] **Polygon.io connected → Postgres** (see [[Polygon.io API]])
- [x] **OHLCV ingestion** — 1d/5m/15m bars for 15 watchlist stocks (see [[Backtesting Architecture]])
- [x] **Options + greeks ingestion** — 20k contracts, daily snapshots (see [[Backtesting Architecture]])
- [x] **Claude Code + Postgres MCP** — direct DB access for research & analysis
- [x] **PDT rules documented** — 3 day-trade limit, emergency-only 3rd, NEVER 4th
- [x] **IV rank / realized vol / GEX-DEX computed** — `market.iv_rank` (1,576), `market.realized_vol` (3,465), `market.gex_dex` (9,350) + overview (15)
- [x] **Watchlist lifecycle** — `config/watchlist.yml` source-of-truth; add / soft-deactivate / re-add via `market.assets.active` + `backfill_status`
- [x] **n8n scheduler** — 6 workflows drive watchlist sync, pending backfills, OHLCV (daily + intraday), options, and derived computes (RV → IV-rank → GEX). Worker container execs Python scripts; secrets via `.env.n8n`.
- [ ] **Historical IV backfill** for IV rank calculation
- [ ] **Greeks filtering engine** — IV regime, delta entry, theta budget (see [[Greeks Strategy]])
- [ ] RSS/News + Reddit scraper pipeline
- [ ] Technical analysis engine (EMA, MACD, RSI, VWAP, ATR, ORB)
- [ ] Options flow scanner (unusual activity, IV rank)
- [ ] Composite signal scoring & Laws compliance check
- [ ] Telegram alerts + Obsidian trade journal

**Phase 3 — Strategy & Backtesting** 🔜
- [ ] Backtesting engine (historical data + simulation, see [[Backtesting Architecture]])
- [ ] Validate greeks filters against historical data (IV regime, delta ranges)
- [ ] Paper trading mode (Alpaca Paper, 30-day minimum)
- [ ] Position sizing & stop-loss automation (swing: 10%/3:1, long-term: 3-tranche)
- [ ] Correlation analysis & sector exposure monitoring
- [ ] Drawdown circuit breakers (10% daily, 20% weekly, 30% monthly)