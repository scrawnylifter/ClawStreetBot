-- ClawStreetBot Phase 2: Composite signal scoring columns
-- Extends trading.signals with the per-component breakdown produced by
-- scripts/generate_signals.py so signals can be filtered/ranked in SQL
-- without unpacking the JSONB metadata blob.

ALTER TABLE trading.signals
    ADD COLUMN IF NOT EXISTS symbol              VARCHAR(20),
    ADD COLUMN IF NOT EXISTS composite_score     NUMERIC(6,2),
    ADD COLUMN IF NOT EXISTS iv_regime           VARCHAR(30),
    ADD COLUMN IF NOT EXISTS iv_regime_score     NUMERIC(6,2),
    ADD COLUMN IF NOT EXISTS iv_rv_spread        NUMERIC(12,6),
    ADD COLUMN IF NOT EXISTS iv_rv_score         NUMERIC(6,2),
    ADD COLUMN IF NOT EXISTS gex_score           NUMERIC(6,2),
    ADD COLUMN IF NOT EXISTS tech_score          NUMERIC(6,2),
    ADD COLUMN IF NOT EXISTS sentiment_score     NUMERIC(6,2),
    ADD COLUMN IF NOT EXISTS iv_outlier_flag     BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS iv_outlier_score    NUMERIC(6,2),
    ADD COLUMN IF NOT EXISTS recommended_strategy VARCHAR(40),
    ADD COLUMN IF NOT EXISTS details             JSONB,
    ADD COLUMN IF NOT EXISTS signal_date         DATE;

CREATE UNIQUE INDEX IF NOT EXISTS signals_symbol_date_uniq
    ON trading.signals(symbol, signal_date);
CREATE INDEX IF NOT EXISTS idx_signals_symbol_date
    ON trading.signals(symbol, signal_date DESC);
CREATE INDEX IF NOT EXISTS idx_signals_composite
    ON trading.signals(composite_score DESC);
