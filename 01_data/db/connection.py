"""Postgres connection factory. Uses SSOT config — never reads env vars directly."""

import psycopg2
from config import settings


def get_connection():
    """Return a psycopg2 connection from config.settings.

    All connection parameters come from the frozen Settings dataclass.
    Callers must close the connection when done (use try/finally).
    """
    return psycopg2.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        dbname=settings.postgres_db,
        user=settings.postgres_user,
        password=settings.postgres_password,
    )