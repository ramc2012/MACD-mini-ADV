"""Warm-up history is downloaded as minutes so it can be stored.

On 29 Sep 2026 the September series expired and every underlying rolled to
October. With the strategy on 30-minute bars, each new contract's 90-day
download was fetched at 30-minute resolution and never persisted -- the
store holds only one-minute bars -- so the minute store stayed empty.
"""
import asyncio
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from macd_trader.candle_store import store_historical_candles
from macd_trader.chart_history import load_chart_history
from macd_trader.config import Settings
from macd_trader.engine import NEW_CONTRACT_HISTORY_DAYS, WARMUP_HISTORY_BARS, TradingEngine
from macd_trader.models import Candle

IST = ZoneInfo("Asia/Kolkata")
SYMBOL = "NSE:TEST26OCT100CE"


def _engine(tmp_path, timeframe_seconds):
    return TradingEngine(Settings(
        symbols_csv="NSE:TEST-EQ", tick_capture_enabled=False, timeframe_seconds=timeframe_seconds,
        database_path=str(tmp_path / "book.sqlite3"),
        mp_database_path=str(tmp_path / "mp.sqlite3"),
        research_database_path=str(tmp_path / "history.sqlite3"),
        tick_database_path=str(tmp_path / "ticks.sqlite3"),
        contract_snapshot_path=str(tmp_path / "contracts.json"),
    ))


def _session_minutes(day):
    """One full regular session of minute bars plus a post-close snapshot."""
    start = datetime.combine(day, time(9, 15), IST)
    rows = []
    for index in range(375):
        stamp = int((start + timedelta(minutes=index)).timestamp())
        price = 100.0 + index
        rows.append(Candle(SYMBOL, stamp, price, price + 0.5, price - 0.5, price + 0.25, 10, True))
    after_close = int(datetime.combine(day, time(15, 45), IST).timestamp())
    rows.append(Candle(SYMBOL, after_close, 1.0, 1.0, 1.0, 1.0, 1, True))
    return rows


class _Broker:
    def __init__(self, rows):
        self.rows = rows
        self.range_calls = []
        self.history_calls = []

    async def history_range(self, symbol, timeframe_seconds, start, end):
        self.range_calls.append((symbol, timeframe_seconds, (end - start).days))
        return list(self.rows)

    async def history(self, symbol, timeframe_seconds, count):
        self.history_calls.append((symbol, timeframe_seconds, count))
        return list(self.rows)


def test_new_contract_downloads_minutes_and_builds_strategy_bars(tmp_path):
    engine = _engine(tmp_path, 1800)
    minutes = _session_minutes(datetime(2026, 9, 28).date())
    engine.broker = _Broker(minutes)

    bars, stored = asyncio.run(engine._download_warmup_history(SYMBOL, NEW_CONTRACT_HISTORY_DAYS))

    assert engine.broker.range_calls == [(SYMBOL, 60, NEW_CONTRACT_HISTORY_DAYS)]
    assert stored == minutes
    # 375 session minutes -> twelve full 30-minute buckets and a 15-minute tail;
    # the post-close snapshot never becomes a bar.
    assert len(bars) == 13
    first = datetime.fromtimestamp(bars[0].timestamp, IST)
    assert (first.hour, first.minute) == (9, 15)
    assert bars[0].open == 100.0 and bars[0].close == 129.25 and bars[0].volume == 300


def test_stale_symbol_window_covers_the_warmup_bars(tmp_path):
    engine = _engine(tmp_path, 1800)
    engine.broker = _Broker(_session_minutes(datetime(2026, 9, 28).date()))

    asyncio.run(engine._download_warmup_history(SYMBOL))

    (_, resolution, days), = engine.broker.range_calls
    assert resolution == 60
    sessions_needed = WARMUP_HISTORY_BARS * 1800 / (375 * 60)
    assert days * 5 / 7 >= sessions_needed
    assert days <= NEW_CONTRACT_HISTORY_DAYS


def test_one_minute_strategy_keeps_its_requests(tmp_path):
    engine = _engine(tmp_path, 60)
    minutes = _session_minutes(datetime(2026, 9, 28).date())
    engine.broker = _Broker(minutes)

    bars, stored = asyncio.run(engine._download_warmup_history(SYMBOL))
    assert engine.broker.history_calls == [(SYMBOL, 60, WARMUP_HISTORY_BARS)]
    assert bars == stored == minutes

    asyncio.run(engine._download_warmup_history(SYMBOL, NEW_CONTRACT_HISTORY_DAYS))
    assert engine.broker.range_calls == [(SYMBOL, 60, NEW_CONTRACT_HISTORY_DAYS)]


def test_stored_minutes_rebuild_the_same_strategy_bars_after_restart(tmp_path):
    engine = _engine(tmp_path, 1800)
    engine.broker = _Broker(_session_minutes(datetime(2026, 9, 28).date()))
    bars, stored = asyncio.run(engine._download_warmup_history(SYMBOL, NEW_CONTRACT_HISTORY_DAYS))

    path = str(tmp_path / "history.sqlite3")
    store_historical_candles(path, stored, None, {SYMBOL: "2026-10-27"})
    reloaded = load_chart_history(path, SYMBOL, 1800, 12, 26, 9)["candles"]

    assert [(c.timestamp, c.open, c.high, c.low, c.close, c.volume) for c in reloaded] == [
        (c.timestamp, c.open, c.high, c.low, c.close, c.volume) for c in bars
    ]


def test_new_contract_with_enough_fresh_stored_bars_needs_no_download(tmp_path):
    engine = _engine(tmp_path, 1800)
    needed = engine.minimum_warmup_bars()
    now = datetime.now(IST)
    fresh = [Candle(SYMBOL, int((now - timedelta(minutes=30 * (needed - i))).timestamp()), 1, 1, 1, 1, 1, True)
             for i in range(needed)]
    assert engine.new_contract_needs_history(fresh) is False
    assert engine.new_contract_needs_history(fresh[1:]) is True
    assert engine.new_contract_needs_history([]) is True


def test_background_backfill_fetches_only_the_older_part_and_stores_it(tmp_path, monkeypatch):
    import macd_trader.engine as engine_module
    monkeypatch.setattr(engine_module, "HISTORY_BACKFILL_PACE_SECONDS", 0)
    engine = _engine(tmp_path, 1800)
    minutes = _session_minutes(datetime(2026, 8, 3).date())
    engine.broker = _Broker(minutes)

    asyncio.run(engine._backfill_contract_history([SYMBOL], {SYMBOL: "2026-10-27"}))

    (_, resolution, days), = engine.broker.range_calls
    # The warm-up already fetched the most recent window; the backfill asks
    # for the rest of the 90 days, overlapping it by a day.
    assert resolution == 60 and days == NEW_CONTRACT_HISTORY_DAYS - engine._warmup_window_days() + 1
    assert engine.history_backfill["stored"] == 1 and engine.history_backfill["pending"] == 0
    assert engine.history_backfill["finished_at"]
    stored = load_chart_history(str(tmp_path / "history.sqlite3"), SYMBOL, 1800, 12, 26, 9)
    assert len(stored["candles"]) == 13
