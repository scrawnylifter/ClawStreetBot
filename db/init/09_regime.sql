-- ClawStreetBot Phase 4: Market regime classification + regime-conditional scoring.
--
-- Adds:
--   * market.regime                    daily SPY/VIX/breadth-based regime labels
--   * trading.regime_weights           per-regime composite scoring weights
--   * trading.regime_factor_analysis   per-regime factor-to-forward-return correlations
--
-- All keys are deduped so the regime scripts can re-run idempotently.

CREATE SCHEMA IF NOT EXISTS market;
CREATE SCHEMA IF NOT EXISTS trading;

-- Daily market regime classification.
-- regime ∈ {bull, bear, transition}
-- spy_trend ∈ {golden_cross, death_cross, neutral}
-- vix_level ∈ {calm, elevated, panic}   (calm <18, 18-30 elevated, >30 panic)
-- breadth_proxy is the fraction (0-1) of sector-proxy symbols above their 50d SMA.
CREATE TABLE IF NOT EXISTS market.regime (
    date            DATE PRIMARY KEY,
    regime          VARCHAR(20) NOT NULL CHECK (regime IN ('bull','bear','transition')),
    spy_trend       VARCHAR(20) NOT NULL CHECK (spy_trend IN ('golden_cross','death_cross','neutral')),
    spy_sma_50      NUMERIC(18,6),
    spy_sma_200     NUMERIC(18,6),
    spy_close       NUMERIC(18,6),
    vix_level       VARCHAR(20) NOT NULL CHECK (vix_level IN ('calm','elevated','panic','unknown')),
    vix_value       NUMERIC(10,4),
    breadth_proxy   NUMERIC(6,4),
    details         JSONB,
    computed_at     TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_market_regime_regime
    ON market.regime(regime);

-- Per-regime composite-scoring weights. Six factors, default mirrors generate_signals.py.
-- A row exists per (regime, scheme) so we can store the static baseline and the optimized set side by side.
CREATE TABLE IF NOT EXISTS trading.regime_weights (
    regime              VARCHAR(20) NOT NULL CHECK (regime IN ('bull','bear','transition')),
    scheme              VARCHAR(30) NOT NULL DEFAULT 'optimized',
    iv_regime_weight    NUMERIC(6,2) NOT NULL,
    gex_weight          NUMERIC(6,2) NOT NULL,
    tech_weight         NUMERIC(6,2) NOT NULL,
    iv_rv_weight        NUMERIC(6,2) NOT NULL,
    sentiment_weight    NUMERIC(6,2) NOT NULL,
    outlier_weight      NUMERIC(6,2) NOT NULL,
    sample_size         INTEGER,
    notes               TEXT,
    computed_at         TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (regime, scheme)
);

-- Per-regime correlation of each factor score to forward returns.
-- horizon_days ∈ {5, 20}.
CREATE TABLE IF NOT EXISTS trading.regime_factor_analysis (
    regime          VARCHAR(20) NOT NULL CHECK (regime IN ('bull','bear','transition')),
    factor          VARCHAR(30) NOT NULL CHECK (factor IN (
                        'iv_regime','gex','tech','iv_rv','sentiment','outlier','composite')),
    horizon_days    INTEGER NOT NULL CHECK (horizon_days IN (5, 20)),
    correlation     NUMERIC(8,5),
    sample_size     INTEGER NOT NULL,
    avg_return      NUMERIC(10,6),
    win_rate        NUMERIC(6,4),
    computed_at     TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (regime, factor, horizon_days)
);

-- Seed the static baseline weights so regime_backtest can fall back to them.
INSERT INTO trading.regime_weights
    (regime, scheme, iv_regime_weight, gex_weight, tech_weight,
     iv_rv_weight, sentiment_weight, outlier_weight, notes)
VALUES
    ('bull',       'static', 25, 20, 20, 15, 10, 10, 'Baseline weights from generate_signals.py'),
    ('bear',       'static', 25, 20, 20, 15, 10, 10, 'Baseline weights from generate_signals.py'),
    ('transition', 'static', 25, 20, 20, 15, 10, 10, 'Baseline weights from generate_signals.py')
ON CONFLICT (regime, scheme) DO NOTHING;
