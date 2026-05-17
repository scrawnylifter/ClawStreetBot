-- Add composite_score column to signal_alerts
-- Previously computed at runtime but not persisted, so alerts showed 0.0
ALTER TABLE market.signal_alerts
ADD COLUMN IF NOT EXISTS composite_score numeric(5,2);