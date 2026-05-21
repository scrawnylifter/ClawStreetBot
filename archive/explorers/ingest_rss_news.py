#!/usr/bin/env python3
"""Ingest financial news (RSS) and social posts (Reddit) into Postgres.

Reads ``config/rss_feeds.yml`` for source definitions, then for each
configured source pulls the latest items and upserts them into
``scraper.articles`` (RSS) or ``scraper.posts`` (Reddit). The
``scraper.sources`` row for each feed is upserted on first run.

Idempotent: dedup is on ``(source_id, external_id)`` where
``external_id`` is the canonical URL (RSS) or the Reddit post ID. Re-runs
skip items already in the table.

Symbol extraction is regex-based: matches ``$TICKER`` cashtags and
uppercase 2-5 letter words that appear near stock-related keywords. The
resulting tickers are intersected with the active watchlist plus a small
stoplist of common English words to keep noise down.

Usage:
    python scripts/ingest_rss_news.py                   # all sources
    python scripts/ingest_rss_news.py --source rss
    python scripts/ingest_rss_news.py --source reddit
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Iterable

import feedparser
import psycopg2
import requests
import yaml
from psycopg2.extras import execute_values

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_env(filename: str) -> None:
    """Populate ``os.environ`` from a ``KEY=VALUE`` dotenv file."""
    path = PROJECT_ROOT / filename
    if not path.exists():
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


load_env(".env.db")

DB_CONFIG = {
    "host": os.environ.get("POSTGRES_HOST", "localhost"),
    "port": int(os.environ.get("POSTGRES_PORT", 5432)),
    "dbname": os.environ["POSTGRES_DB"],
    "user": os.environ["POSTGRES_USER"],
    "password": os.environ["POSTGRES_PASSWORD"],
}

USER_AGENT = "ClawStreetBot/0.1 (+https://github.com/scrawnylifter/ClawStreetBot)"
REDDIT_LIMIT = 25
HTTP_TIMEOUT = 30
RATE_LIMIT_SLEEP = 1.0  # polite between Reddit calls

CASHTAG_RE = re.compile(r"\$([A-Z]{1,5})\b")
TICKER_RE = re.compile(r"\b([A-Z]{2,5})\b")
STOCK_KEYWORDS = {
    "stock", "stocks", "share", "shares", "earnings", "price", "target",
    "upgrade", "downgrade", "buy", "sell", "hold", "calls", "puts",
    "option", "options", "rally", "plunge", "rose", "fell", "ticker",
    "ipo", "etf", "trade", "trading",
}
# Common false positives — uppercase words that aren't tickers.
SYMBOL_STOPLIST = {
    "A", "I", "AI", "ARM", "AS", "AT", "BE", "BY", "CEO", "CFO", "CNBC",
    "DO", "EPS", "ETF", "EU", "EV", "FED", "FOMC", "FY", "GDP", "GO",
    "HE", "IF", "IN", "IPO", "IS", "IT", "ITS", "ME", "MY", "NEW", "NO",
    "NOT", "NYSE", "OF", "OK", "ON", "OR", "OUR", "PM", "Q1", "Q2", "Q3",
    "Q4", "SEC", "SO", "THE", "TO", "UK", "US", "USA", "USD", "UP", "WE",
    "WHY", "YOU", "YOUR", "DAY", "BIG", "ALL", "ARE", "CAN", "HAS", "HAD",
    "HIS", "HER", "HOW", "MAY", "NOW", "ONE", "OUT", "SEE", "TOP", "WAS",
    "WHO", "WILL",
}


def load_feeds() -> dict[str, Any]:
    """Load RSS + Reddit feed configuration from ``config/rss_feeds.yml``."""
    path = PROJECT_ROOT / "config" / "rss_feeds.yml"
    with open(path) as f:
        return yaml.safe_load(f) or {}


def get_watchlist(conn) -> set[str]:
    """Return active watchlist tickers (for symbol-extraction whitelist)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT symbol FROM market.assets "
            "WHERE asset_type='stock' AND active = TRUE"
        )
        return {r[0] for r in cur.fetchall()}


def upsert_source(conn, name: str, source_type: str,
                  base_url: str | None) -> int:
    """Insert source row if missing, return its id."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO scraper.sources (name, source_type, base_url, is_active)
            VALUES (%s, %s, %s, TRUE)
            ON CONFLICT (name) DO UPDATE SET
                source_type = EXCLUDED.source_type,
                base_url    = COALESCE(EXCLUDED.base_url, scraper.sources.base_url)
            RETURNING id
            """,
            (name, source_type, base_url),
        )
        return cur.fetchone()[0]


def touch_source(conn, source_id: int) -> None:
    """Mark a source as scraped just now."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE scraper.sources SET last_scraped_at = NOW() WHERE id = %s",
            (source_id,),
        )


def extract_symbols(text: str, watchlist: set[str]) -> list[str]:
    """Return tickers mentioned in ``text``.

    Cashtags (``$NVDA``) are always taken. Bare uppercase tokens are taken
    only when they appear in the watchlist OR appear near a stock-related
    keyword. Common English-word false positives are filtered out.
    """
    if not text:
        return []
    found: set[str] = set()
    for m in CASHTAG_RE.findall(text):
        if m not in SYMBOL_STOPLIST:
            found.add(m)

    lower = text.lower()
    has_stock_context = any(kw in lower for kw in STOCK_KEYWORDS)
    for m in TICKER_RE.findall(text):
        if m in SYMBOL_STOPLIST:
            continue
        if m in watchlist:
            found.add(m)
        elif has_stock_context and 2 <= len(m) <= 5:
            found.add(m)

    return sorted(found)


def parse_rss_date(entry: dict[str, Any]) -> datetime | None:
    """Best-effort published-at extraction from a feedparser entry."""
    for key in ("published", "updated", "created"):
        val = entry.get(key)
        if not val:
            continue
        try:
            dt = parsedate_to_datetime(val)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except (TypeError, ValueError):
            continue
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed:
        try:
            return datetime(*parsed[:6], tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return None
    return None


def ingest_rss_feed(conn, feed_cfg: dict[str, str],
                    watchlist: set[str]) -> int:
    """Fetch + upsert articles from a single RSS feed. Returns inserted count."""
    name = feed_cfg["name"]
    url = feed_cfg["url"]
    source_id = upsert_source(conn, name, "rss", url)

    parsed = feedparser.parse(url, agent=USER_AGENT)
    if parsed.bozo and not parsed.entries:
        print(f"  {name:<22} parse error: {parsed.bozo_exception}")
        return 0

    rows: list[tuple] = []
    for entry in parsed.entries:
        link = (entry.get("link") or "").strip()
        if not link:
            continue
        title = (entry.get("title") or "").strip() or None
        summary = (entry.get("summary") or entry.get("description") or "").strip() or None
        published_at = parse_rss_date(entry)
        symbols = extract_symbols(
            " ".join(filter(None, [title, summary])), watchlist
        )
        rows.append((
            source_id, link[:500], title, link[:2000], summary,
            symbols or None, published_at,
        ))

    if not rows:
        touch_source(conn, source_id)
        return 0

    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO scraper.articles
                (source_id, external_id, title, url, summary, symbols, published_at)
            VALUES %s
            ON CONFLICT (source_id, external_id) DO NOTHING
            """,
            rows,
            page_size=200,
        )
        inserted = cur.rowcount
    touch_source(conn, source_id)
    return inserted


def fetch_reddit(subreddit: str) -> list[dict[str, Any]]:
    """Fetch hot posts for a subreddit via the public JSON endpoint."""
    url = f"https://www.reddit.com/r/{subreddit}/hot.json"
    resp = requests.get(
        url,
        params={"limit": REDDIT_LIMIT},
        headers={"User-Agent": USER_AGENT},
        timeout=HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    children = resp.json().get("data", {}).get("children", []) or []
    return [c.get("data") or {} for c in children]


def ingest_reddit(conn, subreddit: str, watchlist: set[str]) -> int:
    """Fetch + upsert posts from a single subreddit. Returns inserted count."""
    name = f"reddit/{subreddit}"
    base_url = f"https://www.reddit.com/r/{subreddit}"
    source_id = upsert_source(conn, name, "reddit", base_url)

    try:
        posts = fetch_reddit(subreddit)
    except requests.HTTPError as e:
        print(f"  r/{subreddit:<20} HTTP error: {e}")
        touch_source(conn, source_id)
        return 0
    except requests.RequestException as e:
        print(f"  r/{subreddit:<20} request error: {e}")
        touch_source(conn, source_id)
        return 0

    rows: list[tuple] = []
    for p in posts:
        post_id = p.get("id")
        if not post_id:
            continue
        title = (p.get("title") or "").strip()
        selftext = (p.get("selftext") or "").strip()
        body = "\n\n".join(filter(None, [title, selftext])) or title or "(no content)"
        permalink = p.get("permalink") or ""
        url = f"https://www.reddit.com{permalink}" if permalink else (p.get("url") or "")
        author = p.get("author") or None
        created_utc = p.get("created_utc")
        posted_at: datetime | None = None
        if created_utc is not None:
            try:
                posted_at = datetime.fromtimestamp(float(created_utc), tz=timezone.utc)
            except (TypeError, ValueError):
                posted_at = None
        engagement = {
            "score": p.get("score"),
            "num_comments": p.get("num_comments"),
            "upvote_ratio": p.get("upvote_ratio"),
        }
        symbols = extract_symbols(f"{title}\n{selftext}", watchlist)
        rows.append((
            source_id, post_id[:500], author, body, url[:2000],
            json.dumps(engagement), symbols or None, posted_at,
        ))

    if not rows:
        touch_source(conn, source_id)
        return 0

    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO scraper.posts
                (source_id, external_id, author, content, url,
                 engagement, symbols, posted_at)
            VALUES %s
            ON CONFLICT (source_id, external_id) DO NOTHING
            """,
            rows,
            page_size=200,
        )
        inserted = cur.rowcount
    touch_source(conn, source_id)
    return inserted


def run_rss(conn, feeds: Iterable[dict[str, str]],
            watchlist: set[str]) -> int:
    """Drive RSS ingestion across all configured feeds."""
    total = 0
    for feed in feeds:
        name = feed.get("name", "?")
        try:
            n = ingest_rss_feed(conn, feed, watchlist)
            conn.commit()
            total += n
            print(f"  {name:<22} +{n:>3} articles")
        except Exception as e:
            conn.rollback()
            print(f"  {name:<22} ERROR: {e}")
    return total


def run_reddit(conn, subs: Iterable[str], watchlist: set[str]) -> int:
    """Drive Reddit ingestion across all configured subreddits."""
    total = 0
    for sub in subs:
        try:
            n = ingest_reddit(conn, sub, watchlist)
            conn.commit()
            total += n
            print(f"  r/{sub:<20} +{n:>3} posts")
        except Exception as e:
            conn.rollback()
            print(f"  r/{sub:<20} ERROR: {e}")
        time.sleep(RATE_LIMIT_SLEEP)
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", choices=("rss", "reddit", "all"), default="all",
        help="Which source group to run (default: all)",
    )
    args = parser.parse_args()

    cfg = load_feeds()
    rss_feeds = cfg.get("rss") or []
    reddit_subs = cfg.get("reddit") or []

    conn = psycopg2.connect(**DB_CONFIG)
    try:
        watchlist = get_watchlist(conn)
        print(f"Run: {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
        print(f"Watchlist: {len(watchlist)} symbols\n")

        if args.source in ("rss", "all"):
            print(f"RSS feeds ({len(rss_feeds)}):")
            n = run_rss(conn, rss_feeds, watchlist)
            print(f"  → {n} new articles\n")

        if args.source in ("reddit", "all"):
            print(f"Reddit subs ({len(reddit_subs)}):")
            n = run_reddit(conn, reddit_subs, watchlist)
            print(f"  → {n} new posts")
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
