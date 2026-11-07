"""Sector coil: base dates, checkpoints, own/relative returns; daily closes filled from stored minute bars."""
import asyncio
import sqlite3
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from pytest import approx

from macd_trader import market_calendar, sector_coil as sc, sector_wheel as sw
from macd_trader.models import Candle

IST = ZoneInfo("Asia/Kolkata")


def sessions(start: date, end: date) -> list[date]:
    out, day = [], start
    while day <= end:
        if market_calendar.is_trading_day(day):
            out.append(day)
        day += timedelta(days=1)
    return out


def seed(db: str, symbol: str, prices: dict[date, float]) -> None:
    sw.store(db, symbol, [Candle(symbol, int(datetime.combine(d, datetime.min.time(), IST).timestamp()), p, p, p, p, 1, True)
                          for d, p in prices.items()])


def test_ytd_monthly_coil_turns_at_month_ends_and_reports_relative(tmp_path):
    db = str(tmp_path / "h.sqlite3")
    days = sessions(date(2025, 12, 1), date(2026, 3, 13))
    seed(db, sw.BENCHMARK, {d: 100.0 + (10 if d.year == 2026 else 0) for d in days})          # +10% from 1 Jan
    # IT: up 30% by end Feb, back to +5% by 13 Mar
    seed(db, "NSE:NIFTYIT-INDEX", {d: 100.0 if d.year == 2025 else 130.0 if d < date(2026, 3, 1) else 105.0 for d in days})
    after_close = datetime(2026, 3, 14, 10, 0, tzinfo=IST)                                     # Saturday
    out = sc.build(db, ["NSE:NIFTYIT-INDEX"], {}, after_close, "YTD", "M", "own")
    assert out["base_day"] == "2025-12-31" and out["as_of"] == "2026-03-13"
    assert out["points"] == ["2026-01-30", "2026-02-27", "2026-03-13"]
    it = next(r for r in out["rows"] if r["symbol"] == "NSE:NIFTYIT-INDEX")
    assert it["values"] == [0.0, 30.0, 30.0, 5.0] and it["kind"] == "sector"
    rel = sc.build(db, ["NSE:NIFTYIT-INDEX"], {}, after_close, "YTD", "M", "relative")
    it = next(r for r in rel["rows"] if r["symbol"] == "NSE:NIFTYIT-INDEX")
    assert it["values"][-1] == approx((1.05 / 1.10 - 1) * 100, abs=0.01)
    assert next(r for r in rel["rows"] if r["kind"] == "benchmark")["values"] == [0.0, 0.0, 0.0, 0.0]


def test_template_and_weekly_steps_and_live_price(tmp_path):
    db = str(tmp_path / "h.sqlite3")
    days = sessions(date(2026, 8, 1), date(2026, 11, 6))
    seed(db, sw.BENCHMARK, {d: 100.0 for d in days})
    seed(db, "NSE:NIFTYAUTO-INDEX", {d: 100.0 for d in days})
    saturday = datetime(2026, 11, 7, 10, 0, tzinfo=IST)
    template = sc.build(db, ["NSE:NIFTYAUTO-INDEX"], {}, saturday, "3M", "TEMPLATE", "own")
    assert template["base_day"] == "2026-08-06" and template["points"] == ["2026-10-06", "2026-11-06"]
    weekly = sc.build(db, ["NSE:NIFTYAUTO-INDEX"], {}, saturday, "1M", "W", "own")
    assert weekly["points"][-1] == "2026-11-06" and all(a < b for a, b in zip(weekly["points"], weekly["points"][1:]))
    monday = datetime(2026, 11, 9, 11, 0, tzinfo=IST)
    live = sc.build(db, ["NSE:NIFTYAUTO-INDEX"], {"NSE:NIFTYAUTO-INDEX": 103.0, sw.BENCHMARK: 101.0}, monday, "1M", "M", "own")
    auto = next(r for r in live["rows"] if r["symbol"] == "NSE:NIFTYAUTO-INDEX")
    assert live["live"] and live["points"][-1] == "2026-11-09" and auto["values"][-1] == approx(3.0)


def test_symbol_without_base_close_is_listed_missing_and_gaps_carry_forward(tmp_path):
    db = str(tmp_path / "h.sqlite3")
    days = sessions(date(2025, 12, 1), date(2026, 2, 13))
    seed(db, sw.BENCHMARK, {d: 100.0 for d in days})
    seed(db, "NSE:NIFTYMEDIA-INDEX", {d: 100.0 for d in days if d >= date(2026, 1, 5)})
    seed(db, "NSE:NIFTYPHARMA-INDEX", {d: 100.0 + (2 if d.year == 2026 else 0) for d in days if d != date(2026, 1, 30)})
    out = sc.build(db, ["NSE:NIFTYMEDIA-INDEX", "NSE:NIFTYPHARMA-INDEX"], {}, datetime(2026, 2, 14, 10, 0, tzinfo=IST))
    assert out["missing"] == ["NSE:NIFTYMEDIA-INDEX"]
    pharma = next(r for r in out["rows"] if r["symbol"] == "NSE:NIFTYPHARMA-INDEX")
    assert pharma["values"] == [0.0, 2.0, 2.0]                                           # 30 Jan gap uses 29 Jan


def test_fill_from_minutes_stores_last_bar_before_1530_and_keeps_existing_rows(tmp_path):
    db = str(tmp_path / "h.sqlite3")
    with sqlite3.connect(db) as conn:
        conn.execute("""CREATE TABLE historical_candles (symbol TEXT NOT NULL, timeframe_seconds INTEGER NOT NULL,
            timestamp INTEGER NOT NULL, open REAL, high REAL, low REAL, close REAL, volume INTEGER, asset_type TEXT,
            expiry TEXT, PRIMARY KEY(symbol, timeframe_seconds, timestamp))""")
        def bar(day, clock, close):
            ts = int(datetime.combine(day, clock, IST).timestamp())
            conn.execute("INSERT INTO historical_candles VALUES (?,?,?,?,?,?,?,?,?,?)",
                         ("NSE:NIFTYIT-INDEX", 60, ts, close, close, close, close, 0, "INDEX", None))
        from datetime import time
        bar(date(2026, 11, 5), time(15, 28), 100.0)
        bar(date(2026, 11, 5), time(15, 29), 101.0)
        bar(date(2026, 11, 5), time(15, 35), 999.0)                                     # after the close: ignored
        bar(date(2026, 11, 6), time(15, 29), 102.0)
    seed(db, "NSE:NIFTYIT-INDEX", {date(2026, 11, 6): 102.5})                              # broker row wins
    saturday = datetime(2026, 11, 7, 10, 0, tzinfo=IST)
    assert sw.fill_from_minutes(db, ["NSE:NIFTYIT-INDEX"], saturday) == 1
    with sqlite3.connect(db) as conn:
        assert dict(conn.execute("SELECT day, close FROM daily_closes")) == {"2026-11-05": 101.0, "2026-11-06": 102.5}


def test_refresh_under_recovery_hold_fills_minutes_and_stops_after_one_refusal():
    class Held:
        calls = 0

        async def history_range(self, *_):
            Held.calls += 1
            raise RuntimeError("Historical broker downloads disabled during local-archive recovery")

    import tempfile, os
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "h.sqlite3")
        report = asyncio.run(sw.refresh(Held(), db, ["NSE:NIFTYIT-INDEX", "NSE:NIFTYAUTO-INDEX"],
                                        datetime(2026, 11, 7, 10, 0, tzinfo=IST), pacing=0))
    assert report["held"] and report["errors"] == [] and Held.calls == 1 and report["filled_from_minutes"] == 0
