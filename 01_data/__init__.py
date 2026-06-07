"""L01 Data Layer — ClawsStreetBot data ingestion and sync.

Exposes the job registry for CLI entry via main.py.
Uses absolute imports under the 'layer' package (the container's working package).
"""

from layer.sync.watchlist import sync_watchlists  # noqa: F401

__all__ = ["sync_watchlists"]