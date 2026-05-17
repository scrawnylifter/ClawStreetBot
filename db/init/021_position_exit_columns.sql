-- Phase 5B: Position exit tracking
--
-- exit_monitor.py walks open positions and decides whether to flatten based
-- on the trade plan stored in market.signal_alerts (joined via position_id).
-- These columns let it record:
--
--   sell_order_id      the Alpaca order id of the SELL submitted to close.
--                      While this is NOT NULL, subsequent monitor runs skip
--                      the row (close in flight). A future reconcile_exits
--                      slice will flip status='closed' once the fill arrives.
--   exit_submitted_at  when the SELL was submitted (audit / staleness check).
--   exit_reason        which branch of the decision tree fired:
--                      stop | premium_stop | tp1_partial | tp2 | time_stop |
--                      expiry | manual
--   tp1_hit_at         sticky flag for TP1 partial exits so the position
--                      isn't half-closed twice. Currently set even though
--                      the partial action is deferred to a follow-up slice.

ALTER TABLE trading.positions
    ADD COLUMN IF NOT EXISTS tp1_hit_at         TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS exit_reason        VARCHAR(50),
    ADD COLUMN IF NOT EXISTS sell_order_id      VARCHAR(50),
    ADD COLUMN IF NOT EXISTS exit_submitted_at  TIMESTAMPTZ;

-- Fast path for the monitor's primary query: open positions with no close
-- in flight. Partial index keeps it cheap as the table grows.
CREATE INDEX IF NOT EXISTS idx_positions_monitor_queue
    ON trading.positions(id)
    WHERE status = 'open' AND sell_order_id IS NULL;

COMMENT ON COLUMN trading.positions.tp1_hit_at IS
    'Set when the underlying first reached tp1 and a partial exit fired. Acts as a one-shot guard for TP1 detection in exit_monitor.decide_exit, but reconcile_exits.clear_tp1_partial wipes it on a dead/canceled TP1 partial SELL so the monitor can re-detect TP1 on the next pass.';
COMMENT ON COLUMN trading.positions.exit_reason IS
    'Branch of the exit decision tree that closed (or partial-closed) this position.';
COMMENT ON COLUMN trading.positions.sell_order_id IS
    'Alpaca order id for the closing SELL. NOT NULL ⇒ close in flight; monitor skips.';
COMMENT ON COLUMN trading.positions.exit_submitted_at IS
    'When the closing SELL was submitted (UTC).';
