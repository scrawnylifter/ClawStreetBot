-- ClawStreetBot: Market data tables
-- Stored in the main 'clawstreet' database, 'market' schema

CREATE SCHEMA IF NOT EXISTS market;
CREATE SCHEMA IF NOT EXISTS scraper;
CREATE SCHEMA IF NOT EXISTS trading;

-- Market data: stocks, crypto, forex
CREATE TABLE IF NOT EXISTS market.assets (
    id              SERIAL PRIMARY KEY,
    symbol          VARCHAR(20) NOT NULL UNIQUE,
    name            VARCHAR(200),
    asset_type      VARCHAR(20) NOT NULL CHECK (asset_type IN ('stock', 'crypto', 'forex', 'etf', 'option', 'future')),
    exchange        VARCHAR(50),
    sector          VARCHAR(100),
    industry        VARCHAR(100),
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS market.ohlcv (
    id              BIGSERIAL PRIMARY KEY,
    asset_id        INTEGER NOT NULL REFERENCES market.assets(id),
    timeframe       VARCHAR(10) NOT NULL,
    timestamp       TIMESTAMPTZ NOT NULL,
    open            NUMERIC(18,6),
    high            NUMERIC(18,6),
    low             NUMERIC(18,6),
    close           NUMERIC(18,6),
    volume          BIGINT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(asset_id, timeframe, timestamp)
);

CREATE INDEX idx_ohlcv_asset_tf_ts ON market.ohlcv (asset_id, timeframe, timestamp DESC);

-- Scraped content sources
CREATE TABLE IF NOT EXISTS scraper.sources (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(100) NOT NULL UNIQUE,
    source_type     VARCHAR(30) NOT NULL CHECK (source_type IN ('rss', 'twitter', 'truthsocial', 'reddit', 'news_api', 'web')),
    base_url        VARCHAR(500),
    scrape_interval INTEGER DEFAULT 300,
    is_active       BOOLEAN DEFAULT TRUE,
    last_scraped_at TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Scraped articles / news
CREATE TABLE IF NOT EXISTS scraper.articles (
    id              BIGSERIAL PRIMARY KEY,
    source_id       INTEGER NOT NULL REFERENCES scraper.sources(id),
    external_id     VARCHAR(500),
    title           TEXT,
    url             VARCHAR(2000),
    content         TEXT,
    summary         TEXT,
    sentiment       NUMERIC(3,2),
    symbols         VARCHAR(50)[],
    published_at    TIMESTAMPTZ,
    scraped_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(source_id, external_id)
);

CREATE INDEX idx_articles_symbols ON scraper.articles USING GIN (symbols);
CREATE INDEX idx_articles_published ON scraper.articles (published_at DESC);

-- Social media posts
CREATE TABLE IF NOT EXISTS scraper.posts (
    id              BIGSERIAL PRIMARY KEY,
    source_id       INTEGER NOT NULL REFERENCES scraper.sources(id),
    author          VARCHAR(200),
    content         TEXT NOT NULL,
    url             VARCHAR(2000),
    engagement      JSONB,
    sentiment       NUMERIC(3,2),
    symbols         VARCHAR(50)[],
    posted_at       TIMESTAMPTZ,
    scraped_at      TIMESTAMPTZ DEFAULT NOW(),
    external_id     VARCHAR(500),
    UNIQUE(source_id, external_id)
);

CREATE INDEX idx_posts_symbols ON scraper.posts USING GIN (symbols);
CREATE INDEX idx_posts_posted ON scraper.posts (posted_at DESC);

-- Trading signals
CREATE TABLE IF NOT EXISTS trading.signals (
    id              BIGSERIAL PRIMARY KEY,
    asset_id        INTEGER NOT NULL REFERENCES market.assets(id),
    signal_type     VARCHAR(30) NOT NULL,
    strategy        VARCHAR(100) NOT NULL,
    confidence      NUMERIC(3,2),
    price_at_signal NUMERIC(18,6),
    metadata        JSONB,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Trading positions
CREATE TABLE IF NOT EXISTS trading.positions (
    id              SERIAL PRIMARY KEY,
    asset_id        INTEGER NOT NULL REFERENCES market.assets(id),
    direction       VARCHAR(10) NOT NULL CHECK (direction IN ('long', 'short')),
    entry_price     NUMERIC(18,6) NOT NULL,
    quantity         NUMERIC(18,6) NOT NULL,
    stop_loss       NUMERIC(18,6),
    take_profit     NUMERIC(18,6),
    status          VARCHAR(20) DEFAULT 'open' CHECK (status IN ('open', 'closed', 'cancelled')),
    realized_pnl    NUMERIC(18,6),
    opened_at       TIMESTAMPTZ DEFAULT NOW(),
    closed_at       TIMESTAMPTZ
);