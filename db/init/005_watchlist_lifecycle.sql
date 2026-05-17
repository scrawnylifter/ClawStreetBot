-- ClawStreetBot: Watchlist lifecycle columns
-- Adds active/added_at/deactivated_at/backfill_status to market.assets
-- so adds, removes, and re-adds can be detected and acted on by n8n.

ALTER TABLE market.assets
    ADD COLUMN IF NOT EXISTS active          BOOLEAN     NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS added_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ADD COLUMN IF NOT EXISTS deactivated_at  TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS backfill_status TEXT        NOT NULL DEFAULT 'pending'
        CHECK (backfill_status IN ('pending', 'running', 'complete', 'failed')),
    ADD COLUMN IF NOT EXISTS backfill_error  TEXT;

CREATE INDEX IF NOT EXISTS assets_active_idx
    ON market.assets (active)
    WHERE active;

CREATE INDEX IF NOT EXISTS assets_backfill_pending_idx
    ON market.assets (backfill_status)
    WHERE backfill_status = 'pending';

-- Existing rows predate the lifecycle and are already backfilled.
UPDATE market.assets
   SET backfill_status = 'complete'
 WHERE backfill_status = 'pending'
   AND active = TRUE;
