"""Whale interest: which underlyings traded a whale-sized minute in the last hour.

Owner, 3 Nov 2026: a whale reading is not an indicator, but it makes the
instrument interesting, and a lane signal on an interesting instrument gets
priority. Measured (research/whale_review_20261103, 35 sessions, 3 Sep - 2 Nov):
a minute's volume against the same instrument's median for that 15-minute slot
of the day (trailing ten sessions, no lookahead), and the size of the next 30
minutes' move against ordinary minutes of the same slot --

* stock cash at 30x the slot median or more: 1.37-1.39x the ordinary move
  (60x+: 1.50x), on 30 of 35 days, every month alike;
* an option contract needs 60x: 1.39-1.42x, 34 of 35 days (30-60x: 1.16x);
* below those the move grows only 0.02-0.23x;
* at no size did the bar's side call the next move (with vs against ~even).

So a reading says "this will move more than usual", never which way. That is
what a long option wants. The baseline is the nightly study's
(whale_study.run_study -> whale_minute_baselines), read as of the last session
before today; the live minute bars are the engine's own.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .whale_study import NOTIONAL_FLOOR

IST = ZoneInfo("Asia/Kolkata")
# Volume multiple of the slot median at which the next 30 minutes' move clearly widens.
THRESHOLDS = {"cash": 30.0, "future": 30.0, "option": 60.0}
# A reading keeps its instrument interesting this long: the widening holds at 60 minutes too.
WINDOW_SECONDS = 3600
# So many underlyings crossing in one minute is a feed catch-up (the minute after a gap carries the gap's
# volume), not whales: 28 Oct 2026 had 208 cash names "spike" together. Such minutes are ignored.
BURST_ROOTS = 25
SESSION_OPEN_MINUTES = 9 * 60 + 15
_FUTURE = re.compile(r"^(?P<root>[A-Z0-9&\-]+?)\d{2}(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)FUT$")


def kind_of(symbol: str) -> str:
    body = symbol.split(":", 1)[-1].upper()
    if body.endswith("-INDEX"):
        return "index"
    if body.endswith("FUT"):
        return "future"
    if body.endswith(("CE", "PE")):
        return "option"
    return "cash"


def root_of(symbol: str) -> str | None:
    """The underlying a contract belongs to: RELIANCE for NSE:RELIANCE-EQ, its futures and its options."""
    body = symbol.split(":", 1)[-1].upper()
    kind = kind_of(symbol)
    if kind == "index":
        return None
    if kind == "cash":
        return body[:-3] if body.endswith("-EQ") else body
    if kind == "future":
        match = _FUTURE.match(body)
        return match["root"] if match else None
    from .option_agent import parse_option
    parsed = parse_option(symbol)
    return parsed["root"] if parsed else None


def slot_of(moment: datetime) -> int:
    local = moment.astimezone(IST)
    return (local.hour * 60 + local.minute - SESSION_OPEN_MINUTES) // 15


class WhaleInterest:
    def __init__(self) -> None:
        self.baselines: dict[str, dict[int, float]] = {}
        self.as_of: str | None = None
        self.day: str | None = None
        self.readings: dict[str, list[dict]] = {}      # root -> today's readings, oldest first
        self._minute_roots: dict[int, set[str]] = {}
        self.error: str | None = None

    def load(self, history_path: str, today: str) -> int:
        """The newest stored baselines from before ``today``; resets the day's readings."""
        connection = sqlite3.connect(f"file:{history_path}?mode=ro", uri=True, timeout=30)
        try:
            row = connection.execute(
                "SELECT MAX(as_of) FROM whale_minute_baselines WHERE as_of < ?", (today,)).fetchone()
            as_of = row[0] if row else None
            rows = connection.execute(
                "SELECT symbol, slot_median FROM whale_minute_baselines WHERE as_of = ?", (as_of,)).fetchall() \
                if as_of else []
        except sqlite3.OperationalError:
            as_of, rows = None, []
        finally:
            connection.close()
        baselines = {}
        for symbol, text in rows:
            try:
                baselines[symbol] = {int(slot): float(value) for slot, value in json.loads(text).items() if value}
            except (TypeError, ValueError):
                continue
        self.baselines, self.as_of = baselines, as_of
        if self.day != today:
            self.day, self.readings, self._minute_roots = today, {}, {}
        return len(baselines)

    def on_minute_bar(self, candle) -> dict | None:
        """Score one closed minute bar; the reading when it is whale-sized."""
        kind = kind_of(candle.symbol)
        threshold = THRESHOLDS.get(kind)
        slots = self.baselines.get(candle.symbol)
        if threshold is None or not slots or not candle.volume or not candle.close:
            return None
        moment = datetime.fromtimestamp(candle.timestamp, UTC)
        median = slots.get(slot_of(moment))
        if not median:
            return None
        multiple = candle.volume / median
        if multiple < threshold or candle.volume * candle.close < NOTIONAL_FLOOR.get(kind, 0):
            return None
        root = root_of(candle.symbol)
        if not root:
            return None
        if moment.astimezone(IST).date().isoformat() != self.day:
            return None
        reading = {"root": root, "symbol": candle.symbol, "leg": kind, "at": int(candle.timestamp),
                   "multiple": round(multiple, 1), "strength": round(multiple / threshold, 2),
                   "volume": int(candle.volume), "notional": round(candle.volume * candle.close)}
        stamp = int(candle.timestamp) // 60 * 60
        self._minute_roots.setdefault(stamp, set()).add(root)
        rows = self.readings.setdefault(root, [])
        if not any(r["symbol"] == candle.symbol and r["at"] == reading["at"] for r in rows):
            rows.append(reading)
        return reading

    def _usable(self, reading: dict) -> bool:
        return len(self._minute_roots.get(reading["at"] // 60 * 60, ())) < BURST_ROOTS

    def reading(self, symbol: str, now: datetime | None = None, window: int = WINDOW_SECONDS) -> dict | None:
        """The strongest reading on ``symbol``'s underlying in the trailing window, or None."""
        root = root_of(symbol) if ":" in symbol else symbol
        rows = self.readings.get(root or "", ())
        if not rows:
            return None
        at = int((now or datetime.now(UTC)).timestamp())
        live = [r for r in rows if at - window <= r["at"] <= at and self._usable(r)]
        if not live:
            return None
        best = max(live, key=lambda r: r["strength"])
        return {**best, "age_minutes": round((at - best["at"]) / 60, 1), "readings": len(live)}

    def live(self, now: datetime | None = None, window: int = WINDOW_SECONDS) -> list[dict]:
        """Every interesting underlying now, strongest first."""
        rows = [self.reading(root, now, window) for root in self.readings]
        return sorted((r for r in rows if r), key=lambda r: -r["strength"])

    def export_payload(self, now: datetime | None = None) -> dict:
        """Today's usable readings per underlying, for lanes in other processes (Vanguard's list).

        Built on the event loop (the readings change there); only the write goes to a thread.
        """
        now = now or datetime.now(UTC)
        return {"day": self.day, "as_of_baseline": self.as_of, "written_at": now.isoformat(),
                "window_seconds": WINDOW_SECONDS, "thresholds": THRESHOLDS,
                "readings": {root: [dict(r) for r in rows if self._usable(r)] for root, rows in self.readings.items()}}

    def status(self, now: datetime | None = None) -> dict:
        live = self.live(now)
        return {"day": self.day, "baseline_as_of": self.as_of, "baselines": len(self.baselines),
                "thresholds": THRESHOLDS, "window_minutes": WINDOW_SECONDS // 60,
                "underlyings_today": sum(1 for rows in self.readings.values() if any(self._usable(r) for r in rows)),
                "burst_minutes_ignored": sum(1 for roots in self._minute_roots.values() if len(roots) >= BURST_ROOTS),
                "live": live[:40], "error": self.error}


def from_file(path: Path, root: str, until: datetime, window: int = WINDOW_SECONDS) -> dict | None:
    """The strongest exported reading on ``root`` in the window ending ``until`` (for other processes)."""
    try:
        payload = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if payload.get("day") != until.astimezone(IST).date().isoformat():
        return None
    at = int(until.timestamp())
    rows = [r for r in payload.get("readings", {}).get(root, ()) if at - window <= r["at"] <= at]
    return max(rows, key=lambda r: r["strength"]) if rows else None


def write_export(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload))
    temporary.replace(path)
