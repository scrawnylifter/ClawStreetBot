"""L01 Data Layer CLI. Jobs are registered in the JOBS dict.

Usage:
    python -m layer.main --job watchlist_sync
"""

import argparse
import logging
import sys
from typing import Callable

import structlog

from config import settings

# Log level from SSOT config (LOG_LEVEL), not hardcoded.
_log_level = getattr(logging, settings.log_level.upper(), logging.INFO)

# Configure structlog for JSON output — fail loudly, never silent
structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(_log_level),
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        # Render exc_info (set by logger.exception) into a traceback string —
        # without this, failures log a bare "exc_info": true and swallow the cause.
        structlog.processors.format_exc_info,
        structlog.dev.ConsoleRenderer() if sys.stderr.isatty() else structlog.processors.JSONRenderer(),
    ],
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger(__name__)

# Job registry: maps job name to a callable taking parsed args, returning a result dict
JOBS: dict[str, Callable[[argparse.Namespace], dict]] = {}


def register_job(name: str):
    """Decorator to register a job function in the JOBS dict.

    Registered jobs receive the parsed argparse.Namespace so parameterized jobs
    (watchlist_add/watchlist_remove) can read --symbol/--watchlist. Jobs that
    take no parameters simply ignore it.
    """
    def decorator(func):
        JOBS[name] = func
        return func
    return decorator


def _require_symbol(args: argparse.Namespace) -> str:
    """Return a non-empty --symbol or exit(2) with a clear message."""
    if not args.symbol:
        raise SystemExit(f"--symbol is required for job '{args.job}'")
    return args.symbol


@register_job("watchlist_sync")
def run_watchlist_sync(args: argparse.Namespace) -> dict:
    """Sync Alpaca watchlists to Postgres market.watchlist table."""
    from layer.sync.watchlist import sync_watchlists
    return sync_watchlists()


@register_job("watchlist_add")
def run_watchlist_add(args: argparse.Namespace) -> dict:
    """Add --symbol to an Alpaca watchlist, then re-sync the DB to confirm it landed."""
    from layer.sync.watchlist import add_watchlist_asset
    return add_watchlist_asset(_require_symbol(args), watchlist_name=args.watchlist)


@register_job("watchlist_remove")
def run_watchlist_remove(args: argparse.Namespace) -> dict:
    """Remove --symbol from an Alpaca watchlist, then re-sync the DB to confirm it's gone."""
    from layer.sync.watchlist import remove_watchlist_asset
    return remove_watchlist_asset(_require_symbol(args), watchlist_name=args.watchlist)


def main():
    parser = argparse.ArgumentParser(description="L01 Data Layer — ClawStreetBot")
    parser.add_argument(
        "--job",
        required=True,
        choices=list(JOBS.keys()),
        help="Job to run",
    )
    parser.add_argument(
        "--symbol",
        default=None,
        help="Ticker symbol for watchlist_add / watchlist_remove (e.g. AAPL)",
    )
    parser.add_argument(
        "--watchlist",
        default=None,
        help="Target watchlist name (default: the only watchlist, if exactly one exists)",
    )
    args = parser.parse_args()

    logger.info("l01_job_started", job=args.job, symbol=args.symbol, watchlist=args.watchlist)
    try:
        result = JOBS[args.job](args)
        logger.info("l01_job_completed", job=args.job, result=result)
        sys.exit(0)
    except Exception:
        logger.exception("l01_job_failed", job=args.job)
        sys.exit(1)


if __name__ == "__main__":
    main()