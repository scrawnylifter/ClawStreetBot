-- Signal quality decomposition table for --signal-quality backtest mode
-- Stores per-signal MFE/MAE, signal correctness, exit capture, and decomposition data

CREATE TABLE IF NOT EXISTS trading.backtest_signal_quality (
    id          BIGSERIAL PRIMARY KEY,
    run_id      BIGINT NOT NULL REFERENCES trading.backtest_runs(id),
    symbol      TEXT NOT NULL,
    signal_date DATE NOT NULL,
    direction   TEXT NOT NULL,        -- long / short
    trade_mode  TEXT NOT NULL,        -- day, swing, long_term
    atr         DOUBLE PRECISION,
    -- Signal accuracy (directional correctness, no exit logic)
    mfe_atr     DOUBLE PRECISION,     -- max favorable excursion in ATR units
    mfe_price   DOUBLE PRECISION,    -- max favorable excursion in price
    mfe_day     INT,                  -- day offset from entry when MFE occurred
    mae_atr     DOUBLE PRECISION,    -- max adverse excursion in ATR units
    mae_price   DOUBLE PRECISION,    -- max adverse excursion in price
    mae_day     INT,                  -- day offset from entry when MAE occurred
    signal_correct  BOOLEAN,         -- MFE hit win threshold before MAE hit loss threshold
    signal_grade    TEXT,             -- "strong" | "marginal" | "wrong"
    -- Exit execution quality
    exit_pnl_atr    DOUBLE PRECISION, -- actual trade P&L in ATR units
    exit_reason     TEXT,             -- stop_loss, tp1, tp2, trail_stop, etc.
    exit_capture_pct DOUBLE PRECISION,-- (exit_pnl / MFE) * 100
    -- Greeks metadata
    greeks_pass        BOOLEAN,
    option_delta       DOUBLE PRECISION,
    option_theta_pct   DOUBLE PRECISION,
    iv_regime_at_entry TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(run_id, symbol, signal_date)
);

-- Indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_backtest_sq_run ON trading.backtest_signal_quality(run_id);
CREATE INDEX IF NOT EXISTS idx_backtest_sq_grade ON trading.backtest_signal_quality(signal_grade);
CREATE INDEX IF NOT EXISTS idx_backtest_sq_correct ON trading.backtest_signal_quality(signal_correct);