"""Previous month, earlier weeks, this week, the last sessions and today."""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from macd_trader.profile_series import build_series

IST = ZoneInfo("Asia/Kolkata")
SYMBOL = "NSE:X-EQ"


def _history(tmp_path, days):
    connection = sqlite3.connect(str(tmp_path / "h.sqlite3"))
    connection.execute("""CREATE TABLE historical_candles (symbol TEXT, timeframe_seconds INTEGER,
        timestamp INTEGER, open REAL, high REAL, low REAL, close REAL, volume INTEGER)""")
    rows = []
    for index, day in enumerate(days):
        start = datetime.fromisoformat(f"{day}T09:15:00").replace(tzinfo=IST)
        for minute in range(0, 375, 5):
            price = 100 + index + (minute % 30) / 10
            rows.append((SYMBOL, 60, int((start + timedelta(minutes=minute)).timestamp()),
                         price, price + 0.5, price - 0.5, price, 100))
    connection.executemany("INSERT INTO historical_candles VALUES (?,?,?,?,?,?,?,?)", rows)
    connection.commit()
    return connection


def _weekdays(first: str, last: str) -> list[str]:
    day, end, out = date.fromisoformat(first), date.fromisoformat(last), []
    while day <= end:
        if day.weekday() < 5:
            out.append(day.isoformat())
        day += timedelta(days=1)
    return out


def test_mid_month_series_order(tmp_path):
    history = _history(tmp_path, _weekdays("2026-08-17", "2026-09-29"))
    series = build_series(None, history, SYMBOL, "2026-09-29", 0.5)
    kinds = [(column["kind"], column["label"]) for column in series["columns"]]
    assert kinds[0] == ("month", "Aug 2026")
    assert [label for kind, label in kinds if kind == "week"] == [
        "Week of 31 Aug", "Week of 07 Sep", "Week of 14 Sep", "Week of 21 Sep", "This week (from 28 Sep)"]
    assert [column["from"] for column in series["columns"] if column["kind"] == "day"] == [
        "2026-09-22", "2026-09-23", "2026-09-24", "2026-09-25", "2026-09-28"]
    assert series["columns"][-1]["kind"] == "today"
    month = series["columns"][0]
    assert month["sessions"] == len(_weekdays("2026-08-17", "2026-08-31"))
    # One row grid for every column.
    assert all(abs(row["price"] / 0.5 - round(row["price"] / 0.5)) < 1e-6
               for column in series["columns"] for row in column["rows"])


def test_start_of_month_is_previous_month_this_week_and_days(tmp_path):
    history = _history(tmp_path, _weekdays("2026-08-03", "2026-09-02"))
    series = build_series(None, history, SYMBOL, "2026-09-02", 0.5)
    kinds = [column["kind"] for column in series["columns"]]
    assert kinds[0] == "month" and series["columns"][0]["label"] == "Aug 2026"
    assert [c["label"] for c in series["columns"] if c["kind"] == "week"] == ["This week (from 31 Aug)"]
    assert kinds.count("day") == 5 and kinds[-1] == "today"


def test_no_previous_month_is_noted(tmp_path):
    history = _history(tmp_path, _weekdays("2026-09-08", "2026-09-29"))
    series = build_series(None, history, SYMBOL, "2026-09-29", 0.5)
    assert series["columns"][0]["kind"] != "month"
    assert series["notes"] == ["Aug 2026: no stored minute bars for a monthly profile"]
