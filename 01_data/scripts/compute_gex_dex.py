#!/usr/bin/env python3
"""Compute Gamma Exposure (GEX) and Delta Exposure (DEX) from market greeks.

Reads the latest greeks data from market.greeks joined with market.options
to get contract_type and expiration. For each underlying in the watchlist,
computes per-contract GEX and DEX, then aggregates by (underlying, date,
expiration, strike) into market.gex_dex, and overview totals per
(underlying, date) into market.gex_dex_overview.

Stocknear sign conventions:
    Call GEX = OI * gamma * spot          (positive)
    Put  GEX = -(OI * gamma * spot)       (negated — short gamma exposure)
    Call DEX = OI * delta * spot           (delta > 0)
    Put  DEX = OI * delta * spot           (delta < 0, so negative exposure)

Moneyness per contract:
    Call: (spot / strike - 1) * 100
    Put:  (strike / spot - 1) * 100

Re-runnable and idempotent (UPSERT via ON CONFLICT).

Usage:
    python scripts/compute_gex_dex.py                 # full watchlist, latest date
    python scripts/compute_gex_dex.py --symbol NVDA   # single symbol
    python scripts/compute_gex_dex.py --date 2026-05-15  # specific date
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2.extras import execute_values

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from constants import DB_CONFIG, load_env  # noqa: E402

load_env(".env.db")

# ---------------------------------------------------------------------------
# SQL queries
# ---------------------------------------------------------------------------

FETCH_WATCHLIST_SQL = """
SELECT symbol FROM market.assets
WHERE asset_type IN ('stock', 'etf')
  AND active = TRUE
ORDER BY symbol;
"""

FETCH_LATEST_DATE_SQL = """
SELECT MAX(date) FROM market.greeks;
"""

FETCH_LATEST_DATE_FOR_SYMBOL_SQL = """
SELECT MAX(g.date)
FROM market.greeks g
JOIN market.options o ON o.occ_symbol = g.occ_symbol
WHERE o.underlying = %s;
"""

# Fetch per-contract greeks + option metadata for computing GEX/DEX
FETCH_CONTRACTS_SQL = """
SELECT
    o.underlying,
    o.occ_symbol,
    o.contract_type,
    o.strike,
    o.expiration,
    g.date,
    g.open_interest,
    g.delta,
    g.gamma,
    g.underlying_price
FROM market.greeks g
JOIN market.options o ON o.occ_symbol = g.occ_symbol
WHERE o.underlying = %s
  AND g.date = %s
  AND g.open_interest > 0
  AND g.gamma IS NOT NULL
ORDER BY o.strike, o.expiration, o.contract_type;
"""

UPSERT_GEX_DEX_SQL = """
INSERT INTO market.gex_dex
    (underlying, date, expiration, strike,
     call_gex, put_gex, net_gex,
     call_dex, put_dex, net_dex,
     call_oi, put_oi, total_oi)
VALUES %s
ON CONFLICT (underlying, date, expiration, strike) DO UPDATE SET
    call_gex   = EXCLUDED.call_gex,
    put_gex    = EXCLUDED.put_gex,
    net_gex    = EXCLUDED.net_gex,
    call_dex   = EXCLUDED.call_dex,
    put_dex    = EXCLUDED.put_dex,
    net_dex    = EXCLUDED.net_dex,
    call_oi    = EXCLUDED.call_oi,
    put_oi     = EXCLUDED.put_oi,
    total_oi   = EXCLUDED.total_oi,
    created_at = NOW();
"""

UPSERT_GEX_DEX_OVERVIEW_SQL = """
INSERT INTO market.gex_dex_overview
    (underlying, date,
     total_call_gex, total_put_gex, total_net_gex,
     total_call_dex, total_put_dex, total_net_dex)
VALUES %s
ON CONFLICT (underlying, date) DO UPDATE SET
    total_call_gex = EXCLUDED.total_call_gex,
    total_put_gex  = EXCLUDED.total_put_gex,
    total_net_gex  = EXCLUDED.total_net_gex,
    total_call_dex = EXCLUDED.total_call_dex,
    total_put_dex  = EXCLUDED.total_put_dex,
    total_net_dex  = EXCLUDED.total_net_dex,
    created_at      = NOW();
"""


# ---------------------------------------------------------------------------
# Computation helpers
# ---------------------------------------------------------------------------

def compute_contract_gex_dex(
    contract_type: str,
    open_interest: int,
    delta: Decimal | None,
    gamma: Decimal | None,
    underlying_price: Decimal | None,
    strike: Decimal,
) -> tuple[float, float, float | None]:
    """Compute per-contract GEX, DEX, and moneyness_pct.

    Follows Stocknear sign conventions:
        Call GEX = OI * gamma * spot  (positive)
        Put  GEX = -(OI * gamma * spot)  (negated)
        Call DEX = OI * delta * spot  (delta > 0)
        Put  DEX = OI * delta * spot  (delta < 0, so inherently negative)

    Moneyness:
        Call: (spot / strike - 1) * 100
        Put:  (strike / spot - 1) * 100

    Args:
        contract_type: 'C' for call, 'P' for put.
        open_interest: Open interest for the contract.
        delta: Delta greek (may be None).
        gamma: Gamma greek (guaranteed not None by query filter).
        underlying_price: Spot price (may be None).
        strike: Strike price.

    Returns:
        (gex, dex, moneyness_pct) — gex and dex may be 0.0 if inputs missing;
        moneyness_pct may be None if spot or strike is zero/missing.
    """
    if delta is None or gamma is None or underlying_price is None:
        return (0.0, 0.0, None)

    gamma_f = float(gamma)
    delta_f = float(delta)
    spot_f = float(underlying_price)
    strike_f = float(strike)
    oi = open_interest

    # GEX computation with sign convention
    if contract_type == "C":
        gex = oi * gamma_f * spot_f       # positive — long gamma
        dex = oi * delta_f * spot_f        # positive — long delta
    else:
        gex = -(oi * gamma_f * spot_f)     # negated — short gamma exposure
        dex = oi * delta_f * spot_f         # negative (delta < 0 for puts)

    # Moneyness
    if strike_f > 0 and spot_f > 0:
        if contract_type == "C":
            moneyness_pct = (spot_f / strike_f - 1.0) * 100.0
        else:
            moneyness_pct = (strike_f / spot_f - 1.0) * 100.0
    else:
        moneyness_pct = None

    return (gex, dex, moneyness_pct)


def aggregate_gex_dex(
    rows: list[tuple],
) -> tuple[
    list[tuple],   # gex_dex rows for upsert
    list[tuple],    # overview rows for upsert
    dict[str, int],  # contract count per underlying
    dict[str, list[tuple[float, str]]],  # top strikes by |net_gex|
]:
    """Aggregate raw contract-level GEX/DEX into per-strike/expiry buckets.

    Groups by (underlying, date, expiration, strike), summing call and put
    contributions separately, then computing net values.

    Also builds overview totals per (underlying, date) and tracking stats
    for summary output.

    Args:
        rows: List of tuples from FETCH_CONTRACTS_SQL:
            (underlying, occ_symbol, contract_type, strike, expiration,
             date, open_interest, delta, gamma, underlying_price)

    Returns:
        (gex_dex_rows, overview_map, contract_counts, top_strikes_map)
        - gex_dex_rows: list of tuples for upsert into market.gex_dex
        - overview_map: {(underlying, date_str): {field: value}}
        - contract_counts: {underlying: count}
        - top_strikes_map: {underlying: [(net_gex, strike_label), ...]}
    """
    # Bucket: (underlying, date, expiration, strike) ->
    #   {call_gex, put_gex, call_dex, put_dex, call_oi, put_oi}
    buckets: dict[tuple[str, str, date, Decimal], dict[str, float]] = {}

    # Overview accumulators: (underlying, date) -> totals
    overview: dict[tuple[str, str], dict[str, float]] = {}

    # Contract counts per underlying
    counts: dict[str, int] = {}

    # For top-strikes reporting
    # (underlying, date, expiration, strike) -> net_gex
    strike_net_gex: dict[tuple[str, str, date, Decimal], float] = {}

    for (
        underlying, occ_symbol, contract_type, strike, expiration,
        greeks_date, open_interest, delta, gamma, underlying_price,
    ) in rows:
        gex, dex, moneyness_pct = compute_contract_gex_dex(
            contract_type, open_interest, delta, gamma, underlying_price, strike,
        )

        key = (underlying, str(greeks_date), expiration, strike)

        if key not in buckets:
            buckets[key] = {
                "call_gex": 0.0, "put_gex": 0.0,
                "call_dex": 0.0, "put_dex": 0.0,
                "call_oi": 0, "put_oi": 0,
            }

        b = buckets[key]
        if contract_type == "C":
            b["call_gex"] += gex
            b["call_dex"] += dex
            b["call_oi"] += open_interest
        else:
            b["put_gex"] += gex
            b["put_dex"] += dex
            b["put_oi"] += open_interest

        # Overview accumulation
        ov_key = (underlying, str(greeks_date))
        if ov_key not in overview:
            overview[ov_key] = {
                "total_call_gex": 0.0, "total_put_gex": 0.0,
                "total_call_dex": 0.0, "total_put_dex": 0.0,
            }
        ov = overview[ov_key]
        if contract_type == "C":
            ov["total_call_gex"] += gex
            ov["total_call_dex"] += dex
        else:
            ov["total_put_gex"] += gex
            ov["total_put_dex"] += dex

        # Track contract count
        counts[underlying] = counts.get(underlying, 0) + 1

    # Build gex_dex upsert rows and track net_gex per strike for reporting
    gex_dex_rows: list[tuple] = []
    # (underlying, date_str) -> list of (|net_gex|, strike_label)
    strike_details: dict[str, list[tuple[float, str]]] = {}

    for (
        (underlying, date_str, expiration, strike), b
    ) in buckets.items():
        net_gex = b["call_gex"] + b["put_gex"]
        net_dex = b["call_dex"] + b["put_dex"]
        total_oi = b["call_oi"] + b["put_oi"]

        gex_dex_rows.append((
            underlying,
            date_str,
            expiration,
            strike,
            round(b["call_gex"], 4),
            round(b["put_gex"], 4),
            round(net_gex, 4),
            round(b["call_dex"], 4),
            round(b["put_dex"], 4),
            round(net_dex, 4),
            b["call_oi"],
            b["put_oi"],
            total_oi,
        ))

        # Track for top-strikes report
        strike_label = f"{strike} exp={expiration}"
        underlying_key = underlying
        if underlying_key not in strike_details:
            strike_details[underlying_key] = []
        strike_details[underlying_key].append((abs(net_gex), strike_label))

    # Build overview upsert rows
    overview_rows: list[tuple] = []
    for (underlying, date_str), ov in overview.items():
        total_net_gex = ov["total_call_gex"] + ov["total_put_gex"]
        total_net_dex = ov["total_call_dex"] + ov["total_put_dex"]
        overview_rows.append((
            underlying,
            date_str,
            round(ov["total_call_gex"], 4),
            round(ov["total_put_gex"], 4),
            round(total_net_gex, 4),
            round(ov["total_call_dex"], 4),
            round(ov["total_put_dex"], 4),
            round(total_net_dex, 4),
        ))

    return gex_dex_rows, overview_rows, counts, strike_details


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    """Main entry point for GEX/DEX computation."""
    parser = argparse.ArgumentParser(
        description="Compute GEX/DEX for watchlist symbols from market greeks"
    )
    parser.add_argument(
        "--symbol",
        type=str,
        default=None,
        help="Compute GEX/DEX for a single symbol instead of the full watchlist",
    )
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="Specific date (YYYY-MM-DD) instead of latest in market.greeks",
    )
    args = parser.parse_args()

    conn = psycopg2.connect(**DB_CONFIG)

    try:
        # Resolve watchlist
        with conn.cursor() as cur:
            if args.symbol:
                symbols = [args.symbol.upper()]
            else:
                cur.execute(FETCH_WATCHLIST_SQL)
                symbols = [row[0] for row in cur.fetchall()]

        if not symbols:
            print("No symbols found in watchlist.")
            return 0

        # Resolve target date
        target_date: date | None = None
        if args.date:
            try:
                target_date = date.fromisoformat(args.date)
            except ValueError:
                print(f"Invalid date format: {args.date}. Use YYYY-MM-DD.")
                return 1
        else:
            # Use the latest date available in market.greeks
            with conn.cursor() as cur:
                cur.execute(FETCH_LATEST_DATE_SQL)
                row = cur.fetchone()
                if row is None or row[0] is None:
                    print("No greeks data found in market.greeks.")
                    return 1
                target_date = row[0]

        print(f"Target date: {target_date}")
        print(f"Symbols: {len(symbols)} → {', '.join(symbols)}\n")

        # Process each symbol
        total_gex_dex_rows = 0
        total_overview_rows = 0

        for sym in symbols:
            # If a specific symbol is requested, verify it has data
            if args.symbol:
                with conn.cursor() as cur:
                    cur.execute(FETCH_LATEST_DATE_FOR_SYMBOL_SQL, (sym,))
                    row = cur.fetchone()
                    if row is None or row[0] is None:
                        print(f"  {sym}: No greeks data found, skipping.")
                        continue
                    # Use the latest date for this symbol if --symbol
                    # but only if --date wasn't explicitly set
                    if not args.date:
                        target_date = row[0]

            # Fetch contract-level data
            with conn.cursor() as cur:
                cur.execute(FETCH_CONTRACTS_SQL, (sym, target_date))
                rows = cur.fetchall()

            if not rows:
                print(f"  {sym}: No qualifying contracts (OI > 0, gamma NOT NULL), skipping.")
                continue

            # Aggregate
            gex_dex_rows, overview_rows, counts, strike_details = aggregate_gex_dex(rows)

            # Upsert gex_dex
            if gex_dex_rows:
                with conn.cursor() as cur:
                    execute_values(
                        cur, UPSERT_GEX_DEX_SQL, gex_dex_rows, page_size=500
                    )
                total_gex_dex_rows += len(gex_dex_rows)

            # Upsert overview
            if overview_rows:
                with conn.cursor() as cur:
                    execute_values(
                        cur, UPSERT_GEX_DEX_OVERVIEW_SQL, overview_rows, page_size=500
                    )
                total_overview_rows += len(overview_rows)

            conn.commit()

            # Summary stats
            n_contracts = counts.get(sym, 0)
            overview_data = None
            for row in overview_rows:
                if row[0] == sym:
                    overview_data = row
                    break

            if overview_data:
                total_net_gex = overview_data[4]  # total_net_gex
                total_net_dex = overview_data[7]  # total_net_dex
                print(
                    f"  {sym}: contracts={n_contracts:>6}  "
                    f"net_GEX={total_net_gex:>14,.0f}  "
                    f"net_DEX={total_net_dex:>14,.0f}"
                )
            else:
                print(f"  {sym}: contracts={n_contracts:>6}  (no overview)")

            # Top 3 strikes by |net_gex|
            if sym in strike_details:
                top3 = sorted(strike_details[sym], reverse=True)[:3]
                if top3:
                    labels = [
                        f"{label} (|net_gex|={abs_gex:,.0f})"
                        for abs_gex, label in top3
                    ]
                    print(f"        Top 3 strikes by |net_gex|: {'; '.join(labels)}")

        print(
            f"\nDone. Upserted {total_gex_dex_rows} gex_dex rows "
            f"and {total_overview_rows} gex_dex_overview rows."
        )

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())