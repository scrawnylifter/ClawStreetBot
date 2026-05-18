-- Add 'skipped' to the signal_alerts status enum.
--
-- When execute_trade.py's preflight gates block an approved signal
-- (drawdown halt, stale signal, qty zero, etc.) the signal needs to
-- move out of 'approved' so it doesn't get re-picked by the next
-- cron tick. Previously these signals just stayed 'approved' and
-- got re-processed every minute, generating 130+ duplicate
-- Telegram notifications.
--
-- 'skipped' is a terminal status like 'error' — the signal was
-- approved by the user but couldn't execute due to a hard gate.
-- Unlike 'error', 'skipped' is deterministic (the gate will keep
-- blocking) so retry makes no sense.
--
-- Lifecycle becomes:
--   new → (approved|denied|expired)
--   approved → (executing|skipped)
--   executing → (filled|error)
--   filled → exited

ALTER TABLE market.signal_alerts
    DROP CONSTRAINT IF EXISTS signal_alerts_status_check;
ALTER TABLE market.signal_alerts
    ADD CONSTRAINT signal_alerts_status_check
    CHECK (status IN (
        'new', 'pending', 'approved', 'denied',
        'executing', 'filled', 'exited', 'expired', 'error',
        'skipped'
    ));

COMMENT ON CONSTRAINT signal_alerts_status_check ON market.signal_alerts IS
    'Lifecycle: new → (approved|denied|expired) → (executing|skipped) → (filled|error) → exited. Skipped = approved but blocked by hard gate (drawdown, stale, etc). Terminal — no retry.';