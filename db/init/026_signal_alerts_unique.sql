-- Phase 5B: prevent double-fill phantom positions (M5)
--
-- reconcile_orders.py uses a plain SELECT to pull `status='executing'`
-- rows and then INSERTs into trading.positions for each fill. Two
-- overlapping 1-min cron runs would both see the same row, both call
-- insert_position, and both succeed — there was no UNIQUE on
-- signal_alerts.alpaca_order_id or signal_alerts.position_id, so the
-- second INSERT just produced a phantom open position that exit_monitor
-- would later try to flatten with a real SELL.
--
-- The script-side fix (FOR UPDATE SKIP LOCKED in fetch_executing) handles
-- the overlap case; these UNIQUE indexes are the belt-and-suspenders that
-- guarantee correctness even if someone reverts the lock or runs the
-- script manually alongside the cron.
--
-- Both indexes are partial (filtered by NOT NULL) so they don't constrain
-- rows that haven't reached the relevant lifecycle state yet.

CREATE UNIQUE INDEX IF NOT EXISTS signal_alerts_alpaca_order_id_uq
    ON market.signal_alerts(alpaca_order_id)
    WHERE alpaca_order_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS signal_alerts_position_id_uq
    ON market.signal_alerts(position_id)
    WHERE position_id IS NOT NULL;

COMMENT ON INDEX market.signal_alerts_alpaca_order_id_uq IS
    'M5: exactly one signal_alerts row per Alpaca order. Prevents duplicate INSERTs if reconcile_orders runs are interleaved.';
COMMENT ON INDEX market.signal_alerts_position_id_uq IS
    'M5: exactly one signal_alerts row per trading.positions row. Phantom-position guard.';
