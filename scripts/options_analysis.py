#!/usr/bin/env python3
"""Realistic options analysis for a $1000 account with Laws of Trading constraints."""
import os, re
from pathlib import Path
from datetime import datetime, date

env_path = Path(__file__).parent.parent / ".env.alpaca"
with open(env_path) as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ[k.strip()] = v.strip()

from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.requests import OptionChainRequest, StockSnapshotRequest
from alpaca.data.enums import DataFeed

API_KEY = os.environ["ALPACA_PAPER_API_KEY"]
SECRET_KEY = os.environ["ALPACA_PAPER_SECRET_KEY"]

opt_client = OptionHistoricalDataClient(api_key=API_KEY, secret_key=SECRET_KEY)
stock_client = StockHistoricalDataClient(api_key=API_KEY, secret_key=SECRET_KEY)

# Laws of Trading constraints
PORTFOLIO = 1000
MAX_POSITION = 0.20  # Law 3: 20% max
MIN_DTE = 30         # Law 5: nothing under 30 DTE
MAX_BUDGET = PORTFOLIO * MAX_POSITION  # $200

SYMBOLS = ["WDC", "IREN", "APLD", "SERV", "RKLB", "ASTS", "CIFR", "NVDA", "AMD", "NBIS", "RDDT", "OKLO", "NVO", "MU", "STX"]

def parse_occ(sym):
    m = re.match(r"(\w+)(\d{6})([CP])(\d{8})", sym)
    if m:
        expiry = datetime.strptime(m.group(2), "%y%m%d").date()
        opt_type = "call" if m.group(3) == "C" else "put"
        strike = int(m.group(4)) / 1000
        return expiry, opt_type, strike
    return None, None, None

# Get current stock prices
print("Fetching stock prices...")
snapshots = stock_client.get_stock_snapshot(StockSnapshotRequest(
    symbol_or_symbols=SYMBOLS,
    feed=DataFeed.IEX,
))
prices = {}
for sym in SYMBOLS:
    snap = snapshots.get(sym)
    if snap and snap.latest_trade:
        prices[sym] = float(snap.latest_trade.price)

print(f"\n{'='*80}")
print(f"🦞 OPTIONS ANALYSIS — ${PORTFOLIO:,} Account | Max Position: ${MAX_BUDGET:.0f} (20%) | Min DTE: {MIN_DTE}")
print(f"{'='*80}")

# Find affordable options for each stock
print(f"\n{'Symbol':<6} {'Stock$':>8} {'Contract':<28} {'Type':<5} {'Strike':>8} {'Expiry':>12} {'DTE':>5} {'Ask':>7} {'Cost':>8} {'IV':>7} {'Delta':>7} {'Afford':>7}")
print("-" * 108)

affordable_options = []
today = date.today()

for sym in SYMBOLS:
    if sym not in prices:
        print(f"{sym:<6} NO PRICE DATA")
        continue
    
    stock_price = prices[sym]
    
    try:
        chain = opt_client.get_option_chain(OptionChainRequest(underlying_symbol=sym))
    except Exception as e:
        print(f"{sym:<6} CHAIN ERROR: {e}")
        continue
    
    # Find ATM calls with 30-180 DTE that fit budget
    for contract_sym, data in chain.items():
        expiry, opt_type, strike = parse_occ(contract_sym)
        if not expiry or opt_type != "call":
            continue
        
        dte = (expiry - today).days
        if dte < MIN_DTE or dte > 180:
            continue
        
        # Near the money: within 10% of stock price
        if abs(strike - stock_price) / stock_price > 0.10:
            continue
        
        ask = getattr(data.latest_quote, "ask_price", None) if data.latest_quote else None
        if not ask or ask <= 0:
            continue
        
        # Cost = ask * 100 (one contract = 100 shares)
        cost = ask * 100
        
        iv = getattr(data, "implied_volatility", None)
        greeks = getattr(data, "greeks", None)
        delta = getattr(greeks, "delta", None) if greeks else None
        
        # Only show if near our budget or under
        if cost <= MAX_BUDGET * 1.5:  # Show slightly over budget for comparison
            contract_display = contract_sym[:28]
            afford = "✅" if cost <= MAX_BUDGET else "❌"
            iv_str = f"{iv:.0%}" if iv else "N/A"
            delta_str = f"{delta:.3f}" if delta else "N/A"
            
            affordable_options.append({
                "sym": sym, "stock": stock_price, "contract": contract_sym,
                "strike": strike, "expiry": expiry, "dte": dte,
                "ask": ask, "cost": cost, "iv": iv, "delta": delta,
                "afford": afford
            })
    
    # Sort by DTE, show best 2-3 per symbol
    sym_options = [o for o in affordable_options if o["sym"] == sym]
    sym_options.sort(key=lambda x: (x["dte"], x["strike"]))
    shown = 0
    for o in sym_options:
        if shown >= 3:
            break
        iv_str = f"{o['iv']:.0%}" if o['iv'] else "N/A"
        delta_str = f"{o['delta']:.3f}" if o['delta'] else "N/A"
        opt_type_display = "C" if o["contract"][7] == "C" else "P"
        print(f"{o['sym']:<6} ${o['stock']:>7.2f} {o['contract'][:28]:<28} {opt_type_display:<5} ${o['strike']:>7.2f} {o['expiry']!s:>12} {o['dte']:>5} ${o['ask']:>6.2f} ${o['cost']:>7.0f} {iv_str:>7} {delta_str:>7} {o['afford']:>7}")
        shown += 1

# Summary
affordable = [o for o in affordable_options if o["cost"] <= MAX_BUDGET]
print(f"\n{'='*80}")
print(f"SUMMARY — ${PORTFOLIO:,} Account")
print(f"{'='*80}")
print(f"Total watchlist stocks: {len(SYMBOLS)}")
print(f"Stocks with prices: {len(prices)}")
print(f"Options under ${MAX_BUDGET:.0f} budget: {len(affordable)}")
print()

# Show what $200 actually buys
print("WHAT $200 BUYS YOU (per position):")
print("-" * 60)
by_sym = {}
for o in affordable:
    if o["sym"] not in by_sym or o["dte"] < by_sym[o["sym"]]["dte"]:
        by_sym[o["sym"]] = o

for sym in sorted(by_sym.keys()):
    o = by_sym[sym]
    contracts_can_afford = int(MAX_BUDGET / o["cost"]) if o["cost"] > 0 else 0
    notional = o["strike"] * 100 * contracts_can_afford if contracts_can_afford > 0 else 0
    leverage = notional / MAX_BUDGET if MAX_BUDGET > 0 else 0
    iv_str = f"{o['iv']:.0%}" if o['iv'] else "N/A"
    print(f"  {o['sym']:<6} {o['contract'][:24]}  ask=${o['ask']:.2f}  cost=${o['cost']:.0f}  "
          f"→ {contracts_can_afford} contract{'s' if contracts_can_afford != 1 else ''} "
          f"(controls ${notional:,.0f} notional, {leverage:.1f}x)")

# Reality check
print(f"\n⚠️  REALITY CHECK:")
print(f"  - $1,000 account with 20% max = $200 per position, max 5 concurrent positions")
print(f"  - Most ATM calls on high-priced stocks (NVDA, AMD, MU, STX, WDC) cost ${200}+")
print(f"  - Cheaper stocks (IREN, APLD, CIFR, SERV) give more contracts per $200")
print(f"  - 1 contract controls 100 shares — leverage works both ways")