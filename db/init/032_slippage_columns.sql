-- Track actual fill quality vs signal-time price on every signal_alerts row.
--
-- fill_price       — actual avg fill price from Alpaca (entry_price on the
--                    position row). For option signals this is the option
--                    premium; for stock signals it is the underlying price.
-- slippage_pct     — (fill_price - option_mid) / option_mid * 100. For stock
--                    signals the denominator is trigger_price. Positive =
--                    paid more than the signal-time mid (cost us money on a
--                    long); negative = filled better than mid.
-- slippage_dollars — (fill_price - option_mid) * quantity. Sign matches
--                    slippage_pct.
--
-- Populated by reconcile_orders.py at the moment a BUY fills and by
-- scripts/track_slippage.py for historical backfill. Idempotent on re-run.

ALTER TABLE market.signal_alerts
    ADD COLUMN IF NOT EXISTS fill_price       DECIMAL(12, 6);
ALTER TABLE market.signal_alerts
    ADD COLUMN IF NOT EXISTS slippage_pct     DECIMAL(8, 4);
ALTER TABLE market.signal_alerts
    ADD COLUMN IF NOT EXISTS slippage_dollars DECIMAL(10, 4);

COMMENT ON COLUMN market.signal_alerts.fill_price IS
    'Actual avg fill price (option premium for option signals, underlying for stock). Mirrors trading.positions.entry_price.';
COMMENT ON COLUMN market.signal_alerts.slippage_pct IS
    'Percent slippage vs signal-time mid: (fill_price - reference_price) / reference_price * 100. Positive = paid more than mid.';
COMMENT ON COLUMN market.signal_alerts.slippage_dollars IS
    'Dollar slippage vs signal-time mid: (fill_price - reference_price) * quantity.';
