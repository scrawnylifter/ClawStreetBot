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

- `docker compose up -d` — start all services
- `docker exec -it clawstreet-db psql -U clawstreet -d clawstreet` — Postgres shell
- `source .venv/bin/activate` — activate Python venv
- `python scripts/setup_watchlist.py` — sync watchlist to Alpaca + Postgres
- `python scripts/explore_data.py` — explore Alpaca data
- `python scripts/explore_options.py` — explore options chains
- `python scripts/options_analysis.py` — options greeks/IV analysis

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

Key tables (see `db/init/02_create_tables.sql` for full DDL):
- `market.assets` — symbols with sector/industry tags
- `market.ohlcv` — OHLCV bars (fk → assets)
- `scraper.sources` — RSS/social/news sources
- `scraper.articles` — scraped articles with sentiment + symbol arrays
- `scraper.posts` — social media posts
- `trading.signals` — generated trading signals
- `trading.positions` — open/closed positions

## Watchlist (15 stocks)

NVDA, AMD, MU, WDC, STX, APLD, IREN, NBIS, CIFR, RDDT, SERV, RKLB, ASTS, OKLO, NVO

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

## Code Standards

- Type hints on all public functions
- Docstrings in Google style
- 4-space indentation for Python, 2-space for YAML
- No wildcard imports
- All API keys via env vars or `.env.*` files, never hardcoded
- Use `psycopg2` or `psycopg2-binary` for Postgres connections
- Use `alpaca-py` SDK for all Alpaca API calls
- Use `polygon-api-client` SDK for Polygon.io calls

## Project Structure

```
ClawStreetBot/
├── CLAUDE.md              ← this file
├── docker-compose.yml
├── .env.*                  ← gitignored credentials
├── .venv/                  ← gitignored Python venv
├── db/init/
│   ├── 01_init_databases.sql
│   └── 02_create_tables.sql
├── scripts/
│   ├── setup_watchlist.py
│   ├── explore_data.py
│   ├── explore_options.py
│   ├── options_analysis.py
│   ├── ingest_polygon_ohlcv.py        ← Phase 2: Polygon data ingestion
│   ├── ingest_polygon_options.py      ← Phase 2: Options + greeks ingestion
│   ├── ingest_polygon_fundamentals.py ← Phase 2: Fundamentals ingestion
│   ├── compute_iv_rank.py             ← Phase 2: IV rank calculation
│   └── backtest.py                    ← Phase 3: Backtesting engine
└── obsidian/vault/         ← knowledge base (24 notes across 8 folders)
    ├── Home.md
    ├── Project Roadmap.md
    ├── 01-Fundamentals/     ← Laws of Trading, Trade Entry Criteria
    ├── 02-Strategies/       ← Day Trading, Swing, Long-Term, EMA Crossover, ORB, Buy the Dip, Greeks Strategy
    ├── 03-Market-Research/  ← Watchlist, Backtesting Architecture
    ├── 04-API-References/   ← Alpaca API, Polygon.io API
    ├── 05-Risk-Management/  ← Position Sizing, Loss Limits, Correlation Risk
    ├── 06-Indicators/       ← (empty, ready for TA docs)
    ├── 07-Infrastructure/   ← Database Architecture
    └── 08-Templates/
```

## Current Phase

Phase 1 (foundation) is complete. Phase 2 in progress:
- [x] Docker services running (Postgres, Redis, Obsidian)
- [x] Alpaca paper trading connected
- [x] Options data explorers working
- [x] Watchlist synced (Alpaca + Postgres)
- [x] Obsidian vault with trading rules, strategies, risk management
- [x] Greeks strategy documented (IV regime, delta, theta, gamma, vanna)
- [x] Backtesting architecture documented (data pipeline, schema, analysis)
- [x] Postgres MCP server configured for Claude Code
- [x] Polygon.io credentials verified (REST API + Flat Files S3)
- [ ] Polygon.io ingestion scripts (OHLCV, greeks, fundamentals → Postgres)
- [ ] Greeks filtering engine (IV rank, delta entry, theta budget)
- [ ] RSS/News scraper pipeline
- [ ] Signal generation engine

## Critical Warnings

- **NEVER hardcode risk at 1%** — user explicitly rejected this as too conservative for small accounts
- **NEVER suggest 2:1 R:R for any strategy** — 3:1 minimum across all categories
- **NEVER suggest 90 DTE minimum** — user confirmed 30 DTE
- **NEVER confuse "buy the dip" with "averaging down"** — they are fundamentally different
- **NEVER trigger a 4th day trade in a 5-day window** — PDT ban is catastrophic for accounts under $25K
- **3rd day trade is emergency-only** — must be an exit or hedge, never a new speculative entry
- Always ask before substituting any default value for a trading parameter
- **Strategies do NOT define R:R, position sizing, or take-profit** — those live in Risk Management docs only. Strategy playbooks reference them.