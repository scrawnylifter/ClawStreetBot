-- Phase 5C audit follow-up: surface status='error' rows to the operator.
--
-- execute_trade.py and reconcile_orders.py both write status='error' +
-- error_message on broker submission failures, but nothing in the pipeline
-- reads those rows back. Failures sit in the DB until an operator runs an
-- ad-hoc query. Add a notification-tracking column so alert_telegram can
-- edit the original alert message (Telegram editMessageText) with the
-- failure reason and mark it sent — without re-posting the same error
-- every minute.

ALTER TABLE market.signal_alerts
    ADD COLUMN IF NOT EXISTS error_notified_at TIMESTAMPTZ;

COMMENT ON COLUMN market.signal_alerts.error_notified_at IS
    'When alert_telegram surfaced an error_message to Telegram. NULL ⇒ still pending notification; non-null ⇒ already edited the original message.';
