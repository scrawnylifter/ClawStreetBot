-- Add risk_mode column to signal_alerts for approval keyboard variants
-- standard = default sizing (5% day, 10% swing)
-- conservative = half sizing (2.5% day, 5% swing)
-- aggressive = double sizing (10% day, 20% swing)
ALTER TABLE market.signal_alerts
ADD COLUMN IF NOT EXISTS risk_mode varchar(16) DEFAULT 'standard';

-- Add risk_mode to the approval flow — set when user presses a button
COMMENT ON COLUMN market.signal_alerts.risk_mode IS 'Sizing mode chosen at approval: standard (default), conservative (half risk), aggressive (2x risk)';