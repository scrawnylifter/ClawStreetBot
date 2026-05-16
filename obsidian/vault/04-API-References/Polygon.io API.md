---
created: 2026-05-15
updated: 2026-05-15
tags: [api, polygon, massive, market-data, mOC]
---

# Polygon.io / Massive API Reference

## Overview

[Polygon.io](https://polygon.io) is our **historical and real-time market data provider**, accessed via the **Massive** API platform ([massive.com/docs](https://massive.com/docs)). We use it alongside Alpaca — Alpaca handles **trading and orders**, Massive handles **deep historical data and analysis**.

> **Massive.com = Polygon.io's API platform.** The endpoints, response structure, and authentication are identical. Only the base URL differs (`api.massive.com` vs `api.polygon.io`). All Polygon.io SDKs and client libraries work with both.

- **API Docs:** [https://massive.com/docs](https://massive.com/docs)
- **REST Base URL:** `https://api.polygon.io` (or `https://api.massive.com`)
- **WebSocket:** `wss://socket.polygon.io` (or Massive WS)
- **SDK:** [polygon-api-client (Python)](https://github.com/polygon-io/client-python)
- **Install:** `pip install polygon-api-client`
- **3 Access Methods:** REST API (on-demand queries), WebSocket (real-time streaming), Flat Files (bulk CSV downloads)

## Why Polygon.io / Massive alongside Alpaca?

|| Data | Alpaca | Polygon.io |
|------|--------|-----------|
| Trading / orders | ✅ Broker | ❌ Data only |
| Real-time quotes | 15-min delayed (free) | ✅ Real-time (paid) |
| Historical bars | Limited (2 years free) | ✅ Full history (decades) |
| Options greeks | ✅ Snapshots only | ✅ Full historical chains + greeks |
| Fundamentals | ❌ | ✅ Financials, earnings, dividends |
| News | ✅ Basic | ✅ Full news feed + sentiment |
| Corporate actions | ✅ Basic | ✅ Splits, dividends, spinoffs |
| Aggregates (OHLCV) | ✅ Intraday | ✅ All timescales, tick-level |
| Technical indicators | ❌ | ✅ SMA, EMA, MACD, RSI, BB, ATR |
| Economy data | ❌ | ✅ Treasuries, CPI, GDP, unemployment |
| Alternative data | ❌ | ✅ Consumer spending (EU) |
| Futures/Forex/Crypto | ❌ | ✅ All asset classes |
| Partner data | ❌ | ✅ Benzinga, ETF Global, TMX |

**Rule:** Alpaca for execution. Polygon.io for research, backtesting, and analysis.

## Three Access Methods

### 1. REST API (On-Demand Queries)
Best for: fetching specific data, ad-hoc analysis, backend integration.
- Full endpoint coverage (stocks, options, futures, forex, crypto, indices, economy)
- Cursor-based pagination via `next_url`
- Max results per request: 50,000 (aggregates), 1,000 (trades/quotes/contracts/news)

### 2. WebSocket API (Real-Time Streaming)
Best for: live trading platforms, real-time dashboards, monitoring tools.
- Per-minute or per-second aggregate streams
- Trade and quote streams
- Fair market value streams
- Available for: Stocks, Options, Futures, Indices, Forex, Crypto
- Docs: [massive.com/docs/websocket/quickstart](https://massive.com/docs/websocket/quickstart)

### 3. Flat Files (Bulk CSV Downloads)
Best for: backtesting, ML training, full historical dataset ingestion.
- Download entire datasets as CSVs
- Available for: Stocks (day/minute aggregates, trades, quotes), Options (day/minute aggregates, trades, quotes), Futures, Indices, Forex, Crypto
- Docs: [massive.com/docs/flat-files/quickstart](https://massive.com/docs/flat-files/quickstart)
- **Recommended for initial backfill** — much faster than paginated API calls

## Authentication

Single API key — free tier available, paid tiers unlock real-time and more data.

```python
from polygon import RESTClient

# API key stored in .env.polygon (gitignored)
client = RESTClient(api_key="POLYGON_API_KEY")
```

> ⚠️ **Store your key in `.env.polygon`** (gitignored). Never commit API keys.

### Rate Limits by Tier

|| Tier | Price | Markets | Rate Limit | Data |
|------|-------|---------|------------|------|
| Basic | $29/mo | Stocks | 5 req/min | 15-min delayed |
| Starter | $49/mo | Stocks | Unlimited | Real-time |
| Developer | $199/mo | Stocks + Options + FX | Unlimited | Real-time + historical |
| Advanced | $499/mo | All | Unlimited | Real-time + websocket |

Response headers for rate limiting:
- `X-RateLimit-Limit` — Total requests allowed per minute
- `X-RateLimit-Remaining` — Requests remaining in current window
- `X-RateLimit-Reset` — Unix timestamp when window resets
- `Retry-After` — Seconds until next request (on 429 responses)

> **Recommendation:** Start with **Basic** (for backtesting historical data). Upgrade to **Developer** when we need real-time options data.

## REST API Endpoints — Stocks

### Aggregates (OHLCV Bars)

The core data source for backtesting and technical analysis.

**Endpoint:** `GET /v2/aggs/ticker/{stocksTicker}/range/{multiplier}/{timespan}/{from}/{to}`

```python
from polygon import RESTClient

client = RESTClient(api_key="...")

# Daily bars for NVDA (full history)
aggs = client.list_aggs(
    ticker="NVDA",
    multiplier=1,
    timespan="day",
    from_="2020-01-01",
    to="2026-05-15",
    limit=50000
)

for agg in aggs:
    print(f"{agg.timestamp} | O:{agg.open} H:{agg.high} L:{agg.low} C:{agg.close} V:{agg.volume}")
```

**Path Parameters:**
- `stocksTicker` — Stock ticker (e.g., `"AAPL"`). Prefix `"I:"` for indices (e.g., `"I:SPX"`)
- `multiplier` — Timespan multiplier (1, 5, 15, 60)
- `timespan` — `minute`, `hour`, `day`, `week`, `month`, `quarter`, `year`
- `from` / `to` — Start/end date (`YYYY-MM-DD` or Unix ms timestamp)

**Query Parameters:**
- `adjusted` (default: true) — Adjust for splits
- `sort` — `"asc"` or `"desc"` (default: `"asc"`)
- `limit` — Results per page (default: 5000, max: 50000)

**Response per bar:** `v` (volume), `vw` (VWAP), `o` (open), `c` (close), `h` (high), `l` (low), `t` (timestamp ms), `n` (transactions)

|| Timespan | Multiplier | Use Case |
|----------|-----------|----------|
| `minute` | 1, 5, 15 | Day trading, scalping |
| `hour` | 1 | Swing entry timing |
| `day` | 1 | Swing analysis, backtesting |
| `week` | 1 | Trend identification |
| `month` | 1 | Long-term holding analysis |

### Daily Open/Close

Single-day snapshot for any date. **Endpoint:** `GET /v1/open-close/{stocksTicker}/{date}`

```python
daily = client.get_daily_open_close_agg("NVDA", "2026-05-14")
print(f"Open: {daily.open}, Close: {daily.close}, Volume: {daily.volume}")
```

### Grouped Daily (All tickers, one day)

Bulk fetch for watchlist scanning — one request for all stocks on a given date.

**Endpoint:** `GET /v2/aggs/grouped/locale/us/market/stocks/{date}`

```python
grouped = client.get_grouped_daily_agg("2026-05-14")
for g in grouped:
    if g.ticker in watchlist_symbols:
        print(f"{g.ticker}: Close {g.close}, Volume {g.volume}")
```

### Previous Day Bar

Last trading day's OHLCV. **Endpoint:** `GET /v2/aggs/ticker/{ticker}/prev`

### Stock Snapshots

Real-time or delayed snapshot of current market data.

**Single ticker:** `GET /v2/snapshot/locale/us/markets/stocks/{ticker}`

**All tickers:** `GET /v2/snapshot/locale/us/markets/stocks/tickers`

```python
# Single ticker
snap = client.get_snapshot("AAPL", "stocks")
print(f"Last: {snap.last_trade.price}, Day change: {snap.todays_change_percent}%")

# Gainers / Losers (top 20)
gainers = client.get_snapshot_gain_loss("stocks", "gainers")
losers = client.get_snapshot_gain_loss("stocks", "losers")
```

**Response per ticker:** `day` (c, h, l, o, v, vw), `last_trade` (price, size, timestamp), `last_quote` (ask, ask_size, bid, bid_size, midpoint), `min` (current minute bar), `prev_day`, `todays_change`, `todays_change_percent`, `updated`

### Technical Indicators

Built-in TA calculations — no need to compute manually.

**Endpoint:** `GET /v1/indicators/{indicator}/ticker/{ticker}`

**Available indicators:** `sma`, `ema`, `macd`, `rsi`, `bb` (Bollinger Bands), `stoch` (Stochastic), `atr` (Average True Range), `cci` (Commodity Channel Index), `obv` (On-Balance Volume), `vwap`

```python
# 14-day RSI for NVDA daily
from polygon import RESTClient
client = RESTClient(api_key="...")

# Note: indicators are accessed via raw REST, not the SDK's convenience methods
import requests
resp = requests.get(
    "https://api.polygon.io/v1/indicators/rsi/NVDA",
    params={"timespan": "day", "window": 14, "apiKey": "YOUR_KEY"}
)
data = resp.json()
for v in data["results"]["values"]:
    print(f"Timestamp: {v['timestamp']}, RSI: {v['value']}")
```

**Parameters:**
- `timespan` (required): `minute`, `hour`, `day`, `week`, `month`
- Indicator-specific:
  - SMA/EMA: `window` (default: 50)
  - MACD: `short_window`(12), `long_window`(26), `signal_window`(9)
  - RSI: `window` (default: 14)
  - BB: `window`(20), `upper_multiplier`(2), `lower_multiplier`(2)
- Common: `timestamp_from`, `timestamp_to`, `adjusted`

**Response:** `indicator` (name, description, parameters), `results.values` (array of `{timestamp, value}`). For MACD, `value` is `{macd, signal, histogram}`.

## REST API Endpoints — Options

### Options Overview

Polygon covers all US-listed equity options (CBOE, etc.) with:
- Contract listing with full metadata
- Historical OHLCV bars per contract
- Real-time/delayed snapshots with greeks and IV
- Full chain snapshot for any underlying

### Options Contracts

Find all available option contracts for a symbol.

**Endpoint:** `GET /v3/reference/options/contracts`

```python
# List call options for NVDA expiring >= June 2026
contracts = client.list_options_contracts(
    underlying_ticker="NVDA",
    contract_type="call",
    expiration_date_gte="2026-06-13",
    limit=1000
)

for c in contracts:
    print(f"{c.ticker} | Strike: {c.strike_price} | Exp: {c.expiration_date} | Style: {c.exercise_style}")
```

**Query Parameters:**
- `underlying_ticker` — Underlying stock ticker (e.g., `"NVDA"`)
- `contract_type` — `"call"` or `"put"`
- `expiration_date` / `expiration_date_gte` / `expiration_date_lte` — Filter by expiry
- `strike_price` / `strike_price_gte` / `strike_price_lte` — Filter by strike
- `limit` — Max results (default: 100, max: 1000)
- `sort` — Sort field, `order` — `"asc"` / `"desc"`

**Response per contract:** `ticker`, `underlying_ticker`, `contract_type`, `exercise_style` (american/european), `expiration_date`, `strike_price`, `shares_per_contract`, `primary_exchange`, `settlement_type`, `size`, `cfi_code`, `root_ticker`

### Options Snapshot — Single Contract

**Endpoint:** `GET /v3/snapshot/options/{underlying_asset}/{options_contract}`

```python
snapshot = client.get_option_snapshot("O:NVDA260619C00125000")
print(f"Bid: {snapshot.last_quote.bid}, Ask: {snapshot.last_quote.ask}")
print(f"Delta: {snapshot.greeks.delta}, IV: {snapshot.implied_volatility:.2%}")
print(f"Open Interest: {snapshot.open_interest}")
print(f"Break Even: {snapshot.break_even_price}")
```

**Response fields:** `details` (contract type, exercise style, expiration, strike), `greeks` (delta, gamma, theta, vega, **rho**, **vanna**), `implied_volatility`, `last_trade` (price, size, timestamp), `last_quote` (ask, ask_size, bid, bid_size, midpoint), `day` (change, OHLCV, vwap), `underlying_asset` (price, change, ticker), `break_even_price`, `open_interest`

### Options Snapshot — Full Chain

**Endpoint:** `GET /v3/snapshot/options/{underlying_asset}`

```python
# Get all NVDA options expiring in next 30 days (respects Law 5: min_dte=30)
from datetime import date, timedelta
min_exp = (date.today() + timedelta(days=30)).isoformat()

chain = client.list_snapshot_options_chain(
    underlying_asset="NVDA",
    expiration_date_gte=min_exp,
    limit=1000
)
```

**Query Parameters:**
- `expiration_date_gte` / `expiration_date_lte` — Filter by expiry range
- `contract_type` — `"call"` or `"put"`
- `strike_price_gte` / `strike_price_lte` — Filter by strike range
- `limit` — Results per page (default: 100, max: 1000)

### Options Greeks Lookup

**Endpoint:** `GET /v3/snapshot/option/greeks`

Filter greeks across contracts without fetching full snapshots.

```python
import requests
resp = requests.get(
    "https://api.polygon.io/v3/snapshot/option/greeks",
    params={"underlying_asset": "NVDA", "apiKey": "YOUR_KEY"}
)
```

**Parameters:** `underlying_asset`, `contract_type`, `expiration_date`, `strike_price`

### Options Aggregates (Historical Prices)

**Endpoint:** `GET /v2/aggs/ticker/{optionsTicker}/range/{multiplier}/{timespan}/{from}/{to}`

Same structure as stock aggregates, but uses OCC option ticker format.

```python
# NVDA call option historical data
option_aggs = client.list_aggs(
    ticker="O:NVDA260619C00125000",
    multiplier=1,
    timespan="day",
    from_="2026-01-01",
    to="2026-05-15",
    limit=50000
)
```

OHLCV bars represent option premium prices. Volume/VWAP are based on option contract volume.

## OCC Option Symbol Format

```
O:{root}{YY}{MM}{DD}{C/P}{strike × 1000, zero-padded to 8 digits}
```

Examples:
- `O:NVDA260619C00125000` — NVDA call, exp 2026-06-19, strike $125
- `O:AMD260717P00075000` — AMD put, exp 2026-07-17, strike $75
- `O:SPY260315C00552500` — SPY call, exp 2026-03-15, strike $552.50

> ⚠️ **Strike is multiplied by 1000 and zero-padded to 8 digits** — $125.00 → `00125000`, $552.50 → `00552500`

## REST API Endpoints — Fundamentals

### Financial Statements

Quarterly and annual financials for fundamental analysis.

**Endpoint:** `GET /v2/reference/financials/{ticker}`

```python
financials = client.get_stock_financials("NVDA", timeframe="quarterly", limit=8)
for f in financials:
    print(f"Period: {f.fiscal_period} | Revenue: {f.revenues} | EPS: {f.eps}")
```

|| Field | Description |
|-------|-------------|
| `revenues` | Total revenue |
| `cost_of_revenue` | COGS |
| `gross_profit` | Revenue - COGS |
| `net_income` | Bottom line |
| `eps` | Earnings per share |
| `total_debt` | Total liabilities |

### Earnings

Historical and upcoming earnings dates.

**Endpoint:** `GET /v2/reference/earnings/{ticker}`

```python
earnings = client.get_stock_earnings("NVDA", limit=8)
for e in earnings:
    print(f"{e.fiscal_period} | EPS actual: {e.actual} | EPS estimate: {e.estimated}")
```

### Corporate Actions

Splits, dividends, spinoffs.

**Endpoint:** `GET /v2/reference/splits`, `GET /v2/reference/dividends/{ticker}`

## REST API Endpoints — Technical Indicators

Built-in TA — no need to compute SMA, EMA, MACD, RSI ourselves.

|| Indicator | Endpoint Path | Key Params |
|------------|---------------|------------|
| SMA | `/v1/indicators/sma/{ticker}` | `window` (default: 50) |
| EMA | `/v1/indicators/ema/{ticker}` | `window` (default: 50) |
| MACD | `/v1/indicators/macd/{ticker}` | `short_window`(12), `long_window`(26), `signal_window`(9) |
| RSI | `/v1/indicators/rsi/{ticker}` | `window` (default: 14) |
| BB | `/v1/indicators/bb/{ticker}` | `window`(20), `upper_multiplier`(2), `lower_multiplier`(2) |
| ATR | `/v1/indicators/atr/{ticker}` | `window` (default: 14) |
| Stochastic | `/v1/indicators/stoch/{ticker}` | — |
| CCI | `/v1/indicators/cci/{ticker}` | — |
| OBV | `/v1/indicators/obv/{ticker}` | — |
| VWAP | `/v1/indicators/vwap/{ticker}` | — |

All require `timespan` param (`minute`, `hour`, `day`, `week`, `month`). Support `timestamp_from`, `timestamp_to`, `adjusted`.

> **For ATR calculations** (needed for our position sizing stop-loss), use ATR indicator endpoint instead of computing from raw bars.

## REST API Endpoints — Economy

Macro data for long-term thesis validation.

|| Data | Endpoint Path |
|------|---------------|
| Treasury Yields | `/v2/reference/treasuries` (DGS1, DGS2, DGS5, DGS10, DGS30) |
| CPI | `/v2/reference/cpi` |
| GDP | `/v2/reference/gdp` |
| Unemployment | `/v2/reference/unemployment` |
| Inflation Expectations | `/v2/reference/inflation-expectations` |
| Labor Market | `/v2/reference/labor-market` |

Common params: `from`, `to`, `sort`, `limit`. Response: array of `{timestamp, value}`.

## REST API Endpoints — News & Sentiment

### News

Stock-specific news articles with per-ticker sentiment.

**Endpoint:** `GET /v2/reference/news`

```python
news = client.list_ticker_news("NVDA", limit=10)
for n in news:
    print(f"{n.published_utc} | {n.title}")
    print(f"  Source: {n.publisher.name} | URL: {n.article_url}")
    for insight in n.insights:
        print(f"  Ticker: {insight.ticker} | Sentiment: {insight.sentiment}")
```

**Query Parameters:** `ticker` (comma-separated), `published_utc_gte`/`published_utc_lte`, `order` (asc/desc), `sort`, `limit` (max: 1000), `source`

**Response fields:** `id`, `title`, `article_url`, `author`, `description`, `image_url`, `keywords`, `published_utc`, `publisher` (name, logo_url, homepage_url), `tickers`, `insights` (array of `{ticker, sentiment, sentiment_reason}`)

### Sentiment

Aggregated sentiment scores for a ticker over time.

**Endpoint:** `GET /v2/reference/sentiment`

```python
import requests
resp = requests.get(
    "https://api.polygon.io/v2/reference/sentiment",
    params={"ticker": "NVDA", "apiKey": "YOUR_KEY"}
)
```

**Parameters:** `ticker` (required), `from`, `to`

## REST API Endpoints — Partner Data

### Benzinga (via Polygon)
- **Analyst Ratings** — Buy/sell/hold ratings with price targets
- **Analyst Insights** — Analyst commentary
- **Corporate Guidance** — Company forward guidance
- **Earnings** — Earnings calendar and results
- **Real-time News** — Benzinga's news feed

### ETF Global (via Polygon)
- **ETF Constituents** — What's inside an ETF
- **ETF Fund Flows** — Money flowing in/out
- **ETF Analytics** — Performance metrics
- **ETF Profiles & Exposure** — Sector/geography breakdown
- **ETF Taxonomies** — ETF classification system

> **Potential use:** ETF constituent data to check sector overlap for correlation risk (see [[Correlation Risk]]).

## WebSocket API — Endpoints

For real-time streaming when we upgrade to live trading:

|| Asset Class | Channels |
|-------------|----------|
| **Stocks** | Aggregates (per-minute, per-second), Trades, Quotes, LULD, NOI (order imbalance), FMV |
| **Options** | Aggregates (per-minute, per-second), Trades, Quotes, FMV |
| **Futures** | Aggregates, Trades, Quotes |
| **Indices** | Aggregates (per-minute, per-second), Value |
| **Forex** | Aggregates (per-minute, per-second), Quotes, FMV |
| **Crypto** | Aggregates (per-minute, per-second), Trades, Quotes, FMV |

Docs: [massive.com/docs/websocket/quickstart](https://massive.com/docs/websocket/quickstart)

## Flat Files — Bulk Downloads (S3-Compatible)

For initial backfill, Flat Files are much faster than paginated API calls.

**Access Method:** S3-compatible API via `boto3`:

```python
import boto3
from botocore.config import Config

s3 = boto3.client(
    's3',
    endpoint_url='https://files.massive.com',
    aws_access_key_id=os.environ['POLYGON_FLAT_FILES_ACCESS_ID'],
    aws_secret_access_key=os.environ['POLYGON_FLAT_FILES_SECRET_KEY'],
    region_name='us-east-1',
    config=Config(signature_version='s3v4')
)

# List available datasets
resp = s3.list_objects_v2(Bucket='flatfiles', Delimiter='/', MaxKeys=100)
# Prefixes: global_crypto/, global_forex/, us_futures_*/, us_indices/,
#           us_options_opra/, us_stocks_sip/

# Download a day of stock aggregates
obj = s3.get_object(Bucket='flatfiles', Key='us_stocks_sip/day_aggs_v1/2025/05/2025-05-01.csv.gz')
data = gzip.decompress(obj['Body'].read()).decode()
```

**Credentials:** `.env.polygon` — `POLYGON_FLAT_FILES_ACCESS_ID`, `POLYGON_FLAT_FILES_SECRET_KEY`, `POLYGON_FLAT_FILES_ENDPOINT`

|| Data | Available | Size |
|------|-----------|----------|------|
| **Stocks** | Day aggregates, Minute aggregates, Trades, Quotes | `us_stocks_sip/` | ~0.2 MB/day |
| **Options** | Day aggregates, Minute aggregates, Trades, Quotes | `us_options_opra/` | ~3 MB/day |
| **Futures** | Session aggregates (CME, CBOT, NYMEX, COMEX), Minute aggregates, Trades, Quotes | `us_futures_*/` | varies |
| **Indices** | Day aggregates, Minute aggregates, Values | `us_indices/` | small |
| **Forex** | Day aggregates, Minute aggregates, Quotes | `global_forex/` | small |
| **Crypto** | Day aggregates, Minute aggregates, Trades | `global_crypto/` | varies |

Docs: [massive.com/docs/flat-files/quickstart](https://massive.com/docs/flat-files/quickstart)

> **Strategy:** Use Flat Files for initial historical backfill → then switch to REST/WebSocket for incremental updates.

## Data Storage — Postgres

All Polygon data flows into our `market.*` schema:

```sql
-- Proposed tables for Polygon data
CREATE TABLE market.polygon_daily_bars (
    ticker        TEXT NOT NULL,
    date          DATE NOT NULL,
    open          DECIMAL(12,4),
    high          DECIMAL(12,4),
    low           DECIMAL(12,4),
    close         DECIMAL(12,4),
    volume        BIGINT,
    vwap          DECIMAL(12,4),
    transactions  INTEGER,
    PRIMARY KEY (ticker, date)
);

CREATE TABLE market.polygon_option_chains (
    underlying     TEXT NOT NULL,
    occ_symbol     TEXT NOT NULL,
    expiration     DATE NOT NULL,
    strike         DECIMAL(12,4),
    contract_type  CHAR(1),  -- C or P
    exercise_style TEXT,     -- american or european
    settlement_type TEXT,    -- physical or cash
    primary_exchange TEXT,
    shares_per_contract INT DEFAULT 100,
    PRIMARY KEY (occ_symbol)
);

CREATE TABLE market.polygon_option_bars (
    occ_symbol  TEXT NOT NULL,
    date        DATE NOT NULL,
    open        DECIMAL(12,4),
    high        DECIMAL(12,4),
    low         DECIMAL(12,4),
    close       DECIMAL(12,4),
    volume      BIGINT,
    vwap        DECIMAL(12,4),
    iv          DECIMAL(12,4),
    delta       DECIMAL(8,4),
    gamma       DECIMAL(8,4),
    theta       DECIMAL(8,4),
    vega        DECIMAL(8,4),
    rho         DECIMAL(8,4),    -- NEW: added in Massive
    vanna       DECIMAL(8,4),    -- NEW: added in Massive
    open_interest BIGINT,
    PRIMARY KEY (occ_symbol, date)
);

CREATE TABLE market.polygon_financials (
    ticker         TEXT NOT NULL,
    fiscal_period  TEXT NOT NULL,
    revenues       DECIMAL(16,2),
    cost_of_revenue DECIMAL(16,2),
    gross_profit   DECIMAL(16,2),
    net_income     DECIMAL(16,2),
    eps            DECIMAL(8,4),
    total_debt     DECIMAL(16,2),
    PRIMARY KEY (ticker, fiscal_period)
);

CREATE TABLE market.polygon_news (
    id            TEXT PRIMARY KEY,
    published_utc TIMESTAMP,
    title         TEXT,
    author        TEXT,
    description   TEXT,
    article_url   TEXT,
    source        TEXT,
    tickers       TEXT[],       -- ARRAY of ticker symbols
    sentiment     TEXT,         -- bullish/bearish/neutral (per insight)
    created_at    TIMESTAMP DEFAULT NOW()
);

CREATE TABLE market.polygon_technical_indicators (
    ticker     TEXT NOT NULL,
    date       DATE NOT NULL,
    indicator  TEXT NOT NULL,  -- sma, ema, macd, rsi, atr, etc.
    timespan   TEXT NOT NULL,  -- day, hour, minute, week
    value      DECIMAL(12,6),  -- scalar (SMA, EMA, RSI, ATR)
    macd_signal  DECIMAL(12,6),  -- MACD only
    macd_hist    DECIMAL(12,6),   -- MACD only
    bb_upper     DECIMAL(12,6),  -- BB only
    bb_lower     DECIMAL(12,6),  -- BB only
    PRIMARY KEY (ticker, date, indicator, timespan)
);

CREATE TABLE market.polygon_economy (
    series_id  TEXT NOT NULL,   -- DGS10, CPI, GDP, etc.
    date       DATE NOT NULL,
    value      DECIMAL(12,4),
    PRIMARY KEY (series_id, date)
);
```

## Ingestion Script Structure

```python
# scripts/polygon_ingest.py (proposed)

from polygon import RESTClient
import os
from dotenv import load_dotenv

load_dotenv('.env.polygon')
client = RESTClient(api_key=os.environ['POLYGON_API_KEY'])

WATCHLIST = ['NVDA', 'AMD', 'MU', 'WDC', 'STX', 'APLD', 'IREN', 'NBIS',
             'CIFR', 'RDDT', 'SERV', 'RKLB', 'ASTS', 'OKLO', 'NVO']

def ingest_daily_bars(ticker: str, years: int = 5):
    """Backfill daily OHLCV bars for a symbol."""
    ...

def ingest_option_chain(underlying: str):
    """Fetch current option chain with greeks (Law 5: min_dte=30)."""
    from datetime import date, timedelta
    min_exp = (date.today() + timedelta(days=30)).isoformat()  # Respect 30 DTE minimum
    ...

def ingest_option_bars(occ_symbol: str, days: int = 90):
    """Fetch historical option bars with greeks/IV."""
    ...

def ingest_financials(ticker: str):
    """Fetch quarterly financials for fundamental analysis."""
    ...

def ingest_news(ticker: str, limit: int = 50):
    """Fetch recent news with sentiment for a ticker."""
    ...

def ingest_technical_indicators(ticker: str):
    """Fetch SMA, EMA, RSI, MACD, ATR for position sizing."""
    ...

def ingest_economy():
    """Fetch treasury yields, CPI, GDP for macro thesis."""
    ...

def backfill_watchlist():
    """Full backfill of all watchlist symbols."""
    for ticker in WATCHLIST:
        ingest_daily_bars(ticker)
        ingest_financials(ticker)
        ingest_news(ticker)
    ingest_economy()
```

## Our Plan — Stocks + Options

**Active Plan:** Stocks ($79/mo) + Options Starter ($29/mo) = **$108/mo**

| Feature | Stocks Plan | Options Starter | Combined |
|---------|------------|-----------------|----------|
| Stock OHLCV | ✅ 10yr history | — | ✅ |
| Stock fundamentals | ✅ | — | ✅ |
| Stock technical indicators | ✅ | — | ✅ |
| Stock corporate actions | ✅ | — | ✅ |
| Stock Flat Files | ✅ | — | ✅ |
| Stock WebSockets | ✅ | — | ✅ |
| Options chains + greeks | — | ✅ | ✅ |
| Options IV + snapshots | — | ✅ | ✅ |
| Options open interest | — | ✅ Daily | ✅ |
| Options Flat Files | — | ✅ | ✅ |
| Options history | — | ✅ 2yr | ✅ |
| News | ✅ | — | ✅ |
| API calls | ✅ Unlimited | ✅ Unlimited | ✅ |
| Live data | ✅ 15-min delayed | ✅ 15-min delayed | ✅ |

**Flat Files Access:** S3-compatible at `https://files.massive.com`, bucket `flatfiles`, auth via access_id + secret_key (see `.env.polygon`).

**Not included (we don't need):** Options trades data, 4yr+ options history, real-time options quotes, options WebSockets.

## See Also

- [[Alpaca API]] — Trading execution, orders, positions
- [[Database Architecture]] — Postgres `market.*` schema design
- [[Watchlist]] — 15 tracked symbols
- [[Strategies]] — Why we need historical data (backtesting)
- [[Risk Management]] — Position sizing needs price data for ATR calculations
- [[Laws of Trading]] — Law 5 requires min 30 DTE for all option plays