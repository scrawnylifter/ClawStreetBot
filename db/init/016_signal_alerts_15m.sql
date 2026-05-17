-- Phase 5A: Add 15m timeframe support to signal_alerts
-- Allows distinguishing daily EMA crosses from 15m intraday crosses

ALTER TABLE market.signal_alerts ADD COLUMN IF NOT EXISTS timeframe VARCHAR(5) DEFAULT '1d';
ALTER TABLE market.signal_alerts ADD COLUMN IF NOT EXISTS daily_trend VARCHAR(10);
ALTER TABLE market.signal_alerts ADD COLUMN IF NOT EXISTS daily_ema_position VARCHAR(10);
ALTER TABLE market.signal_alerts ADD COLUMN IF NOT EXISTS intraday_ema_9 NUMERIC;
ALTER TABLE market.signal_alerts ADD COLUMN IF NOT EXISTS intraday_ema_21 NUMERIC;

-- Update unique constraint to include timeframe (same symbol+direction can have 1d and 15m signals)
ALTER TABLE market.signal_alerts DROP CONSTRAINT IF EXISTS signal_alerts_unique_signal;
CREATE UNIQUE INDEX IF NOT EXISTS signal_alerts_unique_signal
    ON market.signal_alerts (symbol, strategy, direction, timeframe, created_at);

COMMENT ON COLUMN market.signal_alerts.timeframe IS 'Chart timeframe: 1d for daily, 15m for intraday';
COMMENT ON COLUMN market.signal_alerts.daily_trend IS 'Daily EMA position (above/below) for 15m signals — confirms direction alignment';
COMMENT ON COLUMN market.signal_alerts.daily_ema_position IS 'Whether daily EMA9 is above or below EMA21 (trend filter)';
COMMENT ON COLUMN market.signal_alerts.intraday_ema_9 IS 'EMA 9 value on the intraday chart at time of signal';
COMMENT ON COLUMN market.signal_alerts.intraday_ema_21 IS 'EMA 21 value on the intraday chart at time of signal';