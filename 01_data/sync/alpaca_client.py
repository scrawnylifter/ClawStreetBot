"""Thin Alpaca TradingClient wrapper for L01 watchlist sync.

All credentials come from config.settings — never reads env vars directly.
"""

from __future__ import annotations

import time

import structlog
from alpaca.trading.client import TradingClient

from config import settings

logger = structlog.get_logger(__name__)

_MAX_RETRIES = 3
_BACKOFF_BASE_SECONDS = 1.0


def _enum_value(value, default):
    """Coerce an alpaca-py enum (or plain value) to its stored string form.

    alpaca-py returns str-enums (AssetClass, AssetStatus, AssetExchange).
    Using `.value` makes the stored form unambiguous instead of relying on
    the str-subclass adapting by coincidence. Plain strings pass through.
    """
    if value is None:
        return default
    return getattr(value, "value", value)


def _with_retry(fn, *, op: str, **log_ctx):
    """Call fn() with bounded exponential backoff; re-raise the last error.

    A single transient failure (429/timeout) in the N+1 detail loop would
    otherwise abort the entire sync. Retries make per-watchlist fetches resilient.
    """
    last_error: BaseException | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 — retry any client/transport error
            last_error = e
            logger.warning(
                "alpaca_retry", op=op, attempt=attempt,
                max_attempts=_MAX_RETRIES, error=str(e), **log_ctx,
            )
            if attempt < _MAX_RETRIES:
                time.sleep(_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))
    logger.error("alpaca_op_failed", op=op, attempts=_MAX_RETRIES, error=str(last_error), **log_ctx)
    raise last_error


def get_trading_client() -> TradingClient:
    """Initialize TradingClient from SSOT config.

    Uses paper trading URL by default (config.settings.alpaca_base_url).
    """
    is_paper = settings.alpaca_base_url == "https://paper-api.alpaca.markets"
    client = TradingClient(
        api_key=settings.alpaca_api_key,
        secret_key=settings.alpaca_secret_key,
        paper=is_paper,
    )
    logger.debug("alpaca_client_initialized", paper=is_paper, base_url=settings.alpaca_base_url)
    return client


def fetch_all_watchlists(client: TradingClient | None = None) -> list[dict]:
    """Fetch all watchlists with full asset details from Alpaca.

    Returns a list of watchlist dicts, each containing:
        - id, name, account_id, created_at, updated_at
        - assets: list of asset dicts with id, symbol, name, class,
          exchange, status, tradable, marginable, shortable,
          easy_to_borrow, fractionable

    This does N+1 API calls (1 list + N detail fetches).
    Alpaca's GET /watchlists only returns name+id;
    GET /watchlists/{id} returns the full assets list.
    """
    if client is None:
        client = get_trading_client()

    # Step 1: Get list of all watchlists (summary only — no assets)
    # alpaca-py 0.x exposes get_watchlists() (no get_all_watchlists alias).
    watchlist_summaries = _with_retry(client.get_watchlists, op="get_watchlists")
    logger.info("alpaca_watchlist_summaries_fetched", count=len(watchlist_summaries))

    # Step 2: For each, fetch full details including assets
    watchlists: list[dict] = []
    for summary in watchlist_summaries:
        full = _with_retry(
            lambda sid=summary.id: client.get_watchlist_by_id(sid),
            op="get_watchlist_by_id",
            watchlist_id=str(summary.id),
        )

        assets_list = []
        for a in (full.assets or []):
            assets_list.append({
                "id": str(a.id),
                "symbol": a.symbol,
                "name": a.name or "",  # asset_name column is NOT NULL DEFAULT ''
                "class": _enum_value(getattr(a, "asset_class", None), "us_equity"),
                "exchange": _enum_value(getattr(a, "exchange", None), ""),
                "status": _enum_value(getattr(a, "status", None), "active"),
                "tradable": getattr(a, "tradable", False) or False,
                "marginable": getattr(a, "marginable", False) or False,
                "shortable": getattr(a, "shortable", False) or False,
                "easy_to_borrow": getattr(a, "easy_to_borrow", False) or False,
                "fractionable": getattr(a, "fractionable", False) or False,
            })

        watchlists.append({
            "id": str(full.id),
            "name": full.name,
            "account_id": str(full.account_id),
            "created_at": full.created_at,
            "updated_at": full.updated_at,
            "assets": assets_list,
        })

    logger.info("alpaca_watchlists_fetched", watchlists=len(watchlist_summaries), total_assets=sum(len(wl["assets"]) for wl in watchlists))
    return watchlists


def add_asset(client: TradingClient, watchlist_id: str, symbol: str):
    """Add a symbol to an Alpaca watchlist (write to the source of truth).

    Wraps POST /watchlists/{id}/{symbol} via alpaca-py's
    add_asset_to_watchlist_by_id. Retried for transient transport errors.
    """
    return _with_retry(
        lambda: client.add_asset_to_watchlist_by_id(watchlist_id, symbol),
        op="add_asset_to_watchlist_by_id",
        watchlist_id=str(watchlist_id),
        symbol=symbol,
    )


def remove_asset(client: TradingClient, watchlist_id: str, symbol: str):
    """Remove a symbol from an Alpaca watchlist (write to the source of truth).

    Wraps DELETE /watchlists/{id}/{symbol} via alpaca-py's
    remove_asset_from_watchlist_by_id. Retried for transient transport errors.
    """
    return _with_retry(
        lambda: client.remove_asset_from_watchlist_by_id(watchlist_id, symbol),
        op="remove_asset_from_watchlist_by_id",
        watchlist_id=str(watchlist_id),
        symbol=symbol,
    )