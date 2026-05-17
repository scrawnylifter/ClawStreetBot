-- Phase 5B: Alert approval + execution lifecycle
--
-- Extends market.signal_alerts so a single row can carry the trade
-- through its full lifecycle:
--
--   new -> approved | denied -> executing -> filled -> exited
--          (denied / expired / error are terminal)
--
-- Existing columns reused:
--   status            VARCHAR(20)   state name (see values above)
--   user_action       VARCHAR(10)   'approved' | 'denied' (was 'rejected')
--   alpaca_order_id   VARCHAR(50)   set when order is submitted
--   telegram_sent     BOOLEAN       set by alert_telegram.py
--   telegram_msg_id   BIGINT        used to edit the message after approve/deny
--
-- New columns this migration adds.

ALTER TABLE market.signal_alerts
    ADD COLUMN IF NOT EXISTS approved_at        TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS denied_at          TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS executed_at        TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS position_id        INTEGER,
    ADD COLUMN IF NOT EXISTS approval_chat_id   BIGINT,
    ADD COLUMN IF NOT EXISTS approval_user_id   BIGINT,
    ADD COLUMN IF NOT EXISTS error_message      TEXT;

-- Wire position_id to trading.positions. Use NOT VALID to avoid scanning
-- existing rows (position_id is NULL for everything pre-migration).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'signal_alerts_position_id_fkey'
    ) THEN
        ALTER TABLE market.signal_alerts
            ADD CONSTRAINT signal_alerts_position_id_fkey
            FOREIGN KEY (position_id) REFERENCES trading.positions(id)
            NOT VALID;
    END IF;
END $$;

-- Normalize the existing 'rejected' wording to 'denied' to match the
-- Telegram button label and the rest of the lifecycle vocabulary.
UPDATE market.signal_alerts SET user_action = 'denied'
    WHERE user_action = 'rejected';
UPDATE market.signal_alerts SET status = 'denied'
    WHERE status = 'rejected';

-- Indexes for the listener + exit monitor.
-- Listener: looks up rows by id (PK already covers it) — no new index.
-- Exit monitor: scans 'approved' / 'executing' / 'filled' rows.
CREATE INDEX IF NOT EXISTS idx_signal_alerts_live
    ON market.signal_alerts(status)
    WHERE status IN ('approved', 'executing', 'filled');

-- Telegram message id lookup (when a callback fires we know msg_id, not row id).
CREATE INDEX IF NOT EXISTS idx_signal_alerts_telegram_msg
    ON market.signal_alerts(telegram_msg_id)
    WHERE telegram_msg_id IS NOT NULL;

COMMENT ON COLUMN market.signal_alerts.status IS
    'Lifecycle state: new -> approved|denied -> executing -> filled -> exited. Terminal: denied, expired, error.';
COMMENT ON COLUMN market.signal_alerts.user_action IS
    'User decision from Telegram: approved | denied. NULL until a button is tapped.';
COMMENT ON COLUMN market.signal_alerts.approved_at IS
    'When the approve button was tapped (NULL otherwise).';
COMMENT ON COLUMN market.signal_alerts.denied_at IS
    'When the deny button was tapped (NULL otherwise).';
COMMENT ON COLUMN market.signal_alerts.executed_at IS
    'When the Alpaca order was successfully submitted.';
COMMENT ON COLUMN market.signal_alerts.position_id IS
    'FK to trading.positions, set once the order fills and a position is recorded.';
COMMENT ON COLUMN market.signal_alerts.approval_chat_id IS
    'Telegram chat id that approved/denied (for audit + multi-chat support later).';
COMMENT ON COLUMN market.signal_alerts.approval_user_id IS
    'Telegram user id that approved/denied (for audit when chat is a group).';
COMMENT ON COLUMN market.signal_alerts.error_message IS
    'Populated when status=error: preflight failure, Alpaca rejection, etc.';
