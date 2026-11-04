"""Spot instruments at the day's high on a 15-minute closing basis (market breadth, MACD lane).

Owner, 4 Nov 2026: "In macd lane market breadth filter spot instruments which are at day high on previous 15min
closing basis".

At the day high: the last completed 15-minute bar of the session closed at or above every high printed earlier in
the day -- a new day high confirmed by the close, not by a wick. The mirror (closed at or below every earlier low)
is the day low. The first bar has nothing earlier to beat, so the first list is at 09:45 IST. ``streak`` counts the
bars in a row, ending with the last one, that did so.

Owner, 4 Nov 2026 (later): "Also include atm CE, PE list in 15 min closing day high" -- today's ATM legs (the traded
watchlist) are judged the same way on their own premium, beside the spots.

Read-only: today's stored minute bars (historical_candles, written from the live feed every ~10 s) aggregated to
15 minutes; the previous close comes from daily_closes, else the previous session's last stored minute. No broker
calls, no execution hooks.
"""
from __future__ import annotations

import math
import sqlite3
from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from . import market_calendar
from .chart_history import aggregate_session_candles
from .models import Candle
from .sector_wheel import label
from .sectors import sector_of
from .sessions import session_close

IST = ZoneInfo("Asia/Kolkata")
TIMEFRAME = 900
OPEN = time(9, 15)
# A bar's last minute reaches the store when the next minute starts and the writer flushes (every 10 s).
GRACE_SECONDS = 30


def session_day(now: datetime) -> date:
    """The latest trading day whose first 15-minute bar has completed (before 09:30, the previous session)."""
    local = now.astimezone(IST)
    day = local.date()
    first_done = datetime.combine(day, OPEN, IST) + timedelta(seconds=TIMEFRAME + GRACE_SECONDS)
    if not market_calendar.is_trading_day(day) or local < first_done:
        day -= timedelta(days=1)
        while not market_calendar.is_trading_day(day):
            day -= timedelta(days=1)
    return day


def _streak(bars: list[Candle], high: bool) -> int:
    """Bars in a row, ending with the last, that closed beyond every earlier extreme of the day."""
    count = 0
    for i in range(len(bars) - 1, 0, -1):
        earlier = bars[:i]
        beyond = (bars[i].close >= max(b.high for b in earlier)) if high else \
                 (bars[i].close <= min(b.low for b in earlier))
        if not beyond:
            break
        count += 1
    return count


def evaluate(instrument: dict, bars: list[Candle], previous_close: float | None, expected_last: int) -> dict | None:
    """One instrument's row from its completed 15-minute bars of the session, oldest first."""
    symbol = instrument["symbol"]
    if len(bars) < 2:
        return None
    last, earlier = bars[-1], bars[:-1]
    high_before = max(b.high for b in earlier)
    low_before = min(b.low for b in earlier)
    day_high, day_low = max(high_before, last.high), min(low_before, last.low)
    return {
        **instrument, "label": instrument.get("underlying") or label(symbol),
        "group": sector_of(instrument.get("spot_symbol") or symbol),
        "index": (instrument.get("spot_symbol") or symbol).endswith("-INDEX"),
        "bar_start": last.timestamp, "stale": last.timestamp != expected_last,
        "close": last.close, "day_high": day_high, "day_low": day_low,
        "high_before": high_before, "low_before": low_before,
        "at_high": last.close >= high_before, "at_low": last.close <= low_before,
        "high_streak": _streak(bars, True), "low_streak": _streak(bars, False),
        "from_high_pct": round((last.close / day_high - 1) * 100, 3),
        "from_low_pct": round((last.close / day_low - 1) * 100, 3),
        "change_pct": round((last.close / previous_close - 1) * 100, 3) if previous_close else None,
        "bars": len(bars),
    }


def build(database_path: str, symbols: list[str], now: datetime | None = None, options: list[dict] | None = None) -> dict:
    """``symbols`` are spots; ``options`` today's ATM legs (symbol, underlying, spot_symbol, option_type, strike, expiry)."""
    now = now or datetime.now(UTC)
    now_ts = int(now.timestamp())
    day = session_day(now)
    opened = int(datetime.combine(day, OPEN, IST).timestamp())
    rows, unavailable = [], []
    with sqlite3.connect(f"file:{database_path}?mode=ro", uri=True, timeout=15) as db:
        instruments = [{"symbol": s, "kind": "SPOT"} for s in symbols] + \
            [{**leg, "kind": leg["option_type"]} for leg in options or []]
        for instrument in instruments:
            symbol = instrument["symbol"]
            closes_at = int(datetime.combine(day, session_close(symbol, day), IST).timestamp())
            # The latest bar that should be complete: after the close, the session's last bar; before it, the last
            # bar that ended at least GRACE_SECONDS ago.
            settled = now_ts - GRACE_SECONDS
            expected_last = opened + ((closes_at - opened - 1) // TIMEFRAME) * TIMEFRAME if settled >= closes_at \
                else opened + ((settled - opened) // TIMEFRAME - 1) * TIMEFRAME
            source = db.execute(
                "SELECT timestamp,open,high,low,close,volume FROM historical_candles "
                "WHERE symbol=? AND timeframe_seconds=60 AND timestamp>=? AND timestamp<? ORDER BY timestamp",
                (symbol, opened, closes_at)).fetchall()
            minutes = [Candle(symbol, int(r[0]), *[float(v) for v in r[1:5]], int(r[5] or 0), True)
                       for r in source if all(v is not None and math.isfinite(v) and v > 0 for v in r[1:5])]
            bars = [bar for bar in aggregate_session_candles(minutes, TIMEFRAME)
                    if bar.timestamp <= expected_last]
            bars.sort(key=lambda bar: bar.timestamp)
            try:
                found = db.execute("SELECT close FROM daily_closes WHERE symbol=? AND day<? ORDER BY day DESC LIMIT 1",
                                   (symbol, day.isoformat())).fetchone()
            except sqlite3.OperationalError as exc:
                if "no such table" not in str(exc):
                    raise
                found = None
            if not found:       # no daily close stored: the previous session's last stored minute
                found = db.execute("SELECT close FROM historical_candles WHERE symbol=? AND timeframe_seconds=60 "
                                   "AND timestamp<? AND timestamp>=? ORDER BY timestamp DESC LIMIT 1",
                                   (symbol, opened, opened - 10 * 86400)).fetchone()
            row = evaluate(instrument, bars, float(found[0]) if found and found[0] else None, expected_last)
            if row is None:
                unavailable.append({"symbol": symbol, "kind": instrument["kind"], "label": instrument.get("underlying") or label(symbol),
                                    "reason": "No stored minute bars today" if not bars else "Only one completed bar"})
            else:
                row["bar_end"] = min(row["bar_start"] + TIMEFRAME, closes_at)      # F&O's last bar is 15:30-15:40
                rows.append(row)
    kinds = ("SPOT", "CE", "PE")
    # Per kind, the bar most rows end on: after 15:15 cash stocks are done while indices print to 15:30 and options
    # to 15:40.
    as_of, as_of_end = {}, {}
    for kind in kinds:
        bars = Counter((row["bar_start"], row["bar_end"]) for row in rows if row["kind"] == kind and not row["stale"])
        as_of[kind], as_of_end[kind] = bars.most_common(1)[0][0] if bars else (None, None)
    return {
        "session": day.isoformat(), "timeframe_seconds": TIMEFRAME, "generated_at": now.isoformat(),
        "as_of": as_of, "as_of_end": as_of_end, "rows": rows, "unavailable": unavailable, "evaluated": len(instruments),
        "at_high": {kind: sum(row["at_high"] for row in rows if row["kind"] == kind) for kind in kinds},
        "at_low": {kind: sum(row["at_low"] for row in rows if row["kind"] == kind) for kind in kinds},
        "listed": {kind: sum(row["kind"] == kind for row in rows) for kind in kinds},
        "rules": {
            "high": "The last completed 15-min bar closed at or above every earlier high of the day",
            "low": "The last completed 15-min bar closed at or below every earlier low of the day",
            "streak": "Completed bars in a row, ending with the last, that did so",
            "first_list": "09:45 IST (the first bar has nothing earlier to beat)",
            "options": "ATM CE and PE: today's ATM leg of each underlying (the traded watchlist), on its own premium; a leg "
                       "with no trade in its latest 15 minutes is marked stale",
            "source": "Stored minute bars from the live feed; change is against the previous session's last stored minute; no broker calls",
        },
    }
