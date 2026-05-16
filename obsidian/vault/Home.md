---
created: 2026-05-14
updated: 2026-05-17
tags: [home, mOC]
---

# ClawStreetBot — Knowledge Base

Welcome to the ClawStreetBot knowledge base. This vault serves as the central brain for our trading assistant — fundamentals, strategies, research, and infrastructure all live here.

## Logic Flow

Every trade follows this chain:

**Laws** (01-Fundamentals) → **Entry Criteria** (01-Fundamentals) → **Strategy** (02-Strategies) → **Risk Management** (05-Risk-Management) → **Execution**

Rules constrain *whether* you trade. Criteria trigger *when* to look. Strategies define *how* to act.

## Navigation

| Folder | Purpose |
|--------|---------|
| [[01-Fundamentals]] | **[[Laws of Trading]]** + **[[Trade Entry Criteria]]** — rules & triggers |
| [[02-Strategies]] | **[[Swing Trading]]** · **[[Long-Term Holding]]** · [[EMA Crossover]] · [[ORB]] · [[Buy the 5% Dip]] · **[[Greeks Strategy]]** |
| [[03-Market-Research]] | Market research, asset analysis, **[[Watchlist]]** · **[[Backtesting Architecture]]** |
| [[04-API-References]] | Broker/exchange API docs — **[[Alpaca API]]** · **[[Polygon.io API]]** |
| [[05-Risk-Management]] | **[[Risk Management]]** · **[[Position Sizing]]** · **[[Loss Limits]]** · **[[Correlation Risk]]** |
| [[06-Indicators]] | Technical indicators, calculations, usage notes |
| [[07-Infrastructure]] | Deployment, monitoring, **[[Database Architecture]]** · **[[n8n Scheduler]]** |
| [[08-Templates]] | Reusable note templates |

## Quick Links

- [[Project Roadmap]]
- [[Laws of Trading]] — 8 non-negotiable rules
- [[Trade Entry Criteria]] — When & why we enter trades
- [[Watchlist]] — 16 stocks with sector/industry breakdown
- [[Risk Management]] — Position sizing, loss limits, correlation risk
- [[Alpaca API]] — Trading execution, orders, positions
- [[Polygon.io API]] — Historical data, fundamentals, options chains
- [[Greeks Strategy]] — IV regime, delta entry/exit, theta budgets, vanna risk
- [[Database Architecture]] — Postgres schemas, Redis usage
- [[n8n Scheduler]] — 12 workflows, Docker socket isolation, API management

## Current Status

**Phase 1 — Foundation** ✅
- [x] Project setup (repo, gitignore, Obsidian vault)
- [x] Docker stack (Postgres, Redis, Obsidian)
- [x] Alpaca Paper Trading connected
- [x] Watchlist configured (16 stocks)
- [x] Data explorer working (stocks, options, news)
- [x] Laws of Trading documented
- [x] Trade Entry Criteria documented

**Phase 2 — Data Ingestion & Signals** ✅
- [x] **Polygon.io connected → Postgres** (see [[Polygon.io API]])
- [x] **OHLCV ingestion** — 1d/5m/15m bars for 16 watchlist stocks
- [x] **Options + greeks ingestion** — 20k contracts, daily snapshots
- [x] **Claude Code + Postgres MCP** — direct DB access for research & analysis
- [x] **PDT rules documented** — 3 day-trade limit, emergency-only 3rd, NEVER 4th
- [x] **IV rank / realized vol / GEX-DEX computed** — `market.iv_rank` (1,576), `market.realized_vol` (3,465), `market.gex_dex` (9,350) + overview (15)
- [x] **Watchlist lifecycle** — `config/watchlist.yml` source-of-truth; add / soft-deactivate / re-add via `market.assets.active` + `backfill_status`
- [x] **n8n scheduler** — 12 workflows drive all ingestion, compute, and signal generation
- [x] **Docker socket isolation** — n8n no longer mounts `/var/run/docker.sock`; it talks to a `wollomatic/socket-proxy` sidecar that whitelists only worker exec
- [x] **Greeks filtering engine** — IV regime + delta/theta-budget gating per contract
- [x] **Technical analysis engine** — EMA/RSI/MACD/ATR/VWAP/Bollinger
- [x] **IV outlier detection** — 3σ z-score flags
- [x] **Fundamentals ingestion** — Polygon quarterly financials (98 periods)
- [x] **RSS/News + Reddit scraper** — 69 articles, 75 posts
- [x] **Composite signal scoring** — 6-factor, 0-100 scale with Laws compliance

**Phase 3 — Backtesting** ✅
- [x] Backtesting engine — day/swing/long_term with user trading rules
- [x] ATR-based stop-loss/take-profit (swing: ATR×2.0, SL/TP 30%/50%/trail)
- [x] PDT tracking (3 day-trade limit, 4th = ban)
- [x] Drawdown circuit breakers (10%/20%/30% daily/weekly/monthly)
- [x] Historical signal backfill (7,908 signals across 501 days)

**Phase 4 — Regime & Trend** ✅
- [x] Market regime classifier — bull/bear/transition via SPY SMA + VIX + breadth (501 days)
- [x] Regime-conditional factor analysis — per-regime factor-to-return correlations
- [x] Dynamic weight optimizer — regime-specific scoring weights
- [x] Multi-timeframe trend detection — micro/intermediate/primary via EMA+ADX+price structure
- [x] Trend-aware intraday adjustments — aligned trend boosts, counter-trend penalizes
- [x] 5-minute intraday signal refresh — re-scores tech from 5m bars, threshold alerts

**Remaining Items**
- [ ] Telegram alert system (signals exist but no push notifications)
- [ ] Order execution engine (still paper-only)
- [ ] Position sizing calculator (backtest has it, no standalone tool)
- [ ] Monitoring/dashboards (no visibility beyond raw DB queries)
- [ ] Regime optimizer needs more diverse data (underperforms static — not a code fix)