-- Phase 5B: TP1 partial exit wiring (H5)
--
-- 021_position_exit_columns added tp1_hit_at as a sticky flag but the actual
-- partial SELL was deferred to a follow-up slice. These columns wire the
-- full partial-fill flow:
--
--   tp1_sell_order_id   Alpaca order id of the 50% partial SELL. While
--                       this is NOT NULL, exit_monitor skips the row
--                       (partial in flight) and reconcile_exits polls
--                       Alpaca for the fill.
--   tp1_filled_qty      Quantity actually filled at TP1. Subtracted from
--                       trading.positions.quantity by reconcile_exits.
--   tp1_filled_avg      Average fill price of the partial.
--   tp1_filled_at       When the partial fill was recorded (UTC).
--   tp1_realized_pnl    Realized P&L from the partial. Distinct from
--                       positions.realized_pnl which only records the
--                       final full-close P&L.

ALTER TABLE trading.positions
    ADD COLUMN IF NOT EXISTS tp1_sell_order_id  VARCHAR(50),
    ADD COLUMN IF NOT EXISTS tp1_filled_qty     NUMERIC,
    ADD COLUMN IF NOT EXISTS tp1_filled_avg     NUMERIC,
    ADD COLUMN IF NOT EXISTS tp1_filled_at      TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS tp1_realized_pnl   NUMERIC;

-- Fast path for the partial reconciler. Tiny partial index — only rows with
-- a partial SELL still in flight match.
CREATE INDEX IF NOT EXISTS idx_positions_tp1_partial_in_flight
    ON trading.positions(id)
    WHERE tp1_sell_order_id IS NOT NULL AND tp1_filled_at IS NULL;

COMMENT ON COLUMN trading.positions.tp1_sell_order_id IS
    'Alpaca order id for the TP1 50% partial SELL. NOT NULL ⇒ partial in flight; exit_monitor skips and reconcile_exits polls.';
COMMENT ON COLUMN trading.positions.tp1_filled_qty IS
    'Quantity filled at TP1 (subtracted from positions.quantity by reconcile_exits on fill).';
COMMENT ON COLUMN trading.positions.tp1_filled_avg IS
    'Average fill price of the TP1 partial.';
COMMENT ON COLUMN trading.positions.tp1_filled_at IS
    'When the TP1 partial fill was recorded (UTC). Distinct from tp1_hit_at, which is set when the underlying first reached TP1.';
COMMENT ON COLUMN trading.positions.tp1_realized_pnl IS
    'Realized P&L from the TP1 partial fill. positions.realized_pnl records the final full-close P&L; total P&L = tp1_realized_pnl + realized_pnl.';
