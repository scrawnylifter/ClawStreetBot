# ClawStreetBot

Autonomous stock screening, alerts, and trading bot. Paper trading on Alpaca, historical data from Polygon.io, knowledge base in Obsidian.

## Coding Conventions (MUST follow)

- **Market hours:** NEVER hardcode 9:30, 16:00, 09:30, session times, or market-open/close strings. ALWAYS import from `shared/constants.py` (which loads from `config/market_hours.yml`). Use `SESSION_OPEN`, `SESSION_CLOSE`, `SCANNER_ORB_START`, `INGEST_INTRADAY_START/END`, `is_market_day()`, `is_market_hours()`, `NYSE_HOLIDAYS`. The YAML is the single source of truth — n8n cron schedules should match the ingestion/scanner windows defined there.
- **Watchlist:** ALWAYS query `market.assets WHERE active=TRUE` — never hardcode symbol lists. The YAML at `config/watchlist.yml` is the source of truth for what should be active; `01_data/scripts/setup_watchlist.py` syncs it to the DB.
- **Layered imports:** scripts in `0X_layer/scripts/` import shared utilities (`constants`, `fetch_alpaca_snapshot`) from `shared/`. Each script does `sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))` then `from constants import DB_CONFIG, load_env`.

## Architecture

- **Language:** Python 3.11
- **Broker:** Alpaca (alpaca-py v0.43.4) — paper trading, OHLCV bars, options chains, greeks, real-time snapshots
- **Market Data:** Alpaca (primary — OHLCV, options, snapshots) + Polygon.io (secondary — fundamentals, flat-file backfill)
- **Database:** PostgreSQL 16 (Docker on clawnet, port 5432)
  - `clawstreet` db: schemas `market`, `scraper`, `trading`
  - `scraped` db: schemas `feeds`, `social`, `analytics`
- **Cache/Queue:** Redis 7 (Docker on clawnet, port 6379)
- **Knowledge Base:** Obsidian (Docker, port 3110) at `obsidian/vault/`
- **Git:** Private repo `scrawnylifter/ClawStreetBot`
- **Code Layout:** Layered top-level directories (`01_data`, `02_scanner`, `03_alert`, `04_approval`, `05_execution`, `06_exit`, `07_reconcile`), each owning its own `scripts/` and `n8n/` subdirectory. Cross-layer utilities live in `shared/`. Operational tooling lives in `admin/`. Decommissioned and v1-pipeline scripts live in `archive/`.

## Pipeline Layers

The bot is organised as a 7-layer pipeline. Each layer is a numbered top-level directory with its own `scripts/` and `n8n/` (and `db/` where relevant). Signal/order state flows strictly downstream.

| # | Layer | Status | Contents | Responsibility |
|---|-------|--------|----------|----------------|
| 01 | **data** | ✅ live | `ingest_alpaca_*`, `compute_*`, `setup_watchlist` | Ingest OHLCV, options, greeks, IV, fundamentals; compute derived analytics (IV rank, RV, GEX/DEX, technicals, trend) |
| 02 | **scanner** | ✅ live | `scan_setups`, `detect_ema_crossover`, `detect_ema_crossover_15m`, `detect_orb`, `detect_liquidity_sweep` | Pattern detection — write rows to `market.signal_alerts` with `status='new'` |
| 03 | **alert** | ✅ live | `alert_telegram` | Telegram dispatch (4-button keyboard) + `expire_stale_new` + `notify_errors` + exit-fill push messages |
|| 04 | **approval** | ✅ ported | `generate_signals`, `process_approved` | Composite signal scoring (6-factor); pre-flight checks (PDT, drawdown) → flip to `approved`/`denied` |
|| 05 | **execution** | ✅ ported | `execute_trade`, `snapshot_equity` | Submit Alpaca paper orders (bracket for stock, mid-price limit for options); daily equity snapshots for drawdown |
|| 06 | **exit** | ✅ ported | `exit_monitor` | TP/SL/trailing-stop/time-stop decision tree; emit SELLs |
|| 07 | **reconcile** | ✅ ported | `reconcile_orders`, `reconcile_exits` | Poll Alpaca for BUY/SELL fills, update `trading.positions`, capture realized P&L |

**Cross-layer:** `shared/` (constants, snapshot helper), `admin/` (backfill runner + n8n REST helper), `config/` (watchlist + market_hours YAMLs), `db/init/` + `n8n/migrations/` (schema), `obsidian/vault/` (knowledge base), `archive/` (decommissioned + v1 pipeline staged for v2 rebuild).

## Key Commands

Scripts live under layer directories — run them with their full layered path. Inside the worker container, the same paths are available under `/app/<layer>/scripts/` because docker-compose bind-mounts each layer to `/app/<layer>:ro`.

**Infra:**
- `docker compose up -d` — start all services (Postgres, Redis, Obsidian, worker, n8n, docker-proxy)
- `docker exec -it clawstreet-db psql -U clawstreet -d clawstreet` — Postgres shell
- `source .venv/bin/activate` — activate Python venv
- `./admin/scripts/n8n_api.sh list` — n8n REST API helper (sources `.env.n8n`)

**Data layer (01_data):**
- `python admin/scripts/backfill_symbol.py TSLA` — full ingestion chain for one symbol (calls into `01_data/scripts/`)
- `python 01_data/scripts/setup_watchlist.py` — sync watchlist YAML → Alpaca + Postgres
- `python 01_data/scripts/ingest_alpaca_ohlcv.py --timeframe 1d` (also `15m`, `5m`)
- `python 01_data/scripts/ingest_alpaca_options.py --all`
- `python 01_data/scripts/ingest_alpaca_iv.py`
- `python 01_data/scripts/ingest_yfinance_fundamentals.py --all`
- `python 01_data/scripts/compute_iv_rank.py`
- `python 01_data/scripts/compute_realized_vol.py`
- `python 01_data/scripts/compute_gex_dex.py`
- `python 01_data/scripts/compute_technical_indicators.py`
- `python 01_data/scripts/compute_greeks_filter.py`
- `python 01_data/scripts/compute_iv_outliers.py`
- `python 01_data/scripts/compute_trend.py --backfill --days 400`

**Scanner layer (02_scanner):**
- `python 02_scanner/scripts/scan_setups.py` — ★ PRIMARY — 8-gate BUY signal scanner (trend, ADX, RSI, IV rank, IV-RV, premium, DTE, R:R)
- `python 02_scanner/scripts/scan_setups.py --dry-run` — stdout only, no Telegram alert
- `python 02_scanner/scripts/detect_ema_crossover.py --lookback 1` — daily EMA 9/21 crossovers (supplementary)
- `python 02_scanner/scripts/detect_ema_crossover_15m.py --lookback 1` — 15m EMA crossovers + real-time snapshot (supplementary)
- `python 02_scanner/scripts/detect_liquidity_sweep.py` — ★ liquidity sweep scanner (5m + daily, close-beyond, Telegram)
- `python 02_scanner/scripts/detect_liquidity_sweep.py --dry-run` — stdout only, no DB/Telegram
- `python 02_scanner/scripts/detect_orb.py` — ★ ORB scanner (opening range breakout on 5m bars, `strategy='orb'`)
- `python 02_scanner/scripts/detect_orb.py --dry-run` — stdout only, no DB writes
- `python shared/fetch_alpaca_snapshot.py --symbol NVDA` — real-time stock price + best option

**Alert layer (03_alert):**
- `python 03_alert/scripts/alert_telegram.py --strategy ema_crossover` — dispatch Telegram alerts for pending signals

**Archived (v1 pipeline + research — kept under `archive/`):**
- `python archive/v1-pipeline/execute_trade.py --dry-run` / `--confirm` — Alpaca paper submission (to be ported into `05_execution/`)
- `python archive/v1-pipeline/reconcile_orders.py --dry-run` — BUY fill state (to be ported into `07_reconcile/`)
- `python archive/v1-pipeline/reconcile_exits.py --dry-run` — SELL fill state + realized P&L (to be ported into `07_reconcile/`)
- `python archive/v1-pipeline/exit_monitor.py --dry-run` — TP/SL/trailing-stop/time-stop conditions (to be ported into `06_exit/`)
- `python archive/v1-pipeline/process_approved.py` — pre-flight checks (PDT, drawdown) + approve/deny (to be ported into `04_approval/`)
- `python archive/v1-dependents/snapshot_equity.py` — Alpaca equity snapshot for drawdown denominator
- `python archive/v1-pipeline/generate_signals.py --all` — 6-factor composite signal scoring
- `python archive/v1-pipeline/intraday_signal.py` — 5-min intraday signal refresh (dual-write to signal_alerts)
- `python archive/backtests/backtest.py --mode swing --start 2024-01-01 --end 2026-05-01`
- `python archive/backtests/regime_backtest.py all --start 2024-05-01 --end 2026-05-01 --mode swing`
- `python archive/backtests/backtest_liquidity_v3.py` — liquidity sweep refinement backtest (A/B/C/D)
- `python archive/explorers/explore_data.py` / `explore_options.py` / `options_analysis.py`

## Credentials (gitignored)

- `.env.db` — Postgres credentials
- `.env.alpaca` — Alpaca API key + secret
- `.env.polygon` — Polygon.io API key + Flat Files S3 credentials (access_id, secret_key, endpoint)
- `.env.obsidian` — Obsidian config
- `.env.telegram` — Telegram bot token + chat ID
- **DB passwords:** avoid `$` or `!` characters (Docker compose interpolation bug)

## Data Sources

- **Alpaca (PRIMARY — free tier, IEX 15-min delayed)**
  - OHLCV bars: 1d, 15m, 5m via `StockHistoricalDataClient` (includes `trade_count` + `VWAP`)
  - Options chains: full chain snapshots with greeks (delta, gamma, theta, vega, IV) + bid/ask via `OptionHistoricalDataClient`
  - Real-time snapshots: stock price + best filtered option at signal time via `shared/fetch_alpaca_snapshot.py`
  - Paper trading: $100K equity, $200K buying power
  - API keys in `.env.alpaca`
- **Polygon.io (SECONDARY — $108/mo, fundamentals + backfill only)**
  - REST API: `POLYGON_API_KEY` in `.env.polygon`
  - Flat Files: S3-compatible at `https://files.massive.com`, bucket `flatfiles`, boto3 s3v4 auth
  - Fundamentals: quarterly financials (revenue, EPS, market cap) — `fundamentals_daily` workflow still active
  - Flat-file backfill: initial historical OHLCV + options data (now complemented by Alpaca)
  - **DECOMMISSIONED for ingestion**: `archive/polygon-decommissioned/ingest_polygon_ohlcv.py` and `ingest_polygon_options.py` replaced by Alpaca equivalents in `01_data/scripts/`
  - See `04-API-References/Polygon.io API.md` for full API details

## Database Schema

Key tables (see `db/init/` for full DDL):
- `market.assets` — watchlist symbols with lifecycle columns (active, added_at, deactivated_at, backfill_status)
- `market.ohlcv` — OHLCV bars (1d, 5m, 15m; fk → assets; includes `trade_count` + `vwap` from Alpaca)
- `market.options` — options contracts with strike, expiry, type, settlement
- `market.greeks` — greeks snapshots (delta, gamma, theta, vega, IV, bid, ask) per contract/date
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
- `trading.positions` — open/closed positions (with exit tracking: sell_order_id, exit_reason, tp1_hit_at, alpaca_order_id, trail_stop_price)
- `trading.backtest_runs` — backtest run metadata (strategy mode, date range, capital, params)
- `trading.backtest_trades` — individual simulated trades with P&L, R-multiples, partial exits
- `trading.backtest_metrics` — aggregate performance per run (win rate, Sharpe, CAGR, max DD, profit factor)
- `trading.regime_weights` — per-regime composite scoring weights (static baseline + optimized)
- `trading.regime_factor_analysis` — per-regime factor-to-forward-return correlations (5d/20d horizons)
- `market.signal_alerts` — strategy-specific trade alerts with entry + exit plans + approval lifecycle (status: new/pending/approved/denied/executing/filled/exited/expired/error, CHECK constraint via migration 030, approval/chat columns, executed_at, composite_score, error_notified_at)
  - Entry alert formatters in `03_alert/scripts/alert_telegram.py`: `ema_crossover` → `format_ema_crossover_alert()`, `ema_crossover_15m` → `format_15m_crossover_alert()`, `setup_scanner` → `format_setup_scanner_alert()`, `liquidity_sweep` → `format_liquidity_sweep_alert()`, `intraday_signal` → `format_intraday_signal_alert()`, `orb` → `format_orb_alert()`. Every header now shows `Strategy: <name> | Timeframe: <tf>` so the user knows which mechanism fired the alert (PR #18).
  - Exit-fill notifications: `format_exit_notification()` + `send_exit_notification()` are fire-and-forget helpers wired into `reconcile_exits` at every close-commit site (full close, TP1 partial, partial-before-cancel, defensive partial). Posts to Telegram after each `conn.commit()`: 💰 wins (TP2 / trail_stop / TP1 partial), ⛔ losses (stop / premium_stop), 📤 mechanics (time_stop / expiry). Telegram failure is logged but never rolls back the DB close (PR #18).
  - All scanners write to `signal_alerts` only; `03_alert/scripts/alert_telegram.py` (alert_dispatch cron) is the SOLE dispatcher with the 4-button approval keyboard.
  - See [[Telegram Alert System]] in Obsidian for full pipeline diagram
- `scraper.youtube_videos` — YouTube video transcripts with channel, duration, fetch status (7 channels ingested)
- `trading.backtest_liquidity_runs` — liquidity sweep backtest run metadata
- `trading.backtest_liquidity_trades` — liquidity sweep backtest individual trades

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

### Mode Classification (`process_approved.py:infer_trade_mode`)
- `aggressive` risk_mode → **day**
- `conservative` risk_mode → **swing**
- `ema_crossover_15m` / `orb` / timeframe `5m`/`15m` → **day**
- `liquidity_sweep` with 5m/15m → **day**, else **swing**
- `ema_crossover` / `setup_scanner` / `daily_signal` → **swing**
- **long_term** is defined in RISK_PCT but not yet produced by any scanner

### Risk Per Trade (position sizing in `process_approved.py`)
- **Day:** 5% of equity (`RISK_PCT['day'] = 0.05`)
- **Swing:** 10% of equity (`RISK_PCT['swing'] = 0.10`)
- **Long-term:** 5% per tranche, max 15% position (`RISK_PCT['long_term'] = 0.05`) — **not yet wired**

### Stop-Loss & Take-Profit — ATR-Based (single source of truth per strategy)

All stops and targets are ATR(14) multiples, NOT percentages. The ATR source varies by timeframe:

| Strategy | Timeframe | Stop | TP1 | TP2 | R:R | ATR Source |
|---|---|---|---|---|---|---|
| setup_scanner / daily_signal | daily | ATR×2.0 | ATR×6.0 | ATR×10.0 | 3:1+ | Daily close |
| ema_crossover (daily) | daily | ATR×2.0 | ATR×6.0 | ATR×10.0 | 3:1+ | Daily close |
| ema_crossover_15m | 15m | ATR×1.5 | ATR×4.5 | ATR×7.5 | 3:1+ | 15m bars |
| orb (Opening Range Breakout) | 5m | ATR×1.5 | ATR×4.5 | ATR×7.5 | 3:1+ | 5m bars |
| liquidity_sweep | 5m | swept level ± ATR×0.05 | R:R ×3.0 | R:R ×5.0 | 3:1+ min | 5m bars |

- **TP1 exits 50% of position** (`qty // 2` in `exit_monitor.py`)
- **TP2** → day/long_term: full close; swing: trail-activate (2× ATR from entry)
- **Liquidity sweep** uses a different model: stop = swept level ± tiny ATR buffer, targets = risk-per-share × R:R multiplier

### Option Premium Stop
- Close option position if mid ≤ **50% of entry price** (OPTION_PREMIUM_STOP_FRACTION = 0.50)
- Enforced in `exit_monitor.py` check #2 and `process_approved.py` sizing
- Applies to all strategies and modes — no per-strategy differentiation

### Time Stops
- **Day trades:** Flatten by 12:45 PDT (15 min before close) in `exit_monitor.py`
- **Swing/long-term:** No time stop (held for days/weeks)
- **No stale-swing timeout implemented** — positions can trail indefinitely after TP2

### DTE Rules
- **Entry:** Minimum 30 DTE — enforced in all scanners, preflight, and option selection (`MIN_DTE = 30`)
- **Exit:** Close if DTE ≤ 1 (`MIN_DTE_HOLDABLE = 1` in `exit_monitor.py`) — lets you hold until near-expiry
- No intermediate "exit before DTE drops below entry-plan" check

### PDT Rule (Account < $25K) — ✅ ENFORCED IN `process_approved.py`
- **3 day trades max in a rolling 5-business-day window**
- 1st DT: normal, planned trade
- 2nd DT: cautious, only strong setups
- 3rd DT: emergency only — exit/hedge, never new speculative entry
- **4th DT = PDT ban** — never trigger this
- Swing positions (held overnight) and long-term holds do NOT count as day trades
- PDT lock resets when oldest trade in window ages past 5 business days

### Drawdown Halts — ✅ ENFORCED IN `process_approved.py`
- **30% daily loss** → halt all new trades
- **40% weekly loss** → halt all new trades
- **50% monthly loss** → halt all new trades
- Widened from 10/20/30% to fit options-trading volatility (50% per-contract premium stops) and paper-account experimentation
- Baseline = latest `market.equity_snapshots` value (NOT the origin $100K)
- Binary enforcement only (no graded yellow/red tiers) — see [[Risk Management Framework]] for the full 3-tier design (not yet implemented)

### Delta Bands (option strike selection)
- **Scanner-time:** `select_best_option()` uses standard band 0.50–0.70 for ALL strategies
- **Post-approval:** `reselect_option_for_risk_mode()` adjusts:
  - `standard`: 0.50–0.70
  - `conservative`: 0.55–0.65
  - `aggressive`: 0.40–0.80
- **Preflight:** Accepts 0.50–0.80 (single band for all modes)
- **NOT YET ENFORCED:** Day-trade 0.70–0.80 spec, long-term 0.60–0.80 spec (see [[Greeks Strategy]])
- **Hard reject:** |delta| < 0.50 (lottery ticket) or > 0.90 (just buy shares)

### Bid-Ask Spread Filter (single source of truth: `shared/constants.py`)
- **Cap:** `MAX_SPREAD_PCT = 0.15` — defined once in `shared/constants.py`, imported everywhere else
- **Definition:** `spread_pct = (ask - bid) / mid` — symmetric around the midpoint
- **Why 15%:** Wider spreads make the round-trip cost alone large enough to wipe a 3:1 R:R setup. Anything tighter than 15% mid is treated as liquid enough to trade
- **Three-layer enforcement** (a stale signal must survive all three to execute):
  1. **Scanner-time** — `shared/fetch_alpaca_snapshot.select_best_option` rejects contracts above the cap before they ever land in `market.signal_alerts`. `02_scanner/scripts/detect_ema_crossover.py` additionally re-queries Alpaca after its DB pick and nullifies `option_symbol` if the live spread is too wide (signal still fires, just stock-only)
  2. **Persistence** — `market.signal_alerts.spread_pct` (migration 031) stores the value at signal time; `03_alert/scripts/alert_telegram.py` red-flags (🚩) anything above the cap in the Telegram Quote line so the user sees the wide spread before they tap Approve
  3. **Preflight** — `04_approval/scripts/process_approved.py` re-checks the spread against the same `MAX_SPREAD_PCT` import. Catches stale signals that widened between scan and approval
- **NOT re-verified at execution time** — execution uses mid-price limit, so stale-wide spreads simply won't fill (natural protection)
- **Mid-price policy:** All option limit prices submitted to Alpaca (`execute_trade.py`) use `(bid + ask) / 2` rounded to a penny — never the ask. Crossing the spread on every entry leaks edge proportional to `spread_pct/2`; the mid is the fair price the market makers are happy to fill near

### Exit Decision Tree (`06_exit/scripts/exit_monitor.py`)
First match wins:
1. **Stop breach** — underlying hits stop_price → full close
2. **Premium stop** — option mid ≤ 50% of entry → full close (options only)
3. **Trail stop** — if trail_stop_price set (after TP2 in swing) → close on breach / ratchet tighter
4. **TP2** — day/long_term: full close; swing: trail-activate (2× ATR from entry)
5. **TP1** — 50% partial close (`qty // 2`), one-shot (sticky `tp1_hit_at`)
6. **Time stop** — day-trade ≥ 12:45 PDT → full close
7. **DTE ≤ 1** — full close (Law 5)

## 🔐 MANDATORY: Secrets & Credentials

**NEVER hardcode API keys, passwords, tokens, or connection strings in commands, scripts, or tool calls.** This is non-negotiable.

### Rules
1. **All credentials live in `.env.*` files** — `.env.db`, `.env.polygon`, `.env.alpaca`, `.env.n8n` (gitignored)
2. **Scripts source `.env.*` automatically** — use the `load_env()` pattern (see any `01_data/scripts/compute_*.py`, or the helper exported by `shared/constants.py`)
3. **Shell helpers source `.env.*` automatically** — use `admin/scripts/n8n_api.sh` for all n8n REST API calls (never raw curl with hardcoded keys)
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
./admin/scripts/n8n_api.sh list    # sources key from .env.n8n
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

## Docker / runtime path mapping

`docker-compose.yml` bind-mounts each layer read-only into the worker container at the same path. n8n cron jobs invoke scripts via `docker exec clawstreet-worker python /app/<layer>/scripts/<name>.py`:

| Host path | Container path |
|-----------|----------------|
| `./01_data` | `/app/01_data` |
| `./02_scanner` | `/app/02_scanner` |
| `./03_alert` | `/app/03_alert` |
| `./04_approval` | `/app/04_approval` |
| `./05_execution` | `/app/05_execution` |
| `./06_exit` | `/app/06_exit` |
| `./07_reconcile` | `/app/07_reconcile` |
| `./shared` | `/app/shared` |
| `./admin` | `/app/admin` |
| `./config` | `/app/config` |
| `./db/init` | `/app/db/init` |

`PYTHONPATH=/app` is set on the worker; scripts that need `shared/` insert `parents[2] / "shared"` into `sys.path` so they work both inside the container and from the local venv.

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
│   ├── watchlist.yml           ← YAML source-of-truth for tracked symbols
│   └── market_hours.yml        ← single source of truth for session/cron windows
├── db/init/                      ← lex order = Docker init run order
│   ├── 001_init_databases.sql
│   ├── 002_create_tables.sql
│   ├── 003_polygon_tables.sql   ← Options, greeks, IV rank, fundamentals, ingest_state
│   ├── 004_rv_gex_tables.sql    ← Realized volatility, GEX/DEX tables
│   ├── 005_watchlist_lifecycle.sql ← active/added_at/deactivated_at/backfill_status
│   ├── 006_derived_analytics.sql   ← Technical indicators, greeks filter, IV outliers
│   ├── 007_signals_scoring.sql     ← Signal scoring columns + unique constraint
│   ├── 008_backtest.sql           ← Backtest engine tables (runs, trades, metrics)
│   ├── 009_regime.sql             ← Regime classification + weights + factor analysis
│   ├── 010_trend.sql              ← Trend status table (micro/intermediate/primary)
│   ├── 015_signal_alerts.sql     ← Signal alerts (EMA, ORB, Dip trade plans)
│   ├── 016_signal_alerts_15m.sql ← 15m intraday signal alerts
│   ├── 017_ohlcv_alpaca_columns.sql  ← trade_count, vwap for Alpaca bars
│   ├── 018_alpaca_options_columns.sql ← bid, ask for Alpaca options
│   ├── 019_backtest_liquidity.sql   ← liquidity sweep backtest tables
│   ├── 020_alert_lifecycle.sql     ← alert approval lifecycle (status enum, executed_at, approval columns)
│   ├── 021_position_exit_columns.sql ← position exit tracking (sell_order_id, exit_submitted_at, exit_reason, tp1_hit_at)
│   ├── 022_composite_score.sql      ← composite_score column on signal_alerts
│   ├── 023_risk_mode.sql            ← risk_mode column on signal_alerts (4-button keyboard)
│   ├── 024_position_tp1_partial.sql ← TP1 50% partial close columns (tp1_sell_order_id, tp1_filled_*)
│   ├── 025_equity_snapshots.sql     ← daily equity snapshots for drawdown denominator
│   ├── 026_signal_alerts_unique.sql ← unique constraint on signal_alerts (dedup)
│   ├── 027_positions_alpaca_order_id.sql ← alpaca_order_id on trading.positions + partial UNIQUE index
│   ├── 028_error_notified.sql            ← error_notified_at on signal_alerts (error surfacing tracker)
│   ├── 029_position_trail_stop.sql        ← trail_stop_price on trading.positions (trailing stop after TP2)
│   └── 030_status_check_constraints.sql  ← CHECK constraints on signal_alerts.status and trading.positions.status (enum hardening)
├── n8n/migrations/            ← out-of-band schema patches applied via psql (e.g., 031_spread_pct_column.sql)
├── docker/
│   ├── worker/Dockerfile       ← Python 3.11 worker (n8n execs into this)
│   └── n8n/Dockerfile          ← n8n + wollomatic socket-proxy for secure exec
│
├── 01_data/                    ← LAYER 1: Data ingestion + derived analytics
│   ├── scripts/
│   │   ├── setup_watchlist.py            ← Sync config/watchlist.yml → Alpaca + Postgres
│   │   ├── ingest_alpaca_ohlcv.py        ← ★ OHLCV (1d/15m/5m) with trade_count + VWAP
│   │   ├── ingest_alpaca_options.py      ← ★ Options chains + greeks + bid/ask
│   │   ├── ingest_alpaca_iv.py           ← Implied volatility snapshots
│   │   ├── ingest_yfinance_fundamentals.py ← Fundamentals (yfinance, replaced Polygon)
│   │   ├── compute_iv_rank.py            ← IV rank calculation
│   │   ├── compute_realized_vol.py       ← Realized volatility (20d/5d + IV-RV spread)
│   │   ├── compute_gex_dex.py            ← GEX/DEX computation (strike/expiry + overview)
│   │   ├── compute_technical_indicators.py ← EMA/RSI/MACD/ATR/VWAP/Bollinger
│   │   ├── compute_greeks_filter.py      ← IV regime + delta/theta-budget gating per contract
│   │   ├── compute_iv_outliers.py        ← 3σ z-score IV outlier flags
│   │   └── compute_trend.py              ← Multi-timeframe trend (micro/inter/primary)
│   └── n8n/
│       ├── watchlist_sync.json           ← every 5 min
│       ├── backfill_pending.json         ← every 5 min (admin/scripts/backfill_runner.py)
│       ├── alpaca_ohlcv_daily.json       ← Mon-Fri 15:00 PDT (1d bars)
│       ├── alpaca_ohlcv_intraday.json    ← Mon-Fri hourly :05 (7-13 PDT) (15m + 5m)
│       ├── alpaca_options_daily.json     ← Mon-Fri 14:55 PDT
│       ├── derived_daily.json            ← Mon-Fri 15:30 PDT (7 nodes)
│       ├── fundamentals_weekly.json      ← weekly fundamentals refresh
│       └── trend_daily.json              ← Mon-Fri 11:00 PDT
│
├── 02_scanner/                 ← LAYER 2: Pattern detection (write signal_alerts.status='new')
│   ├── scripts/
│   │   ├── scan_setups.py                ← ★ PRIMARY: 8-gate BUY signal scanner
│   │   ├── detect_ema_crossover.py       ← Daily EMA 9/21 + ADX>25 (supplementary)
│   │   ├── detect_ema_crossover_15m.py   ← 15m EMA cross + real-time Alpaca snapshot
│   │   ├── detect_orb.py                 ← ★ ORB (Opening Range Breakout) scanner — strategy='orb'
│   │   └── detect_liquidity_sweep.py     ← ★ LIVE liquidity sweep scanner (5m + daily, close-beyond)
│   └── n8n/
│       ├── setup_scanner.json            ← ★ Mon-Fri every 15min 6-12 PDT (PRIMARY)
│       ├── ema_crossover_detector.json   ← Mon-Fri 6:00 PDT pre-market (supplementary)
│       ├── ema_crossover_15m.json        ← Mon-Fri every 15min 6-12 PDT (supplementary)
│       ├── orb_detector.json             ← ★ Mon-Fri every 5min 6-13 PDT (ORB)
│       └── liquidity_sweep.json          ← ★ Mon-Fri every 5min 6-12 PDT
│
├── 03_alert/                   ← LAYER 3: Telegram dispatch + exit-fill push messages
│   ├── scripts/
│   │   └── alert_telegram.py             ← SOLE Telegram dispatcher; expire_stale_new + notify_errors
│   └── n8n/
│       └── alert_dispatch.json           ← ★ Mon-Fri every 1min 6-13 PDT
│
├── 04_approval/                ← LAYER 4: Approval + pre-flight
│   ├── scripts/
│   │   ├── generate_signals.py       ← 6-factor composite signal scoring (0-100)
│   │   └── process_approved.py       ← Drawdown halts + PDT + pre-flight → approve/deny
│   └── n8n/
│       ├── signals_daily.json        ← Mon-Fri 16:30 PDT
│       └── process_approved.json     ← Mon-Fri every 1min 6-13 PDT
│
├── 05_execution/               ← LAYER 5: Alpaca paper submission
│   ├── scripts/
│   │   ├── execute_trade.py          ← Alpaca order submit; bracket for stock, mid-price for options
│   │   └── snapshot_equity.py        ← Daily equity snapshot (drawdown denominator)
│   └── n8n/
│       ├── execute_trade.json        ← Mon-Fri every 1min 6-13 PDT
│       └── equity_snapshot_daily.json ← Mon-Fri 14:30 PDT
│
├── 06_exit/                    ← LAYER 6: Exit decision tree
│   ├── scripts/
│   │   └── exit_monitor.py           ← TP/SL/trail-stop/time-stop; trail after TP2 for swing
│   └── n8n/
│       └── exit_monitor.json         ← Mon-Fri every 5min 6-13 PDT
│
├── 07_reconcile/               ← LAYER 7: Fill reconciliation
│   ├── scripts/
│   │   ├── reconcile_orders.py       ← BUY fill → trading.positions
│   │   └── reconcile_exits.py       ← SELL fill → close + realized P&L + exit push notification
│   └── n8n/
│       ├── reconcile_orders.json     ← Mon-Fri every 1min 6-14 PDT
│       └── reconcile_exits.json      ← Mon-Fri every 1min 6-14 PDT
│
├── shared/                     ← Cross-layer Python utilities (imported by 02_scanner, 03_alert, etc.)
│   ├── constants.py                      ← Market hours, session times, MAX_SPREAD_PCT, load_env(), is_market_*()
│   └── fetch_alpaca_snapshot.py          ← ★ Real-time stock + best option snapshot; DELTA_BANDS; reselect_option_for_risk_mode()
│
├── admin/                      ← Operational tooling (not part of the live pipeline)
│   ├── scripts/
│   │   ├── backfill_symbol.py            ← Full ingestion chain for one symbol (calls 01_data/scripts/*)
│   │   ├── backfill_runner.py            ← n8n wrapper: query pending symbols + run backfill_symbol.py per symbol
│   │   └── n8n_api.sh                    ← n8n REST API helper (sources .env.n8n)
│
├── archive/                    ← Decommissioned + v1-pipeline staged for v2 rebuild
│   ├── v1-pipeline/                      ← intraday_signal, alert_telegram (old), execute_trade, exit_monitor, process_approved, reconcile_orders, reconcile_exits, telegram_callback_listener, generate_signals
│   ├── v1-dependents/                    ← snapshot_equity, backfill_signals (+ equity_snapshot_daily.json)
│   ├── polygon-decommissioned/           ← ingest_polygon_ohlcv, ingest_polygon_options, ingest_polygon_fundamentals, backfill_historical_iv
│   ├── explorers/                        ← explore_data, explore_options, options_analysis, ingest_rss_news
│   ├── backtests/                        ← backtest, backtest_liquidity*, diagnose_liquidity_backtest, regime_backtest
│   └── n8n-workflows/                    ← old workflow JSONs paired with archived scripts
│
└── obsidian/vault/         ← knowledge base (30 notes across 8 folders)
    ├── Home.md
    ├── Project Roadmap.md
    ├── 01-Fundamentals/     ← Laws of Trading, Trade Entry Criteria, Unified Entry & Exit Checklist
    ├── 02-Strategies/       ← Day Trading, Swing, Long-Term, EMA Crossover, ORB, Buy the Dip, Greeks Strategy, Liquidity 5m, Risk Management Framework
    ├── 03-Market-Research/  ← Watchlist, Backtesting Architecture
    ├── 04-API-References/   ← Alpaca API, Alpaca Data Pipeline, Polygon.io API
    ├── 05-Risk-Management/  ← Position Sizing, Loss Limits, Correlation Risk
    ├── 06-Indicators/       ← (empty, ready for TA docs)
    ├── 07-Infrastructure/   ← Database Architecture, n8n Scheduler, Telegram Alert System, Order Execution Engine, Monitoring & Dashboards
    └── 08-Templates/        ← Strategy Template, API Reference Template
```

## Current Phase

All phases 1-4 complete. **Phase 5A (signal detection) complete. Phase 5B (execution loop) shipped in v1, now ported to v2 layered structure. Phase 5F (ORB scanner) shipped. Layers 01-07 all populated with scripts + n8n workflows.**

- [x] Docker services running (Postgres, Redis, Obsidian, worker, n8n)
- [x] Alpaca paper trading connected
- [x] Options data explorers working
- [x] Watchlist synced (Alpaca + Postgres)
- [x] Obsidian vault with trading rules, strategies, risk management
- [x] Greeks strategy documented (IV regime, delta, theta, gamma, vanna)
- [x] Backtesting architecture documented (data pipeline, schema, analysis)
- [x] Postgres MCP server configured for Claude Code
- [x] Polygon.io credentials verified (REST API + Flat Files S3)
- [x] Polygon.io ingestion scripts (OHLCV, greeks, fundamentals → Postgres) — now archived
- [x] Alpaca data migration — OHLCV + options ingestion moved from Polygon to Alpaca (free tier)
- [x] Alpaca OHLCV ingestion (`01_data/scripts/ingest_alpaca_ohlcv.py`) — 1d/15m/5m with trade_count + VWAP
- [x] Alpaca options ingestion (`01_data/scripts/ingest_alpaca_options.py`) — chains + greeks + bid/ask
- [x] Real-time snapshot enrichment (`shared/fetch_alpaca_snapshot.py`) — stock price + best option at signal time
- [x] 15m EMA crossover detector (`02_scanner/scripts/detect_ema_crossover_15m.py`) — intraday signals with live option data
- [x] n8n workflows migrated — `01_data/n8n/alpaca_ohlcv_daily`, `alpaca_ohlcv_intraday`, `alpaca_options_daily` (active); old Polygon ingestion JSONs in `archive/`
- [x] DB migrations — trade_count/vwap on ohlcv, bid/ask on greeks
- [x] IV rank / realized vol / GEX-DEX computed
- [x] Technical indicators (EMA, RSI, MACD, ATR, VWAP, Bollinger)
- [x] Greeks filtering engine (IV regime, delta, theta-budget per contract)
- [x] IV outlier detection (3σ z-score)
- [x] Fundamentals ingestion (98 periods)
- [x] RSS/News + Reddit scraper pipeline (69 articles, 75 posts) — script archived
- [x] Composite signal scoring engine (6-factor, 0-100) — currently archived under `v1-pipeline`
- [x] n8n scheduler — layered workflows in `01_data/n8n`, `02_scanner/n8n`, `03_alert/n8n`; archived v1 workflows in `archive/n8n-workflows/`
- [x] `admin/scripts/n8n_api.sh` helper + NODES_EXCLUDE=[] fix for ExecuteCommand
- [x] Docker proxy hardened (allowHEAD + allowGET for exec/{id}/json)
- [x] All cron schedules converted from ET to PDT (America/Los_Angeles)
- [x] Secrets management skill (NEVER hardcode API keys)
- [x] Watchlist lifecycle (YAML source-of-truth, soft-deactivate, backfill chain)
- [x] Backtesting engine (day/swing/long_term with user trading rules, ATR-based SL/TP, partial exits, PDT tracking) — archived
- [x] 5-minute intraday signal refresh (re-scores tech from 5m bars, threshold alerts) — archived
- [x] Market regime classifier (bull/bear/transition via SPY SMA + VIX + breadth, 501 days)
- [x] Regime-conditional factor analysis (per-regime factor-to-return correlations)
- [x] Dynamic weight optimizer (regime-specific scoring weights)
- [x] Multi-timeframe trend detection (micro/intermediate/primary via EMA+ADX+price structure)
- [x] Historical signal backfill (7,908 signals across 501 days)
- [x] Trend-aware intraday adjustments (signal + aligned trend = boost, counter-trend = penalty)
- [x] **Layered refactor** — flat `scripts/` directory split into `01_data/`, `02_scanner/`, `03_alert/`, `shared/`, `admin/`; n8n workflows colocated with their layer; docker-compose mounts per-layer

### Phase 5A: Signal Detection ✅ (complete)
- [x] EMA crossover detector (`02_scanner/scripts/detect_ema_crossover.py`) — 9/21 cross + ADX>25, writes `market.signal_alerts`
- [x] Signal alerts table (`015_signal_alerts.sql`) — full trade plan storage (entry, stops, TP, trend context, greeks, invalidation)
- [x] Telegram alert sender (`03_alert/scripts/alert_telegram.py`) — strategy-specific trade alerts with bid/ask/mid from Alpaca snapshot
- [x] Backfill runner (`admin/scripts/backfill_runner.py`) — n8n wrapper replacing inline shell in backfill_pending workflow
- [x] **Alpaca data migration** — OHLCV + options moved from Polygon ($108/mo) to Alpaca (free)
- [x] **15m EMA crossover detector** (`02_scanner/scripts/detect_ema_crossover_15m.py`) — intraday signals + real-time option enrichment
- [x] **Real-time snapshot** (`shared/fetch_alpaca_snapshot.py`) — stock price + best option at signal time
- [x] **DB migrations** — `017_ohlcv_alpaca_columns.sql` (trade_count, vwap), `018_alpaca_options_columns.sql` (bid, ask)
- [x] **★ Setup scanner** (`02_scanner/scripts/scan_setups.py`) — 8-gate BUY signal scanner; PRIMARY alert mechanism (EMA detectors are now supplementary)
- [x] **★ n8n workflow `setup_scanner`** — runs every 15min during market hours (Mon–Fri 6–12 PDT); silence = no signal
- [x] **★ Liquidity sweep scanner** (`02_scanner/scripts/detect_liquidity_sweep.py`) — 5m + daily, close-beyond confirmation (PF 1.56), Telegram alerts
- [x] **★ Liquidity sweep backtest** (`archive/backtests/backtest_liquidity_v3.py`) — 6-month, 16 symbols; close-beyond = THE key filter
- [x] **ORB breakout detector** (`02_scanner/scripts/detect_orb.py`) — opening range on first 15m bar, breakout on 5m close-beyond, external-level filter, ATR-based intraday stops (strategy='orb', writes signal_alerts)
- [ ] Buy the 5% Dip detector (`detect_dip.py`) — 5% pullback + thesis check + 3-tranche plan
- [ ] Options chain filter (`filter_options.py`) — DTE≥30, delta range, theta budget

### Phase 5B: Exit Monitors & Alert Delivery ✅ (shipped in v1; layers 04–07 staged in `archive/v1-pipeline/` pending v2 port)
- [x] Pre-flight checks (`process_approved.py`) — Laws, PDT (projected, business-day-aware), drawdown halts (period-start denominator + unrealized via Alpaca equity)
- [x] Telegram alert dispatch (`03_alert/scripts/alert_telegram.py` + `alert_dispatch` n8n cron) — 4-button approval keyboard (Approve / Conservative / Aggressive / Deny)
- [x] Telegram callback listener (`telegram_callback_listener.py`) — long-poll daemon, writes `risk_mode`
- [x] Alpaca paper execution (`execute_trade.py`) — `client_order_id`-deduped submits, risk_mode-aware sizing; sets `executed_at=NOW()` before Alpaca submit to prevent orphan rows
- [x] BUY-fill reconciliation (`reconcile_orders.py`) — `FOR UPDATE SKIP LOCKED`, per-row commit, partial UNIQUE indexes on `alpaca_order_id` / `position_id`; CANCEL branch writes `position` with `status='cancelled'` + partial-fill qty; `recover_orphan_executing()` catches both NULL `alpaca_order_id` and NULL `executed_at`
- [x] Exit monitor (`exit_monitor.py`) — stop / premium / TP2 / TP1 partial / time-stop (12:45 PDT) / DTE expiry; fails position with `status='error'` on missing signal row (no silent default to 'bullish'); `seen_ids` livelock guard; `risk_mode`-aware time-stop (aggressive = day-trade flattening); **trailing stop after TP2 for swing** (TRAIL_ACTIVATE + TRAIL_UPDATE actions)
- [x] TP1 50% partial close — submit, reconcile, reduce position quantity
- [x] SELL-fill reconciliation (`reconcile_exits.py`) — closes position, writes `realized_pnl`, flips signal_alerts to `status='exited'`; `FOR UPDATE OF p SKIP LOCKED` + one-row-at-a-time fetch (concurrency safe); `recover_orphan_sells()` scans Alpaca for orphan SELLs; `partial_close_sell()` for partial fills
- [x] Daily equity snapshots (`snapshot_equity.py` + `equity_snapshot_daily` n8n cron) — drawdown halt denominator; missing snapshot = FAIL in `process_approved.py` (not WARN)
- [x] DB migrations: 020 alert lifecycle, 021 position exit columns, 022 composite_score, 023 risk_mode, 024 tp1 partial, 025 equity_snapshots, 026 signal_alerts unique, 027 positions alpaca_order_id, 028 error_notified, 029 position trail_stop_price, 030 status check constraints
- [x] n8n workflows: `alert_dispatch` (now in `03_alert/n8n/`); `execute_trade`, `reconcile_orders`, `reconcile_exits`, `exit_monitor`, `equity_snapshot_daily` (currently in `archive/n8n-workflows/`, to move into 04–07 on rebuild)

#### PR #14 — Third-Pass Audit Fixes
- **CRITICAL** — `exit_monitor` livelock: `fetch_open_position_locked` re-selected the same HOLD row every iteration; fixed with `seen_ids` list passed as `p.id <> ALL(%s)` exclusion
- **HIGH** — `exit_monitor` ignored `risk_mode` in time-stop: aggressive setups (day-trade scalp) weren't flattened at 12:45 PDT; `risk_mode` now flows through `_POSITION_SELECT` to `decide_exit`
- **HIGH** — `reconcile_orders` lock leak: PENDING rows held `FOR UPDATE` locks across the entire batch (no per-row commit); added `conn.commit()` after each row
- **HIGH** — `process_approved` PDT counter referenced dead status `'filled'` (only exists on `signal_alerts`, not `trading_positions`); removed the stale `'filled'` check
- **MEDIUM** — `alert_telegram` double-send: SELECT-then-UPDATE without row lock let overlapping 1-min crons grab the same row; refactored to one-row-at-a-time `SELECT … FOR UPDATE SKIP LOCKED` + `seen_ids` guard
- **MEDIUM** — `telegram_callback_listener` double-tap: rapid Approve→Aggressive could both read `status='new'` and the later overwrote `risk_mode`; added `FOR UPDATE` so the second callback blocks then hits "already actioned"
- **LOW** — `alert_telegram` `POSTGRES_DB` default was `"clawstreetbot"` (typo); corrected to `"clawstreet"` matching all other scripts
- **LOW** — `.gitignore` mojibake: `Thumbs.db` + `.venv/` concatenated on one line; split into separate entries

#### PR #15 — Lifecycle Hardening
- **Expire stale `status='new'`** — `expire_stale_new()` in `alert_telegram.py` flips `status='new'` rows older than 24h to `'expired'` and strips the inline keyboard; runs at top of every `alert_dispatch` cron tick
- **Surface `status='error'` to Telegram** — `notify_errors()` in `alert_telegram.py` edits original Telegram alert with failure reason via `editMessageText`; tracks notification via `signal_alerts.error_notified_at` column (migration 028) so each error is surfaced exactly once
- **Partial-fill on closing SELL** — `partial_close_sell()` in `reconcile_exits.py` reduces position quantity on partial fills (cancel-with-partial or rare short-fill), records partial realized P&L via `COALESCE+=` accumulation, clears sell stamps so `exit_monitor` retries the residual; `mark_closed` also uses `COALESCE+=` for multi-leg accounting
- **Link positions → Alpaca BUY order** — migration 027 adds `trading.positions.alpaca_order_id` with partial UNIQUE index; `reconcile_orders.insert_position` now writes the order id directly on the position row

#### PR #16 — Phase 5C: Orphan Recovery + Trailing Stop
- **Orphan executing recovery** — `recover_orphan_executing()` in `reconcile_orders.py` flips `status='executing'` AND `alpaca_order_id IS NULL` AND `executed_at < NOW() - 5 min` to `status='error'`; runs at top of every `reconcile_orders` cron tick before normal fetch; `notify_errors` surfaces failure on next `alert_dispatch`
- **Trailing stop after TP2 for swing** — `exit_monitor.py` now activates a trailing stop instead of full-closing at TP2 for swing positions; new actions `TRAIL_ACTIVATE` (initial trail = underlying ∓ 2×ATR) + `TRAIL_UPDATE` (monotonically raise/lower); migration 029 adds `trading.positions.trail_stop_price` (NULL ⇒ trail not active); trail breach fires `ACTION_FULL_CLOSE` with `reason='trail_stop'`; day/long_term modes still full-close at TP2

#### PR #17 — Pass-4 Audit: 7 Critical Money-Loss/State-Loss Fixes
- **C1 — Orphan executing rows rotted forever** — `execute_trade.py` now sets `executed_at=NOW()` before Alpaca submit (was NULL until fill, making reaper unable to find them); `recover_orphan_executing()` in `reconcile_orders.py` now catches both `alpaca_order_id IS NULL` and `executed_at IS NULL` cases
- **C2 — Partial-then-cancel BUYs silently lost** — `reconcile_orders.py` had no CANCEL handling path; partially filled then cancelled BUYs left no position row, so `exit_monitor` never closed them; fixed with CANCEL branch that writes a `position` with `status='cancelled'` and any partial-fill quantity
- **C3 — `decide_exit` defaulted to 'bullish' on missing signal** — LEFT JOIN + COALESCE meant a missing signal row produced a default 'bullish' stance, inverting bearish exit logic; fixed by failing the position with `status='error'` when no signal row is found
- **C4 — `reconcile_exits` double-decremented quantity / double-counted P&L** — no row locks let concurrent crons process the same SELL fill twice; fixed with `FOR UPDATE OF p SKIP LOCKED` + one-row-at-a-time fetch + per-row commit
- **C5 — Orphan SELL orders had no recovery** — SIGKILL mid-reconcile left a live SELL at Alpaca but DB stuck open; added `recover_orphan_sells()` in `reconcile_exits.py` that scans Alpaca for SELL orders with no matching DB position and either marks position closed or logs for manual review
- **C6 — Drawdown halt bypassed on missing equity snapshot** — missing snapshot logged WARN (not FAIL), allowing 10/20/30% thresholds to be silently bypassed; fixed: missing snapshot = FAIL in `process_approved.py`
- **C7 — `notify_errors` batch-commit rolled back on Telegram outage** — exception mid-loop rolled back all progress, re-notifying already-surfaced errors on retry; fixed with per-row commit in `alert_telegram.py`

### Phase 5C: Backlog (deferred)
- [x] ~~**ORB breakout detector** (`02_scanner/scripts/detect_orb.py`)~~ → **Done in PRs #20-22** — opening range on first 15m bar, 5m close-beyond breakout, external-level filter, ATR-based intraday stops; `orb_detector.json` n8n workflow every 5min 6-13 PDT; `format_orb_alert()` in alert_telegram.py; DB env-var fallback fix; full-session scan window (not last 3 bars)
- [ ] Buy the 5% Dip detector (`detect_dip.py`) — pullback + thesis check + 3-tranche scale-in
- [x] ~~Per-risk-mode option selection — currently the scanner binds a 0.50-0.70 delta contract at scan time, before the user picks Conservative/Aggressive (audit M1)~~ → **Done in PR #19** (DELTA_BANDS in `shared/fetch_alpaca_snapshot.py` + reselect_option_for_risk_mode() in execute_trade.py)
- [x] ~~Bracket orders for stock entries on Alpaca — current entries are naked, exits rely 100% on `exit_monitor` uptime (audit H7)~~ → **Done in PR #19** (order_class=BRACKET for non-option stock entries with stop_loss + take_profit; cancel_open_orders_for_symbol before exit_monitor closes)
- [x] ~~Trailing stop after TP2 for swing mode — currently TP2 full-closes~~ → **Done in PR #16** (TRAIL_ACTIVATE + TRAIL_UPDATE + migration 029)
- [ ] Risk alerts (`alert_risk.py`) — drawdown halt, PDT warning, position breach push notifications
- [ ] Aggressive button UX — currently silently promotes a swing setup to day-mode for PDT purposes; surface this in the Telegram preview before approval
- [ ] Idempotency keys for Telegram API — `alert_telegram` has no message-id dedup at the API level; relies on DB locking only (audit M5 residual)
- [ ] Exit-monitor graceful degradation — if Alpaca API is down, HOLD rows accumulate; consider exponential backoff + max-hold timer (audit C1 residual)

### Phase 5E: Notification UX ✅ (shipped — PR #18)
- [x] Strategy name in entry-alert headers — every formatter now shows `Strategy: <name> | Timeframe: <tf>` (was missing on setup_scanner, ema_crossover, ema_crossover_15m)
- [x] Exit-fill Telegram notifications — `reconcile_exits` fires a push after every close commit (full close, TP1 partial, partial-before-cancel, defensive partial). 💰 wins / ⛔ losses / 📤 mechanics. Fire-and-forget — Telegram failure logged but never rolls back DB.

#### PR #19 — Status-Check Constraints, Risk-Mode Delta Bands, Bracket Orders (M1 + H7)
- **CHECK constraints on status enums** — migration `030_status_check_constraints.sql` adds `signal_alerts_status_check` (9 valid values) and re-states `positions_status_check` (open/closed/cancelled). Typos like `'exeucting'` or `'fllied'` now fail at INSERT/UPDATE instead of silently corrupting the lifecycle state machine. Pre-flight repair included (rejected→denied, closed→exited).
- **Per-risk-mode delta bands (M1)** — `DELTA_BANDS` dict in `shared/fetch_alpaca_snapshot.py` maps conservative (0.55–0.65, aim 0.60), standard (0.50–0.70, aim 0.60), aggressive (0.40–0.80, aim 0.50). `reselect_option_for_risk_mode()` in `execute_trade.py` re-queries Alpaca for the best contract in the user-chosen band and overwrites the signal's option fields on `market.signal_alerts`. Standard is no-op (scanner already picked in that band).
- **Bracket orders for stock entries (H7)** — `execute_trade.py` now submits `order_class=BRACKET` (market BUY + stop-loss leg at stop_price + take-profit leg at tp2_price) for non-option bullish entries. `exit_monitor.cancel_open_orders_for_symbol()` cancels bracket legs before exit_monitor issues its own close. Options and bearish stock entries still use plain market orders.
- **Cron drift fix** — `reconcile_orders` and `reconcile_exits` run 6–14 PDT (not 6–13), giving an extra hour past close for late fills.

### Phase 5F: ORB Scanner ✅ (PRs #20–22)
- [x] **ORB breakout detector** (`02_scanner/scripts/detect_orb.py`) — scans SPY 15m bars for Opening Range (first 15m candle high/low after 9:30 ET), then detects 5m close-beyond breakouts after 9:45 ET; external-level filter skips setups within 0.5% of prior day's high/low; ATR-based intraday stops (ATR×1.5 stop, ATR×4.5 TP1, ATR×7.5 TP2)
- [x] **`02_scanner/n8n/orb_detector.json` n8n workflow** — every 5min, 6–13 PDT, Mon–Fri; triggers `detect_orb.py` which writes to `market.signal_alerts` with `strategy='orb'`
- [x] **`format_orb_alert()`** in `03_alert/scripts/alert_telegram.py` — `alert_dispatch.json` picks up `strategy='orb'` rows via SQL and dispatches Telegram alerts with 4-button approval keyboard
- [x] **`execute_trade.py` handles `strategy='orb'`** — ATR-based intraday stops (1.5× ATR stop, 4.5× ATR TP1, 7.5× ATR TP2); day-trade mode (flatten before close)
- [x] **`exit_monitor.py` manages ORB exits** — same as other intraday strategies (stop, TP1 partial, TP2/trail, time-stop)
- [x] **DB env-var fallback** (PR #21) — `detect_orb.py` sources `.env.db` for `POSTGRES_DB` with fallback to `"clawstreet"` (matching all other scripts)
- [x] **Full-session scan window** (PR #22) — scans all 5m bars after 9:45 ET in the session, not just the last 3 bars; ensures the ORB breakout isn't missed on wider moves
- [x] **SPY 15m bars backfilled** — 2,667 bars (~120 trading days) for Opening Range calculation

### Remaining Items (non-Phase 5)
- [ ] Rebuild layers 04–07 (approval, execution, exit, reconcile) from `archive/v1-pipeline/` into their numbered directories
- [ ] Position sizing calculator (backtest has it, no standalone tool)
- [ ] Monitoring/dashboards (no visibility beyond raw DB queries)
- [ ] Regime optimizer needs more diverse data (underperforms static with full history — not a code fix)

## Security Architecture

- **n8n does NOT have direct docker.sock access** — routes through `wollomatic/socket-proxy`
- Proxy allowlists: `GET /_ping`, `GET /version`, `GET /containers/clawstreet-worker/json`, `GET /exec/{id}/json`, `HEAD /_ping`, `HEAD /version`, `POST /containers/clawstreet-worker/exec`, `POST /exec/{id}/start`, `POST /exec/{id}/resize`
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
