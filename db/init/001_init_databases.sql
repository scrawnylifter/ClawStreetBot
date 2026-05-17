-- ClawStreetBot database initialization
-- Runs once on first container start (or manually via psql)

-- Create scraped content database if it doesn't exist
-- (Postgres doesn't support IF NOT EXISTS for databases, so we use a DO block)
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_database WHERE datname = 'scraped') THEN
        CREATE DATABASE scraped;
    END IF;
END
$$;

-- Grant access to the scraped database
GRANT ALL PRIVILEGES ON DATABASE scraped TO clawstreet;