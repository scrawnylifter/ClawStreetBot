#!/usr/bin/env python3
"""Telegram Callback Listener — Phase 5B

Long-poll daemon that listens for Approve / Deny button presses on the
alert messages sent by alert_telegram.py and records the user's decision
in market.signal_alerts.

Flow per callback:
    1. Parse callback_data ("approve:<id>" or "deny:<id>")
    2. Verify the chat is in the allowlist (single TELEGRAM_CHAT_ID for now)
    3. Look up the signal row; reject if already actioned (idempotent)
    4. UPDATE status / user_action / approved_at|denied_at / approval_*
    5. Remove the inline keyboard from the original message and send a
       short reply confirming the decision
    6. answerCallbackQuery to clear the Telegram spinner

This script does NOT place trades. Execution is the next slice
(execute_trade.py); for now an `approved` row is just a record that
the user pressed the button. That gives us a safe place to validate the
loop end-to-end before any money moves.

Usage:
    python telegram_callback_listener.py                # run forever
    python telegram_callback_listener.py --once          # one poll then exit
    python telegram_callback_listener.py --reset-offset  # discard backlog on next start
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import psycopg2.extras

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("telegram_callback_listener")

REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = REPO_ROOT / "state"
OFFSET_FILE = STATE_DIR / "telegram_offset.txt"

LONG_POLL_TIMEOUT = 30  # seconds; Telegram caps at 50

# ---------------------------------------------------------------------------
# Config loading (matches alert_telegram.py pattern — works in worker + local)
# ---------------------------------------------------------------------------

def _read_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                out[k.strip()] = v.strip()
    return out


def load_env(name: str) -> dict[str, str]:
    """Find .env.<name> in either /app (docker worker) or repo root."""
    for candidate in (Path(f"/app/.env.{name}"), REPO_ROOT / f".env.{name}"):
        if candidate.exists():
            return _read_env_file(candidate)
    raise FileNotFoundError(f".env.{name} not found")


def get_connection():
    # POSTGRES_HOST is set by docker-compose (=postgres) but not in .env.db, so
    # the OS env wins for host/port. The credentials always come from the file.
    cfg = load_env("db")
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST") or cfg.get("POSTGRES_HOST") or "localhost",
        port=int(os.environ.get("POSTGRES_PORT") or cfg.get("POSTGRES_PORT") or 5432),
        user=cfg.get("POSTGRES_USER") or os.environ.get("POSTGRES_USER", "clawstreet"),
        password=cfg.get("POSTGRES_PASSWORD") or os.environ.get("POSTGRES_PASSWORD", ""),
        dbname=cfg.get("POSTGRES_DB") or os.environ.get("POSTGRES_DB", "clawstreet"),
    )


# ---------------------------------------------------------------------------
# Telegram Bot API
# ---------------------------------------------------------------------------

class TelegramAPIError(Exception):
    pass


def tg_call(token: str, method: str, params: dict, timeout: int = 60) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = json.dumps(params).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        raise TelegramAPIError(f"{method} HTTP {e.code}: {body[:200]}") from e

    if not payload.get("ok"):
        raise TelegramAPIError(f"{method} not ok: {payload}")
    return payload["result"]


def get_updates(token: str, offset: int | None, poll_timeout: int) -> list[dict]:
    params: dict = {
        "timeout": poll_timeout,
        "allowed_updates": ["callback_query"],
    }
    if offset is not None:
        params["offset"] = offset
    # HTTP timeout slightly longer than long-poll so the server is the one
    # that hangs up, not us.
    return tg_call(token, "getUpdates", params, timeout=poll_timeout + 10)


def answer_callback(token: str, callback_query_id: str, text: str | None = None) -> None:
    params: dict = {"callback_query_id": callback_query_id}
    if text:
        params["text"] = text
    try:
        tg_call(token, "answerCallbackQuery", params, timeout=15)
    except TelegramAPIError as e:
        log.warning("answerCallbackQuery failed: %s", e)


def clear_message_keyboard(token: str, chat_id: int, message_id: int) -> None:
    """Remove the inline keyboard from a previously-sent alert message."""
    params = {
        "chat_id": chat_id,
        "message_id": message_id,
        "reply_markup": {"inline_keyboard": []},
    }
    try:
        tg_call(token, "editMessageReplyMarkup", params, timeout=15)
    except TelegramAPIError as e:
        # Not fatal — message may have been deleted, or buttons already gone.
        log.warning("editMessageReplyMarkup failed: %s", e)


def send_reply(token: str, chat_id: int, reply_to_message_id: int, text: str) -> None:
    params = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "reply_to_message_id": reply_to_message_id,
        "allow_sending_without_reply": True,
        "disable_notification": True,
    }
    try:
        tg_call(token, "sendMessage", params, timeout=15)
    except TelegramAPIError as e:
        log.warning("sendMessage (reply) failed: %s", e)


# ---------------------------------------------------------------------------
# Offset persistence (so a restart doesn't replay events)
# ---------------------------------------------------------------------------

def load_offset() -> int | None:
    if not OFFSET_FILE.exists():
        return None
    try:
        return int(OFFSET_FILE.read_text().strip())
    except (ValueError, OSError):
        return None


def save_offset(offset: int) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = OFFSET_FILE.with_suffix(".tmp")
    tmp.write_text(str(offset))
    tmp.replace(OFFSET_FILE)


# ---------------------------------------------------------------------------
# Callback handler
# ---------------------------------------------------------------------------

def parse_callback_data(data: str) -> tuple[str, int, str] | None:
    """Parse callback_data from approval keyboard buttons.

    Formats:
        approve:<id>            -> ('approve', <id>, 'standard')   # backwards compat
        approve:<id>:standard    -> ('approve', <id>, 'standard')
        approve:<id>:conservative -> ('approve', <id>, 'conservative')
        approve:<id>:aggressive  -> ('approve', <id>, 'aggressive')
        deny:<id>                -> ('deny', <id>, '')

    Returns None on malformed input.
    """
    if not data or ":" not in data:
        return None

    parts = data.split(":")
    action = parts[0]

    if action not in ("approve", "deny"):
        return None

    try:
        signal_id = int(parts[1])
    except (ValueError, IndexError):
        return None

    if action == "deny":
        return "deny", signal_id, ""

    # approve — extract risk mode
    risk_mode = parts[2] if len(parts) >= 3 else "standard"
    if risk_mode not in ("standard", "conservative", "aggressive"):
        risk_mode = "standard"  # fallback for unknown modes

    return "approve", signal_id, risk_mode


def handle_callback(
    conn,
    token: str,
    allowed_chat_id: int,
    callback: dict,
) -> None:
    cb_id = callback["id"]
    data = callback.get("data", "")
    chat = callback.get("message", {}).get("chat", {}) or {}
    chat_id = chat.get("id")
    message_id = callback.get("message", {}).get("message_id")
    user = callback.get("from", {}) or {}
    user_id = user.get("id")
    user_name = user.get("username") or user.get("first_name") or str(user_id)

    parsed = parse_callback_data(data)
    if parsed is None:
        log.warning("Ignoring malformed callback_data=%r from user=%s", data, user_id)
        answer_callback(token, cb_id, "Unrecognized button.")
        return

    action, signal_id, risk_mode = parsed

    if chat_id != allowed_chat_id:
        log.warning(
            "BLOCKED callback from chat_id=%s user=%s (allowed chat=%s)",
            chat_id, user_id, allowed_chat_id,
        )
        answer_callback(token, cb_id, "Not authorized.")
        return

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """SELECT id, symbol, strategy, direction, status, user_action,
                      telegram_msg_id
                 FROM market.signal_alerts WHERE id = %s""",
            (signal_id,),
        )
        row = cur.fetchone()

        if row is None:
            log.warning("Callback for unknown signal_id=%s", signal_id)
            answer_callback(token, cb_id, "Signal not found.")
            return

        # Idempotency: only 'new' rows are actionable.
        if row["status"] != "new" or row["user_action"] is not None:
            log.info(
                "Signal %s already %s (user_action=%s) — ignoring duplicate %s",
                signal_id, row["status"], row["user_action"], action,
            )
            already = (row["user_action"] or row["status"]).upper()
            answer_callback(token, cb_id, f"Already {already}.")
            # Make sure the buttons are gone even if we got here by accident.
            if row["telegram_msg_id"]:
                clear_message_keyboard(token, chat_id, row["telegram_msg_id"])
            return

        # Optional sanity check: the message the user pressed should be the
        # one we recorded as the alert message. Log a warning if not, but
        # still honor the decision (user may have forwarded buttons etc.).
        if row["telegram_msg_id"] and message_id and row["telegram_msg_id"] != message_id:
            log.warning(
                "Signal %s msg_id mismatch: db=%s callback=%s",
                signal_id, row["telegram_msg_id"], message_id,
            )

        now = datetime.now(timezone.utc)
        if action == "approve":
            cur.execute(
                """UPDATE market.signal_alerts
                      SET user_action      = 'approved',
                          status           = 'approved',
                          approved_at      = %s,
                          approval_chat_id = %s,
                          approval_user_id = %s,
                          risk_mode        = %s
                    WHERE id = %s""",
                (now, chat_id, user_id, risk_mode, signal_id),
            )
            # Label for reply message
            mode_labels = {
                "conservative": "🔵 CONSERVATIVE",
                "aggressive": "🟡 AGGRESSIVE",
                "standard": "✅ APPROVED",
            }
            verdict_emoji = "✅"
            verdict_label = mode_labels.get(risk_mode, "✅ APPROVED")
        else:
            cur.execute(
                """UPDATE market.signal_alerts
                      SET user_action      = 'denied',
                          status           = 'denied',
                          denied_at        = %s,
                          approval_chat_id = %s,
                          approval_user_id = %s
                    WHERE id = %s""",
                (now, chat_id, user_id, signal_id),
            )
            verdict_emoji = "❌"
            verdict_label = "DENIED"
        conn.commit()

    log.info(
        "Signal %s (%s %s %s) %s by user=%s",
        signal_id, row["symbol"], row["strategy"], row["direction"],
        verdict_label, user_name,
    )

    # UI: remove buttons + reply with verdict.
    if row["telegram_msg_id"]:
        clear_message_keyboard(token, chat_id, row["telegram_msg_id"])
    reply_text = (
        f"{verdict_emoji} <b>{verdict_label}</b> "
        f"by @{user_name} at {now.strftime('%H:%M:%S UTC')}"
    )
    if row["telegram_msg_id"]:
        send_reply(token, chat_id, row["telegram_msg_id"], reply_text)
    answer_callback(token, cb_id, f"{verdict_emoji} {verdict_label}")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

_STOP = False

def _install_signal_handlers() -> None:
    def _handle(_signum, _frame):
        global _STOP
        log.info("Shutdown requested, finishing current iteration...")
        _STOP = True
    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true",
                        help="Poll once (short timeout) and exit; useful for cron/test.")
    parser.add_argument("--reset-offset", action="store_true",
                        help="Discard any saved offset (skip backlog on this run).")
    args = parser.parse_args()

    tg = load_env("telegram")
    token = tg.get("TELEGRAM_BOT_TOKEN")
    allowed_chat_id_raw = tg.get("TELEGRAM_CHAT_ID")
    if not token or not allowed_chat_id_raw:
        log.error("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID in .env.telegram")
        return 2
    allowed_chat_id = int(allowed_chat_id_raw)

    if args.reset_offset and OFFSET_FILE.exists():
        OFFSET_FILE.unlink()
        log.info("Cleared saved offset.")

    offset = load_offset()
    log.info(
        "Listener starting (allowed_chat_id=%s, offset=%s, mode=%s)",
        allowed_chat_id, offset, "once" if args.once else "forever",
    )

    conn = get_connection()
    _install_signal_handlers()

    poll_timeout = 0 if args.once else LONG_POLL_TIMEOUT

    try:
        while not _STOP:
            try:
                updates = get_updates(token, offset, poll_timeout)
            except TelegramAPIError as e:
                log.error("getUpdates failed, backing off: %s", e)
                time.sleep(5)
                continue
            except urllib.error.URLError as e:
                log.error("Network error, backing off: %s", e)
                time.sleep(5)
                continue

            for upd in updates:
                update_id = upd["update_id"]
                cb = upd.get("callback_query")
                if cb is not None:
                    try:
                        handle_callback(conn, token, allowed_chat_id, cb)
                    except Exception:
                        # Don't let a single bad message take down the loop.
                        log.exception("handle_callback raised")
                        try:
                            conn.rollback()
                        except Exception:
                            pass

                offset = update_id + 1
                save_offset(offset)

            if args.once:
                break
    finally:
        conn.close()

    log.info("Listener stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
