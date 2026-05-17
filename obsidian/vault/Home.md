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
| [[02-Strategies]] | **[[Swing Trading]]** · **[[Long-Term Holding]]** · [[EMA Crossover]] · [[ORB]] · [[Buy the 5% Dip]] · **[[Greeks Strategy]]** · [[Liquidity — 5m Day Trading]] |
| [[03-Market-Research]] | Market research, asset analysis, **[[Watchlist]]** · **[[Backtesting Architecture]]** |
| [[04-API-References]] | Broker/exchange API docs — **[[Alpaca API]]** · **[[Alpaca Data Pipeline]]** · **[[Polygon.io API]]** |
| [[05-Risk-Management]] | **[[Risk Management]]** · **[[Position Sizing]]** · **[[Loss Limits]]** · **[[Correlation Risk]]** |
| [[06-Indicators]] | Technical indicators, calculations, usage notes |
| [[07-Infrastructure]] | **[[Database Architecture]]** · **[[n8n Scheduler]]** · **[[Telegram Alert System]]** (v2 trade setups + exits) · **[[Order Execution Engine]]** · **[[Monitoring & Dashboards]]** |
| [[08-Templates]] | Reusable note templates |

## Quick Links

- [[Project Roadmap]]
- [[Laws of Trading]] — 8 non-negotiable rules
- [[Trade Entry Criteria]] — When & why we enter trades
- [[Watchlist]] — 16 stocks with sector/industry breakdown
- [[Risk Management]] — Position sizing, loss limits, correlation risk
- [[Alpaca API]] — Trading execution, orders, positions
- [[Alpaca Data Pipeline]] — OHLCV ingestion, options chains, real-time snapshots (Phase 5 migration)
- [[Polygon.io API]] — Fundamentals, flat-file backfill (secondary data source)
- [[Greeks Strategy]] — IV regime, delta entry/exit, theta budgets, vanna risk
- [[Database Architecture]] — Postgres schemas, Redis usage
- [[n8n Scheduler]] — 18 workflows (15 active, 3 deactivated), Docker socket isolation
- [[Telegram Alert System]] — Strategy-specific trade alerts with entry + exit plans (EMA, ORB, Dip)
- [[Order Execution Engine]] — Alpaca paper trading with Laws compliance
- [[Monitoring & Dashboards]] — Portfolio, signals, pipeline health, risk visibility

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
- [x] **n8n scheduler** — 18 workflows (15 active, 3 deactivated) driving all ingestion, compute, and signal generation
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

**Phase 5A — Signal Detection & Alerts (in progress)** 🔧
- [x] **EMA crossover detector** (`detect_ema_crossover.py`) — 9/21 cross + ADX>25, writes to `market.signal_alerts` *(supplementary)*
- [x] **Signal alerts table** (`015_signal_alerts.sql`) — full trade plan storage (entry, stops, TP, trend context, greeks, invalidation)
- [x] **Telegram alert sender** (`alert_telegram.py`) — strategy-specific trade alerts with bid/ask/mid from Alpaca snapshot
- [x] **Alpaca data migration** — OHLCV + options ingestion moved from Polygon ($108/mo) to Alpaca (free); see [[Alpaca Data Pipeline]]
- [x] **Alpaca OHLCV ingestion** (`ingest_alpaca_ohlcv.py`) — 1d/15m/5m bars with trade_count + VWAP
- [x] **Alpaca options ingestion** (`ingest_alpaca_options.py`) — chains + greeks + bid/ask
- [x] **Real-time snapshot** (`fetch_alpaca_snapshot.py`) — stock price + best option at signal time
- [x] **15m EMA crossover detector** (`detect_ema_crossover_15m.py`) — intraday signals with live option enrichment *(supplementary)*
- [x] **DB migrations** — `017_ohlcv_alpaca_columns.sql` (trade_count, VWAP), `018_alpaca_options_columns.sql` (bid, ask)
- [x] **n8n workflow migration** — alpaca_ohlcv_daily, alpaca_ohlcv_intraday, alpaca_options_daily (active); old Polygon workflows deactivated
- [x] **★ Setup scanner** (`scan_setups.py`) — **PRIMARY alert mechanism** — 8-gate BUY signal scanner (trend, ADX, RSI, IV rank, IV-RV spread, premium cost, DTE, R:R); silence = no signal
- [x] **★ n8n workflow `setup_scanner`** — runs every 15min during market hours (Mon–Fri 6–12 PDT)
- [ ] **ORB breakout detector** (`detect_orb.py`) — opening range + volume+VWAP
- [ ] **Buy the 5% Dip detector** (`detect_dip.py`) — 5% pullback + thesis check + 3-tranche plan
- [ ] **Options chain filter** (`filter_options.py`) — DTE≥30, delta range, theta budget
- [ ] **Exit Monitors** (see [[Telegram Alert System]])
  - Price-based exits: TP1/TP2/stop, trailing after TP2
  - Invalidation exits: EMA reversal, ORB false breakout, thesis break
  - Greeks deterioration: delta <0.30, theta over budget, IV rank >75%
  - Time stops: ORB flatten before close, EMA 5-10 day review
- [ ] **Alert Delivery + Execution** (see [[Telegram Alert System]])
  - Telegram Y/N approval flow with full trade context (entry + exit plan)
  - Pre-flight checks: Laws 3/5, PDT, drawdown, greeks filters
  - Alpaca bracket orders with tiered exits
  - Risk alerts: drawdown halt, PDT warning, position breach
  - Entry + exit alerts (not just entry — every position has a close plan)