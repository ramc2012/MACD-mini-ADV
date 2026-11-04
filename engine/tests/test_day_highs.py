"""Owner, 4 Nov 2026: list the spots at the day high on the previous 15-min close."""
import sqlite3
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from macd_trader import day_highs
from macd_trader.candle_store import CANDLES_DDL
from macd_trader.sector_wheel import SCHEMA

IST = ZoneInfo("Asia/Kolkata")
DAY = "2026-11-04"


def _at(clock: str) -> int:
    return int(datetime.fromisoformat(f"{DAY}T{clock}:00").replace(tzinfo=IST).timestamp())


def _store(tmp_path, bars: dict[str, list[tuple[float, float, float, float]]]):
    """15-minute bars (open, high, low, close) from 09:15, written as minute bars: open, then the extremes, then the close."""
    path = str(tmp_path / "h.sqlite3")
    with sqlite3.connect(path) as db:
        db.executescript(CANDLES_DDL)
        db.execute(SCHEMA)
        for symbol, rows in bars.items():
            db.execute("INSERT INTO daily_closes VALUES (?,?,?)", (symbol, "2026-11-03", 100.0))
            for n, (o, h, l, c) in enumerate(rows):
                start = _at("09:15") + n * 900
                minutes = [(o, o, o, o), (o, h, o, h), (h, h, l, l)] + [(c, c, c, c)] * 12
                for m, (mo, mh, ml, mc) in enumerate(minutes):
                    db.execute("INSERT INTO historical_candles (symbol,timeframe_seconds,timestamp,open,high,low,close,"
                               "volume,asset_type) VALUES (?,?,?,?,?,?,?,?,?)",
                               (symbol, 60, start + m * 60, mo, mh, ml, mc, 10, "EQUITY"))
    return path


def _now(clock: str, seconds: int = 0) -> datetime:
    return datetime.fromtimestamp(_at(clock) + seconds, UTC)


def test_a_close_beyond_every_earlier_high_is_at_the_day_high(tmp_path):
    path = _store(tmp_path, {
        "NSE:UP-EQ": [(100, 101, 99, 100.5), (100.5, 102, 100, 101.5), (101.5, 103, 101, 102.8)],   # wick high 102 beaten
        "NSE:WICK-EQ": [(100, 104, 99, 100.5), (100.5, 103, 100, 103.5)],                            # 104 wick not beaten
        "NSE:DOWN-EQ": [(100, 101, 99, 99.5), (99.5, 100, 98, 98.2), (98.2, 98.5, 97, 96.9)],
    })
    result = day_highs.build(path, ["NSE:UP-EQ", "NSE:WICK-EQ", "NSE:DOWN-EQ"], _now("10:00", 40))
    rows = {row["label"]: row for row in result["rows"]}
    assert rows["UP"]["at_high"] and rows["UP"]["high_streak"] == 2 and not rows["UP"]["at_low"]
    assert rows["UP"]["change_pct"] == 2.8
    assert not rows["WICK"]["at_high"]                       # a close under an earlier wick is not a day high
    assert rows["DOWN"]["at_low"] and rows["DOWN"]["low_streak"] == 2 and not rows["DOWN"]["at_high"]
    assert result["at_high"] == 1 and result["at_low"] == 1
    assert result["as_of"] == _at("09:45")


def test_only_completed_bars_count_and_the_first_bar_has_nothing_to_beat(tmp_path):
    path = _store(tmp_path, {"NSE:UP-EQ": [(100, 101, 99, 100.5), (100.5, 102, 100, 101.5), (101.5, 103, 101, 102.8)]})
    # 10:00:10: the 09:45 bar ended 10 s ago, inside the grace for its last minute -- the 09:30 bar is the last one.
    early = day_highs.build(path, ["NSE:UP-EQ"], _now("10:00", 10))
    assert early["rows"][0]["bar_start"] == _at("09:30") and early["rows"][0]["high_streak"] == 1
    # 09:31: one completed bar is not enough; before 09:30 the previous session is shown.
    first = day_highs.build(path, ["NSE:UP-EQ"], _now("09:30", 31))
    assert first["rows"] == [] and first["unavailable"][0]["reason"] == "Only one completed bar"
    assert day_highs.session_day(_now("09:29")).isoformat() == "2026-11-03"


def test_a_symbol_whose_feed_stopped_is_marked_stale(tmp_path):
    path = _store(tmp_path, {"NSE:UP-EQ": [(100, 101, 99, 100.5), (100.5, 102, 100, 101.5)],
                             "NSE:LIVE-EQ": [(100, 101, 99, 100.5), (100.5, 102, 100, 101.5), (101.5, 103, 101, 102.8)]})
    result = day_highs.build(path, ["NSE:UP-EQ", "NSE:LIVE-EQ"], _now("10:00", 40))
    rows = {row["label"]: row for row in result["rows"]}
    assert rows["UP"]["stale"] and not rows["LIVE"]["stale"]


def test_after_the_cash_close_the_header_bar_is_the_one_most_rows_end_on(tmp_path):
    bars = [(100, 101, 99, 100.5)] * 25
    path = _store(tmp_path, {"NSE:A-EQ": bars[:24], "NSE:B-EQ": bars[:24], "NSE:NIFTY50-INDEX": bars})
    result = day_highs.build(path, ["NSE:A-EQ", "NSE:B-EQ", "NSE:NIFTY50-INDEX"], _now("21:00"))
    rows = {row["symbol"]: row for row in result["rows"]}
    assert result["as_of"] == _at("15:00") and rows["NSE:A-EQ"]["bar_start"] == _at("15:00")
    assert rows["NSE:NIFTY50-INDEX"]["bar_start"] == _at("15:15") and not rows["NSE:NIFTY50-INDEX"]["stale"]
