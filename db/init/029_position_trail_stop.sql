-- Phase 5C audit follow-up: trailing stop after TP2 for swing mode.
--
-- CLAUDE.md documents the swing exit ladder as
--   "30% first target, 50% second target, trail remaining"
-- but exit_monitor.decide_exit currently treats TP2 the same for every
-- mode: ACTION_FULL_CLOSE. The swing setup loses the trail upside — a
-- TP2 hit on a strong trend closes the runner at exactly TP2 instead of
-- letting it follow the move higher.
--
-- Storing the trail stop on the position is enough: exit_monitor reads
-- the live underlying, raises trail_stop_price monotonically (bullish)
-- / lowers it monotonically (bearish), and closes the position when the
-- underlying breaches it. Trail distance is recomputed each tick from
-- signal_alerts.atr_14 × 2 (matches the original swing SL distance),
-- with a fallback to the original (entry - stop_loss) gap if ATR is
-- missing on the signal.
--
-- NULL ⇒ trail not yet activated; the row is governed by the standard
-- stop / TP1 / TP2 ladder. Non-NULL ⇒ trail mode; the standard TP2
-- branch is bypassed and the only exit conditions are trail breach,
-- premium stop, time stop, and DTE expiry.

ALTER TABLE trading.positions
    ADD COLUMN IF NOT EXISTS trail_stop_price NUMERIC(18,6);

COMMENT ON COLUMN trading.positions.trail_stop_price IS
    'Trailing stop on the underlying for swing-mode positions after TP2 fires. NULL ⇒ trail not activated. Monotonically raised (bullish) or lowered (bearish) by exit_monitor each tick; underlying crossing it triggers ACTION_FULL_CLOSE with exit_reason=trail_stop.';
