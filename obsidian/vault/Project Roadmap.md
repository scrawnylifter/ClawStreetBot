---
created: 2026-05-14
updated: 2026-05-17
tags: [roadmap, mOC]
---

# Project Roadmap

## Phase 1 — Foundation ✅
- [x] Project setup (repo, gitignore, Obsidian vault)
- [x] Docker stack (Postgres 16, Redis 7, Obsidian)
- [x] Database schema (market, scraper, trading)
- [x] Alpaca Paper Trading connected (alpaca-py v0.43.4)
- [x] Watchlist configured (16 stocks + sectors)
- [x] Data explorer working (bars, snapshots, news, movers)
- [x] Options chain data confirmed (greeks, IV, full chain)
- [x] Laws of Trading documented and enforced via skill
- [x] Trade Entry Criteria documented — the "why" behind every trade

## Phase 2 — Data Ingestion & Signals ✅
- [x] **Polygon.io integration** — historical OHLCV, options, fundamentals → Postgres
- [x] **Greeks filtering engine** — IV regime, delta entry, theta budget, vanna alerts (see [[Greeks Strategy]])
- [x] **Claude Code + Postgres MCP** — direct DB access for research, ad-hoc queries, schema management
- [x] **RSS/News scraper** — Seeking Alpha, MarketWatch, Reuters → `scraper.articles` (69 articles)
- [x] **Reddit scraper** — r/wallstreetbets, r/options → `scraper.posts` (75 posts)
- [x] **Technical analysis engine** — EMA, MACD, RSI, VWAP, ATR, Bollinger → `market.technical_indicators`
- [x] **Greeks-based entry/exit filters** — IV regime, delta 0.50-0.90, theta budget per contract
- [x] **IV rank heatmap** — `market.iv_rank` (1,576 rows)
- [x] **IV outlier detection** — 3σ z-score flags in `market.iv_outliers`
- [x] **Realized volatility** — 20d/5d annualized RV + IV-RV spread in `market.realized_vol` (3,465 rows)
- [x] **GEX/DEX** — per strike/expiry + overview in `market.gex_dex` (9,350) + `market.gex_dex_overview` (15)
- [x] **Fundamentals** — Polygon quarterly financials → `market.fundamentals` (98 periods)
- [x] **Composite signal scoring** — 6-factor (tech 40%, options flow 25%, IV rank 15%, sentiment 10%, GEX 5%, regime 5%), 0-100 scale with Laws compliance check

## Phase 3 — Strategy & Backtesting ✅ (see [[Backtesting Architecture]])
- [x] Backtesting engine — day/swing/long_term with user trading rules, ATR-based SL/TP
- [x] Position sizing calculator — swing: 10%/3:1, long-term: 3-tranche conviction model
- [x] Stop-loss / take-profit automation — swing: ATR×2.0, TP 30%/50%/trail
- [x] Drawdown circuit breakers — 10% daily, 20% weekly, 30% monthly
- [x] PDT tracking — 3 day-trade limit, emergency-only 3rd, 4th = ban
- [x] Partial exits — tiered take-profit (30%, 50%, trail remaining)
- [x] Historical signal backfill — 7,908 signals across 501 days

## Phase 4 — Regime & Trend ✅
- [x] Market regime classifier — bull/bear/transition via SPY SMA crossover + VIX proxy + breadth (501 days)
- [x] Regime-conditional factor analysis — per-regime factor-to-return correlations (5d/20d horizons)
- [x] Dynamic weight optimizer — regime-specific composite scoring weights
- [x] Multi-timeframe trend detection — micro (3-5d), intermediate (2-6wk), primary (months) via EMA+ADX+price structure
- [x] 5-minute intraday signal refresh — re-scores technical factor from 5m bars, threshold alerts
- [x] Trend-aware intraday adjustments — aligned trend boosts signal, counter-trend penalizes

## Phase 5 — Execution & Alerts

### 5A: Signal Detection & Data Pipeline ✅
- [x] EMA crossover detector (`detect_ema_crossover.py`) — 9/21 cross + ADX>25
- [x] Signal alerts table (`015_signal_alerts.sql`) — full trade plan storage
- [x] Telegram alert sender (`alert_telegram.py`) — strategy-specific trade alerts with bid/ask/mid
- [x] **Alpaca data migration** — OHLCV + options moved from Polygon to Alpaca (free tier)
  - `ingest_alpaca_ohlcv.py` — 1d/15m/5m bars with trade_count + VWAP
  - `ingest_alpaca_options.py` — option chains + greeks + bid/ask
  - `fetch_alpaca_snapshot.py` — real-time stock price + best option at signal time
  - `detect_ema_crossover_15m.py` — 15m crossover with live option enrichment
  - 3 new n8n workflows: `alpaca_ohlcv_daily`, `alpaca_ohlcv_intraday`, `alpaca_options_daily`
  - Old Polygon ingestion workflow JSONs deleted from repo
  - DB migrations: `017_ohlcv_alpaca_columns.sql` (trade_count, vwap), `018_alpaca_options_columns.sql` (bid, ask)
- [x] **★ Setup scanner** (`scan_setups.py`) — PRIMARY alert mechanism — 8-gate BUY signal scanner; silence = no signal
- [x] **★ Liquidity sweep scanner** (`detect_liquidity_sweep.py`) — 5m + daily, close-beyond confirmation (PF 1.56)

### 5B: Order Execution Engine ✅ (see [[Order Execution Engine]])
- [x] **Telegram approval flow** (`alert_telegram.py` + `alert_dispatch` cron) — 4-button keyboard: Approve / Conservative / Aggressive / Deny
- [x] **Callback listener** (`telegram_callback_listener.py`) — long-poll daemon flips `status` and writes `risk_mode`
- [x] **Pre-flight checks** (`process_approved.py`) — Laws 3/5, PDT projection (business-day-aware, excludes self-row), drawdown halts using period-start equity + unrealized P&L via Alpaca equity, greeks filters
- [x] **Alpaca paper execution** (`execute_trade.py`) — `--confirm` required, `client_order_id`-deduped submits, risk_mode-aware sizing
- [x] **BUY reconciliation** (`reconcile_orders.py`) — `FOR UPDATE SKIP LOCKED` + UNIQUE indexes on `alpaca_order_id` / `position_id` prevent phantom positions
- [x] **Exit monitor** (`exit_monitor.py`) — decision tree: stop / premium / TP2 / TP1 partial / time-stop (12:45 PDT) / DTE expiry; row-level locked with `SELECT FOR UPDATE OF p SKIP LOCKED`
- [x] **TP1 50% partial close** — submit, reconcile, reduce position quantity; `tp1_realized_pnl` recorded separately from full-close P&L
- [x] **SELL reconciliation** (`reconcile_exits.py`) — closes position, records `realized_pnl`, flips signal_alerts to `status='exited'`
- [x] **Daily equity snapshots** (`snapshot_equity.py` + `equity_snapshot_daily` cron) — drawdown halt denominator (`market.equity_snapshots`)
- [x] DB migrations: 020 alert lifecycle, 021 position exit columns, 022 composite_score, 023 risk_mode, 024 tp1 partial, 025 equity_snapshots, 026 signal_alerts unique
- [x] n8n workflows: `alert_dispatch`, `execute_trade`, `reconcile_orders`, `reconcile_exits`, `exit_monitor`, `equity_snapshot_daily`

### 5C: Backlog (deferred)
- [ ] ORB breakout detector (`detect_orb.py`) — opening range + volume + VWAP
- [ ] Buy the 5% Dip detector (`detect_dip.py`) — 5% pullback + thesis check + 3-tranche scale-in
- [ ] Per-risk-mode option selection — scanner currently binds a 0.50-0.70 delta contract at scan time, before the user picks Conservative/Aggressive (audit M1)
- [ ] Bracket orders for stock entries — current entries are naked, exits rely 100% on `exit_monitor` uptime (audit H7)
- [ ] Trailing stop after TP2 for swing mode — currently TP2 full-closes
- [ ] Risk alerts (`alert_risk.py`) — drawdown halt, PDT warning, position breach push notifications
- [ ] Aggressive button UX — currently silently promotes a swing setup to day-mode for PDT purposes; surface in Telegram preview before approval

### 5D: Monitoring & Dashboards (see [[Monitoring & Dashboards]])
- [ ] Materialized views for dashboard queries (portfolio, signals, risk, freshness)
- [ ] `scripts/dashboard_api.py` — FastAPI read-only JSON endpoints
- [ ] HTML dashboard: positions, signals, context, pipeline, risk
- [ ] Docker service on port 8080 (LAN-only, read-only DB access)
- [ ] Mobile-responsive, dark theme, 60s auto-refresh
- [ ] Alert timeline + pipeline failure highlighting

## Phase 6 — Advanced
- [ ] Multi-asset support (crypto, futures)
- [ ] Machine learning signal augmentation
- [ ] Portfolio optimization

---

## See Also
- [[Laws of Trading]] — Non-negotiable rules
- [[Trade Entry Criteria]] — When and why we enter trades
- [[Greeks Strategy]] — IV regime, delta entry/exit, theta budgets, vanna risk
- [[Backtesting Architecture]] — Data pipeline, Postgres schema, backtest engine design
- [[Watchlist]] — Current tracked assets
- [[Database Architecture]] — Storage design
- [[n8n Scheduler]] — 22 active workflows driving ingestion, compute, signal detection, alert dispatch, execution, reconciliation, and exit monitoring