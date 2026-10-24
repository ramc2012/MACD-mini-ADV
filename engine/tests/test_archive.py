"""The market-data archive (archive.py): sealed days are verified, self-describing and idempotent, and the hot
stores only lose what the archive covers."""
import json
import sqlite3
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pyarrow.parquet as pq

from macd_trader import archive as arc
from macd_trader import whale

IST = ZoneInfo("Asia/Kolkata")


def _at(day, hh, mm):
    return int(datetime(2026, 10, day, hh, mm, tzinfo=IST).timestamp())


def test_a_sealed_day_is_verified_hashed_and_described(tmp_path):
    archive = arc.Archive(tmp_path)
    rows = [("NSE:A", 1_000 + i, 1.0, 1.0, 1.0, 1.0, 10, "spot", None) for i in range(5)] + \
           [("NSE:B", 2_000, 2.0, 2.0, 2.0, 2.0, 5, "option", "2026-10-27")]
    info = archive.write("bars_1m", "2026-10-22", "data", iter([rows[:3], rows[3:]]), source="test")
    assert info["rows"] == 6 and info["symbols"] == 2 and len(info["sha256"]) == 64
    folder = tmp_path / "bars_1m" / "day=2026-10-22"
    assert pq.ParquetFile(folder / "data.parquet").metadata.num_rows == 6
    side = json.loads((folder / "data.json").read_text())
    assert side["symbol_rows"] == {"NSE:A": 5, "NSE:B": 1} and (side["min_time"], side["max_time"]) == (1000, 2000)
    assert archive.covers("bars_1m", "2026-10-22", 6) and not archive.covers("bars_1m", "2026-10-22", 7)
    assert archive.symbol_totals("bars_1m") == {"NSE:A": 5, "NSE:B": 1}
    assert not list(folder.glob(".*tmp"))                                   # no temporary files left behind


def test_an_empty_day_is_recorded_so_it_is_not_retried(tmp_path):
    archive = arc.Archive(tmp_path)
    archive.write("bars_1m", "2026-10-02", "data", iter([]), source="test")
    assert archive.rows("bars_1m", "2026-10-02") == 0
    assert not (tmp_path / "bars_1m" / "day=2026-10-02" / "data.parquet").exists()


def test_recovered_parts_add_to_the_day(tmp_path):
    archive = arc.Archive(tmp_path)
    archive.write("bars_1m", "2026-09-10", "data", iter([[("NSE:A", 1, 1, 1, 1, 1, 1, "spot", None)]]), source="t")
    archive.write("bars_1m", "2026-09-10", "recovered-old", iter([[("NSE:GONE", 1, 1, 1, 1, 1, 1, "option", "x")]]),
                  source="old copy")
    assert archive.rows("bars_1m", "2026-09-10") == 2 and archive.rows("bars_1m", "2026-09-10", "data") == 1
    assert archive.symbol_totals("bars_1m") == {"NSE:A": 1, "NSE:GONE": 1}


def test_raw_tick_retention_drops_only_old_days(tmp_path):
    archive = arc.Archive(tmp_path)
    for day in ("2026-06-01", "2026-10-01"):
        archive.write("ticks", day, "data", iter([[("NSE:A", 1, 1.0, 1, 1, 1.0, 1.0, 1, 1, None, 1, 1)]]), source="t")
    assert archive.drop_days_before("ticks", "2026-07-01") == ["2026-06-01"]
    assert archive.days("ticks") == ["2026-10-01"]


def _chain_db(path):
    db = sqlite3.connect(path)
    whale.ensure_schema(db)
    for minute in range(3):
        for strike in (100.0, 110.0):
            db.execute("INSERT INTO chain_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (_at(1, 10, minute), "NIFTY", "2026-10-06", strike, "CE", f"S{strike}", 1, 1, 100 + minute,
                        105.0, 0, 0, 90, 0, None))
    db.execute("INSERT INTO chain_days VALUES ('2026-10-01', 'NIFTY')")
    db.commit()
    return db


def test_old_chains_are_thinned_only_once_archived(tmp_path):
    path = str(tmp_path / "h.sqlite3")
    db = _chain_db(path)
    assert whale.condense_chain_snapshots(db, "2027-03-01", keep_days=90) == 0          # no archive: kept
    archive = arc.Archive(tmp_path / "archive")
    assert whale.condense_chain_snapshots(db, "2027-03-01", keep_days=90, archive=archive) == 0   # not sealed
    report = arc.seal_pending(archive, history_path=path, ticks_path="", today=date(2026, 10, 2), bars_lookback_days=0)
    assert report["errors"] == [] and archive.rows("chains", "2026-10-01") == 6
    assert whale.condense_chain_snapshots(db, "2027-03-01", keep_days=90, archive=archive) == 4   # the close stays
    assert db.execute("SELECT COUNT(*) FROM chain_snapshots").fetchone()[0] == 2
    # a second pass does not re-seal a day whose hot copy shrank
    again = arc.seal_pending(archive, history_path=path, ticks_path="", today=date(2026, 10, 2), bars_lookback_days=0)
    assert again["sealed"] == again["resealed"] == [] and archive.rows("chains", "2026-10-01") == 6


def test_bars_on_a_day_off_the_trading_calendar_are_sealed_too(tmp_path):
    path = str(tmp_path / "h.sqlite3")
    db = sqlite3.connect(path)
    db.execute("""CREATE TABLE historical_candles (symbol TEXT, timeframe_seconds INTEGER, timestamp INTEGER, open REAL,
                  high REAL, low REAL, close REAL, volume INTEGER, asset_type TEXT, expiry TEXT, downloaded_at TEXT,
                  PRIMARY KEY(symbol, timeframe_seconds, timestamp))""")
    saturday, friday = _at(17, 10, 0), _at(16, 10, 0)
    for ts in (friday, saturday):
        db.execute("INSERT INTO historical_candles VALUES ('NSE:A',60,?,1,1,1,1,1,'spot',NULL,'x')", (ts,))
    db.commit()
    archive = arc.Archive(tmp_path / "archive")
    report = arc.seal_pending(archive, history_path=path, ticks_path="", today=date(2026, 10, 19), bars_lookback_days=5)
    assert report["errors"] == []
    assert archive.rows("bars_1m", "2026-10-16") == 1 and archive.rows("bars_1m", "2026-10-17") == 1
    assert archive.symbol_totals("bars_1m") == {"NSE:A": 2}
