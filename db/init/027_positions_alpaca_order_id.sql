-- Phase 5C audit follow-up: link trading.positions back to the originating
-- Alpaca BUY order.
--
-- Until now the only path from a position row to its broker order id was via
-- market.signal_alerts.alpaca_order_id (joined on signal_alerts.position_id =
-- positions.id). That makes audit, recovery, and manual reconciliation
-- fragile — if a signal_alerts row is ever corrupted or hand-modified the
-- position is effectively orphaned from its broker fill. Store the order id
-- directly on the position so the link is independent of signal_alerts.

ALTER TABLE trading.positions
    ADD COLUMN IF NOT EXISTS alpaca_order_id VARCHAR(50);

-- Partial UNIQUE: at most one trading.positions row per Alpaca BUY order id.
-- Mirrors the same defense already in place on signal_alerts.alpaca_order_id
-- (migration 026), so a duplicate reconcile_orders run that bypasses the
-- FOR UPDATE SKIP LOCKED guard fails closed instead of silently creating a
-- phantom position.
CREATE UNIQUE INDEX IF NOT EXISTS positions_alpaca_order_id_uq
    ON trading.positions(alpaca_order_id)
    WHERE alpaca_order_id IS NOT NULL;

COMMENT ON COLUMN trading.positions.alpaca_order_id IS
    'Alpaca order id of the BUY that opened this position. Populated by reconcile_orders.insert_position; UNIQUE when non-null to block duplicate reconciliation.';
