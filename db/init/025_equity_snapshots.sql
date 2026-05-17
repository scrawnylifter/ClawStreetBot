-- Phase 5B: Daily account equity snapshots for drawdown halts (H8)
--
-- Before this, calc_drawdown() in process_approved.py summed realized P&L
-- only and divided by *current* equity. That had two bugs:
--   1. Current equity in the denominator shrinks as the account loses,
--      so the computed fraction understates the loss.
--   2. Unrealized P&L on open positions was completely ignored — an open
--      position down 15% intraday wouldn't trigger any halt until it closed.
--
-- The correct formula is:
--   drawdown_fraction = (current_equity - start_of_period_equity)
--                       / start_of_period_equity
-- where current_equity is the live Alpaca paper account equity (which
-- already includes both realized P&L and unrealized P&L on open positions)
-- and start_of_period_equity is the snapshot at the beginning of the
-- relevant window (day / week / month).
--
-- scripts/snapshot_equity.py records one row per calendar date. The
-- function reads:
--   daily_start   = latest snapshot where snapshot_date < CURRENT_DATE
--   weekly_start  = latest snapshot where snapshot_date < date_trunc('week', CURRENT_DATE)
--   monthly_start = latest snapshot where snapshot_date < date_trunc('month', CURRENT_DATE)
-- and divides accordingly. Periods with no snapshot return None and
-- preflight surfaces a WARN rather than mistakenly clearing the halt.

CREATE TABLE IF NOT EXISTS market.equity_snapshots (
    snapshot_date DATE          PRIMARY KEY,
    equity        NUMERIC(14,2) NOT NULL,
    recorded_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

-- Cheap to maintain — one row per day — and the lookups are
-- (snapshot_date < boundary ORDER BY snapshot_date DESC LIMIT 1), which
-- the descending index serves directly.
CREATE INDEX IF NOT EXISTS idx_equity_snapshots_date_desc
    ON market.equity_snapshots(snapshot_date DESC);

COMMENT ON TABLE market.equity_snapshots IS
    'Daily Alpaca paper account equity snapshots. Populated by scripts/snapshot_equity.py via cron. Consumed by process_approved.calc_drawdown for the drawdown-halt denominator.';
COMMENT ON COLUMN market.equity_snapshots.snapshot_date IS
    'Calendar date of the snapshot (one row per day).';
COMMENT ON COLUMN market.equity_snapshots.equity IS
    'Account equity at snapshot time (cash + unrealized position value, per Alpaca).';
COMMENT ON COLUMN market.equity_snapshots.recorded_at IS
    'Wall-clock timestamp the row was written. If the script is re-run on the same date, recorded_at updates and equity is replaced.';
