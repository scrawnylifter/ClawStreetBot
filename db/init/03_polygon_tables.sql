-- ClawStreetBot Phase 2: Polygon.io data tables
-- Options contracts, greeks snapshots, IV rank, fundamentals

-- Options contracts metadata
CREATE TABLE IF NOT EXISTS market.options (
    id              SERIAL PRIMARY KEY,
    occ_symbol      VARCHAR(30) UNIQUE NOT NULL,        -- O:NVDA260619C00125000
    underlying      VARCHAR(10) NOT NULL,
    contract_type   CHAR(1) NOT NULL CHECK (contract_type IN ('C','P')),
    strike          NUMERIC(12,4) NOT NULL,
    expiration      DATE NOT NULL,
    exercise_style  VARCHAR(10) DEFAULT 'american',
    shares_per_contract INTEGER DEFAULT 100,
    cfi_code        VARCHAR(6),
    primary_exchange VARCHAR(20),
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_options_underlying ON market.options(underlying);
CREATE INDEX IF NOT EXISTS idx_options_expiration ON market.options(expiration);
CREATE INDEX IF NOT EXISTS idx_options_under_exp ON market.options(underlying, expiration);

-- Greeks snapshots (daily per contract)
CREATE TABLE IF NOT EXISTS market.greeks (
    id              BIGSERIAL PRIMARY KEY,
    occ_symbol      VARCHAR(30) NOT NULL REFERENCES market.options(occ_symbol),
    date            DATE NOT NULL,
    delta           NUMERIC(10,6),
    gamma           NUMERIC(10,6),
    theta           NUMERIC(10,6),
    vega            NUMERIC(10,6),
    rho             NUMERIC(10,6),
    vanna           NUMERIC(10,6),
    iv              NUMERIC(12,6),
    open_interest   BIGINT,
    bid             NUMERIC(12,4),
    ask             NUMERIC(12,4),
    midpoint        NUMERIC(12,4),
    last_price      NUMERIC(12,4),
    volume          BIGINT,
    vwap            NUMERIC(12,4),
    break_even      NUMERIC(12,4),
    underlying_price NUMERIC(12,4),
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(occ_symbol, date)
);
CREATE INDEX IF NOT EXISTS idx_greeks_symbol_date ON market.greeks(occ_symbol, date DESC);
CREATE INDEX IF NOT EXISTS idx_greeks_date ON market.greeks(date DESC);

-- IV Rank (derived from historical greeks)
CREATE TABLE IF NOT EXISTS market.iv_rank (
    id              SERIAL PRIMARY KEY,
    symbol          VARCHAR(10) NOT NULL,
    date            DATE NOT NULL,
    current_iv      NUMERIC(10,6),
    iv_rank_52w     NUMERIC(6,2),       -- 0-100 percentile
    iv_low_52w      NUMERIC(10,6),
    iv_high_52w     NUMERIC(10,6),
    iv_percentile   NUMERIC(6,2),
    iv_std_dev      NUMERIC(10,6),
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(symbol, date)
);
CREATE INDEX IF NOT EXISTS idx_iv_rank_symbol_date ON market.iv_rank(symbol, date DESC);

-- Fundamentals (financials snapshot)
CREATE TABLE IF NOT EXISTS market.fundamentals (
    id              SERIAL PRIMARY KEY,
    symbol          VARCHAR(10) NOT NULL,
    date            DATE NOT NULL,                     -- period end date
    period          VARCHAR(10),                       -- 'Q1','Q2','Q3','Q4','FY','TTM'
    fiscal_year     INTEGER,
    revenue         NUMERIC(20,2),
    net_income      NUMERIC(20,2),
    eps             NUMERIC(12,4),
    pe_ratio        NUMERIC(12,4),
    market_cap      NUMERIC(20,2),
    debt_to_equity  NUMERIC(12,4),
    free_cash_flow  NUMERIC(20,2),
    dividend_yield  NUMERIC(8,4),
    gross_profit    NUMERIC(20,2),
    operating_income NUMERIC(20,2),
    total_assets    NUMERIC(20,2),
    total_liabilities NUMERIC(20,2),
    shares_outstanding NUMERIC(20,2),
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(symbol, date, period)
);
CREATE INDEX IF NOT EXISTS idx_fundamentals_symbol_date ON market.fundamentals(symbol, date DESC);

-- Ingestion tracking: when did we last pull data for each (symbol, timeframe, source)?
CREATE TABLE IF NOT EXISTS market.ingest_state (
    id              SERIAL PRIMARY KEY,
    source          VARCHAR(30) NOT NULL,              -- 'polygon_ohlcv', 'polygon_options', etc.
    symbol          VARCHAR(20) NOT NULL,
    timeframe       VARCHAR(10),                       -- '1d','5m','15m', NULL for non-bar data
    last_timestamp  TIMESTAMPTZ,                       -- timestamp of latest bar ingested
    last_run_at     TIMESTAMPTZ DEFAULT NOW(),
    row_count       BIGINT DEFAULT 0,
    status          VARCHAR(20) DEFAULT 'ok',
    error_message   TEXT,
    UNIQUE(source, symbol, timeframe)
);
CREATE INDEX IF NOT EXISTS idx_ingest_state_lookup ON market.ingest_state(source, symbol, timeframe);
