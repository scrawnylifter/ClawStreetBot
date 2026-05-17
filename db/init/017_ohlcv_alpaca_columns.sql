-- Add Alpaca-specific bar fields to market.ohlcv.
-- Alpaca bars include trade_count and VWAP; Polygon bars did not populate these.
-- Both columns are nullable so historical Polygon rows remain valid.

ALTER TABLE market.ohlcv
    ADD COLUMN IF NOT EXISTS trade_count BIGINT,
    ADD COLUMN IF NOT EXISTS vwap        NUMERIC(18,6);
