-- ClawStreetBot: Liquidity strategy backtest tables
-- Stores liquidity-sweep / FVG backtest runs and per-trade results so the
-- 5m liquidity strategy can be benchmarked alongside the existing engine.

CREATE SCHEMA IF NOT EXISTS trading;

CREATE TABLE IF NOT EXISTS trading.backtest_liquidity_runs (
    id                  BIGSERIAL PRIMARY KEY,
    run_name            VARCHAR(160) NOT NULL,
    mode                VARCHAR(20) NOT NULL CHECK (mode IN ('all','sweep','fvg')),
    start_date          DATE NOT NULL,
    end_date            DATE NOT NULL,
    initial_capital     NUMERIC(18,2) NOT NULL,
    final_capital       NUMERIC(18,2),
    symbols             VARCHAR(50)[],
    params              JSONB,
    notes               TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(run_name)
);

CREATE INDEX IF NOT EXISTS idx_btliq_runs_mode_dates
    ON trading.backtest_liquidity_runs(mode, start_date, end_date);

CREATE TABLE IF NOT EXISTS trading.backtest_liquidity_trades (
    id                  BIGSERIAL PRIMARY KEY,
    run_id              BIGINT NOT NULL REFERENCES trading.backtest_liquidity_runs(id) ON DELETE CASCADE,
    asset_id            INTEGER REFERENCES market.assets(id),
    symbol              VARCHAR(20) NOT NULL,
    direction           VARCHAR(10) NOT NULL CHECK (direction IN ('long','short')),
    entry_kind          VARCHAR(20) NOT NULL CHECK (entry_kind IN ('sweep','fvg')),
    liquidity_kind      VARCHAR(30) NOT NULL,
    liquidity_level     NUMERIC(18,6) NOT NULL,
    sweep_timestamp     TIMESTAMPTZ NOT NULL,
    entry_timestamp     TIMESTAMPTZ NOT NULL,
    entry_price         NUMERIC(18,6) NOT NULL,
    quantity            NUMERIC(18,6) NOT NULL,
    stop_loss           NUMERIC(18,6) NOT NULL,
    tp1_price           NUMERIC(18,6) NOT NULL,
    tp2_price           NUMERIC(18,6) NOT NULL,
    atr_at_entry        NUMERIC(18,6),
    risk_per_share      NUMERIC(18,6) NOT NULL,
    capital_at_entry    NUMERIC(18,2) NOT NULL,
    exit_timestamp      TIMESTAMPTZ,
    exit_price          NUMERIC(18,6),
    exit_reason         VARCHAR(30),
    gross_pnl           NUMERIC(18,6),
    r_multiple          NUMERIC(10,4),
    hold_bars           INTEGER,
    partial_exits       JSONB,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(run_id, symbol, entry_timestamp, entry_kind, liquidity_level)
);

CREATE INDEX IF NOT EXISTS idx_btliq_trades_run
    ON trading.backtest_liquidity_trades(run_id);
CREATE INDEX IF NOT EXISTS idx_btliq_trades_symbol_ts
    ON trading.backtest_liquidity_trades(symbol, entry_timestamp);
