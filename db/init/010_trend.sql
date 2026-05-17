-- ClawStreetBot Phase 5: Multi-timeframe trend detection for swing trading.
--
-- Adds:
--   * market.trend_status   per-symbol/per-day trend score + multi-timeframe labels
--
-- Trend score (0-100) combines:
--   EMA stack alignment (0-30) + ADX strength (0-25) +
--   price structure (0-25) + direction bonus (0-20)
--
-- Idempotent: PRIMARY KEY (symbol, date) so the compute script can re-run.

CREATE SCHEMA IF NOT EXISTS market;

CREATE TABLE IF NOT EXISTS market.trend_status (
    symbol              VARCHAR(20)  NOT NULL,
    date                DATE         NOT NULL,
    trend_score         NUMERIC(6,2) NOT NULL,
    micro_trend         VARCHAR(10)  NOT NULL CHECK (micro_trend         IN ('bull','bear','neutral')),
    intermediate_trend  VARCHAR(10)  NOT NULL CHECK (intermediate_trend  IN ('bull','bear','neutral')),
    primary_trend       VARCHAR(10)  NOT NULL CHECK (primary_trend       IN ('bull','bear','neutral')),
    adx                 NUMERIC(8,4),
    ema_stack           VARCHAR(20)  NOT NULL CHECK (ema_stack           IN ('aligned_bull','partial','aligned_bear')),
    price_structure     VARCHAR(20)  NOT NULL CHECK (price_structure     IN ('higher_highs','mixed','lower_lows')),
    trend_strength      VARCHAR(20)  NOT NULL CHECK (trend_strength      IN ('strong','moderate','weak','no_trend')),
    details             JSONB,
    computed_at         TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (symbol, date)
);

CREATE INDEX IF NOT EXISTS idx_trend_status_date         ON market.trend_status(date DESC);
CREATE INDEX IF NOT EXISTS idx_trend_status_score        ON market.trend_status(trend_score DESC);
CREATE INDEX IF NOT EXISTS idx_trend_status_intermediate ON market.trend_status(intermediate_trend, date DESC);
