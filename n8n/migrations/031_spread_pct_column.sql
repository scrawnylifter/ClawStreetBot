-- Migration 031 — spread_pct column on market.signal_alerts
--
-- Persists the live bid-ask spread (as a fraction of mid) recorded at signal
-- time so the Telegram alert can surface it and downstream tools can audit
-- post-hoc whether the scanner-time spread filter was wide-open or tight
-- when the trade fired. Mirrors fetch_alpaca_snapshot.best_option.spread_pct
-- and the scripts.constants.MAX_SPREAD_PCT gate (0.15 today).
--
-- Nullable: scanners that don't enrich live options (e.g. liquidity sweep on
-- stock-only setups) leave it NULL; the alert formatter and preflight gate
-- both handle NULL as "unknown, don't red-flag".

ALTER TABLE market.signal_alerts
    ADD COLUMN IF NOT EXISTS spread_pct numeric;

COMMENT ON COLUMN market.signal_alerts.spread_pct IS
    'Option bid-ask spread as a fraction of mid at signal time. '
    'NULL if no live quote was captured. Hard cap enforced at '
    'scripts.constants.MAX_SPREAD_PCT (0.15) in both the scanner and preflight.';
