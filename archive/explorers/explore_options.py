#!/usr/bin/env python3
"""Explore NVDA option chain data from Alpaca."""
import os, re
from pathlib import Path
from datetime import datetime

env_path = Path(__file__).parent.parent / ".env.alpaca"
with open(env_path) as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ[k.strip()] = v.strip()

from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.requests import OptionChainRequest

client = OptionHistoricalDataClient(
    api_key=os.environ["ALPACA_PAPER_API_KEY"],
    secret_key=os.environ["ALPACA_PAPER_SECRET_KEY"],
)

chain = client.get_option_chain(OptionChainRequest(underlying_symbol="NVDA"))
print(f"NVDA Option Chain: {len(chain)} contracts\n")

# Inspect one contract to find available attributes
sample = list(chain.values())[0]
print(f"Available attributes: {[a for a in dir(sample) if not a.startswith('_') and not callable(getattr(sample, a, None))]}\n")

def parse_occ(sym):
    m = re.match(r"(\w+)(\d{6})([CP])(\d{8})", sym)
    if m:
        expiry = datetime.strptime(m.group(2), "%y%m%d").date()
        opt_type = "call" if m.group(3) == "C" else "put"
        strike = int(m.group(4)) / 1000
        return expiry, opt_type, strike
    return None, None, None

contracts = []
for sym, data in chain.items():
    expiry, opt_type, strike = parse_occ(sym)
    if not expiry:
        continue
    last_p = getattr(data.latest_trade, "price", None) if data.latest_trade else None
    bid = getattr(data.latest_quote, "bid_price", None) if data.latest_quote else None
    ask = getattr(data.latest_quote, "ask_price", None) if data.latest_quote else None
    oi = getattr(data, "open_interest", None)
    iv = getattr(data, "implied_volatility", None)
    greeks = getattr(data, "greeks", None)
    delta = getattr(greeks, "delta", None) if greeks else None
    gamma = getattr(greeks, "gamma", None) if greeks else None
    theta = getattr(greeks, "theta", None) if greeks else None

    contracts.append({
        "sym": sym, "expiry": expiry, "type": opt_type, "strike": strike,
        "last": last_p, "bid": bid, "ask": ask, "oi": oi,
        "iv": iv, "delta": delta, "gamma": gamma, "theta": theta,
    })

contracts.sort(key=lambda x: (x["expiry"], x["strike"]))

expiries = sorted(set(c["expiry"] for c in contracts))
nearest = expiries[0]
print(f"Nearest expiry: {nearest} ({len(expiries)} total expirations)")
print(f"All expirations: {expiries[:8]}...\n")

# Calls
print(f"NVDA CALLS — Expiry {nearest} (stock ~$235.90)")
print(f"{'Strike':>8} {'Last':>8} {'Bid':>8} {'Ask':>8} {'OI':>8} {'IV':>7} {'Delta':>7} {'Gamma':>7} {'Theta':>7}")
print("-" * 75)
for c in contracts:
    if c["type"] == "call" and c["expiry"] == nearest and 225 <= c["strike"] <= 250:
        last = f"${c['last']:.2f}" if c["last"] else "N/A"
        bid = f"${c['bid']:.2f}" if c["bid"] else "N/A"
        ask = f"${c['ask']:.2f}" if c["ask"] else "N/A"
        oi = f"{c['oi']:,.0f}" if c["oi"] else "N/A"
        iv = f"{c['iv']:.1%}" if c["iv"] else "N/A"
        delta = f"{c['delta']:.3f}" if c["delta"] else "N/A"
        gamma = f"{c['gamma']:.4f}" if c["gamma"] else "N/A"
        theta = f"{c['theta']:.4f}" if c["theta"] else "N/A"
        print(f"${c['strike']:>7.2f} {last:>8} {bid:>8} {ask:>8} {oi:>8} {iv:>7} {delta:>7} {gamma:>7} {theta:>7}")

# Puts
print(f"\nNVDA PUTS — Expiry {nearest} (stock ~$235.90)")
print(f"{'Strike':>8} {'Last':>8} {'Bid':>8} {'Ask':>8} {'OI':>8} {'IV':>7} {'Delta':>7} {'Gamma':>7} {'Theta':>7}")
print("-" * 75)
for c in contracts:
    if c["type"] == "put" and c["expiry"] == nearest and 225 <= c["strike"] <= 250:
        last = f"${c['last']:.2f}" if c["last"] else "N/A"
        bid = f"${c['bid']:.2f}" if c["bid"] else "N/A"
        ask = f"${c['ask']:.2f}" if c["ask"] else "N/A"
        oi = f"{c['oi']:,.0f}" if c["oi"] else "N/A"
        iv = f"{c['iv']:.1%}" if c["iv"] else "N/A"
        delta = f"{c['delta']:.3f}" if c["delta"] else "N/A"
        gamma = f"{c['gamma']:.4f}" if c["gamma"] else "N/A"
        theta = f"{c['theta']:.4f}" if c["theta"] else "N/A"
        print(f"${c['strike']:>7.2f} {last:>8} {bid:>8} {ask:>8} {oi:>8} {iv:>7} {delta:>7} {gamma:>7} {theta:>7}")