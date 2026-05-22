-- Backtest greeks strategy validation tables
-- Adds greeks-specific columns to backtest_trades and a new backtest_greeks_summary table

-- Add greeks columns to existing backtest_trades
ALTER TABLE trading.backtest_trades
  ADD COLUMN IF NOT EXISTS trade_mode VARCHAR DEFAULT 'swing',
  ADD COLUMN IF NOT EXISTS option_delta NUMERIC,
  ADD COLUMN IF NOT EXISTS option_theta NUMERIC,
  ADD COLUMN IF NOT EXISTS option_theta_pct NUMERIC,  -- |theta|/mid
  ADD COLUMN IF NOT EXISTS option_vega NUMERIC,
  ADD COLUMN IF NOT EXISTS option_gamma NUMERIC,
  ADD COLUMN IF NOT EXISTS iv_rank_at_entry NUMERIC,
  ADD COLUMN IF NOT EXISTS iv_regime_at_entry VARCHAR,
  ADD COLUMN IF NOT EXISTS delta_band_min NUMERIC,
  ADD COLUMN IF NOT EXISTS delta_band_max NUMERIC,
  ADD COLUMN IF NOT EXISTS theta_budget NUMERIC,
  ADD COLUMN IF NOT EXISTS greeks_pass BOOLEAN DEFAULT TRUE,
  ADD COLUMN IF NOT EXISTS greeks_fail_reasonS TEXT[];

-- Per-run greeks summary: aggregate stats by trade_mode
CREATE TABLE IF NOT EXISTS trading.backtest_greeks_summary (
  id          BIGSERIAL PRIMARY KEY,
  run_id      BIGINT NOT NULL REFERENCES trading.backtest_runs(id),
  trade_mode  VARCHAR NOT NULL,  -- day, swing, long_term
  total_signals  INT DEFAULT 0,
  greeks_passed  INT DEFAULT 0,  -- signals that passed all greeks gates
  greeks_failed  INT DEFAULT 0,  -- signals rejected by greeks gates
  delta_rejects  INT DEFAULT 0,   -- |delta| outside band
  theta_rejects  INT DEFAULT 0,   -- theta budget exceeded
  iv_regime_rejects INT DEFAULT 0, -- sell_premium regime
  iv_outlier_rejects INT DEFAULT 0, -- IV spike
  avg_delta   NUMERIC,
  avg_theta_pct NUMERIC,
  avg_iv_rank NUMERIC,
  win_rate    NUMERIC,            -- of greeks-passed trades
  avg_r_multiple NUMERIC,
  profit_factor NUMERIC,
  notes       TEXT,
  created_at  TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(run_id, trade_mode)
);

-- Index for fast mode-based lookups
CREATE INDEX IF NOT EXISTS idx_backtest_trades_mode ON trading.backtest_trades(trade_mode);
CREATE INDEX IF NOT EXISTS idx_backtest_greeks_summary_run ON trading.backtest_greeks_summary(run_id);