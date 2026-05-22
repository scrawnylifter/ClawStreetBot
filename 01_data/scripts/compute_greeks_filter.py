#!/usr/bin/env python3
"""Apply the Greeks Strategy filter to the latest option chain per symbol.

Joins market.iv_rank, market.realized_vol, and market.greeks with
market.options to produce a per-contract evaluation for each watchlist
symbol's most-recent greeks date. Each row records the IV regime, the
contract details, and any rejection reasons.

Regime gating (from CLAUDE.md → Greeks Strategy):
    IV rank < 25   → 'buy_premium'        (cheap premium)
    25 <= IV < 50  → 'directional'
    50 <= IV < 75  → 'spreads_cautious'
    IV >= 75       → 'sell_premium'       (NO naked buying)

Per-contract rejection rules:
    |delta| < 0.50                        → 'delta_too_low'
    |delta| > 0.90                        → 'delta_too_high'
    swing band (default)  delta not in 0.50-0.70 → 'delta_outside_swing_band'
    theta / midpoint > 3% (swing budget)  → 'theta_over_budget'
    regime == 'sell_premium' and naked    → 'iv_rank_too_rich_for_buying'

The script also reads `iv_rv_spread` (current_iv - rv_20d) and tags
premium as rich/cheap when |spread| > 0.15 in the regime_notes.

Re-runnable and idempotent (INSERT ON CONFLICT DO UPDATE).

Usage:
    python scripts/compute_greeks_filter.py
    python scripts/compute_greeks_filter.py --symbol NVDA
"""
from __future__ import annotations

import argparse
import os
from datetime import date
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2.extras import execute_values

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_env(filename: str) -> None:
    """Load environment variables from a dotenv-style file."""
    path = PROJECT_ROOT / filename
    if not path.exists():
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


load_env(".env.db")

DB_CONFIG = {
    "host": os.environ.get("POSTGRES_HOST", "localhost"),
    "port": int(os.environ.get("POSTGRES_PORT", 5432)),
    "dbname": os.environ["POSTGRES_DB"],
    "user": os.environ["POSTGRES_USER"],
    "password": os.environ["POSTGRES_PASSWORD"],
}

# Strategy thresholds — mirror the rules in CLAUDE.md.
DELTA_FLOOR = 0.50
DELTA_CEILING = 0.90
SWING_BAND = (0.50, 0.70)
THETA_BUDGET_SWING = 0.03     # 3% of premium per day
IV_RV_SPREAD_THRESHOLD = 0.15

FETCH_SYMBOLS_SQL = """
SELECT symbol FROM market.assets
WHERE asset_type IN ('stock', 'etf')
  AND active = TRUE
ORDER BY symbol;
"""

# Latest IV-rank row per symbol.
LATEST_IV_RANK_SQL = """
SELECT DISTINCT ON (symbol) symbol, date, iv_rank_52w, current_iv
FROM market.iv_rank
WHERE symbol = %s
ORDER BY symbol, date DESC;
"""

# Latest realized-vol row per symbol (for IV-RV spread).
LATEST_RV_SQL = """
SELECT DISTINCT ON (symbol) symbol, date, rv_20d
FROM market.realized_vol
WHERE symbol = %s
ORDER BY symbol, date DESC;
"""

# Latest greeks snapshot per symbol joined with options metadata.
LATEST_GREEKS_SQL = """
WITH latest AS (
    SELECT MAX(g.date) AS dt
    FROM market.greeks g
    JOIN market.options o ON o.occ_symbol = g.occ_symbol
    WHERE o.underlying = %s
)
SELECT
    g.occ_symbol, o.contract_type, o.strike, o.expiration,
    g.delta, g.gamma, g.theta, g.vega, g.iv, g.midpoint, g.date
FROM market.greeks g
JOIN market.options o ON o.occ_symbol = g.occ_symbol
JOIN latest l ON g.date = l.dt
WHERE o.underlying = %s
ORDER BY o.expiration, o.contract_type, o.strike;
"""

UPSERT_SQL = """
INSERT INTO market.greeks_filter (
    symbol, date, regime, iv_rank, iv_rv_spread,
    contract_symbol, contract_type, strike, expiration,
    delta, gamma, theta, vega, iv, midpoint,
    regime_notes, passes_filter, rejection_reasons
) VALUES %s
ON CONFLICT (symbol, date, contract_symbol) DO UPDATE SET
    regime            = EXCLUDED.regime,
    iv_rank           = EXCLUDED.iv_rank,
    iv_rv_spread      = EXCLUDED.iv_rv_spread,
    contract_type     = EXCLUDED.contract_type,
    strike            = EXCLUDED.strike,
    expiration        = EXCLUDED.expiration,
    delta             = EXCLUDED.delta,
    gamma             = EXCLUDED.gamma,
    theta             = EXCLUDED.theta,
    vega              = EXCLUDED.vega,
    iv                = EXCLUDED.iv,
    midpoint          = EXCLUDED.midpoint,
    regime_notes      = EXCLUDED.regime_notes,
    passes_filter     = EXCLUDED.passes_filter,
    rejection_reasons = EXCLUDED.rejection_reasons,
    created_at        = NOW();
"""


def classify_regime(iv_rank: float | None) -> tuple[str, str]:
    """Map an IV rank percentile to a strategy regime.

    Args:
        iv_rank: IV rank 0-100 (or None if unknown).

    Returns:
        Tuple of (regime_label, regime_notes).
    """
    if iv_rank is None:
        return ("unknown", "IV rank unavailable — defer to manual review.")
    if iv_rank < 25:
        return (
            "buy_premium",
            f"IV rank {iv_rank:.1f} < 25 — premium is cheap, prefer long options.",
        )
    if iv_rank < 50:
        return (
            "directional",
            f"IV rank {iv_rank:.1f} in [25,50) — standard directional plays.",
        )
    if iv_rank < 75:
        return (
            "spreads_cautious",
            f"IV rank {iv_rank:.1f} in [50,75) — prefer spreads over naked longs.",
        )
    return (
        "sell_premium",
        f"IV rank {iv_rank:.1f} >= 75 — premium is rich, do NOT buy naked options.",
    )


def evaluate_contract(
    regime: str,
    delta: float | None,
    theta: float | None,
    midpoint: float | None,
) -> list[str]:
    """Apply per-contract rejection rules and return failure reasons.

    Args:
        regime: Regime label from `classify_regime`.
        delta: Contract delta (may be negative for puts).
        theta: Contract theta (negative for long options).
        midpoint: Mid-market premium price.

    Returns:
        List of rejection-reason tokens; empty if the contract passes.
    """
    reasons: list[str] = []

    if delta is None:
        reasons.append("delta_missing")
    else:
        abs_delta = abs(float(delta))
        if abs_delta < DELTA_FLOOR:
            reasons.append("delta_too_low")
        elif abs_delta > DELTA_CEILING:
            reasons.append("delta_too_high")
        elif not (SWING_BAND[0] <= abs_delta <= SWING_BAND[1]):
            reasons.append("delta_outside_swing_band")

    if theta is not None and midpoint is not None and float(midpoint) > 0:
        theta_ratio = abs(float(theta)) / float(midpoint)
        if theta_ratio > THETA_BUDGET_SWING:
            reasons.append("theta_over_budget")

    if regime == "sell_premium":
        reasons.append("iv_rank_too_rich_for_buying")

    return reasons


def compute_for_symbol(conn: Any, symbol: str) -> list[tuple]:
    """Build per-contract filter rows for a symbol's latest option chain.

    Args:
        conn: psycopg2 database connection.
        symbol: Underlying ticker.

    Returns:
        List of tuples ready for upsert into market.greeks_filter.
    """
    with conn.cursor() as cur:
        cur.execute(LATEST_IV_RANK_SQL, (symbol,))
        iv_row = cur.fetchone()
        cur.execute(LATEST_RV_SQL, (symbol,))
        rv_row = cur.fetchone()
        cur.execute(LATEST_GREEKS_SQL, (symbol, symbol))
        contracts = cur.fetchall()

    if not contracts:
        print(f"  {symbol}: no greeks rows, skipping")
        return []

    iv_rank = float(iv_row[2]) if iv_row and iv_row[2] is not None else None
    current_iv = float(iv_row[3]) if iv_row and iv_row[3] is not None else None
    rv_20d = float(rv_row[2]) if rv_row and rv_row[2] is not None else None
    iv_rv_spread = (
        current_iv - rv_20d
        if current_iv is not None and rv_20d is not None
        else None
    )

    regime, regime_notes = classify_regime(iv_rank)
    if iv_rv_spread is not None:
        if iv_rv_spread > IV_RV_SPREAD_THRESHOLD:
            regime_notes += (
                f" IV-RV spread {iv_rv_spread:+.3f} > {IV_RV_SPREAD_THRESHOLD}"
                " — premium rich vs. realized."
            )
        elif iv_rv_spread < -IV_RV_SPREAD_THRESHOLD:
            regime_notes += (
                f" IV-RV spread {iv_rv_spread:+.3f} < -{IV_RV_SPREAD_THRESHOLD}"
                " — premium cheap vs. realized."
            )

    eval_date: date = contracts[0][10]
    out: list[tuple] = []
    for (
        occ_symbol,
        ctype,
        strike,
        expiration,
        delta,
        gamma,
        theta,
        vega,
        iv,
        midpoint,
        _dt,
    ) in contracts:
        reasons = evaluate_contract(regime, delta, theta, midpoint)
        out.append(
            (
                symbol,
                eval_date,
                regime,
                iv_rank,
                iv_rv_spread,
                occ_symbol,
                ctype,
                strike,
                expiration,
                delta,
                gamma,
                theta,
                vega,
                iv,
                midpoint,
                regime_notes,
                len(reasons) == 0,
                reasons,
            )
        )
    return out


def main() -> int:
    """Main entry point for the Greeks filter."""
    parser = argparse.ArgumentParser(
        description="Apply the Greeks Strategy filter to the latest option chain"
    )
    parser.add_argument(
        "--symbol",
        type=str,
        default=None,
        help="Evaluate a single symbol instead of the full watchlist",
    )
    args = parser.parse_args()

    conn = psycopg2.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cur:
            if args.symbol:
                symbols = [args.symbol.upper()]
            else:
                cur.execute(FETCH_SYMBOLS_SQL)
                symbols = [row[0] for row in cur.fetchall()]

        all_rows: list[tuple] = []
        for sym in symbols:
            print(f"Evaluating greeks for {sym}...")
            rows = compute_for_symbol(conn, sym)
            all_rows.extend(rows)
            if rows:
                passing = sum(1 for r in rows if r[16])
                print(
                    f"  {sym}: regime={rows[0][2]} iv_rank={rows[0][3]}"
                    f" passing={passing}/{len(rows)}"
                )

        if not all_rows:
            print("No rows to upsert.")
            return 0

        print(f"Upserting {len(all_rows)} rows into market.greeks_filter...")
        with conn.cursor() as cur:
            execute_values(cur, UPSERT_SQL, all_rows, page_size=500)
        conn.commit()
        print(f"Done. Upserted {len(all_rows)} greeks_filter rows.")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
