-- Backtest slice analytics — per-slice metrics grouped by regime, symbol, sector, or named period
-- Each row represents one "slice" of a backtest run, enabling multi-dimensional analysis

CREATE TABLE IF NOT EXISTS trading.backtest_slices (
  id BIGSERIAL PRIMARY KEY,
  run_id BIGINT NOT NULL REFERENCES trading.backtest_runs(id),
  slice_type VARCHAR NOT NULL,   -- 'regime', 'symbol', 'sector', 'period'
  slice_value VARCHAR NOT NULL,  -- 'bull', 'NVDA', 'Technology', 'iran_crisis'
  mode VARCHAR,                  -- 'day', 'swing', 'long_term'
  total_signals INT,
  greeks_passed INT,
  trades INT,
  winners INT,
  losers INT,
  win_rate NUMERIC,
  avg_r NUMERIC,
  profit_factor NUMERIC,
  avg_hold_days NUMERIC,
  max_dd_pct NUMERIC,
  total_pnl NUMERIC,
  params JSONB DEFAULT '{}',
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(run_id, slice_type, slice_value, mode)
);

-- Fast lookups by run and by slice type
CREATE INDEX IF NOT EXISTS idx_backtest_slices_run ON trading.backtest_slices(run_id);
CREATE INDEX IF NOT EXISTS idx_backtest_slices_type ON trading.backtest_slices(slice_type, slice_value);