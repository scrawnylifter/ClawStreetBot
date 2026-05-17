---
created: 2026-05-14
updated: 2026-05-14
tags: [api, alpaca, trading, mOC]
---

# Alpaca API Reference

## Overview

[Alpaca](https://alpaca.markets) is our **broker and primary market data provider**. We use the official `alpaca-py` SDK (v0.43.4). As of Phase 5, Alpaca provides all OHLCV bars, options chains, greeks, and real-time snapshots — replacing Polygon.io for these data types (see [[Alpaca Data Pipeline]] for migration details).

- **SDK:** [alpacahq/alpaca-py](https://github.com/alpacahq/alpaca-py)
- **Docs:** [https://docs.alpaca.markets](https://docs.alpaca.markets)
- **Install:** `pip install alpaca-py`

## Authentication

Two API keys — one for **Paper Trading** (testing), one for **Live Trading** (real money).

```python
from alpaca.trading.client import TradingClient

# Paper trading (default for development)
trading_client = TradingClient(
    api_key="ALPACA_PAPER_API_KEY",
    secret_key="ALPACA_PAPER_SECRET_KEY",
    paper=True  # Uses paper-api.alpaca.markets
)

# Live trading (real money!)
trading_client = TradingClient(
    api_key="ALPACA_LIVE_API_KEY",
    secret_key="ALPACA_LIVE_SECRET_KEY",
    paper=False  # Uses api.alpaca.markets
)
```

> ⚠️ **Always start with Paper mode.** Keys stored in `.env.alpaca` (gitignored).

## API Endpoints (Base URLs)

| Mode | REST URL | WebSocket |
|------|----------|-----------|
| Paper Trading | `https://paper-api.alpaca.markets` | `wss://paper-api.alpaca.markets/stream` |
| Live Trading | `https://api.alpaca.markets` | `wss://api.alpaca.markets/stream` |
| Market Data | `https://data.alpaca.markets` | `wss://stream.data.alpaca.markets` |
| Data Sandbox | `https://data.sandbox.alpaca.markets` | — |

## Client Classes

### Trading — `TradingClient`

Account management, orders, positions, watchlists.

| Method | Description |
|--------|-------------|
| `get_account()` | Account details (balance, equity, buying power) |
| `get_all_assets(filters)` | List all tradable assets |
| `get_asset(symbol)` | Get single asset info |
| `submit_order(order)` | Place a market/limit/stop order |
| `get_order_by_id(id)` | Get order status |
| `cancel_order_by_id(id)` | Cancel order |
| `cancel_orders()` | Cancel all open orders |
| `get_all_positions()` | Current portfolio positions |
| `close_position(symbol)` | Close a position |
| `close_all_positions()` | Liquidate everything |
| `get_portfolio_history()` | Historical equity curve |
| `get_clock()` | Market open/close status |
| `get_calendar()` | Market calendar |
| `get_option_contracts()` | Search options chain |
| `create_watchlist()` | Create watchlist |
| `get_corporate_announcements()` | Dividends, splits, mergers |

### Stock Data — `StockHistoricalDataClient`

Historical and latest stock data.

| Method | Data | Description |
|--------|------|-------------|
| `get_stock_bars()` | OHLCV | Historical candlestick data |
| `get_stock_quotes()` | Bid/Ask | NBBO quotes |
| `get_stock_trades()` | Trades | Tick-by-tick trade data |
| `get_stock_latest_bar()` | OHLCV | Latest bar for symbol(s) |
| `get_stock_latest_quote()` | Bid/Ask | Real-time quote |
| `get_stock_latest_trade()` | Price | Latest trade price |
| `get_stock_snapshot()` | All | Snapshot (bar + quote + trade) |

### Crypto Data — `CryptoHistoricalDataClient`

| Method | Data | Description |
|--------|------|-------------|
| `get_crypto_bars()` | OHLCV | Crypto candlesticks |
| `get_crypto_quotes()` | Bid/Ask | Crypto quotes |
| `get_crypto_trades()` | Trades | Crypto trades |
| `get_crypto_latest_bar()` | OHLCV | Latest crypto bar |
| `get_crypto_latest_orderbook()` | Depth | Order book (bids/asks) |

### Options Data — `OptionHistoricalDataClient`

| Method | Description |
|--------|-------------|
| `get_option_chain()` | Full options chain for underlying |
| `get_option_bars()` | OHLCV for specific option contract |
| `get_option_latest_quote()` | Latest option quote |
| `get_option_latest_trade()` | Latest option trade |

### News — `NewsClient`

| Method | Description |
|--------|-------------|
| `get_news()` | News articles (filtered by symbol, date) |

### Corporate Actions — `CorporateActionsClient`

| Method | Description |
|--------|-------------|
| `get_corporate_actions()` | Dividends, splits, mergers, name changes |

### Screener — `ScreenerClient`

| Method | Description |
|--------|-------------|
| `get_market_movers()` | Top gainers/losers |
| `get_most_actives()` | Most actively traded |

### WebSocket Streams (Real-time)

| Stream | Events |
|--------|--------|
| `StockDataStream` | Live trades, quotes, bars, daily bars |
| `CryptoDataStream` | Live crypto trades, quotes, bars, orderbooks |
| `NewsDataStream` | Live news articles as they publish |
| `TradingStream` | Order fills, position updates, cancellations |

## TimeFrames

```python
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

# Available units: Minute, Hour, Day, Week, Month
bars = StockBarsRequest(
    symbol_or_symbols="AAPL",
    timeframe=TimeFrame(1, TimeFrameUnit.Day),  # 1 Day bars
    start="2026-01-01",
    end="2026-05-01"
)
```

## Data Feeds (Free vs Paid)

| Feed | Tier | Description |
|------|------|-------------|
| **IEX** | Free | Delayed 15min, fewer exchanges |
| **SIP** | Paid | Real-time, all exchanges (required by SEC) |
| **DELAYED_SIP** | Paid (cheaper) | SIP data delayed 15 minutes |
| **OTC** | Free | OTC market data |
| **BOATS** | Paid | Consolidated tape |
| **OVERNIGHT** | Free | Overnight/extended hours |

> **Free tier:** IEX feed gives you 15-min delayed stock data. For real-time, you need SIP. Crypto data is real-time on free tier.

## Rate Limits

| Endpoint | Free Tier | Paid |
|----------|-----------|------|
| Trading API | 200 req/min | 200 req/min |
| Market Data | 200 req/min per endpoint | Same |
| WebSocket | 1 connection per feed | Multiple |

## Order Types

| Type | Enum | Description |
|------|------|-------------|
| Market | `market` | Execute immediately at best price |
| Limit | `limit` | Execute at specified price or better |
| Stop | `stop` | Market order triggered at price |
| Stop Limit | `stop_limit` | Limit order triggered at price |
| Trailing Stop | `trailing_stop` | Trails by percentage or dollar amount |

### Bracket Orders

```python
from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass

# Simple market buy
order = MarketOrderRequest(
    symbol="AAPL",
    qty=10,
    side=OrderSide.BUY,
    time_in_force=TimeInForce.DAY
)

# Bracket order (buy + take-profit + stop-loss)
order = LimitOrderRequest(
    symbol="AAPL",
    qty=10,
    limit_price=150.00,
    side=OrderSide.BUY,
    time_in_force=TimeInForce.DAY,
    order_class=OrderClass.BRACKET,
    take_profit=TakeProfitRequest(limit_price=160.00),
    stop_loss=StopLossRequest(stop_price=140.00)
)
```

## Asset Classes

| Class | Enum | Notes |
|-------|------|-------|
| US Equity | `us_equity` | Stocks & ETFs |
| US Options | `us_option` | Options contracts |
| Crypto | `crypto` | Spot crypto |
| Crypto Perps | `crypto_perp` | Perpetual futures |

## Key Enums

```
TimeInForce: DAY, GTC, OPG, CLS, IOC, FOK
OrderSide: BUY, SELL
OrderClass: SIMPLE, BRACKET, OCO, OTO
Adjustment: RAW, SPLIT, DIVIDEND, ALL
```

## Quick Start Example

```python
from alpaca.trading.client import TradingClient
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from datetime import datetime

# Initialize clients
trading = TradingClient(
    api_key="YOUR_KEY",
    secret_key="YOUR_SECRET",
    paper=True
)

data = StockHistoricalDataClient(
    api_key="YOUR_KEY",
    secret_key="YOUR_SECRET"
)

# Check account
account = trading.get_account()
print(f"Equity: ${account.equity}")

# Get historical bars
bars = data.get_stock_bars(StockBarsRequest(
    symbol_or_symbols=["AAPL", "TSLA", "NVDA"],
    timeframe=TimeFrame(1, TimeFrameUnit.Day),
    start=datetime(2026, 1, 1),
    end=datetime(2026, 5, 1)
))

# Market clock
clock = trading.get_clock()
print(f"Market open: {clock.is_open}")
```

## See Also

- [[Alpaca Data Pipeline]] — Our ingestion scripts, n8n workflows, and migration details
- [[Trading Strategies]] — Strategy implementation
- [[Risk Framework]] — Position sizing and limits

## API Quirks & Gotchas

These are things we discovered that weren't obvious from the docs:

- **NewsRequest.symbols** takes a **string**, not a list. Use `symbols="AAPL"` not `symbols=["AAPL"]`
- **Snapshot attributes**: Use `previous_daily_bar` (not `prev_daily_bar`), `daily_bar`, `latest_trade`, `latest_quote`
- **NewsClient** returns a `NewsSet` — access articles via `result.data.get("news", [])`
- **DataFeed.IEX** is the free tier (15-min delayed). Use `DataFeed.SIP` for real-time (paid)
- **Pydantic warning** on `MostActivesRequest` is benign (enum serialization)
- **Asset listing** returns 13,000+ active US equities — always filter by `tradable=True`
- **Paper account** starts with $100,000 equity and $200,000 buying power