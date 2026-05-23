"""Shared trading-pipeline constants.

Single source of truth for thresholds that must stay in lockstep across the
scanner → snapshot → preflight → executor chain. If the constant lives in
more than one place, a stale copy will eventually drift past a real one and
silently invert a gate.
"""
import os
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Market hours — loaded from config/market_hours.yml
# ---------------------------------------------------------------------------
def _load_market_hours() -> dict:
    with open(PROJECT_ROOT / "config" / "market_hours.yml") as f:
        return yaml.safe_load(f)

_MARKET_HOURS = _load_market_hours()

ET = timezone(timedelta(hours=-5))   # US/Eastern (non-DST; use America/New_York for aware)
PDT = timezone(timedelta(hours=-7))  # US/Pacific (non-DST shorthand)

# Regular session (ET)
SESSION_OPEN = datetime.strptime(_MARKET_HOURS["regular_session"]["open"], "%H:%M").time()
SESSION_CLOSE = datetime.strptime(_MARKET_HOURS["regular_session"]["close"], "%H:%M").time()

# Ingestion windows (ET)
INGEST_INTRADAY_START = datetime.strptime(_MARKET_HOURS["ingestion"]["intraday_start"], "%H:%M").time()
INGEST_INTRADAY_END = datetime.strptime(_MARKET_HOURS["ingestion"]["intraday_end"], "%H:%M").time()
INGEST_DAILY_AFTER_CLOSE = datetime.strptime(_MARKET_HOURS["ingestion"]["daily_after_close"], "%H:%M").time()

# Scanner windows (ET)
SCANNER_ORB_START = datetime.strptime(_MARKET_HOURS["scanner"]["orb_start"], "%H:%M").time()
SCANNER_INTRADAY_END = datetime.strptime(_MARKET_HOURS["scanner"]["intraday_end"], "%H:%M").time()

# NYSE holidays (parsed from config)
NYSE_HOLIDAYS: set[date] = set()
for _h in _MARKET_HOURS.get("holidays_2026", []):
    NYSE_HOLIDAYS.add(date.fromisoformat(_h))


def is_market_day(d: date | None = None) -> bool:
    """True if d is a weekday and not an NYSE holiday."""
    if d is None:
        d = date.today()
    return d.weekday() < 5 and d not in NYSE_HOLIDAYS


def is_market_hours(et_now: time | None = None) -> bool:
    """True if current time (ET) is within the regular session."""
    if et_now is None:
        et_now = datetime.now(timezone(timedelta(hours=-4 if date.today().month < 3 or date.today().month > 10 else -5))).time()
    return SESSION_OPEN <= et_now <= SESSION_CLOSE

# Maximum acceptable option bid-ask spread as a fraction of mid price.
# (ask - bid) / mid, so symmetric around the midpoint.
#
# Enforced at three layers:
#   1. fetch_alpaca_snapshot.select_best_option — rejects wide-spread
#      contracts at signal time before they ever hit signal_alerts.
#   2. Scanner-time hardening (e.g. detect_ema_crossover) — re-queries
#      Alpaca for live bid/ask on the DB-picked contract and clears
#      option_symbol if the live spread is too wide.
#   3. process_approved preflight — catches stale spreads that widened
#      between scan and approval.
MAX_SPREAD_PCT = 0.15
MAX_SPREAD_PCT_DECIMAL = Decimal("0.15")

# ---------------------------------------------------------------------------
# Signal freshness / expiry windows
# ---------------------------------------------------------------------------
# How long a signal remains actionable after creation. After this window
# elapses, alert_telegram.py expires the row to 'expired' status and
# telegram_callback_listener.py rejects approval.
#
# The key insight: ORB signals are only valid for ~60 min after market open
# (they're based on a fixed opening range — by 11 AM the range is stale
# and the probability of continuation has decayed). Setup scanner signals
# are broader but still involve live option quotes that go stale.
#
# Strategy-specific windows (minutes). Options prices move fast —
# 15 min is the hard cap. Only swing-style daily signals get longer.
# Used by:
#   - alert_telegram.py: expire_stale_new() per-strategy TTL
#   - telegram_callback_listener.py: reject stale approvals
#   - process_approved.py: preflight gate #0
#   - detect_orb.py: skip generation outside the ORB session window
SIGNAL_TTL_MINUTES: dict[str, int] = {
    "orb": 15,                  # Opening range — seconds count at the bell
    "ema_crossover_15m": 15,   # 15m cross — tight window
    "ema_crossover": 60,       # Daily EMA cross — swing trade, more room
    "setup_scanner": 15,       # 8-gate composite with live option quotes
    "liquidity_sweep": 15,     # 5m sweep — momentum decays fast
    "daily_signal": 60,       # End-of-day composite — valid for next session
}

DEFAULT_SIGNAL_TTL_MINUTES = 15  # fallback for strategies not in the dict


# ---------------------------------------------------------------------------
# DB env loading — shared so v2 scripts don't each redefine it
# ---------------------------------------------------------------------------
def load_env(filename: str) -> None:
    """Source a KEY=VALUE .env file into os.environ (setdefault — won't clobber)."""
    path = PROJECT_ROOT / filename
    if not path.exists():
        alt = Path("/app") / filename
        if alt.exists():
            path = alt
        else:
            return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


# ---------------------------------------------------------------------------
# Unified Greeks Strategy — single source of truth for delta/theta/IV gates
# ---------------------------------------------------------------------------
# Mode-keyed delta bands (CLAUDE.md → Greeks Strategy). Used by:
#   - shared/fetch_alpaca_snapshot.select_best_option (scanner-time)
#   - 05_execution/scripts/execute_trade.reselect_option_for_risk_mode
#   - 04_approval/scripts/process_approved.preflight
# Each entry: (min, max). Target deltas: day=0.75, swing=0.60, long_term=0.75.
MODE_DELTA_BANDS = {
    "day":       (0.65, 0.85),
    "swing":     (0.50, 0.70),
    "long_term": (0.65, 0.85),
}
MODE_DELTA_TARGETS = {
    "day":       0.75,
    "swing":     0.60,
    "long_term": 0.75,
}

# Hard rejects regardless of mode.
DELTA_FLOOR   = 0.50   # |delta| < 0.50 → lottery ticket, always reject
DELTA_CEILING = 0.90   # |delta| > 0.90 → just buy shares, always reject

# Per-mode theta budget as a fraction of option mid (|theta|/mid).
# Day trades can absorb more theta because they're held minutes-to-hours;
# long-term holds get the tightest budget because theta compounds over weeks.
THETA_BUDGETS = {
    "day":       0.05,
    "swing":     0.03,
    "long_term": 0.01,
}


def classify_regime(iv_rank) -> str:
    """Classify IV regime from iv_rank percentile (0-100).

    Returns one of: buy_premium / directional / spreads_cautious / sell_premium.
    Mirrors the regime gating in 01_data/scripts/compute_greeks_filter.py and
    obsidian/.../Greeks Strategy.md so preflight can refuse naked premium-buying
    when the underlying is in a sell-premium regime.
    """
    if iv_rank is None:
        return "directional"
    try:
        r = float(iv_rank)
    except (TypeError, ValueError):
        return "directional"
    if r < 25:
        return "buy_premium"
    if r < 50:
        return "directional"
    if r < 75:
        return "spreads_cautious"
    return "sell_premium"


load_env(".env.db")

DB_CONFIG = {
    "host": os.environ.get("POSTGRES_HOST", "localhost"),
    "port": int(os.environ.get("POSTGRES_PORT", 5432)),
    "dbname": os.environ.get("POSTGRES_DB", "clawstreet"),
    "user": os.environ.get("POSTGRES_USER", "clawstreet"),
    "password": os.environ.get("POSTGRES_PASSWORD", ""),
}