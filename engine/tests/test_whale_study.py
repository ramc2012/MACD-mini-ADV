"""Minute-level whale spikes: baselines, labels, control, walk-forward split."""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from macd_trader import whale_study

IST = ZoneInfo("Asia/Kolkata")


def _history(tmp_path):
    connection = sqlite3.connect(str(tmp_path / "h.sqlite3"))
    connection.execute("""CREATE TABLE historical_candles (symbol TEXT, timeframe_seconds INTEGER,
        timestamp INTEGER, open REAL, high REAL, low REAL, close REAL, volume INTEGER)""")
    return connection


def _weekdays(first, count):
    day, out = date.fromisoformat(first), []
    while len(out) < count:
        if day.weekday() < 5:
            out.append(day.isoformat())
        day += timedelta(days=1)
    return out


def test_spikes_against_the_instruments_own_slot_baseline(tmp_path):
    history = _history(tmp_path)
    rows = []
    for d_index, day in enumerate(_weekdays("2026-08-03", 14)):
        start = datetime.fromisoformat(f"{day}T09:15:00").replace(tzinfo=IST)
        price = 1000.0
        for minute in range(375):
            ts = int((start + timedelta(minutes=minute)).timestamp())
            volume = 1_000
            step = (0.2 if minute % 2 else -0.2) + (0.03 if (minute // 30) % 2 else -0.03)   # two-way, drifting
            if d_index >= 6 and minute == 30:          # a buy spike at 09:45 ...
                volume, step = 20_000, 1.0
            if d_index >= 6 and 30 < minute <= 60:     # ... that keeps going for 30 minutes
                step = 0.4 if minute % 2 else -0.1
            open_ = price
            price += step
            rows.append(("NSE:X-EQ", 60, ts, open_, max(open_, price) + 0.1, min(open_, price) - 0.1, price, volume))
    history.executemany("INSERT INTO historical_candles VALUES (?,?,?,?,?,?,?,?)", rows)
    history.commit()
    report = whale_study.run_study(history, "2026-08-01", "2026-08-31", min_sessions=10)
    assert report["events"] == 8                       # days 7-14, once each (cooldown)
    assert report["train"]["n"] + report["test"]["n"] == 8
    assert report["train"]["to"] < report["test"]["from"]
    assert report["test"]["hit_rate"] == 1.0           # every spike ran 30 minutes its way
    assert report["control_test"]["n"] > 0 and report["control_test"]["hit_rate"] < 1.0
    stored = history.execute("SELECT symbol, sessions, p995 FROM whale_minute_baselines").fetchall()
    assert stored and stored[0][0] == "NSE:X-EQ" and stored[0][1] == 10
    assert whale_study.latest(history)["latest"]["study_id"] == report["study_id"]
