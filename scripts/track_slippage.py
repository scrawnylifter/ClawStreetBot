#!/usr/bin/env python3
"""track_slippage.py — backfill fill_price / slippage columns on signal_alerts.

Joins market.signal_alerts with trading.positions (linked via position_id)
to populate fill_price, slippage_pct, and slippage_dollars for historical
rows that filled before migration 032 added the columns.

Idempotent: only writes rows where fill_price IS NULL AND the matching
position is known. Re-running after new fills lands the latest values
without disturbing rows already populated by reconcile_orders.py.

Usage:
    python scripts/track_slippage.py            # backfill all eligible rows
    python scripts/track_slippage.py --dry-run  # print, no writes
    python scripts/track_slippage.py --id 33    # one signal id
"""
from __future__ import annotations

import argparse
import logging
import sys
from decimal import Decimal
from pathlib import Path

import psycopg2
import psycopg2.extras

sys.path.insert(0, str(Path(__file__).resolve().parent))
import process_approved as pa  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("track_slippage")


def fetch_rows_to_backfill(conn, signal_id: int | None) -> list[dict]:
    """Return signal_alerts rows whose fill data hasn't been computed yet.

    Joins to trading.positions on signal_alerts.position_id (set when
    reconcile_orders writes 'filled'). Stock-only signals use
    trigger_price as the reference; option signals use option_mid.
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        query = """
            SELECT sa.id,
                   sa.symbol,
                   sa.option_symbol,
                   sa.option_mid,
                   sa.trigger_price,
                   p.entry_price AS fill_price,
                   p.quantity    AS quantity
              FROM market.signal_alerts sa
              JOIN trading.positions p ON p.id = sa.position_id
             WHERE sa.fill_price IS NULL
               AND sa.position_id IS NOT NULL
               AND p.entry_price IS NOT NULL
        """
        params: list = []
        if signal_id is not None:
            query += " AND sa.id = %s"
            params.append(signal_id)
        query += " ORDER BY sa.id ASC"
        cur.execute(query, params)
        return list(cur.fetchall())


def compute_slippage(
    fill_price: Decimal,
    reference_price: Decimal | None,
    quantity: Decimal | None,
) -> tuple[Decimal | None, Decimal | None]:
    """Return (slippage_pct, slippage_dollars). Returns (None, None) when
    the reference price is missing or zero — without a fair-mid baseline,
    slippage is undefined."""
    if reference_price is None or reference_price <= 0:
        return None, None
    diff = fill_price - reference_price
    pct = (diff / reference_price) * Decimal("100")
    dollars = (diff * quantity) if quantity is not None else None
    return pct, dollars


def backfill_one(conn, row: dict, dry_run: bool) -> str:
    """Compute slippage for one row and (unless dry_run) write it back."""
    has_option = bool(row.get("option_symbol"))
    reference_price = row["option_mid"] if has_option else row["trigger_price"]
    fill_price = row["fill_price"]
    qty = row["quantity"]

    slip_pct, slip_dollars = compute_slippage(fill_price, reference_price, qty)
    label = "option" if has_option else "stock"
    ref_str = f"{reference_price}" if reference_price is not None else "—"

    if slip_pct is None:
        return (f"#{row['id']} {row['symbol']} SKIP — {label}, no reference "
                f"price (ref={ref_str}, fill={fill_price})")

    if dry_run:
        return (f"#{row['id']} {row['symbol']} DRY-RUN — {label} "
                f"fill={fill_price} ref={ref_str} → "
                f"slip {slip_pct:+.2f}% (${slip_dollars or 0:+.2f})")

    with conn.cursor() as cur:
        cur.execute(
            """UPDATE market.signal_alerts
                  SET fill_price       = %s,
                      slippage_pct     = %s,
                      slippage_dollars = %s
                WHERE id = %s
                  AND fill_price IS NULL""",
            (fill_price, slip_pct, slip_dollars, row["id"]),
        )
    conn.commit()
    return (f"#{row['id']} {row['symbol']} BACKFILLED — {label} "
            f"fill={fill_price} ref={ref_str} → "
            f"slip {slip_pct:+.2f}% (${slip_dollars or 0:+.2f})")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--id", type=int, default=None,
                        help="Backfill a single signal id.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print computed slippage without writing.")
    args = parser.parse_args()

    conn = pa.get_connection()
    try:
        rows = fetch_rows_to_backfill(conn, args.id)
        if not rows:
            print("No signal_alerts rows need slippage backfill.")
            return 0
        log.info("Backfilling slippage for %d row(s)%s",
                 len(rows), " (DRY RUN)" if args.dry_run else "")
        for r in rows:
            print(backfill_one(conn, r, args.dry_run))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
