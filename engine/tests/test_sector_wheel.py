"""Sector wheel: rolling own/relative returns from daily closes, live stand-in for today, month-start leaders."""
import asyncio
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from pytest import approx

from macd_trader import market_calendar, sector_wheel as sw
from macd_trader.models import Candle

IST = ZoneInfo("Asia/Kolkata")


def sessions(end: date, count: int) -> list[date]:
    out, day = [], end
    while len(out) < count:
        if market_calendar.is_trading_day(day):
            out.append(day)
        day -= timedelta(days=1)
    return out[::-1]


class Broker:
    def __init__(self, paths):
        self.paths, self.calls = paths, []

    async def history_range(self, symbol, timeframe_seconds, start, end):
        self.calls.append((symbol, timeframe_seconds))
        return [Candle(symbol, int(datetime.combine(d, datetime.min.time(), IST).timestamp()), p, p, p, p, 1, True)
                for d, p in self.paths[symbol]]


def test_refresh_then_build_relative_returns_and_month_start_leaders(tmp_path):
    db = str(tmp_path / "h.sqlite3")
    days = sessions(date(2026, 10, 16), 70)
    oct_days = [d for d in days if d.month == 10]
    bench = [(d, 100.0) for d in days]
    it = [(d, 100.0 + (5 if d >= oct_days[2] else 0) + (2 if d == days[-1] else 0)) for d in days]   # jumps in Oct's first 3 sessions
    fmcg = [(d, 100.0 - (3 if d >= oct_days[0] else 0)) for d in days]
    broker = Broker({sw.BENCHMARK: bench, "NSE:NIFTYIT-INDEX": it, "NSE:NIFTYFMCG-INDEX": fmcg})
    after_close = datetime(2026, 10, 17, 10, 0, tzinfo=IST)                      # Saturday
    report = asyncio.run(sw.refresh(broker, db, [sw.BENCHMARK, "NSE:NIFTYIT-INDEX", "NSE:NIFTYFMCG-INDEX"], after_close, pacing=0))
    assert report["stale"] == 3 and all(tf == 86_400 for _, tf in broker.calls)
    assert sw.stale_symbols(db, [sw.BENCHMARK], after_close) == []            # nothing to fetch twice
    out = sw.build(db, ["NSE:NIFTYIT-INDEX", "NSE:NIFTYFMCG-INDEX"], {}, after_close)
    rows = {r["symbol"]: r for r in out["rows"]}
    assert out["as_of"] == "2026-10-16" and not out["live"]
    assert rows["NSE:NIFTYIT-INDEX"]["own"]["1D"] == approx((107 / 105 - 1) * 100, abs=0.01)
    assert rows["NSE:NIFTYIT-INDEX"]["relative"]["60D"] == approx(7.0, abs=0.01)
    assert out["month_start"]["leaders"][0] == "NSE:NIFTYIT-INDEX" and out["month_start"]["laggards"][-1] == "NSE:NIFTYFMCG-INDEX"
    assert rows["NSE:NIFTYIT-INDEX"]["month_start"]["rank"] == 1
    assert out["month_start"]["sessions_since"] == len(oct_days) - 3


def test_live_price_stands_in_for_today_during_the_session(tmp_path):
    db = str(tmp_path / "h.sqlite3")
    days = sessions(date(2026, 10, 16), 10)
    for symbol in (sw.BENCHMARK, "NSE:SBIN-EQ"):
        sw.store(db, symbol, [Candle(symbol, int(datetime.combine(d, datetime.min.time(), IST).timestamp()), 100, 100, 100, 100, 1, True) for d in days])
    monday = datetime(2026, 10, 19, 11, 0, tzinfo=IST)
    out = sw.build(db, ["NSE:SBIN-EQ"], {"NSE:SBIN-EQ": 102.0, sw.BENCHMARK: 101.0}, monday)
    row = next(r for r in out["rows"] if r["symbol"] == "NSE:SBIN-EQ")
    assert out["live"] and row["own"]["1D"] == approx(2.0) and row["relative"]["1D"] == approx((1.02 / 1.01 - 1) * 100, abs=0.01)
    assert row["kind"] == "stock" and row["group"] != "stock"


def test_missing_dates_do_not_shift_comparison_windows_and_evening_is_closed(tmp_path):
    db = str(tmp_path / 'h.sqlite3')
    days = sessions(date(2026, 10, 16), 70)
    for symbol in (sw.BENCHMARK, 'NSE:SBIN-EQ'):
        selected = days if symbol == sw.BENCHMARK else [d for d in days if d != days[-6]]
        sw.store(db, symbol, [Candle(symbol, int(datetime.combine(d, datetime.min.time(), IST).timestamp()),
                 100, 100, 100, 100, 1, True) for d in selected])
    out = sw.build(db, ['NSE:SBIN-EQ'], {'NSE:SBIN-EQ': 200}, datetime(2026, 10, 16, 18, 0, tzinfo=IST))
    row = next(r for r in out['rows'] if r['symbol'] == 'NSE:SBIN-EQ')
    assert not out['live'] and row['price'] == 100
    assert row['own']['5D'] is None and row['relative']['5D'] is None
    assert row['own']['20D'] == 0


def test_partial_refresh_retries_failed_symbols_before_marking_day_done(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from macd_trader.engine import TradingEngine
    db = str(tmp_path / 'h.sqlite3')
    symbol = sw.BENCHMARK
    sw.store(db, symbol, [Candle(symbol, 1792108800, 100, 100, 100, 100, 1, True)])
    calls = []
    async def refresh(*_args):
        calls.append(1)
        return {'errors': ['retry me'] if len(calls) == 1 else [], 'stored': 1}
    monkeypatch.setattr(sw, 'refresh', refresh)
    monkeypatch.setattr(sw, 'stale_symbols', lambda *_: [])
    monkeypatch.setattr(sw, 'build', lambda *_: {'rows': [{}]})
    engine = SimpleNamespace(broker=None, settings=SimpleNamespace(research_database_path=db),
                             latest_ticks={}, _wheel_symbols=lambda: [symbol])
    async def run():
        await TradingEngine.sector_wheel(engine)
        await engine._wheel_refresh_task
        assert not hasattr(engine, '_wheel_refreshed_for')
        await TradingEngine.sector_wheel(engine)
        await engine._wheel_refresh_task
        assert engine._wheel_refreshed_for
        await TradingEngine.sector_wheel(engine)
    asyncio.run(run())
    assert len(calls) == 2
