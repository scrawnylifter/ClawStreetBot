-- Alpaca options snapshot fields for market.greeks.
-- bid / ask already exist from the Polygon era; ADD COLUMN IF NOT EXISTS keeps
-- this migration safe to re-run. IV continues to use the existing `iv` column.

ALTER TABLE market.greeks
    ADD COLUMN IF NOT EXISTS bid NUMERIC(10,4),
    ADD COLUMN IF NOT EXISTS ask NUMERIC(10,4);
