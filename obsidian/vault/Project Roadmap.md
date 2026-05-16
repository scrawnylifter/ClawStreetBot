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

## Phase 5 — Execution & Alerts (NEXT)
- [ ] Telegram alert system — push notifications when signals cross thresholds
- [ ] Order execution engine — market, limit, stop, bracket orders via Alpaca
- [ ] Position sizing calculator — standalone tool beyond backtest engine
- [ ] Monitoring & dashboards — positions, P&L, violations, performance
- [ ] Regime optimizer improvement — needs more diverse bull/bear data to outperform static weights

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
- [[n8n Scheduler]] — 12 workflows driving all pipelines