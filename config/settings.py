"""
ClawStreetBot v2 — Single source of truth for all config.

No other module reads os.environ directly. Import from config:
    from config import settings

Inside Docker containers, env vars come from docker-compose (via .env).
load_dotenv() is for local development only — it's a no-op in containers
where env vars are already set by Docker.
"""

import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

# In containers, env vars come from docker-compose. load_dotenv is for local dev.
load_dotenv()


@dataclass(frozen=True)
class Settings:
    """All config and credentials. Immutable after creation.

    Add new fields here and in .env.example. No shortcuts.
    """

    # ── Postgres ───────────────────────────────────────
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_user: str = "cbs"
    postgres_password: str = ""
    postgres_db: str = "clawstreetbot"

    # ── Redis ───────────────────────────────────────────
    redis_host: str = "redis"
    redis_port: int = 6379
    redis_password: str = ""

    # ── n8n ──────────────────────────────────────────────
    n8n_host: str = "localhost"
    n8n_port: int = 5678
    n8n_user: str = "admin"
    n8n_password: str = ""
    n8n_schema: str = "n8n"  # n8n uses its own schema within our postgres

    # ── Alpaca ──────────────────────────────────────────
    alpaca_api_key: str = ""
    alpaca_secret_key: str = ""
    alpaca_base_url: str = "https://paper-api.alpaca.markets"

    # ── Telegram ─────────────────────────────────────────
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # ── Application ──────────────────────────────────────
    log_level: str = "INFO"
    environment: str = "development"  # development | staging | production

    @property
    def postgres_dsn(self) -> str:
        """Full connection string for psycopg2/asyncpg."""
        return (
            f"postgresql://{self.postgres_user}:***@"
            f"{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def postgres_dsn_with_password(self) -> str:
        """Connection string with password — use ONLY inside containers, never log this."""
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}@"
            f"{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def redis_url(self) -> str:
        """Connection URL for redis-py."""
        return f"redis://:{self.redis_password}@{self.redis_host}:{self.redis_port}/0"

    @classmethod
    def from_env(cls) -> "Settings":
        """Load and validate all config from environment variables.

        Raises ValueError with the list of missing vars if any are unset.
        """
        missing = []

        def require(key: str, default: Optional[str] = None) -> str:
            val = os.environ.get(key, default)
            if val is None:
                missing.append(key)
                return ""
            return val

        def require_int(key: str, default: int = 0) -> int:
            val = os.environ.get(key)
            if val is None:
                return default
            try:
                return int(val)
            except ValueError:
                missing.append(f"{key} (must be integer)")
                return 0

        settings = cls(
            postgres_host=require("POSTGRES_HOST", "postgres"),
            postgres_port=require_int("POSTGRES_PORT", 5432),
            postgres_user=require("POSTGRES_USER", "cbs"),
            postgres_password=require("POSTGRES_PASSWORD", ""),
            postgres_db=require("POSTGRES_DB", "clawstreetbot"),
            redis_host=require("REDIS_HOST", "redis"),
            redis_port=require_int("REDIS_PORT", 6379),
            redis_password=require("REDIS_PASSWORD", ""),
            n8n_host=require("N8N_HOST", "localhost"),
            n8n_port=require_int("N8N_PORT", 5678),
            n8n_user=require("N8N_USER", "admin"),
            n8n_password=require("N8N_PASSWORD", ""),
            n8n_schema=require("N8N_SCHEMA", "n8n"),
            alpaca_api_key=require("ALPACA_API_KEY", ""),
            alpaca_secret_key=require("ALPACA_SECRET_KEY", ""),
            alpaca_base_url=require("ALPACA_BASE_URL", "https://paper-api.alpaca.markets"),
            telegram_bot_token=require("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=require("TELEGRAM_CHAT_ID", ""),
            log_level=require("LOG_LEVEL", "INFO"),
            environment=require("ENVIRONMENT", "development"),
        )

        # Validate required secrets are non-empty
        required_secrets = [
            "postgres_password",
            "redis_password",
            "n8n_password",
        ]
        empty = [k for k in required_secrets if getattr(settings, k) == ""]
        if empty:
            raise ValueError(
                f"Missing or empty required env vars: {empty}. "
                f"Copy .env.example to .env and fill in your values."
            )

        return settings


# Singleton — import this, never instantiate Settings directly
settings = Settings.from_env()