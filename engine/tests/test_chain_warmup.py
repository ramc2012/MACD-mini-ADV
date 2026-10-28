"""A newly selected strike warms its indicators from the option-chain marks already stored (28 Oct 2026)."""
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from macd_trader.chain_warmup import chain_candles, chain_minutes

IST = ZoneInfo("Asia/Kolkata")
SYMBOL = "NSE:NIFTY26N0322600CE"


def store(path, marks):
    db = sqlite3.connect(path)
    db.execute("""CREATE TABLE chain_snapshots (ts INTEGER NOT NULL, underlying TEXT NOT NULL, expiry TEXT NOT NULL,
        strike REAL NOT NULL, option_type TEXT NOT NULL, symbol TEXT NOT NULL, ltp REAL, volume INTEGER, oi INTEGER,
        spot REAL, bid REAL, ask REAL, prev_oi INTEGER, fp REAL, vix REAL, PRIMARY KEY (ts, symbol))""")
    db.executemany("INSERT INTO chain_snapshots(ts,underlying,expiry,strike,option_type,symbol,ltp,volume) VALUES (?,?,?,?,?,?,?,?)",
                   [(int(at.timestamp()), "NIFTY", "2026-11-03", 22600, "CE", SYMBOL, ltp, vol) for at, ltp, vol in marks])
    db.commit()
    db.close()


def test_marks_become_session_bars_with_traded_volume(tmp_path):
    day = datetime(2026, 10, 27, tzinfo=IST)
    marks = [(day.replace(hour=9, minute=15, second=20), 100.0, 1000), (day.replace(hour=9, minute=30, second=15), 104.0, 1600),
             (day.replace(hour=9, minute=44, second=50), 98.0, 2000), (day.replace(hour=9, minute=45, second=30), 101.0, 2500),
             (day.replace(hour=15, minute=50), 90.0, 9000)]                       # after the F&O close: dropped
    path = str(tmp_path / "h.sqlite3")
    store(path, marks)
    minutes = chain_minutes(path, SYMBOL)
    assert [m.volume for m in minutes] == [0, 600, 400, 500, 6500]               # deltas within the day
    bars = chain_candles(path, SYMBOL, 1800)
    assert [(b.open, b.high, b.low, b.close) for b in bars] == [(100.0, 104.0, 98.0, 98.0), (101.0, 101.0, 101.0, 101.0)]
    assert chain_candles(path, "NSE:OTHER26N0322600CE", 1800) == []
    assert chain_candles(str(tmp_path / "missing.sqlite3"), SYMBOL, 1800) == []
