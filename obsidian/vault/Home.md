---
created: 2026-05-14
updated: 2026-05-20
tags: [home, mOC]
---

# ClawStreetBot — Knowledge Base

Welcome to the ClawStreetBot knowledge base. This vault serves as the central brain for our trading assistant — fundamentals, strategies, research, and infrastructure all live here.

## Logic Flow

Every trade follows this chain:

**Laws** (01-Fundamentals) → **Entry Criteria** (01-Fundamentals) → **Unified Checklist** (01-Fundamentals) → **Strategy** (02-Strategies) → **Risk Management** (05-Risk-Management) → **Execution**

Rules constrain *whether* you trade. Criteria trigger *when* to look. The checklist ranks *what matters most*. Strategies define *how* to act.

## Navigation

| Folder | Purpose |
|--------|---------|
| [[01-Fundamentals]] | **[[Laws of Trading]]** + **[[Trade Entry Criteria]]** + **[[Unified Entry & Exit Checklist]]** — rules, triggers & synthesis |
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
- [[Unified Entry & Exit Checklist]] — Synthesized from all strategies, ranked by backtest proof
- [[Watchlist]] — 16 stocks with sector/industry breakdown
- [[Risk Management]] — Position sizing, loss limits, correlation risk
- [[Alpaca API]] — Trading execution, orders, positions
- [[Alpaca Data Pipeline]] — OHLCV ingestion, options chains, real-time snapshots (Phase 5 migration)
- [[Polygon.io API]] — Fundamentals, flat-file backfill (secondary data source)
- [[Greeks Strategy]] — IV regime, delta entry/exit, theta budgets, vanna risk
- [[Database Architecture]] — Postgres schemas, Redis usage
- [[n8n Scheduler]] — 22 active workflows, 42 scripts, 22 migrations; Docker socket isolation
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
- [x] **n8n scheduler** — 22 active workflows driving ingestion, compute, signal generation, alert dispatch, and execution; old Polygon JSONs deleted from repo
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

**Phase 5A — Signal Detection & Alerts ✅**
- [x] **EMA crossover detector** (`detect_ema_crossover.py`) — 9/21 cross + ADX>25 *(supplementary)*
- [x] **Signal alerts table** (`015_signal_alerts.sql`) — full trade plan storage
- [x] **Telegram alert sender** (`alert_telegram.py`) — strategy-specific alerts with bid/ask/mid + 4-button approval keyboard
- [x] **Alpaca data migration** — OHLCV + options ingestion moved from Polygon ($108/mo) to Alpaca (free); see [[Alpaca Data Pipeline]]
- [x] **Alpaca OHLCV/options/snapshot ingestion** — `ingest_alpaca_ohlcv.py`, `ingest_alpaca_options.py`, `fetch_alpaca_snapshot.py`
- [x] **15m EMA crossover detector** (`detect_ema_crossover_15m.py`) — intraday signals with live option enrichment *(supplementary)*
- [x] **DB migrations** — `017_ohlcv_alpaca_columns.sql` (trade_count, VWAP), `018_alpaca_options_columns.sql` (bid, ask)
- [x] **n8n workflow migration** — alpaca_ohlcv_daily, alpaca_ohlcv_intraday, alpaca_options_daily (active); old Polygon JSONs deleted
- [x] **★ Setup scanner** (`scan_setups.py`) — **PRIMARY** 8-gate BUY signal scanner; silence = no signal
- [x] **★ Liquidity sweep scanner** (`detect_liquidity_sweep.py`) — 5m + daily, close-beyond confirmation (PF 1.56)
- [x] **4-hour de-dup cooldown** in every scanner — prevents per-cron-tick alert spam

**Phase 5B — Execution & Exits ✅**
- [x] **Telegram approval flow** — 4-button keyboard via `alert_dispatch` cron; `telegram_callback_listener.py` flips `status` and persists `risk_mode`
- [x] **Pre-flight checks** (`process_approved.py`) — Laws 3/5, PDT (projected, business-day-aware, excludes self-row), drawdown halts (period-start equity + unrealized P&L), greeks filters
- [x] **Alpaca paper execution** (`execute_trade.py`) — `client_order_id`-deduped submits, risk_mode-aware sizing
- [x] **BUY reconciliation** (`reconcile_orders.py`) — `FOR UPDATE SKIP LOCKED` + UNIQUE on `alpaca_order_id` / `position_id` (no phantom positions)
- [x] **Exit monitor** (`exit_monitor.py`) — stop / premium / TP2 / TP1-partial / time-stop (12:45 PDT) / DTE expiry
- [x] **TP1 50% partial close** — submit, reconcile, reduce position quantity, separate `tp1_realized_pnl`
- [x] **SELL reconciliation** (`reconcile_exits.py`) — closes position, writes `realized_pnl`, flips signal_alerts to `status='exited'`
- [x] **Daily equity snapshots** (`snapshot_equity.py` + `equity_snapshot_daily` cron) — drawdown halt denominator
- [x] DB migrations 020–026 + n8n workflows `alert_dispatch`, `execute_trade`, `reconcile_orders`, `reconcile_exits`, `exit_monitor`, `equity_snapshot_daily`

**Phase 5C — Backlog (deferred)** 🔧
- [ ] **ORB breakout detector** (`detect_orb.py`) — opening range + volume + VWAP
- [ ] **Buy the 5% Dip detector** (`detect_dip.py`) — 5% pullback + thesis check + 3-tranche scale-in
- [ ] **Per-risk-mode option selection** — scanner currently binds 0.50-0.70 delta at scan time, before the user picks Conservative/Aggressive (audit M1)
- [ ] **Bracket orders for stock entries** — current entries are naked, exits rely 100% on `exit_monitor` uptime (audit H7)
- [ ] **Trailing stop after TP2** for swing mode — currently TP2 full-closes
- [ ] **Risk alerts** (`alert_risk.py`) — drawdown halt, PDT warning, position breach push notifications
- [ ] **Aggressive button UX** — silently promotes a swing setup to day-mode for PDT purposes; surface in Telegram preview before approval
- [ ] **status=expired cron** — schedule a periodic job to flip `signal_alerts` rows stuck in `pending`/`approved` past EOD to `status='expired'` (prevents stale execution)
- [ ] **Orphan executing rows** — add a reconciler or guard in `execute_trade.py` / `reconcile_orders.py` that detects and recovers `signal_alerts` stuck in `executing` status (e.g., no matching `alpaca_order_id` after a timeout), resetting to `approved` or marking `expired`