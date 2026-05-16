---
created: 2026-05-16
updated: 2026-05-16
tags: [backtesting, data-pipeline, polygon, postgres, mOC]
---

# Backtesting Architecture — Data Pipeline & Analysis Framework

> Backtesting without good data is just storytelling. Every strategy must be validated against real historical data before a single dollar goes on the line.

This document covers the full pipeline: **Polygon.io → Postgres → Analysis → Backtest**, and how Claude Code (via MCP) fits in as a research assistant.

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│                     DATA INGESTION LAYER                      │
│                                                               │
│  Polygon.io REST API ──→ Ingestion Scripts (Python)          │
│  Polygon.io Flat Files ──→ Bulk CSV Loader (Phase 2)         │
│  Polygon.io WebSocket ──→ Real-time Stream (Phase 3)         │
│                                                               │
│  Formats: OHLCV bars, options chains, greeks, fundamentals,   │
│           news, corporate actions, economic indicators         │
└──────────────────────┬───────────────────────────────────────┘
                       │
                       v
┌──────────────────────────────────────────────────────────────┐
│                     STORAGE LAYER                             │
│                                                               │
│  PostgreSQL 16 (Docker, clawnet:5432)                         │
│  ├── market.assets ─── symbols, sectors, industries          │
│  ├── market.ohlcv ───── daily/hourly/minute bars              │
│  ├── market.options ─── contracts, strikes, expirations      │
│  ├── market.greeks ──── delta, gamma, theta, vega, rho, IV   │
│  ├── market.fundamentals ─ financials, earnings, dividends   │
│  ├── market.iv_rank ─── 52-week IV high/low/percentile        │
│  ├── scraper.sources ── RSS/API sources                       │
│  ├── scraper.articles ── news articles with sentiment         │
│  └── trading.signals ─── generated trade signals              │
│                                                               │
│  MCP Access: Claude Code → Postgres MCP → Direct SQL queries │
└──────────────────────┬───────────────────────────────────────┘
                       │
                       v
┌──────────────────────────────────────────────────────────────┐
│                     ANALYSIS LAYER                             │
│                                                               │
│  Python (pandas, numpy, psycopg2)                            │
│  ├── Technical indicators (TA-Lib / pandas-ta)               │
│  ├── Greeks calculations & filters                           │
│  ├── IV Rank computation                                     │
│  ├── Correlation analysis                                    │
│  └── Signal scoring engine                                   │
│                                                               │
│  Claude Code (via MCP) ──→ Research, ad-hoc queries,          │
│  └── visualizations, debug SQL, schema changes               │
└──────────────────────┬───────────────────────────────────────┘
                       │
                       v
┌──────────────────────────────────────────────────────────────┐
│                     BACKTESTING ENGINE                         │
│                                                               │
│  Input: Strategy rules + historical data from Postgres        │
│  Output: P&L curve, win rate, max drawdown, Sharpe ratio      │
│                                                               │
│  1. Signal Generation ──→ What trades would have triggered?   │
│  2. Position Sizing ─────→ How much per trade (per rules)?    │
│  3. Execution Simulation ─→ Entry/exit prices, slippage      │
│  4. Greeks Overlay ──────→ Filter by IV Rank, delta, theta    │
│  5. Risk Management ─────→ Drawdown halts, position limits     │
│  6. Results ─────────────→ Statistics, equity curve, journal  │
└──────────────────────────────────────────────────────────────┘
```

---

## Phase 2a: Polygon.io Data Ingestion

### Priority Order (Build First → Last)

| # | Data Type | Polygon Endpoint | Postgres Table | Priority |
|---|-----------|-----------------|----------------|----------|
| 1 | OHLCV (daily bars) | `/v2/aggs/ticker/{ticker}/range/1/day/` | `market.ohlcv` | 🔴 Critical |
| 2 | Options snapshots (current greeks) | `/v3/snapshot/option/{ticker}` | `market.greeks` | 🔴 Critical |
| 3 | Options chain listing | `/v3/snapshot/options/{underlying}` | `market.options` | 🔴 Critical |
| 4 | IV calculation & rank | Derived from greeks snapshots | `market.iv_rank` | 🟡 High |
| 5 | Fundamentals (financials) | `/vX/financials/{ticker}` | `market.fundamentals` | 🟡 High |
| 6 | News & sentiment | `/v2/reference/news` | `scraper.articles` | 🟢 Medium |
| 7 | OHLCV (intraday) | `/v2/aggs/ticker/{ticker}/range/{timespan}/` | `market.ohlcv` | 🟢 Medium |
| 8 | Corporate actions | `/v2/reference/splits`, dividends | `market.corporate_actions` | 🟢 Medium |
| 9 | Economic indicators | `/vX/metrics/financials/{ticker}` | `market.economic` | ⚪ Low |

### Ingestion Scripts

```
ClawStreetBot/
├── scripts/
│   ├── ingest_polygon_ohlcv.py       # Daily bars for watchlist
│   ├── ingest_polygon_options.py     # Options chains + greeks snapshots
│   ├── ingest_polygon_fundamentals.py # Financials, earnings, dividends
│   ├── ingest_polygon_news.py        # News articles with sentiment
│   ├── compute_iv_rank.py            # Calculate IV rank from historical IV
│   └── setup_watchlist.py            # Already exists (Alpaca sync)
```

### Ingestion Design Principles

- **Idempotent:** Run any script multiple times without duplicating data (UPSERT pattern)
- **Incremental:** Only fetch new data since last ingestion (track `last_updated` per symbol)
- **Rate-limit aware:** Respect Polygon rate limits (5 req/min on Basic tier)
- **Watchlist-scoped:** Only ingest data for our 15 watchlist stocks by default
- **Gitignored credentials:** All API keys from `.env.polygon`

### Database Schema Additions

```sql
-- Options contracts metadata
CREATE TABLE market.options (
    id              SERIAL PRIMARY KEY,
    occ_symbol      VARCHAR(30) UNIQUE NOT NULL,  -- O:NVDA260619C00125000
    underlying      VARCHAR(10) NOT NULL,
    contract_type   CHAR(1) NOT NULL,              -- C or P
    strike          DECIMAL(12,4) NOT NULL,
    expiration      DATE NOT NULL,
    exercise_style VARCHAR(4) DEFAULT 'american',
    cfi_code        VARCHAR(6),
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_options_underlying ON market.options(underlying);
CREATE INDEX idx_options_expiration ON market.options(expiration);

-- Greeks snapshots (daily per contract)
CREATE TABLE market.greeks (
    id              SERIAL PRIMARY KEY,
    occ_symbol      VARCHAR(30) REFERENCES market.options(occ_symbol),
    date            DATE NOT NULL,
    delta           DECIMAL(8,4),
    gamma           DECIMAL(8,4),
    theta           DECIMAL(8,4),
    vega            DECIMAL(8,4),
    rho             DECIMAL(8,4),
    vanna           DECIMAL(8,4),
    iv              DECIMAL(12,4),       -- Implied volatility
    open_interest   INTEGER,
    bid             DECIMAL(12,4),
    ask             DECIMAL(12,4),
    midpoint        DECIMAL(12,4),
    last_price      DECIMAL(12,4),
    volume          BIGINT,
    vwap            DECIMAL(12,4),
    break_even      DECIMAL(12,4),
    underlying_price DECIMAL(12,4),
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(occ_symbol, date)
);
CREATE INDEX idx_greeks_symbol_date ON market.greeks(occ_symbol, date);
CREATE INDEX idx_greeks_date ON market.greeks(date);

-- IV Rank (derived from greeks snapshots)
CREATE TABLE market.iv_rank (
    id              SERIAL PRIMARY KEY,
    symbol          VARCHAR(10) NOT NULL,
    date            DATE NOT NULL,
    current_iv      DECIMAL(8,4),
    iv_rank_52w     DECIMAL(8,4),    -- 0-100 percentile
    iv_low_52w      DECIMAL(8,4),
    iv_high_52w     DECIMAL(8,4),
    iv_percentile   DECIMAL(8,4),
    iv_std_dev      DECIMAL(8,4),
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(symbol, date)
);
CREATE INDEX idx_iv_rank_symbol ON market.iv_rank(symbol);

-- Fundamentals
CREATE TABLE market.fundamentals (
    id              SERIAL PRIMARY KEY,
    symbol          VARCHAR(10) NOT NULL,
    date            DATE NOT NULL,
    revenue         DECIMAL(18,2),
    net_income      DECIMAL(18,2),
    eps             DECIMAL(12,4),
    pe_ratio        DECIMAL(12,4),
    market_cap      DECIMAL(18,2),
    debt_to_equity  DECIMAL(12,4),
    free_cash_flow  DECIMAL(18,2),
    dividend_yield  DECIMAL(8,4),
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(symbol, date)
);
CREATE INDEX idx_fundamentals_symbol ON market.fundamentals(symbol);
```

---

## Phase 2b: Claude Code + MCP Integration

### MCP Configuration

The Postgres MCP server is configured in `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  postgres:
    command: "npx"
    args:
      - "-y"
      - "@modelcontextprotocol/server-postgres"
      - "postgresql://clawstreet:ClawStr33tBot2026@localhost:5432/clawstreet"
    timeout: 30
```

### What Claude Code Can Do via MCP

With the Postgres MCP server, Claude Code gets **direct database access** for:

1. **Ad-hoc research queries:**
   - "What's NVDA's IV rank over the last 30 days?"
   - "Show me all contracts with delta between 0.50-0.70 expiring in July"
   - "Which watchlist stocks have IV Rank below 25%?"

2. **Schema management:**
   - Creating tables, adding indexes, migrating schemas
   - Validating data integrity after ingestion

3. **Exploratory analysis:**
   - Computing correlations between watchlist stocks
   - Checking greeks distributions across our universe
   - Identifying unusual options activity

4. **Backtesting data preparation:**
   - Generating signal datasets for backtest runs
   - Computing indicator values from raw OHLCV data
   - Creating time-series windows for walk-forward analysis

### Claude Code Project Context

Add a `CLAUDE.md` or `.claude/` project config that includes:
- Database schema reference
- Watchlist symbols
- Trading rules (Laws of Trading, risk profiles)
- Query patterns for common research tasks

---

## Phase 2c: Backtesting Engine

### Requirements

| Requirement | Detail |
|------------|--------|
| **Data source** | Postgres `market.*` tables (ingested from Polygon.io) |
| **Timeframes** | Daily (primary), intraday (Phase 3) |
| **Watchlist** | 15 stocks: NVDA, AMD, MU, WDC, STX, APLD, IREN, NBIS, CIFR, RDDT, SERV, RKLB, ASTS, OKLO, NVO |
| **Strategy types** | EMA Crossover, ORB, Buy the 5% Dip, Swing momentum |
| **Greeks overlay** | IV regime filters, delta entry qualification, theta budget |
| **Risk rules** | 10% risk/trade (swing), 3:1 R:R, 20% position cap, drawdown halts |
| **Slippage model** | 0.1% for stocks, bid-ask spread midpoint for options |
| **Commission model** | $0.005/share (stocks), $0.65/contract (options) |

### Backtest Pipeline

```python
# Pseudocode for backtest engine
for signal_date in date_range(start, end):
    # 1. Get market data for this date
    bars = get_ohlcv(watchlist, signal_date)
    greeks = get_greeks(watchlist, signal_date)
    iv_rank = get_iv_rank(watchlist, signal_date)

    # 2. Generate signals (strategy rules)
    signals = strategy.evaluate(bars, indicators)

    # 3. Apply greeks filters
    for signal in signals:
        if iv_rank[signal.symbol] > 75:           # IV too high
            signal.reject("IV Rank > 75%")
            continue
        if signal.delta < 0.50 or signal.delta > 0.90:  # Delta out of range
            signal.reject(f"Delta {signal.delta} out of range")
            continue
        if signal.theta_budget > 0.05:             # Theta too expensive
            signal.reject(f"Theta budget {signal.theta_budget:.1%}")
            continue

    # 4. Size positions (risk management rules)
    for signal in signals:
        position = calculate_position_size(
            equity=portfolio.equity,
            strategy=signal.strategy,  # "day", "swing", "long_term"
            entry=signal.entry_price,
            stop=signal.stop_price,
        )
        if position.risk_pct > 0.10:  # Risk rule
            position = cap_risk(position, max_risk=0.10)
        if position.pct_equity > 0.20:  # Law 3
            position = cap_position(position, max_pct=0.20)

    # 5. Execute trades (simulate)
    for position in positions:
        fill = simulate_execution(position, bars, slippage, commission)
        portfolio.open(fill)

    # 6. Check exits (stop-loss, take-profit, time stop, greeks exits)
    for position in portfolio.open_positions:
        if hit_stop_loss(position, bars):      position.close(stop_loss)
        elif hit_take_profit(position, bars):  position.close(take_profit)
        elif hit_time_stop(position, signal_date): position.close(time_stop)
        elif theta_exceeded(position, greeks): position.close(theta_exit)
        elif iv_regime_shift(position, iv_rank): position.close(iv_exit)

    # 7. Drawdown halts
    if portfolio.daily_drawdown > 0.10:  # 10% daily
        halt("Daily drawdown circuit breaker")
    elif portfolio.weekly_drawdown > 0.20:  # 20% weekly
        halt("Weekly drawdown circuit breaker")
    elif portfolio.monthly_drawdown > 0.30:  # 30% monthly
        halt("Monthly drawdown circuit breaker")

    # 8. Record results
    portfolio.record_daily_snapshot()
```

### Output Metrics

| Metric | Description |
|--------|-------------|
| Total P&L | Cumulative profit/loss |
| Win rate | % of profitable trades |
| Average win | Average P&L on winning trades |
| Average loss | Average P&L on losing trades |
| R:R achieved | Actual reward-to-risk ratio |
| Max drawdown | Largest peak-to-trough decline |
| Sharpe ratio | Risk-adjusted return |
| Sortino ratio | Downside risk-adjusted return |
| Profit factor | Gross profits / gross losses |
| Kelly % | Optimal bet size from win rate & R:R |
| Greeks filter impact | P&L with vs. without greeks filters |
| IV regime distribution | % of trades by IV regime (low/normal/elevated/high) |

---

## Phase 2d: Flat Files Bulk Import (Optional)

For initial historical backfill, Polygon's Flat Files are much faster than paginated API calls.

1. Download bulk CSVs from Polygon.io (stocks, options)
2. Load into Postgres via `COPY` command (10-100x faster than row-by-row INSERT)
3. Then switch to incremental REST API updates for ongoing data

```
# Example: Bulk import daily bars
psql -c "\COPY market.ohlcv FROM '/data/polygon/stocks/2024/NVDA.csv' WITH (FORMAT csv, HEADER true)"
```

---

## Claude Code Session Plan

When working with Claude Code on backtesting:

1. **Start any Claude Code session in the ClawStreetBot workspace** — it needs project context
2. **MCP provides direct SQL access** — Claude can explore data, create tables, write queries
3. **Use Claude for:** writing ingestion scripts, computing indicators, debugging queries, analyzing results
4. **Don't use Claude for:** real-time trading decisions (that's the bot's job)

### Example Claude Code Prompts

- "What's the average IV rank for NVDA over the past 90 days?"
- "Show me all options contracts for AMD expiring in the next 60 days with delta between 0.50 and 0.70"
- "Calculate the 14-day ATR for each watchlist stock"
- "Backtest EMA crossover (9/21) on RDDT for the last 6 months with 10% risk and 3:1 R:R"
- "Which watchlist stocks currently have IV Rank below 25%?"

---

## See Also

- [[Polygon.io API]] — Full API reference for ingestion scripts
- [[Alpaca API]] — Real-time execution and current snapshots
- [[Greeks Strategy]] — How greeks filter and qualify trades
- [[Trade Entry Criteria]] — Signal categories and composite scoring
- [[Position Sizing]] — Position size calculations (the backtest enforces these)
- [[Loss Limits]] — Stop-loss, drawdown halts, time stops (the backtest enforces these)
- [[Database Architecture]] — Full schema reference