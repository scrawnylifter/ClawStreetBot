#!/usr/bin/env python3
"""
ClawStreetBot — Data Explorer
Connects to Alpaca Paper Trading API and explores available data.
Run: python scripts/explore_data.py
"""

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Load .env.alpaca manually
env_path = Path(__file__).parent.parent / ".env.alpaca"
if env_path.exists():
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())

from alpaca.trading.client import TradingClient
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.historical.news import NewsClient
from alpaca.data.historical.screener import ScreenerClient
from alpaca.data.requests import (
    StockBarsRequest,
    StockLatestBarRequest,
    StockSnapshotRequest,
    NewsRequest,
    MarketMoversRequest,
    MostActivesRequest,
    GetAssetsRequest,
)
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.data.enums import DataFeed, MarketType
from alpaca.trading.enums import AssetClass, AssetStatus

API_KEY = os.environ.get("ALPACA_PAPER_API_KEY", "")
SECRET_KEY = os.environ.get("ALPACA_PAPER_SECRET_KEY", "")

if not API_KEY or API_KEY == "YOUR_PAPER_API_KEY":
    print("ERROR: Set ALPACA_PAPER_API_KEY and ALPACA_PAPER_SECRET_KEY in .env.alpaca")
    sys.exit(1)

# Initialize clients
trading = TradingClient(api_key=API_KEY, secret_key=SECRET_KEY, paper=True)
stock_data = StockHistoricalDataClient(api_key=API_KEY, secret_key=SECRET_KEY)
news_client = NewsClient(api_key=API_KEY, secret_key=SECRET_KEY)
screener = ScreenerClient(api_key=API_KEY, secret_key=SECRET_KEY)

print("=" * 60)
print("🦞 ClawStreetBot — Alpaca Data Explorer")
print("=" * 60)

# 1. Account
print("\n📊 ACCOUNT")
print("-" * 40)
account = trading.get_account()
print(f"  Status:        {account.status}")
print(f"  Equity:        ${account.equity}")
print(f"  Cash:          ${account.cash}")
print(f"  Buying Power:  ${account.buying_power}")
print(f"  Pattern Day Trader: {account.pattern_day_trader}")

# 2. Market Clock
print("\n🕐 MARKET CLOCK")
print("-" * 40)
clock = trading.get_clock()
print(f"  Is Open:       {clock.is_open}")
print(f"  Next Open:     {clock.next_open}")
print(f"  Next Close:    {clock.next_close}")

# 3. Assets — sample
print("\n📈 ASSETS (first 10 US equities)")
print("-" * 40)
assets = trading.get_all_assets(GetAssetsRequest(
    asset_class=AssetClass.US_EQUITY,
    status=AssetStatus.ACTIVE,
))
count = 0
for a in assets:
    if count >= 10:
        break
    if a.tradable:
        print(f"  {a.symbol:6}  {a.name[:40]:40}  exchange={a.exchange}  fractionable={a.fractionable}")
        count += 1
print(f"  ... total active US equity assets: {len(assets)}")

# Popular symbols for data exploration
SYMBOLS = ["AAPL", "TSLA", "NVDA", "MSFT", "AMZN", "GOOGL", "META", "SPY", "QQQ"]

# 4. Stock Bars
print("\n📉 STOCK BARS (AAPL, last 5 trading days)")
print("-" * 40)
try:
    bars = stock_data.get_stock_bars(StockBarsRequest(
        symbol_or_symbols="AAPL",
        timeframe=TimeFrame(1, TimeFrameUnit.Day),
        start=datetime.now() - timedelta(days=7),
        feed=DataFeed.IEX,
    ))
    for bar in bars.data.get("AAPL", [])[:5]:
        print(f"  {bar.timestamp.strftime('%Y-%m-%d')}  O={bar.open:>9}  H={bar.high:>9}  L={bar.low:>9}  C={bar.close:>9}  V={bar.volume:>12}")
except Exception as e:
    print(f"  Error: {e}")

# 5. Latest Bar
print("\n📊 LATEST BAR (AAPL)")
print("-" * 40)
try:
    latest = stock_data.get_stock_latest_bar(StockLatestBarRequest(
        symbol_or_symbols="AAPL",
        feed=DataFeed.IEX,
    ))
    bar = latest.get("AAPL") or latest
    print(f"  Timestamp: {bar.timestamp}")
    print(f"  Close:      {bar.close}")
    print(f"  Volume:     {bar.volume}")
except Exception as e:
    print(f"  Error: {e}")

# 6. Snapshots
print("\n📷 SNAPSHOTS (multiple symbols)")
print("-" * 40)
try:
    snapshots = stock_data.get_stock_snapshot(StockSnapshotRequest(
        symbol_or_symbols=SYMBOLS[:5],
        feed=DataFeed.IEX,
    ))
    for sym, snap in snapshots.items():
        print(f"  {sym}: last_trade=${snap.latest_trade.price:.2f}  bid={snap.latest_bid.price:.2f}  ask={snap.latest_ask.price:.2f}  prev_close={snap.prev_daily_bar.close:.2f}")
except Exception as e:
    print(f"  Error: {e}")

# 7. Market Movers
print("\n🚀 MARKET MOVERS (today's top gainers)")
print("-" * 40)
try:
    movers = screener.get_market_movers(MarketMoversRequest(
        market_type=MarketType.STOCKS,
    ))
    print(f"  Gainers:  {getattr(movers, 'gainers', movers)[:3] if hasattr(movers, 'gainers') else movers}")
except Exception as e:
    print(f"  Error: {e}")

try:
    actives = screener.get_most_actives(MostActivesRequest(
        market_type=MarketType.STOCKS,
    ))
    print(f"  Most Active: {actives}")
except Exception as e:
    print(f"  Error: {e}")

# 8. News
print("\n📰 NEWS (recent, AAPL-related)")
print("-" * 40)
try:
    news = news_client.get_news(NewsRequest(
        symbols=["AAPL"],
        limit=5,
    ))
    for n in news:
        headline = (n.headline or "")[:60]
        print(f"  [{n.created_at.strftime('%Y-%m-%d')}] {headline}...")
except Exception as e:
    print(f"  Error: {e}")

print("\n" + "=" * 60)
print("✅ Data exploration complete!")
print("=" * 60)