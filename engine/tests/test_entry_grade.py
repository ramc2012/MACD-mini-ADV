"""The decision matrix's grades are logged at every lane's entries (entry_grade.py): own underlying and NIFTY."""
import sqlite3
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from macd_trader import agentic_features as feat
from macd_trader.entry_grade import EntryGrader

IST = ZoneInfo("Asia/Kolkata")
DAY = date(2026, 10, 13)


def at(day, hh, mm):
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=IST)


def seed(path):
    db = sqlite3.connect(path)
    db.execute("""CREATE TABLE historical_candles (symbol TEXT, timeframe_seconds INTEGER, timestamp INTEGER, open REAL,
                  high REAL, low REAL, close REAL, volume INTEGER)""")
    db.execute("CREATE TABLE daily_closes (symbol TEXT, day TEXT, close REAL)")
    db.execute("CREATE TABLE agent_index_trades (run_id INTEGER, underlying TEXT, day TEXT, side INTEGER)")
    days, d = [], DAY
    while len(days) < 60:
        d = feat.previous_session(d)
        days.append(d)
    rows, closes = [], []
    for sym, base in (("NSE:NIFTY50-INDEX", 23000.0), ("NSE:SBIN-EQ", 800.0)):
        for k, d in enumerate(sorted(days)):
            level = base * (1 + 0.002 * k)                       # a steady uptrend
            closes.append((sym, d.isoformat(), level))
            if k >= 44:                                          # minute bars for the last 16 sessions
                for i in range(375):
                    p = level * (1 + 0.004 * (i / 374 - 0.5))
                    rows.append((sym, 60, int((at(d, 9, 15) + timedelta(minutes=i)).timestamp()), p, p * 1.0001, p * 0.9999, p, 0))
        for i in range(80):                                      # DAY: drives up from the open
            p = base * 1.13 * (1 + 0.0004 * i)
            rows.append((sym, 60, int((at(DAY, 9, 15) + timedelta(minutes=i)).timestamp()), p, p * 1.0002, p * 0.9999, p * 1.0001, 0))
    db.executemany("INSERT INTO historical_candles VALUES (?,?,?,?,?,?,?,?)", rows)
    db.executemany("INSERT INTO daily_closes VALUES (?,?,?)", closes)
    db.execute("INSERT INTO agent_index_trades VALUES (7, 'NIFTY', '2026-10-12', 1)")
    db.commit()


def test_an_entry_is_graded_on_its_own_underlying_and_on_nifty(tmp_path):
    seed(tmp_path / "h.sqlite3")
    grader = EntryGrader(str(tmp_path / "h.sqlite3"))
    out = grader.grade("NSE:SBIN26OCT800CE", at(DAY, 10, 30))
    assert out["side"] == "CE" and out["own"]["symbol"] == "NSE:SBIN-EQ" and out["nifty"]["symbol"] == "NSE:NIFTY50-INDEX"
    for cell in (out["own"], out["nifty"]):
        assert cell["intraday"] in "ABCD" and cell["swing"] in "ABCD" and cell["open_type"] is not None
    assert out["own"]["swing_score"] >= 2                         # with both trends
    assert out["nifty"]["swing_score"] >= out["own"]["swing_score"]   # NIFTY also has the scorecard's CE call
    put = grader.grade("NSE:NIFTY26OCT23000PE", at(DAY, 10, 30))
    assert put["own"] == put["nifty"] and put["nifty"]["swing"] == "D"
    assert grader.health()["graded"] == 2 and grader.error is None


def test_a_missing_store_or_unknown_symbol_grades_nothing(tmp_path):
    assert EntryGrader(None).grade("NSE:SBIN26OCT800CE") is None
    assert EntryGrader(str(tmp_path / "missing.sqlite3")).grade("NSE:SBIN26OCT800CE") is None
    assert EntryGrader(str(tmp_path / "missing.sqlite3")).grade("NSE:SBIN-EQ") is None
