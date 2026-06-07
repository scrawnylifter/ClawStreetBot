"""Database operations for market.watchlist table."""

from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


def ensure_schema_and_table(conn) -> None:
    """Create market schema and watchlist table if they don't exist.

    Reads and executes the migration SQL file. Idempotent — safe on every sync.
    """
    migration_path = Path(__file__).parent / "migrations" / "001_watchlist.sql"
    sql = migration_path.read_text()
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()
    logger.info("watchlist_schema_ensured")


def upsert_watchlist_rows(conn, rows: list[dict[str, Any]]) -> int:
    """Upsert watchlist rows. Returns number of rows upserted.

    Uses ON CONFLICT (id, symbol) DO UPDATE — handles both inserts
    and updates in a single call. Does NOT commit; the caller owns the
    transaction so the upsert and stale-delete commit atomically.
    """
    if not rows:
        return 0

    sql = """
        INSERT INTO market.watchlist (
            id, name, account_id, symbol, asset_id, asset_class,
            exchange, asset_name, status, tradable, marginable,
            shortable, easy_to_borrow, fractionable,
            alpaca_created_at, alpaca_updated_at, synced_at
        ) VALUES (
            %(id)s, %(name)s, %(account_id)s, %(symbol)s, %(asset_id)s,
            %(asset_class)s, %(exchange)s, %(asset_name)s, %(status)s,
            %(tradable)s, %(marginable)s, %(shortable)s,
            %(easy_to_borrow)s, %(fractionable)s,
            %(alpaca_created_at)s, %(alpaca_updated_at)s, %(synced_at)s
        )
        ON CONFLICT (id, symbol) DO UPDATE SET
            name = EXCLUDED.name,
            account_id = EXCLUDED.account_id,
            asset_id = EXCLUDED.asset_id,
            asset_class = EXCLUDED.asset_class,
            exchange = EXCLUDED.exchange,
            asset_name = EXCLUDED.asset_name,
            status = EXCLUDED.status,
            tradable = EXCLUDED.tradable,
            marginable = EXCLUDED.marginable,
            shortable = EXCLUDED.shortable,
            easy_to_borrow = EXCLUDED.easy_to_borrow,
            fractionable = EXCLUDED.fractionable,
            alpaca_created_at = EXCLUDED.alpaca_created_at,
            alpaca_updated_at = EXCLUDED.alpaca_updated_at,
            synced_at = EXCLUDED.synced_at
    """
    with conn.cursor() as cur:
        cur.executemany(sql, rows)
    logger.info("watchlist_rows_upserted", count=len(rows))
    return len(rows)


def delete_stale_rows(
    conn,
    alpaca_keys: set[tuple[str, str]],
    db_keys: set[tuple[str, str]],
) -> int:
    """Delete rows (id, symbol) present in our DB but no longer in Alpaca.

    `db_keys` is the current DB state — passed in by the caller (which already
    fetched it for the add/update diff) so we avoid a second full-table scan.
    If alpaca_keys is empty (Alpaca returned zero entries), deletes every row
    with a warning. Does NOT commit; the caller owns the transaction.
    Returns number of rows deleted.
    """
    if not alpaca_keys:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM market.watchlist")
            deleted = cur.rowcount
        logger.warning("watchlist_truncated", reason="alpaca_returned_zero_entries", deleted=deleted)
        return deleted

    to_delete = db_keys - alpaca_keys
    if not to_delete:
        return 0

    # Build parameterized DELETE for stale rows (values bound, not interpolated)
    delete_sql = "DELETE FROM market.watchlist WHERE (id, symbol) IN (%s)" % \
        ",".join(["(%s, %s)"] * len(to_delete))
    flat_values = [val for pair in to_delete for val in pair]
    with conn.cursor() as cur:
        cur.execute(delete_sql, flat_values)
        deleted = cur.rowcount
    logger.info("watchlist_stale_rows_deleted", count=deleted)
    return deleted


def get_current_watchlist_keys(conn) -> set[tuple[str, str]]:
    """Return set of (id, symbol) tuples currently in our DB."""
    with conn.cursor() as cur:
        cur.execute("SELECT id, symbol FROM market.watchlist")
        return {(str(row[0]), row[1]) for row in cur.fetchall()}