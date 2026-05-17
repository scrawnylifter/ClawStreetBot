-- ClawStreetBot Phase 3: Backtest engine tables
-- Stores backtest runs, individual simulated trades, and aggregate metrics
-- so historical strategy performance can be queried and compared.

CREATE SCHEMA IF NOT EXISTS trading;

-- One row per backtest invocation
CREATE TABLE IF NOT EXISTS trading.backtest_runs (
    id                  BIGSERIAL PRIMARY KEY,
    run_name            VARCHAR(120) NOT NULL,
    strategy_mode       VARCHAR(20) NOT NULL CHECK (strategy_mode IN ('day','swing','long_term')),
    start_date          DATE NOT NULL,
    end_date            DATE NOT NULL,
    initial_capital     NUMERIC(18,2) NOT NULL,
    final_capital       NUMERIC(18,2),
    signal_threshold    NUMERIC(6,2),
    symbols             VARCHAR(50)[],
    params              JSONB,
    notes               TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(run_name)
);

CREATE INDEX IF NOT EXISTS idx_backtest_runs_mode_dates
    ON trading.backtest_runs(strategy_mode, start_date, end_date);

-- One row per simulated trade
CREATE TABLE IF NOT EXISTS trading.backtest_trades (
    id                  BIGSERIAL PRIMARY KEY,
    run_id              BIGINT NOT NULL REFERENCES trading.backtest_runs(id) ON DELETE CASCADE,
    symbol              VARCHAR(20) NOT NULL,
    signal_date         DATE NOT NULL,
    composite_score     NUMERIC(6,2),
    iv_regime           VARCHAR(30),
    direction           VARCHAR(10) NOT NULL CHECK (direction IN ('long','short')),
    entry_date          DATE NOT NULL,
    entry_price         NUMERIC(18,6) NOT NULL,
    quantity            NUMERIC(18,6) NOT NULL,
    stop_loss           NUMERIC(18,6),
    tp1_price           NUMERIC(18,6),
    tp2_price           NUMERIC(18,6),
    atr_at_entry        NUMERIC(18,6),
    risk_per_share      NUMERIC(18,6),
    capital_at_entry    NUMERIC(18,2),
    exit_date           DATE,
    exit_price          NUMERIC(18,6),
    exit_reason         VARCHAR(30),
    gross_pnl           NUMERIC(18,6),
    net_pnl             NUMERIC(18,6),
    r_multiple          NUMERIC(10,4),
    hold_days           INTEGER,
    partial_exits       JSONB,
    pdt_flag            BOOLEAN DEFAULT FALSE,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(run_id, symbol, signal_date)
);

CREATE INDEX IF NOT EXISTS idx_backtest_trades_run
    ON trading.backtest_trades(run_id);
CREATE INDEX IF NOT EXISTS idx_backtest_trades_symbol_date
    ON trading.backtest_trades(symbol, signal_date);

-- Aggregate metrics per run
CREATE TABLE IF NOT EXISTS trading.backtest_metrics (
    run_id              BIGINT PRIMARY KEY REFERENCES trading.backtest_runs(id) ON DELETE CASCADE,
    total_trades        INTEGER NOT NULL DEFAULT 0,
    winners             INTEGER NOT NULL DEFAULT 0,
    losers              INTEGER NOT NULL DEFAULT 0,
    win_rate            NUMERIC(6,4),
    avg_win             NUMERIC(18,6),
    avg_loss            NUMERIC(18,6),
    avg_r_multiple      NUMERIC(10,4),
    profit_factor       NUMERIC(10,4),
    expectancy          NUMERIC(18,6),
    total_return        NUMERIC(18,6),
    cagr                NUMERIC(10,6),
    sharpe              NUMERIC(10,4),
    max_drawdown        NUMERIC(10,6),
    pdt_violations      INTEGER NOT NULL DEFAULT 0,
    computed_at         TIMESTAMPTZ DEFAULT NOW()
);
