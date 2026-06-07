-- L01 Data Layer: Watchlist table
-- Mirrors Alpaca watchlists in our Postgres database.
-- One row per (watchlist_id, symbol) pair — denormalized for query simplicity.
-- Alpaca's GET /watchlists/{id} embeds assets, so we flatten into a single table.

CREATE SCHEMA IF NOT EXISTS market;

CREATE TABLE IF NOT EXISTS market.watchlist (
    id                UUID        NOT NULL,       -- Alpaca watchlist ID
    name              TEXT        NOT NULL,       -- Watchlist name
    account_id        UUID        NOT NULL,       -- Alpaca account ID
    symbol            TEXT        NOT NULL,       -- Ticker symbol (e.g. 'AAPL')
    asset_id          UUID        NOT NULL,       -- Alpaca asset ID
    asset_class       TEXT        NOT NULL DEFAULT 'us_equity',
    exchange          TEXT        NOT NULL DEFAULT '',
    asset_name        TEXT        NOT NULL DEFAULT '',
    status            TEXT        NOT NULL DEFAULT 'active',
    tradable          BOOLEAN     NOT NULL DEFAULT false,
    marginable        BOOLEAN     NOT NULL DEFAULT false,
    shortable         BOOLEAN     NOT NULL DEFAULT false,
    easy_to_borrow    BOOLEAN     NOT NULL DEFAULT false,
    fractionable      BOOLEAN     NOT NULL DEFAULT false,
    alpaca_created_at TIMESTAMPTZ NOT NULL,       -- From Alpaca watchlist.created_at
    alpaca_updated_at TIMESTAMPTZ NOT NULL,       -- From Alpaca watchlist.updated_at
    synced_at         TIMESTAMPTZ NOT NULL DEFAULT now(),  -- Our sync timestamp

    PRIMARY KEY (id, symbol)
);

-- Downstream queries: "give me all symbols on watchlists"
CREATE INDEX IF NOT EXISTS idx_watchlist_symbol ON market.watchlist (symbol);

-- Downstream queries: "give me watchlists for this name"
CREATE INDEX IF NOT EXISTS idx_watchlist_name ON market.watchlist (name);

-- Downstream queries: "when was this last synced?"
CREATE INDEX IF NOT EXISTS idx_watchlist_synced_at ON market.watchlist (synced_at);