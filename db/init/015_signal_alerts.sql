-- Signal alerts: stores detected trade setups for EMA, ORB, Dip strategies
-- Each alert is a complete trade decision with entry + exit rules
CREATE TABLE IF NOT EXISTS market.signal_alerts (
    id              SERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    symbol          VARCHAR(20) NOT NULL,
    strategy        VARCHAR(20) NOT NULL,  -- ema_crossover, orb_breakout, buy_dip
    direction       VARCHAR(10) NOT NULL,  -- bullish, bearish
    status          VARCHAR(20) DEFAULT 'new',  -- new, approved, rejected, expired, closed
    regime          VARCHAR(20),            -- bull, transition, bear (from market.regime)

    -- Entry context
    trigger_price   NUMERIC,                -- price at signal detection
    ema_9           NUMERIC,
    ema_21          NUMERIC,
    adx             NUMERIC,
    rsi             NUMERIC,
    atr_14          NUMERIC,
    volume_ratio    NUMERIC,                -- vs 20-day average

    -- Trade plan (swing: ATR×2.0 SL, 30%/50% TP)
    stop_price      NUMERIC,
    tp1_price       NUMERIC,               -- +30%
    tp2_price       NUMERIC,               -- +50%
    risk_reward      NUMERIC,               -- R:R ratio

    -- Trend context
    micro_trend     VARCHAR(10),
    intermediate_trend VARCHAR(10),
    primary_trend   VARCHAR(10),
    trend_score     NUMERIC,
    ema_stack       VARCHAR(20),

    -- Invalidation conditions (stored as JSON array)
    invalidation    JSONB,

    -- Best option contract (if available)
    option_symbol   VARCHAR(30),
    option_strike   NUMERIC,
    option_expiry   DATE,
    option_delta    NUMERIC,
    option_theta    NUMERIC,
    option_bid      NUMERIC,
    option_ask      NUMERIC,
    option_mid      NUMERIC,

    -- IV context
    iv_rank         NUMERIC,
    iv_rv_spread    NUMERIC,

    -- GEX context
    net_gex         NUMERIC,

    -- Alert delivery
    telegram_sent   BOOLEAN DEFAULT FALSE,
    telegram_msg_id  BIGINT,
    user_action     VARCHAR(10),           -- approved, rejected, None
    alpaca_order_id VARCHAR(50),

    -- Prevent duplicate signals
    UNIQUE(symbol, strategy, direction, created_at)
);

CREATE INDEX IF NOT EXISTS idx_signal_alerts_status ON market.signal_alerts(status);
CREATE INDEX IF NOT EXISTS idx_signal_alerts_symbol ON market.signal_alerts(symbol);
CREATE INDEX IF NOT EXISTS idx_signal_alerts_created ON market.signal_alerts(created_at DESC);

COMMENT ON TABLE market.signal_alerts IS 'Strategy-specific trade alerts with entry + exit plans. Populated by detect_*.py scripts, consumed by alert_telegram.py.';