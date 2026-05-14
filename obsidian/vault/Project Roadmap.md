---
created: 2026-05-14
updated: 2026-05-14
tags: [roadmap, mOC]
---

# Project Roadmap

## Phase 1 — Foundation ✅
- [x] Project setup (repo, gitignore, Obsidian vault)
- [x] Docker stack (Postgres 16, Redis 7, Obsidian)
- [x] Database schema (market, scraper, trading)
- [x] Alpaca Paper Trading connected (alpaca-py v0.43.4)
- [x] Watchlist configured (15 stocks + sectors)
- [x] Data explorer working (bars, snapshots, news, movers)
- [x] Options chain data confirmed (greeks, IV, full chain)
- [x] Laws of Trading documented and enforced via skill
- [x] Signal Framework defined — the "why" behind every trade

## Phase 2 — Data Ingestion & Signals
- [ ] 2a: RSS/News scraper (Seeking Alpha, MarketWatch, Reuters)
- [ ] 2a: Alpaca News API integration for watchlist symbols
- [ ] 2a: Reddit mention scraper (r/wallstreetbets, r/options)
- [ ] 2a: Store all signals in Postgres `scraper.*` tables
- [ ] 2b: Technical analysis engine (EMA, MACD, RSI, VWAP, ATR, Bollinger)
- [ ] 2b: Opening Range Breakout (ORB) detection
- [ ] 2b: Unusual volume detection (2x+ average)
- [ ] 2c: Options flow scanner (unusual activity, OI spikes, IV rank)
- [ ] 2c: Put/call ratio calculator per symbol
- [ ] 2c: IV rank heatmap across watchlist
- [ ] 2d: News sentiment classifier (bullish/bearish/neutral)
- [ ] 2d: Composite signal scoring (tech 40%, options flow 25%, news 20%, macro 15%)
- [ ] 2e: Laws of Trading compliance check on every signal
- [ ] 2e: Telegram alert system via bot
- [ ] 2e: Obsidian trade journal auto-logging

## Phase 3 — Strategy & Backtesting
- [ ] Backtesting engine (historical data + simulation)
- [ ] Paper trading mode (Alpaca Paper, 30-day minimum)
- [ ] Position sizing calculator (ATR-based, 20% max)
- [ ] Stop-loss / take-profit automation (30%/50% exits)
- [ ] Daily loss limits (5% rolling → cooldown per Law 1)

## Phase 4 — Live Trading
- [ ] Order execution engine (market, limit, stop, bracket)
- [ ] Monitoring & alerting (positions, P&L, violations)
- [ ] Performance dashboards

## Phase 5 — Advanced
- [ ] Multi-asset support (crypto, futures)
- [ ] Machine learning signal augmentation
- [ ] Portfolio optimization

---

## See Also
- [[Laws of Trading]] — Non-negotiable rules
- [[Signal Framework]] — What triggers every trade
- [[Watchlist]] — Current tracked assets
- [[Database Architecture]] — Storage design