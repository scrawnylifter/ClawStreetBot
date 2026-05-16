# ClawStreetBot

Autonomous stock screening, alerts, and trading bot. Paper trading on Alpaca, historical data from Polygon.io, knowledge base in Obsidian.

## Architecture

- **Language:** Python 3.11
- **Broker:** Alpaca (alpaca-py v0.43.4) — paper trading
- **Market Data:** Polygon.io / Massive API — historical OHLCV, options, fundamentals
- **Database:** PostgreSQL 16 (Docker on clawnet, port 5432)
  - `clawstreet` db: schemas `market`, `scraper`, `trading`
  - `scraped` db: schemas `feeds`, `social`, `analytics`
- **Cache/Queue:** Redis 7 (Docker on clawnet, port 6379)
- **Knowledge Base:** Obsidian (Docker, port 3110) at `obsidian/vault/`
- **Git:** Private repo `scrawnylifter/ClawStreetBot`

## Key Commands

- `docker compose up -d` — start all services (Postgres, Redis, Obsidian, worker, n8n, docker-proxy)
- `docker exec -it clawstreet-db psql -U clawstreet -d clawstreet` — Postgres shell
- `source .venv/bin/activate` — activate Python venv
- `python scripts/setup_watchlist.py` — sync watchlist YAML → Alpaca + Postgres
- `python scripts/backfill_symbol.py TSLA` — full ingestion chain for one symbol
- `python scripts/explore_data.py` — explore Alpaca data
- `python scripts/explore_options.py` — explore options chains
- `python scripts/options_analysis.py` — options greeks/IV analysis
- `python scripts/backtest.py --mode swing --start 2024-01-01 --end 2026-05-01` — run backtest
- `python scripts/generate_signals.py --all` — generate daily signals
- `python scripts/intraday_signal.py` — 5-min intraday signal refresh (re-scores tech from 5m bars)
- `python scripts/regime_backtest.py all --start 2024-05-01 --end 2026-05-01 --mode swing` — regime-conditional backtest

## Credentials (gitignored)

- `.env.db` — Postgres credentials
- `.env.alpaca` — Alpaca API key + secret
- `.env.polygon` — Polygon.io API key + Flat Files S3 credentials (access_id, secret_key, endpoint)
- `.env.obsidian` — Obsidian config
- **DB passwords:** avoid `$` or `!` characters (Docker compose interpolation bug)

## Data Sources

- **Polygon.io — Stocks $79/mo + Options Starter $29/mo = $108/mo**
  - REST API: `POLYGON_API_KEY` in `.env.polygon`
  - Flat Files: S3-compatible at `https://files.massive.com`, bucket `flatfiles`, boto3 s3v4 auth
  - Stocks: 10yr OHLCV, fundamentals, technical indicators, corporate actions, news, WebSockets
  - Options: 2yr history, greeks (delta/gamma/theta/vega), IV, daily open interest, snapshots, Flat Files
  - See `04-API-References/Polygon.io API.md` for full API details

## Database Schema

Key tables (see `db/init/` for full DDL):
- `market.assets` — watchlist symbols with lifecycle columns (active, added_at, deactivated_at, backfill_status)
- `market.ohlcv` — OHLCV bars (1d, 5m, 15m; fk → assets)
- `market.options` — options contracts with strike, expiry, type, settlement
- `market.greeks` — greeks snapshots (delta, gamma, theta, vega, IV) per contract/date
- `market.iv_rank` — IV rank percentiles per symbol/date (1,576 rows)
- `market.realized_vol` — 20d/5d annualized RV + IV-RV spread per symbol/date (3,465 rows)
- `market.gex_dex` — GEX/DEX per strike/expiry per symbol/date (9,350 rows)
- `market.gex_dex_overview` — net GEX/DEX totals per underlying/date (15 rows)
- `market.ingest_state` — tracks last-ingested timestamp per symbol/timeframe for incremental updates
- `market.technical_indicators` — EMA/RSI/MACD/ATR/VWAP/Bollinger per symbol/date (4,125 rows)
- `market.greeks_filter` — IV regime + per-contract filter results (19,976 rows, 543 passing)
- `market.iv_outliers` — 3σ z-score IV outlier flags (sparse, only flagged rows)
- `market.fundamentals` — quarterly financials: revenue, net_income, eps, market_cap (98 periods)
- `market.regime` — daily market regime classification: bull/bear/transition via SPY SMA crossover + VIX proxy + breadth (501 days)
- `market.trend_status` — multi-timeframe trend: micro/intermediate/primary direction + strength + score (4,400 rows)
- `scraper.sources` — RSS/social/news sources (8 sources)
- `scraper.articles` — scraped articles with sentiment + symbol arrays (69 articles)
- `scraper.posts` — social media posts (75 posts)
- `trading.signals` — generated trading signals with 6-factor composite scoring (0-100)
- `trading.positions` — open/closed positions
- `trading.backtest_runs` — backtest run metadata (strategy mode, date range, capital, params)
- `trading.backtest_trades` — individual simulated trades with P&L, R-multiples, partial exits
- `trading.backtest_metrics` — aggregate performance per run (win rate, Sharpe, CAGR, max DD, profit factor)
- `trading.regime_weights` — per-regime composite scoring weights (static baseline + optimized)
- `trading.regime_factor_analysis` — per-regime factor-to-forward-return correlations (5d/20d horizons)

## Watchlist (16 symbols)

NVDA, AMD, MU, WDC, STX, APLD, IREN, NBIS, CIFR, RDDT, SERV, RKLB, ASTS, OKLO, NVO, SPY

## Trading Rules (NON-NEGOTIABLE)

**These are the user's rules. Never substitute industry defaults. If unsure, ask.**

### Laws of Trading
1. Never Trade With Emotion — no FOMO, revenge, or YOLO trading
2. If It's Bothering You, You're Too Deep — size down
3. Never More Than 20% in a Single Position — hard cap
4. Realize Gains — take profits at 30-50%, don't let winners turn to losers
5. No Short-Dated Options — nothing under 30 DTE, never touch 0DTE
6. Know the Difference Between Luck and Skill — repeatable outcomes matter
7. There Will Always Be Another Opportunity — don't force bad trades
8. Do Your Fucking Research — know what you're trading and why

### Risk Management — Day Trading
- **Risk per trade:** 5% of capital (more trades/session = lower per-trade risk)
- **Risk/Reward:** 3:1 (same floor, no exceptions)
- **Stop-loss:** ATR × 1.5 or opening range boundary
- **Take-profit:** 20% first target, 40% second target, flatten before close
- **Time stop:** Flatten before market close — no overnight risk
- **30 DTE on contracts is insurance, not hold time** — you might hold a 45 DTE call for 30 minutes
- **PDT:** Max 3 day trades per 5-business-day window (2 normal, 1 emergency only), 4th = ban

### Risk Management — Swing Trading
- **Risk per trade:** 10% of capital
- **Risk/Reward:** 3:1 (25% win rate breakeven)
- **Stop-loss:** ATR × 2.0
- **Take-profit:** 30% first target, 50% second target, trail remaining
- **Drawdown halts:** 10% daily, 20% weekly, 30% monthly

### Risk Management — Long-Term Holding
- **3-tranche conviction model** (5% risk per tranche, max 15% position)
- **Drawdown tolerance:** 30-40%
- **Stop method:** thesis-based ("is my reason for buying still true?"), not price-based
- **Take-profit:** 50% first target, 100% second target, ride to 200%+
- **"Buy the dip" ≠ "averaging down"** — deliberate scale-in vs. denial

### Options-Specific
- Alpaca OCC format: `ROOTYYMMDD[CP]STRIKE×1000`
- Polygon OCC format: `O:ROOTYYMMDD[CP]STRIKE×1000` (zero-padded to 8 digits)
- No `feed=` param on Alpaca option requests (raises error)
- Paper tier returns `open_interest=None` sometimes

### PDT Rule (Account < $25K)
- **3 day trades max in a rolling 5-business-day window**
- 1st DT: normal, planned trade
- 2nd DT: cautious, only strong setups
- 3rd DT: emergency only — exit/hedge, never new speculative entry
- **4th DT = PDT ban** — never trigger this
- Swing positions (held overnight) and long-term holds do NOT count as day trades
- PDT lock resets when oldest trade in window ages past 5 business days

### Greeks Strategy (see `02-Strategies/Greeks Strategy.md`)
- **IV Rank < 25%** → option buying zone (cheap premium)
- **IV Rank 25-50%** → directional plays, standard strikes
- **IV Rank 50-75%** → cautious, consider spreads
- **IV Rank > 75%** → NO naked buying (premium too expensive)
- **Delta range:** 0.50–0.70 (swing), 0.70–0.80 (day), 0.60–0.80 (long-term)
- **Reject |delta| < 0.50** (lottery ticket) or **> 0.90** (just buy shares)
- **Theta budget:** day < 5%/premium, swing < 3%, long-term < 1%
- **Gamma risk:** high near expiry (Law 5 backs this up), moderate for swing, low for LT
- **Vanna:** monitor around IV regime changes and earnings — delta shifts when IV shifts

## 🔐 MANDATORY: Secrets & Credentials

**NEVER hardcode API keys, passwords, tokens, or connection strings in commands, scripts, or tool calls.** This is non-negotiable.

### Rules
1. **All credentials live in `.env.*` files** — `.env.db`, `.env.polygon`, `.env.alpaca`, `.env.n8n` (gitignored)
2. **Scripts source `.env.*` automatically** — use the `load_env()` pattern (see any `scripts/compute_*.py`)
3. **Shell helpers source `.env.*` automatically** — use `scripts/n8n_api.sh` for all n8n REST API calls (never raw curl with hardcoded keys)
4. **Docker compose uses `env_file:` directives** — never put secrets in `environment:` blocks
5. **`.env.*.example` files** are committed with placeholder values; real keys are gitignored
6. **If a key is invalidated** (e.g., after n8n restart) — regenerate in the service UI, update `.env.*`, restart

### ❌ WRONG
```bash
curl -H "X-N8N-API-KEY: eyJhbGciOi..." ...
psql -U clawstreet -d clawstreet -h localhost -p 5432 -w ...
```

### ✅ RIGHT
```bash
./scripts/n8n_api.sh list          # sources key from .env.n8n
source .env.db && psql ...         # or use load_env() in Python
```

## Code Standards

- Type hints on all public functions
- Docstrings in Google style
- 4-space indentation for Python, 2-space for YAML
- No wildcard imports
- All API keys via env vars or `.env.*` files, never hardcoded
- Use `psycopg2` or `psycopg2-binary` for Postgres connections
- Use `alpaca-py` SDK for all Alpaca API calls
- Use `polygon-api-client` SDK for Polygon.io calls
- **See 🔐 MANDATORY section above** — the "never hardcoded" line is backed by the full skill: `.claude/skills/secrets-management.md`

## Project Structure

```
ClawStreetBot/
├── CLAUDE.md              ← this file
├── docker-compose.yml
├── requirements.txt            ← Python deps for worker image + local venv
├── .env.*                  ← gitignored credentials
├── .venv/                  ← gitignored Python venv
├── config/
│   ├── rss_feeds.yml           ← RSS feeds + Reddit subs for scraper
│   └── watchlist.yml           ← YAML source-of-truth for tracked symbols
├── db/init/
│   ├── 01_init_databases.sql
│   ├── 02_create_tables.sql
│   ├── 03_polygon_tables.sql   ← Options, greeks, IV rank, fundamentals, ingest_state
│   ├── 04_rv_gex_tables.sql    ← Realized volatility, GEX/DEX tables
│   ├── 05_watchlist_lifecycle.sql ← active/added_at/deactivated_at/backfill_status
│   └── 06_derived_analytics.sql   ← Technical indicators, greeks filter, IV outliers
│   └── 07_signals_scoring.sql     ← Signal scoring columns + unique constraint
│   ├── 08_backtest.sql           ← Backtest engine tables (runs, trades, metrics)
│   ├── 09_regime.sql             ← Regime classification + weights + factor analysis
│   └── 10_trend.sql              ← Trend status table (micro/intermediate/primary)
├── docker/
│   ├── worker/Dockerfile       ← Python 3.11 worker (n8n execs into this)
│   └── n8n/Dockerfile          ← n8n + wollomatic socket-proxy for secure exec
├── n8n/
│   └── workflows/              ← Source-of-truth JSON for n8n workflows
│       ├── watchlist_sync.json     ← every 5 min
│       ├── backfill_pending.json   ← every 5 min (picks pending symbols)
│       ├── ohlcv_daily.json        ← Mon-Fri 18:00 ET
│       ├── ohlcv_intraday.json     ← Mon-Fri hourly :05
│       ├── options_daily.json      ← Mon-Fri 17:55 ET
│       ├── derived_daily.json      ← Mon-Fri 18:30 ET (7 nodes)
│       ├── fundamentals_daily.json ← Mon-Fri 19:00 ET
│       ├── rss_news_scanner.json   ← Mon-Fri every 30m 9:30-16:00 ET
│       ├── signals_daily.json      ← Mon-Fri 19:30 ET (includes daily backtest)
│       ├── intraday_signal_5m.json ← Mon-Fri every 5 min 9:30-16:00 ET
│       ├── trend_daily.json        ← Mon-Fri 18:00 ET (trend detection + status)
│       └── regime_weekly.json      ← Sat 11:00 ET (classify + optimize + compare)
├── scripts/
│   ├── setup_watchlist.py      ← Sync config/watchlist.yml → Alpaca + Postgres
│   ├── backfill_symbol.py      ← Full ingestion chain for one symbol
│   ├── explore_data.py
│   ├── explore_options.py
│   ├── options_analysis.py
│   ├── ingest_polygon_ohlcv.py        ← Phase 2: Polygon data ingestion
│   ├── ingest_polygon_options.py      ← Phase 2: Options + greeks ingestion
│   ├── ingest_polygon_fundamentals.py ← Phase 2: Fundamentals ingestion
│   ├── ingest_rss_news.py              ← Phase 2: RSS + Reddit scraper
│   ├── compute_iv_rank.py             ← Phase 2: IV rank calculation
│   ├── compute_realized_vol.py        ← Phase 2: Realized volatility (20d/5d + IV-RV spread)
│   ├── compute_gex_dex.py             ← Phase 2: GEX/DEX computation (strike/expiry + overview)
│   ├── compute_technical_indicators.py ← Phase 2: EMA/RSI/MACD/ATR/VWAP/Bollinger
│   ├── compute_greeks_filter.py        ← Phase 2: IV regime + delta/theta-budget gating per contract
│   ├── compute_iv_outliers.py          ← Phase 2: 3σ z-score IV outlier flags
│   ├── n8n_api.sh                      ← n8n REST API helper (sources .env.n8n)
│   ├── generate_signals.py            ← Phase 2: Composite signal scoring (6-factor, 0-100)
│   ├── intraday_signal.py             ← 5-min intraday tech re-score + threshold alerts
│   ├── compute_trend.py              ← Phase 4: Multi-timeframe trend detection (micro/inter/primary)
│   ├── backfill_signals.py           ← Phase 4: Historical signal backfill across 501 days
│   ├── backfill_historical_iv.py      ← Phase 2: Historical IV backfill for IV rank calculation
│   ├── backtest.py                    ← Phase 3: Backtesting engine
│   └── regime_backtest.py            ← Phase 4: Regime classification + dynamic weights + compare
└── obsidian/vault/         ← knowledge base (27 notes across 8 folders)
    ├── Home.md
    ├── Project Roadmap.md
    ├── 01-Fundamentals/     ← Laws of Trading, Trade Entry Criteria
    ├── 02-Strategies/       ← Day Trading, Swing, Long-Term, EMA Crossover, ORB, Buy the Dip, Greeks Strategy
    ├── 03-Market-Research/  ← Watchlist, Backtesting Architecture
    ├── 04-API-References/   ← Alpaca API, Polygon.io API
    ├── 05-Risk-Management/  ← Position Sizing, Loss Limits, Correlation Risk
    ├── 06-Indicators/       ← (empty, ready for TA docs)
    ├── 07-Infrastructure/   ← Database Architecture, n8n Scheduler, Telegram Alert System, Order Execution Engine, Monitoring & Dashboards
    └── 08-Templates/
```

## Current Phase

Phase 1 (foundation) and Phase 2 (analytics) are complete. Phase 3 (backtesting) is complete:

All phases 1-3 complete + Phase 4 (regime/trend) live. Operational pipeline running daily/weekly.
- [x] Docker services running (Postgres, Redis, Obsidian, worker, n8n)
- [x] Alpaca paper trading connected
- [x] Options data explorers working
- [x] Watchlist synced (Alpaca + Postgres)
- [x] Obsidian vault with trading rules, strategies, risk management
- [x] Greeks strategy documented (IV regime, delta, theta, gamma, vanna)
- [x] Backtesting architecture documented (data pipeline, schema, analysis)
- [x] Postgres MCP server configured for Claude Code
- [x] Polygon.io credentials verified (REST API + Flat Files S3)
- [x] Polygon.io ingestion scripts (OHLCV, greeks, fundamentals → Postgres)
- [x] IV rank / realized vol / GEX-DEX computed
- [x] Technical indicators (EMA, RSI, MACD, ATR, VWAP, Bollinger)
- [x] Greeks filtering engine (IV regime, delta, theta-budget per contract)
- [x] IV outlier detection (3σ z-score)
- [x] Fundamentals ingestion (Polygon quarterly financials, 98 periods)
- [x] RSS/News + Reddit scraper pipeline (69 articles, 75 posts)
- [x] Composite signal scoring engine (6-factor, 0-100)
- [x] n8n scheduler (12 workflows + wollomatic socket-proxy)
- [x] n8n_api.sh helper + NODES_EXCLUDE=[] fix for ExecuteCommand
- [x] Secrets management skill (NEVER hardcode API keys)
- [x] Watchlist lifecycle (YAML source-of-truth, soft-deactivate, backfill chain)
- [x] Backtesting engine (day/swing/long_term with user trading rules, ATR-based SL/TP, partial exits, PDT tracking)
- [x] 5-minute intraday signal refresh (re-scores tech from 5m bars, threshold alerts)
- [x] Market regime classifier (bull/bear/transition via SPY SMA + VIX + breadth, 501 days)
- [x] Regime-conditional factor analysis (per-regime factor-to-return correlations)
- [x] Dynamic weight optimizer (regime-specific scoring weights)
- [x] Multi-timeframe trend detection (micro/intermediate/primary via EMA+ADX+price structure)
- [x] Historical signal backfill (7,908 signals across 501 days)
- [x] Trend-aware intraday adjustments (signal + aligned trend = boost, counter-trend = penalty)

### Remaining Items
- [ ] Telegram alert system (signals exist but no push notifications)
- [ ] Order execution engine (still paper-only)
- [ ] Position sizing calculator (backtest has it, no standalone tool)
- [ ] Monitoring/dashboards (no visibility beyond raw DB queries)
- [ ] Regime optimizer needs more diverse data (underperforms static with full history — not a code fix)

## Security Architecture

- **n8n does NOT have direct docker.sock access** — routes through `wollomatic/socket-proxy`
- Proxy allowlists ONLY: `GET /containers/clawstreet-worker/json` and `POST /containers/clawstreet-worker/exec` + `/exec/*/start`
- Denied at proxy: `docker ps`, `docker stop/rm/kill`, `docker run` (no container creation), filesystem mounts, exec into any other container
- Proxy container hardened: `read_only`, `cap_drop ALL`, `no-new-privileges`, runs as `65534:DOCKER_GID`
- All credentials via `.env.*` files (gitignored), never hardcoded
- Redis password via `$$REDIS_PASSWORD` env var (no hardcoded fallbacks)

## Critical Warnings

- **NEVER hardcode risk at 1%** — user explicitly rejected this as too conservative for small accounts
- **NEVER suggest 2:1 R:R for any strategy** — 3:1 minimum across all categories
- **NEVER suggest 90 DTE minimum** — user confirmed 30 DTE
- **NEVER confuse "buy the dip" with "averaging down"** — they are fundamentally different
- **NEVER trigger a 4th day trade in a 5-day window** — PDT ban is catastrophic for accounts under $25K
- **3rd day trade is emergency-only** — must be an exit or hedge, never a new speculative entry
- Always ask before substituting any default value for a trading parameter
- **Strategies do NOT define R:R, position sizing, or take-profit** — those live in Risk Management docs only. Strategy playbooks reference them.