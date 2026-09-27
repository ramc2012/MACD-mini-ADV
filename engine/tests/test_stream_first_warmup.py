from types import SimpleNamespace

from macd_trader.engine import TradingEngine
from macd_trader.models import Candle
from macd_trader.candle_store import store_historical_candles


def test_live_bars_are_replayed_after_history_without_duplicates():
    symbol = "NSE:SBIN-EQ"
    historical = [Candle(symbol, 60, 100, 101, 99, 100, 10, True),
                  Candle(symbol, 120, 100, 102, 99, 101, 11, True)]
    live = [Candle(symbol, 120, 100, 103, 99, 102, 12, True),
            Candle(symbol, 180, 102, 104, 101, 103, 13, True)]
    engine = TradingEngine.__new__(TradingEngine)
    from collections import defaultdict, deque
    engine.history = defaultdict(lambda: deque(maxlen=500))
    engine._warming_symbols = {symbol}
    engine._warm_live_candles = {symbol: live}
    replayed = []
    engine.strategy = SimpleNamespace(
        on_closed_candle=lambda candle, *, warmup: replayed.append((candle, warmup)))

    engine._finish_symbol_warmup(symbol, historical)

    assert [row.timestamp for row in engine.history[symbol]] == [60, 120, 180]
    assert engine.history[symbol][1].close == 102  # live bar wins at overlap
    assert [row.timestamp for row, warmup in replayed if warmup] == [60, 120, 180]
    assert symbol not in engine._warming_symbols


def test_history_cache_keeps_expiry_for_each_option(tmp_path):
    first = Candle("NSE:TEST26SEP100CE", 60, 10, 11, 9, 10, 2, True)
    second = Candle("NSE:TEST26OCT100CE", 60, 11, 12, 10, 11, 3, True)
    database = str(tmp_path / "history.sqlite3")
    store_historical_candles(database, [first, second], expiry_by_symbol={
        first.symbol: "2026-09-29", second.symbol: "2026-10-27",
    })
    import sqlite3
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT symbol, expiry FROM historical_candles ORDER BY symbol").fetchall()
    assert rows == [(second.symbol, "2026-10-27"), (first.symbol, "2026-09-29")]
