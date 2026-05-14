#!/usr/bin/env python3
"""Set up ClawStreetBot watchlist in Alpaca and Postgres."""
import os, sys
from pathlib import Path
from datetime import datetime

# Load env
env_path = Path(__file__).parent.parent / ".env.alpaca"
with open(env_path) as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ[k.strip()] = v.strip()

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import CreateWatchlistRequest, GetAssetsRequest
from alpaca.trading.enums import AssetClass, AssetStatus
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.requests import StockSnapshotRequest
from alpaca.data.enums import DataFeed
import psycopg2

API_KEY = os.environ["ALPACA_PAPER_API_KEY"]
SECRET_KEY = os.environ["ALPACA_PAPER_SECRET_KEY"]

SYMBOLS = ["WDC", "IREN", "APLD", "SERV", "RKLB", "ASTS", "CIFR", "NVDA", "AMD", "NBIS", "RDDT", "OKLO", "NVO", "MU", "STX"]

trading = TradingClient(api_key=API_KEY, secret_key=SECRET_KEY, paper=True)
data_client = StockHistoricalDataClient(api_key=API_KEY, secret_key=SECRET_KEY)

# === 1. Create Alpaca Watchlist ===
print("=" * 70)
print("🦞 SETTING UP CLAWSTREETBOT WATCHLIST")
print("=" * 70)

# Remove existing if present
try:
    existing = trading.get_watchlists()
    for wl in existing:
        if wl.name == "ClawStreetBot":
            trading.delete_watchlist_by_id(wl.id)
            print(f"Deleted existing watchlist: {wl.name}")
except Exception as e:
    print(f"Note: {e}")

# Create new watchlist
try:
    watchlist = trading.create_watchlist(CreateWatchlistRequest(
        name="ClawStreetBot",
        symbols=SYMBOLS,
    ))
    print(f"\n✅ Alpaca Watchlist: {watchlist.name}")
    print(f"   Symbols ({len(watchlist.symbols)}): {', '.join(watchlist.symbols)}")
except Exception as e:
    print(f"\n❌ Watchlist error: {e}")

# === 2. Get Asset Details ===
print("\n📊 FETCHING ASSET DATA...")
all_assets = trading.get_all_assets(GetAssetsRequest(
    asset_class=AssetClass.US_EQUITY,
    status=AssetStatus.ACTIVE,
))
asset_map = {a.symbol: a for a in all_assets if a.symbol in SYMBOLS}
missing = [s for s in SYMBOLS if s not in asset_map]
if missing:
    print(f"   ⚠️  Not found in Alpaca: {missing}")

# === 3. Get Snapshots ===
print("\n📈 CURRENT PRICES...")
try:
    snapshots = data_client.get_stock_snapshot(StockSnapshotRequest(
        symbol_or_symbols=SYMBOLS,
        feed=DataFeed.IEX,
    ))
except Exception as e:
    print(f"   Snapshot error: {e}")
    snapshots = {}

print(f"\n{'Symbol':<6} {'Name':<35} {'Price':>8} {'Prev':>9} {'Chg%':>8} {'Volume':>12}")
print("-" * 80)
asset_rows = []
for sym in SYMBOLS:
    asset = asset_map.get(sym)
    name = (asset.name or "")[:35] if asset else sym
    exchange = str(asset.exchange).split(".")[-1] if asset else "?"
    
    snap = snapshots.get(sym)
    if snap and snap.latest_trade:
        price = float(snap.latest_trade.price)
        prev = float(snap.previous_daily_bar.close) if snap.previous_daily_bar else 0
        chg = ((price - prev) / prev * 100) if prev else 0
        vol = int(snap.daily_bar.volume) if snap.daily_bar else 0
        print(f"{sym:<6} {name:<35} ${price:>7.2f} ${prev:>8.2f} {chg:>+7.2f}% {vol:>12,}")
        asset_rows.append((sym, name, exchange, price))
    else:
        print(f"{sym:<6} {name:<35} {'N/A':>8} {'N/A':>9} {'N/A':>8} {'N/A':>12}")
        asset_rows.append((sym, name, exchange, None))

# === 4. Insert into Postgres ===
print("\n🗄️  INSERTING INTO POSTGRES...")
try:
    conn = psycopg2.connect(
        host="localhost",
        port=5432,
        dbname="clawstreet",
        user="clawstreet",
        password="ClawStr33tBot2026"
    )
    cur = conn.cursor()
    
    inserted = 0
    for sym, name, exchange, price in asset_rows:
        cur.execute("""
            INSERT INTO market.assets (symbol, name, asset_type, exchange)
            VALUES (%s, %s, 'stock', %s)
            ON CONFLICT (symbol) DO UPDATE SET name=EXCLUDED.name, exchange=EXCLUDED.exchange, updated_at=NOW()
        """, (sym, name, exchange))
        inserted += 1
    
    conn.commit()
    print(f"   ✅ Inserted/updated {inserted} assets in market.assets")
    
    # Verify
    cur.execute("SELECT symbol, name, exchange FROM market.assets ORDER BY symbol")
    rows = cur.fetchall()
    print(f"\n   Assets in DB ({len(rows)}):")
    for row in rows:
        print(f"     {row[0]:<6} {row[1][:35]:<35} {row[2]}")
    
    cur.close()
    conn.close()
except Exception as e:
    print(f"   ❌ Postgres error: {e}")

print("\n" + "=" * 70)
print("✅ WATCHLIST SETUP COMPLETE")
print("=" * 70)