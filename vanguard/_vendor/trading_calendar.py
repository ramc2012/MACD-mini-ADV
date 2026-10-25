from __future__ import annotations

import json
import os
from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

IST = timezone(timedelta(hours=5, minutes=30))


def _resolve_calendar_file() -> Path:
    env_path = os.environ.get("TRADING_CALENDAR_FILE", "").strip()
    if env_path:
        return Path(env_path)
    docker_path = Path("/app/trading_calendar.json")
    if docker_path.parent.is_dir():
        return docker_path
    return Path(__file__).resolve().parents[1] / "runtime" / "trading_calendar.json"


_CALENDAR_FILE = _resolve_calendar_file()

# ─── NSE session structure, effective 2026-08-03 ────────────────────────────
#
# NSE circular (2026-05-30) changed two things from 03 Aug 2026:
#
#   1. EQUITY DERIVATIVES now close at 15:40 (was 15:30) — index AND stock
#      F&O. Options keep trading for ten minutes after the cash close.
#   2. A CLOSING AUCTION SESSION (CAS) replaced the VWAP closing-price method
#      for F&O-eligible STOCKS: continuous cash trading in those names stops
#      at 15:15, the auction runs 15:15–15:35, and the auction price is the
#      official close.
#
# Consequences this codebase must respect:
#   * anything that collects DERIVATIVE data or marks/exits option positions
#     must stay awake until 15:40, not 15:30 — on 2026-08-03 (day one of the
#     change) option premium bars died out around 15:20 and the app captured
#     none of the final, most important twenty minutes of F&O trading;
#   * F&O-stock cash quotes GO QUIET 15:15–15:35 by design. That is the
#     auction, not a stale feed — freshness guards must not read it as one.
#     (Observed 2026-08-03: 1-minute spot keys fell 92 → 15 across that
#     window, then recovered to ~80 once CAS concluded.)
#   * NON-F&O cash equities are unchanged at 15:30.
#
# The morning pre-open auction rework lands 2026-09-07 and is NOT modelled
# here yet.
# DELIBERATELY NOT added to _DEFAULT_SESSIONS: `is_exchange_open` returns True
# if ANY session covers `now`, so listing a 15:40 derivatives session there
# would silently extend the NSE session for all ~18 `is_exchange_open` callers
# at once — including cash-equity lanes that must still stop at 15:30. The new
# windows are exposed as explicit constants + helpers instead, so a caller
# opts in to the derivatives clock rather than inheriting it by accident.
NSE_OPEN = "09:15"
NSE_CASH_CLOSE = "15:30"
NSE_DERIVATIVES_CLOSE = "15:40"
NSE_CAS_OPEN = "15:15"
NSE_CAS_CLOSE = "15:35"

_DEFAULT_SESSIONS: dict[str, list[dict[str, str]]] = {
    "NSE": [{"key": "regular", "label": "Regular", "open": "09:15", "close": "15:30"}],
    # BSE (SENSEX/BANKEX) was missing, so `_sessions_for_date("BSE", ...)` was
    # always empty: `is_exchange_open("BSE")` never returned True and the
    # directional lane could not open a SENSEX entry from 2026-06-04 onward.
    "BSE": [{"key": "regular", "label": "Regular", "open": "09:15", "close": "15:30"}],
    "MCX": [
        {"key": "morning", "label": "Morning", "open": "09:00", "close": "17:00"},
        {"key": "evening", "label": "Evening", "open": "17:00", "close": "23:30"},
    ],
}

_NSE_2026_CLOSED = [
    ("2026-01-26", "Republic Day"),
    ("2026-03-03", "Holi"),
    ("2026-03-26", "Shri Ram Navami"),
    ("2026-03-31", "Shri Mahavir Jayanti"),
    ("2026-04-03", "Good Friday"),
    ("2026-04-14", "Dr. Baba Saheb Ambedkar Jayanti"),
    ("2026-05-01", "Maharashtra Day"),
    ("2026-05-28", "Bakri Id"),
    ("2026-06-26", "Muharram"),
    ("2026-09-14", "Ganesh Chaturthi"),
    ("2026-10-02", "Mahatma Gandhi Jayanti"),
    ("2026-10-20", "Dussehra"),
    ("2026-11-10", "Diwali-Balipratipada"),
    ("2026-11-24", "Prakash Gurpurb Sri Guru Nanak Dev"),
    ("2026-12-25", "Christmas"),
]

_MCX_2026_CLOSED = [
    ("2026-01-01", "New Year Day"),
    ("2026-01-26", "Republic Day"),
    ("2026-03-03", "Holi"),
    ("2026-03-26", "Shri Ram Navami"),
    ("2026-03-31", "Shri Mahavir Jayanti"),
    ("2026-04-03", "Good Friday"),
    ("2026-04-14", "Dr. Baba Saheb Ambedkar Jayanti"),
    ("2026-05-01", "Maharashtra Day"),
    ("2026-10-02", "Mahatma Gandhi Jayanti"),
    ("2026-12-25", "Christmas"),
]

_MCX_2026_EVENING_ONLY = [
    ("2026-05-28", "Bakri Id"),
    ("2026-06-26", "Muharram"),
    ("2026-09-14", "Ganesh Chaturthi"),
    ("2026-10-20", "Dussehra"),
    ("2026-11-10", "Diwali-Balipratipada"),
    ("2026-11-24", "Prakash Gurpurb Sri Guru Nanak Dev"),
]


def _default_exceptions(exchange: str) -> list[dict[str, Any]]:
    if exchange in {"NSE", "BSE"}:
        return [
            {"date": day, "name": name, "status": "closed", "sessions": []}
            for day, name in _NSE_2026_CLOSED
        ]
    if exchange == "MCX":
        rows = [
            {"date": day, "name": name, "status": "closed", "sessions": []}
            for day, name in _MCX_2026_CLOSED
        ]
        rows.extend(
            {"date": day, "name": name, "status": "partial", "sessions": ["evening"]}
            for day, name in _MCX_2026_EVENING_ONLY
        )
        return sorted(rows, key=lambda item: str(item["date"]))
    return []


def _default_config() -> dict[str, Any]:
    return {
        "enabled": True,
        "timezone": "Asia/Kolkata",
        "exchanges": {
            exchange: {
                "enabled": True,
                "sessions": deepcopy(sessions),
                "exceptions": _default_exceptions(exchange),
            }
            for exchange, sessions in _DEFAULT_SESSIONS.items()
        },
    }


def _parse_time(value: Any) -> time:
    hour, minute = str(value or "00:00").split(":", 1)
    return time(int(hour), int(minute[:2]))


def _parse_date(value: Any) -> date:
    return date.fromisoformat(str(value or "").strip()[:10])


def _normalize_exception(raw: dict[str, Any]) -> dict[str, Any] | None:
    try:
        day = _parse_date(raw.get("date")).isoformat()
    except Exception:
        return None
    status = str(raw.get("status") or "closed").strip().lower()
    if status not in {"closed", "partial", "open"}:
        status = "closed"
    sessions = [
        str(item).strip().lower()
        for item in (raw.get("sessions") or [])
        if str(item or "").strip()
    ]
    return {
        "date": day,
        "name": str(raw.get("name") or raw.get("description") or "").strip(),
        "status": status,
        "sessions": sessions,
    }


def _normalize_exchange_config(exchange: str, raw: dict[str, Any] | None) -> dict[str, Any]:
    base = {
        "enabled": True,
        "sessions": deepcopy(_DEFAULT_SESSIONS.get(exchange, [])),
        "exceptions": _default_exceptions(exchange),
    }
    if not isinstance(raw, dict):
        return base
    base["enabled"] = bool(raw.get("enabled", base["enabled"]))
    if isinstance(raw.get("sessions"), list) and raw["sessions"]:
        sessions: list[dict[str, str]] = []
        for session in raw["sessions"]:
            if not isinstance(session, dict):
                continue
            key = str(session.get("key") or "").strip().lower()
            open_at = str(session.get("open") or "").strip()
            close_at = str(session.get("close") or "").strip()
            if not key or not open_at or not close_at:
                continue
            sessions.append(
                {
                    "key": key,
                    "label": str(session.get("label") or key.title()).strip(),
                    "open": open_at,
                    "close": close_at,
                }
            )
        if sessions:
            base["sessions"] = sessions
    if isinstance(raw.get("exceptions"), list):
        normalized = [
            item
            for item in (_normalize_exception(row) for row in raw["exceptions"] if isinstance(row, dict))
            if item is not None
        ]
        base["exceptions"] = sorted(normalized, key=lambda item: str(item["date"]))
    return base


def _normalize_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    defaults = _default_config()
    if not isinstance(raw, dict):
        return defaults
    return {
        "enabled": bool(raw.get("enabled", defaults["enabled"])),
        "timezone": "Asia/Kolkata",
        "exchanges": {
            "NSE": _normalize_exchange_config("NSE", (raw.get("exchanges") or {}).get("NSE")),
            "BSE": _normalize_exchange_config("BSE", (raw.get("exchanges") or {}).get("BSE")),
            "MCX": _normalize_exchange_config("MCX", (raw.get("exchanges") or {}).get("MCX")),
        },
    }


class TradingCalendar:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _CALENDAR_FILE
        self._config = _normalize_config(self._load_file())

    def _load_file(self) -> dict[str, Any] | None:
        if not self._path.exists():
            return None
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def reload(self) -> dict[str, Any]:
        self._config = _normalize_config(self._load_file())
        return self.serialize()

    def update(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._config = _normalize_config(payload)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._config, indent=2, sort_keys=True), encoding="utf-8")
        return self.serialize()

    def serialize(self) -> dict[str, Any]:
        return deepcopy(self._config)

    def _exchange_config(self, exchange: str) -> dict[str, Any]:
        exchange_key = exchange.upper()
        return self._config.get("exchanges", {}).get(exchange_key) or _normalize_exchange_config(exchange_key, None)

    def _exception_for(self, exchange: str, target_date: date) -> dict[str, Any] | None:
        for row in self._exchange_config(exchange).get("exceptions") or []:
            if str(row.get("date")) == target_date.isoformat():
                return row
        return None

    def _sessions_for_date(self, exchange: str, target_date: date) -> list[dict[str, str]]:
        exchange_config = self._exchange_config(exchange)
        if target_date.weekday() >= 5:
            return []
        sessions = list(exchange_config.get("sessions") or [])
        if not bool(self._config.get("enabled", True)) or not bool(exchange_config.get("enabled", True)):
            return sessions
        exception = self._exception_for(exchange, target_date)
        if not exception:
            return sessions
        status = str(exception.get("status") or "closed").lower()
        if status == "closed":
            return []
        if status == "open":
            return sessions
        allowed = {str(item).lower() for item in exception.get("sessions") or []}
        return [session for session in sessions if str(session.get("key") or "").lower() in allowed]

    def is_exchange_open(self, exchange: str, now: datetime | None = None) -> bool:
        current = (now or datetime.now(IST)).astimezone(IST)
        for session in self._sessions_for_date(exchange, current.date()):
            if _parse_time(session.get("open")) <= current.time() <= _parse_time(session.get("close")):
                return True
        return False

    def has_exchange_session(self, exchange: str, target_date: date) -> bool:
        return bool(self._sessions_for_date(exchange, target_date))

    def next_exchange_open(self, exchange: str, now: datetime | None = None) -> datetime:
        current = (now or datetime.now(IST)).astimezone(IST)
        if self.is_exchange_open(exchange, current):
            return current
        for offset in range(0, 370):
            target_date = current.date() + timedelta(days=offset)
            for session in self._sessions_for_date(exchange, target_date):
                candidate = datetime.combine(target_date, _parse_time(session.get("open")), tzinfo=IST)
                if candidate > current:
                    return candidate
        return current + timedelta(days=1)

    def exchange_status(self, exchange: str, now: datetime | None = None) -> dict[str, Any]:
        current = (now or datetime.now(IST)).astimezone(IST)
        exception = self._exception_for(exchange, current.date())
        active_session = None
        for session in self._sessions_for_date(exchange, current.date()):
            if _parse_time(session.get("open")) <= current.time() <= _parse_time(session.get("close")):
                active_session = session
                break
        next_open = self.next_exchange_open(exchange, current)
        status = "open" if active_session else "closed"
        reason = None
        if exception and not active_session:
            reason = exception.get("name") or exception.get("status")
        elif current.weekday() >= 5:
            reason = "weekend"
        elif not active_session:
            reason = "outside_session"
        return {
            "exchange": exchange.upper(),
            "as_of": current.isoformat(),
            "is_open": bool(active_session),
            "status": status,
            "active_session": active_session,
            "today_exception": exception,
            "today_sessions": self._sessions_for_date(exchange, current.date()),
            "next_open_at": next_open.isoformat(),
            "reason": reason,
        }

    def status_payload(self, now: datetime | None = None) -> dict[str, Any]:
        current = (now or datetime.now(IST)).astimezone(IST)
        return {
            "config": self.serialize(),
            "status": {
                "as_of": current.isoformat(),
                "NSE": self.exchange_status("NSE", current),
                "MCX": self.exchange_status("MCX", current),
            },
        }


trading_calendar = TradingCalendar()


# ─── NSE derivatives / CAS helpers (2026-08-03 regime) ──────────────────────
#
# Explicit opt-in clocks for the post-2026-08-03 structure. All of these still
# require a real trading day — they compose `has_exchange_session` so exchange
# holidays and the weekend are honoured exactly as before.

def _minute_of_day(now: datetime | None = None) -> tuple[datetime, int]:
    current = (now or datetime.now(IST)).astimezone(IST)
    return current, current.hour * 60 + current.minute


def _hhmm(value: str) -> int:
    parsed = _parse_time(value)
    return parsed.hour * 60 + parsed.minute


def nse_derivatives_open(now: datetime | None = None, *, slack_minutes: int = 0) -> bool:
    """True while NSE EQUITY DERIVATIVES are tradeable (09:15–15:40 IST).

    Use this for anything that collects option/future data or marks and exits
    derivative positions. `slack_minutes` widens both ends for collectors that
    want to catch the opening and closing prints.
    """
    current, minute = _minute_of_day(now)
    if not trading_calendar.has_exchange_session("NSE", current.date()):
        return False
    return (_hhmm(NSE_OPEN) - slack_minutes) <= minute <= (_hhmm(NSE_DERIVATIVES_CLOSE) + slack_minutes)


def nse_in_closing_auction(now: datetime | None = None) -> bool:
    """True during the F&O-stock Closing Auction Session (15:15–15:35 IST).

    F&O-eligible cash names do not trade continuously in this window, so their
    quotes legitimately stop updating. Staleness guards should treat a quiet
    feed here as EXPECTED, never as a dead feed.
    """
    current, minute = _minute_of_day(now)
    if not trading_calendar.has_exchange_session("NSE", current.date()):
        return False
    return _hhmm(NSE_CAS_OPEN) <= minute <= _hhmm(NSE_CAS_CLOSE)


def nse_session_over(now: datetime | None = None) -> bool:
    """True once EVERYTHING on NSE is done for the day (after 15:40 IST).

    Post-close jobs must key off this rather than the old 15:30/15:35 marks —
    those now fire while derivatives are still trading.
    """
    current, minute = _minute_of_day(now)
    if not trading_calendar.has_exchange_session("NSE", current.date()):
        return True
    return minute > _hhmm(NSE_DERIVATIVES_CLOSE)
