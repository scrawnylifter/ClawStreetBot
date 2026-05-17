#!/usr/bin/env python3
"""Real-time underlying + option snapshot for signal-time enrichment.

Fetches the current stock price for a symbol along with its live option chain
(greeks + bid/ask), filters to the strategy's preferred contracts
(>= 30 DTE, |delta| in [0.50, 0.70]), picks the best single contract, and
emits a compact JSON document on stdout for downstream consumers (alerter,
preflight checks, etc.).

Usage:
    python scripts/fetch_alpaca_snapshot.py --symbol NVDA
    python scripts/fetch_alpaca_snapshot.py --symbol NVDA --type C
    python scripts/fetch_alpaca_snapshot.py --symbol NVDA --max-dte 60

Output:
    {
      "symbol": "NVDA",
      "price": 123.45,
      "as_of": "2026-05-16T20:31:02+00:00",
      "best_option": {
        "occ_symbol": "NVDA260619C00125000",
        "contract_type": "C",
        "strike": 125.0,
        "expiry": "2026-06-19",
        "dte": 34,
        "delta": 0.5523,
        "theta": -0.0421,
        "bid": 4.10,
        "ask": 4.25,
        "mid": 4.175,
        "iv": 0.4912
      }
    }
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_env(filename: str) -> None:
    path = PROJECT_ROOT / filename
    if not path.exists():
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


load_env(".env.alpaca")

from alpaca.data.historical import StockHistoricalDataClient  # noqa: E402
from alpaca.data.historical.option import OptionHistoricalDataClient  # noqa: E402
from alpaca.data.requests import OptionChainRequest, StockSnapshotRequest  # noqa: E402

ALPACA_API_KEY = os.environ["ALPACA_PAPER_API_KEY"]
ALPACA_SECRET_KEY = os.environ["ALPACA_PAPER_SECRET_KEY"]

# Strategy filter — swing/long-term Greeks profile (see CLAUDE.md).
MIN_DTE = 30
DELTA_MIN = 0.50
DELTA_MAX = 0.70


def fnum(x) -> float | None:
    if x is None:
        return None
    try:
        v = float(x)
        if v != v:  # NaN
            return None
        return v
    except (TypeError, ValueError):
        return None


def parse_occ(occ: str) -> tuple[str, str, date, float] | None:
    """Parse Alpaca OCC symbol → (root, 'C'|'P', expiry, strike)."""
    if not occ or len(occ) < 16:
        return None
    body = occ[-15:]
    root = occ[:-15]
    if not root:
        return None
    try:
        yy = int(body[0:2])
        mm = int(body[2:4])
        dd = int(body[4:6])
        ctype = body[6]
        strike = int(body[7:15]) / 1000.0
    except (ValueError, IndexError):
        return None
    if ctype not in ("C", "P"):
        return None
    try:
        exp = date(2000 + yy, mm, dd)
    except ValueError:
        return None
    return root, ctype, exp, strike


def get_underlying_price(symbol: str) -> float | None:
    """Latest trade price; fall back to quote midpoint, then daily close."""
    client = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
    req = StockSnapshotRequest(symbol_or_symbols=symbol, feed="iex")
    snaps = client.get_stock_snapshot(req)
    snap = snaps.get(symbol) if isinstance(snaps, dict) else snaps
    if snap is None:
        return None

    trade = getattr(snap, "latest_trade", None)
    price = fnum(getattr(trade, "price", None)) if trade else None
    if price is not None:
        return price

    quote = getattr(snap, "latest_quote", None)
    if quote is not None:
        bid = fnum(getattr(quote, "bid_price", None))
        ask = fnum(getattr(quote, "ask_price", None))
        if bid is not None and ask is not None and bid > 0 and ask > 0:
            return (bid + ask) / 2.0

    bar = getattr(snap, "daily_bar", None) or getattr(snap, "previous_daily_bar", None)
    return fnum(getattr(bar, "close", None)) if bar else None


def select_best_option(symbol: str, want_type: str | None,
                       min_dte: int, max_dte: int) -> dict | None:
    """Return the single best contract matching strategy filters, or None."""
    client = OptionHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
    today = date.today()

    req = OptionChainRequest(
        underlying_symbol=symbol,
        expiration_date_gte=today,
        expiration_date_lte=date.fromordinal(today.toordinal() + max_dte),
    )
    if want_type in ("C", "P"):
        req.type = "call" if want_type == "C" else "put"

    chain = client.get_option_chain(req)

    best: dict | None = None
    best_score = float("inf")  # lower is better — distance from delta=0.60

    for occ, snap in chain.items():
        parsed = parse_occ(occ)
        if parsed is None:
            continue
        _root, ctype, expiry, strike = parsed

        if want_type and ctype != want_type:
            continue

        dte = (expiry - today).days
        if dte < min_dte:
            continue

        g = getattr(snap, "greeks", None)
        delta = fnum(getattr(g, "delta", None)) if g else None
        theta = fnum(getattr(g, "theta", None)) if g else None
        if delta is None:
            continue

        abs_delta = abs(delta)
        if abs_delta < DELTA_MIN or abs_delta > DELTA_MAX:
            continue

        # Reject sign mismatches (Alpaca usually returns negative delta for puts).
        if ctype == "C" and delta < 0:
            continue
        if ctype == "P" and delta > 0:
            continue

        q = getattr(snap, "latest_quote", None)
        bid = fnum(getattr(q, "bid_price", None)) if q else None
        ask = fnum(getattr(q, "ask_price", None)) if q else None
        if bid is None or ask is None or bid <= 0 or ask <= 0:
            continue
        mid = (bid + ask) / 2.0
        iv = fnum(getattr(snap, "implied_volatility", None))

        # Score: prefer delta closest to 0.60 (midpoint of 0.50–0.70 band).
        score = abs(abs_delta - 0.60)
        if score < best_score:
            best_score = score
            best = {
                "occ_symbol": occ,
                "contract_type": ctype,
                "strike": strike,
                "expiry": expiry.isoformat(),
                "dte": dte,
                "delta": round(delta, 4),
                "theta": round(theta, 4) if theta is not None else None,
                "bid": round(bid, 4),
                "ask": round(ask, 4),
                "mid": round(mid, 4),
                "iv": round(iv, 4) if iv is not None else None,
            }

    return best


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True, help="Underlying ticker (e.g., NVDA)")
    parser.add_argument("--type", choices=["C", "P"],
                        help="Restrict to calls or puts (default: best of either)")
    parser.add_argument("--min-dte", type=int, default=MIN_DTE,
                        help=f"Minimum days to expiration (default {MIN_DTE})")
    parser.add_argument("--max-dte", type=int, default=120,
                        help="Maximum days to expiration (default 120)")
    args = parser.parse_args()

    symbol = args.symbol.upper()
    price = get_underlying_price(symbol)
    best = select_best_option(symbol, args.type, args.min_dte, args.max_dte)

    out = {
        "symbol": symbol,
        "price": round(price, 4) if price is not None else None,
        "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "best_option": best,
    }
    json.dump(out, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0 if best is not None else 2


if __name__ == "__main__":
    sys.exit(main())
