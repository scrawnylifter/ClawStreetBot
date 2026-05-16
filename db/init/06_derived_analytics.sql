-- ClawStreetBot Phase 2: Derived analytics tables
-- Technical indicators, Greeks filter results, IV outlier flags.

-- Technical indicators per symbol per day (1d timeframe)
CREATE TABLE IF NOT EXISTS market.technical_indicators (
    id              SERIAL PRIMARY KEY,
    symbol          VARCHAR(20) NOT NULL,
    date            DATE NOT NULL,
    ema_9           NUMERIC(18,6),
    ema_21          NUMERIC(18,6),
    ema_50          NUMERIC(18,6),
    ema_200         NUMERIC(18,6),
    rsi_14          NUMERIC(10,4),
    macd_line       NUMERIC(18,6),
    macd_signal     NUMERIC(18,6),
    macd_hist       NUMERIC(18,6),
    atr_14          NUMERIC(18,6),
    vwap            NUMERIC(18,6),
    bb_upper        NUMERIC(18,6),
    bb_middle       NUMERIC(18,6),
    bb_lower        NUMERIC(18,6),
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(symbol, date)
);
CREATE INDEX IF NOT EXISTS idx_tech_ind_symbol_date
    ON market.technical_indicators(symbol, date DESC);

-- Greeks filter output per symbol per contract per evaluation date
CREATE TABLE IF NOT EXISTS market.greeks_filter (
    id                  BIGSERIAL PRIMARY KEY,
    symbol              VARCHAR(20) NOT NULL,
    date                DATE NOT NULL,
    regime              VARCHAR(30) NOT NULL,    -- buy_premium / directional / spreads_cautious / sell_premium
    iv_rank             NUMERIC(6,2),
    iv_rv_spread        NUMERIC(12,6),
    contract_symbol     VARCHAR(30) NOT NULL,
    contract_type       CHAR(1) NOT NULL CHECK (contract_type IN ('C','P')),
    strike              NUMERIC(12,4),
    expiration          DATE,
    delta               NUMERIC(10,6),
    gamma               NUMERIC(10,6),
    theta               NUMERIC(10,6),
    vega                NUMERIC(10,6),
    iv                  NUMERIC(12,6),
    midpoint            NUMERIC(12,4),
    regime_notes        TEXT,
    passes_filter       BOOLEAN NOT NULL DEFAULT FALSE,
    rejection_reasons   TEXT[] DEFAULT ARRAY[]::TEXT[],
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(symbol, date, contract_symbol)
);
CREATE INDEX IF NOT EXISTS idx_greeks_filter_symbol_date
    ON market.greeks_filter(symbol, date DESC);
CREATE INDEX IF NOT EXISTS idx_greeks_filter_regime
    ON market.greeks_filter(regime);

-- IV outlier flags: rows recorded only when |z-score| > 3
CREATE TABLE IF NOT EXISTS market.iv_outliers (
    id              SERIAL PRIMARY KEY,
    symbol          VARCHAR(20) NOT NULL,
    date            DATE NOT NULL,
    current_iv      NUMERIC(12,6),
    iv_mean_1y      NUMERIC(12,6),
    iv_std_1y       NUMERIC(12,6),
    z_score         NUMERIC(10,4),
    direction       VARCHAR(4) NOT NULL CHECK (direction IN ('high','low')),
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(symbol, date)
);
CREATE INDEX IF NOT EXISTS idx_iv_outliers_symbol_date
    ON market.iv_outliers(symbol, date DESC);
CREATE INDEX IF NOT EXISTS idx_iv_outliers_direction
    ON market.iv_outliers(direction);
