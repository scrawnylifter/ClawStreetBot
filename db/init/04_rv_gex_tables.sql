-- ClawStreetBot Phase 3: Realized volatility & GEX/DEX tables
-- RV, gamma exposure, delta exposure, and overview aggregates

-- Realized volatility per symbol per day
CREATE TABLE IF NOT EXISTS market.realized_vol (
    id              SERIAL PRIMARY KEY,
    symbol          VARCHAR(20) NOT NULL,
    date            DATE NOT NULL,
    rv_20d          NUMERIC(12,6),          -- 20-day annualized realized volatility
    rv_5d           NUMERIC(12,6),          -- 5-day annualized realized volatility
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(symbol, date)
);
CREATE INDEX IF NOT EXISTS idx_rv_symbol_date ON market.realized_vol(symbol, date DESC);

-- Gamma & delta exposure per strike per expiration per underlying per day
CREATE TABLE IF NOT EXISTS market.gex_dex (
    id              BIGSERIAL PRIMARY KEY,
    underlying      VARCHAR(10) NOT NULL,
    date            DATE NOT NULL,
    expiration      DATE NOT NULL,
    strike          NUMERIC(12,4) NOT NULL,
    call_gex        NUMERIC(20,4),          -- call gamma * OI * 100 * S^2
    put_gex         NUMERIC(20,4),          -- put gamma * OI * 100 * S^2
    net_gex         NUMERIC(20,4),          -- call_gex + put_gex (sign matters)
    call_dex        NUMERIC(20,4),          -- call delta * OI * 100 * S
    put_dex         NUMERIC(20,4),          -- put delta * OI * 100 * S
    net_dex         NUMERIC(20,4),          -- call_dex + put_dex (sign matters)
    call_oi         BIGINT,
    put_oi          BIGINT,
    total_oi        BIGINT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(underlying, date, expiration, strike)
);
CREATE INDEX IF NOT EXISTS idx_gex_dex_underlying_date ON market.gex_dex(underlying, date DESC);

-- GEX/DEX overview aggregate per underlying per day
CREATE TABLE IF NOT EXISTS market.gex_dex_overview (
    id              SERIAL PRIMARY KEY,
    underlying      VARCHAR(10) NOT NULL,
    date            DATE NOT NULL,
    total_call_gex  NUMERIC(20,4),
    total_put_gex   NUMERIC(20,4),
    total_net_gex   NUMERIC(20,4),
    total_call_dex  NUMERIC(20,4),
    total_put_dex   NUMERIC(20,4),
    total_net_dex   NUMERIC(20,4),
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(underlying, date)
);
CREATE INDEX IF NOT EXISTS idx_gex_dex_overview_underlying_date ON market.gex_dex_overview(underlying, date DESC);