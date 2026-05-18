-- Status enum hardening for market.signal_alerts and trading.positions.
--
-- Until now market.signal_alerts.status was a bare VARCHAR(20) with no
-- CHECK — every status transition in the codebase relies on string
-- literals matching what other scripts expect, with zero schema-level
-- safety net. A typo like 'exeucting' or 'fllied' would silently corrupt
-- the lifecycle state machine and the row would either stall forever or
-- get processed by the wrong cron. trading.positions.status already had
-- a CHECK from migration 002 (open|closed|cancelled), so this migration
-- only adds the missing one on signal_alerts.
--
-- Valid signal_alerts.status values, sourced by greping every UPDATE/
-- INSERT/WHERE clause across the lifecycle scripts:
--
--   new       — fresh row from a scanner, awaiting Telegram dispatch
--               (alert_telegram picks up status='new' AND telegram_sent=FALSE)
--   pending   — reserved (no script writes this; older lifecycle leftover)
--   approved  — user tapped Approve / Conservative / Aggressive in Telegram
--               (telegram_callback_listener writes this)
--   denied    — user tapped Deny in Telegram (terminal)
--   executing — execute_trade.lock_and_mark_executing flipped from approved,
--               Alpaca BUY submitted (or about to be submitted on retry)
--   filled    — reconcile_orders saw the BUY fill at Alpaca, position opened
--   exited    — reconcile_exits saw the SELL fill, position closed (terminal)
--   expired   — expire_stale_new flipped a status='new' row older than 24h
--               (alert_telegram's lifecycle prelude; terminal)
--   error     — execute_trade or reconcile_orders couldn't submit/fill
--               (alert_telegram.notify_errors surfaces these; terminal)
--
-- The legacy 015 comment mentioned 'rejected' and 'closed' as statuses —
-- those names were renamed early on to 'denied' and 'exited' respectively
-- and no live code uses the older names. They are NOT in the enum so a
-- mistaken UPDATE to 'rejected' will now fail loudly instead of stalling
-- silently.
--
-- ALTER … ADD CONSTRAINT will fail with the current rows if any of them
-- have an out-of-enum status. To handle that defensively, we COALESCE-
-- repair the obvious legacy values first (rejected → denied, closed →
-- exited), then add the constraint.

-- Step 1: repair any rows with legacy status values so the CHECK
-- constraint adds cleanly. Idempotent — no-op when none exist.
UPDATE market.signal_alerts SET status = 'denied' WHERE status = 'rejected';
UPDATE market.signal_alerts SET status = 'exited' WHERE status = 'closed';

-- Step 2: any remaining out-of-enum value is surprising and worth blocking
-- on. Surface it as an explicit error before ADD CONSTRAINT would raise a
-- less helpful one.
DO $$
DECLARE
    bad_count INTEGER;
    bad_values TEXT;
BEGIN
    SELECT COUNT(*), string_agg(DISTINCT status, ', ')
      INTO bad_count, bad_values
      FROM market.signal_alerts
     WHERE status NOT IN ('new', 'pending', 'approved', 'denied',
                          'executing', 'filled', 'exited', 'expired',
                          'error');
    IF bad_count > 0 THEN
        RAISE EXCEPTION 'Migration 030: % signal_alerts row(s) have unexpected status values: %. Repair these before applying the CHECK constraint.',
            bad_count, bad_values;
    END IF;
END $$;

-- Step 3: add the CHECK. NOT VALID would skip the existing-row check, but
-- we've already validated above, so a normal ADD is correct here.
ALTER TABLE market.signal_alerts
    DROP CONSTRAINT IF EXISTS signal_alerts_status_check;
ALTER TABLE market.signal_alerts
    ADD CONSTRAINT signal_alerts_status_check
    CHECK (status IN (
        'new', 'pending', 'approved', 'denied',
        'executing', 'filled', 'exited', 'expired', 'error'
    ));

COMMENT ON CONSTRAINT signal_alerts_status_check ON market.signal_alerts IS
    'Lifecycle states: new → (approved|denied|expired) → executing → (filled|error) → exited. Any typo fails fast at INSERT/UPDATE.';

-- Step 4: trading.positions.status already had a CHECK from migration 002
-- (open|closed|cancelled). Re-state it idempotently here so a future
-- ALTER TABLE that accidentally dropped the original doesn't leave the
-- column unconstrained.
ALTER TABLE trading.positions
    DROP CONSTRAINT IF EXISTS positions_status_check;
ALTER TABLE trading.positions
    ADD CONSTRAINT positions_status_check
    CHECK (status IN ('open', 'closed', 'cancelled'));

COMMENT ON CONSTRAINT positions_status_check ON trading.positions IS
    'Position lifecycle: open → (closed|cancelled). Cancelled is reserved for never-opened paths (e.g. broker rejected the BUY).';
