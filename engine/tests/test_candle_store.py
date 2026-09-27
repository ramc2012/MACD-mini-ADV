import sqlite3
from pathlib import Path

from macd_trader.candle_store import LiveCandleWriter
from macd_trader.models import Candle


def test_writer_persists_minute_bars_with_per_bar_volume(tmp_path: Path):
    database = tmp_path / "historical.sqlite3"
    writer = LiveCandleWriter(str(database))
    # Fyers tick volume is cumulative for the session; the store keeps per-bar.
    writer.add(Candle("NSE:SBIN-EQ", 1_755_000_000, 800, 802, 799, 801, volume=1_000, closed=True))
    writer.add(Candle("NSE:SBIN-EQ", 1_755_000_060, 801, 803, 800, 802, volume=1_500, closed=True))
    assert writer.flush_sync() == 2

    connection = sqlite3.connect(database)
    rows = connection.execute(
        "SELECT timestamp, close, volume, asset_type, timeframe_seconds FROM historical_candles ORDER BY timestamp"
    ).fetchall()
    connection.close()
    assert [row[0] for row in rows] == [1_755_000_000, 1_755_000_060]
    assert rows[0][2] == 1_000 and rows[1][2] == 500
    assert rows[0][3] == "stock" and rows[0][4] == 60


def test_writer_rebuffers_rows_when_flush_fails(tmp_path: Path):
    writer = LiveCandleWriter(str(tmp_path / "missing" / "no-such-dir" / "db.sqlite3"))
    writer.add(Candle("NSE:NIFTY50-INDEX", 1_755_000_000, 1, 1, 1, 1, volume=0, closed=True))
    assert writer.flush_sync() == 0
    assert writer.status()["pending_rows"] == 1
    assert writer.status()["last_error"]


def test_writer_upsert_lets_official_download_overwrite(tmp_path: Path):
    database = tmp_path / "historical.sqlite3"
    writer = LiveCandleWriter(str(database))
    writer.add(Candle("NSE:SBIN26AUG800CE", 1_755_000_000, 10, 11, 9, 10.5, volume=100, closed=True))
    writer.flush_sync()
    writer.add(Candle("NSE:SBIN26AUG800CE", 1_755_000_000, 10, 12, 9, 11.0, volume=100, closed=True))
    writer.flush_sync()
    connection = sqlite3.connect(database)
    rows = connection.execute("SELECT close, asset_type FROM historical_candles").fetchall()
    connection.close()
    assert rows == [(11.0, "option")]


def test_trade_book_does_not_use_wal_on_bind_mounted_storage(tmp_path: Path):
    """WAL needs shared memory + byte-range locks that Docker Desktop's macOS
    bind mount does not reliably provide; it corrupted the desk book. The
    rollback journal with synchronous=FULL is the durable choice here."""
    from macd_trader.repository import TradeRepository

    repository = TradeRepository(str(tmp_path / "book.sqlite3"))
    try:
        mode = repository._connection.execute("PRAGMA journal_mode;").fetchone()[0]
        sync = repository._connection.execute("PRAGMA synchronous;").fetchone()[0]
        assert mode.lower() == "delete", f"journal_mode must not be wal, got {mode}"
        assert sync == 2, f"synchronous must be FULL(2), got {sync}"
    finally:
        repository.close()


def test_candle_writer_does_not_use_wal_either(tmp_path: Path):
    """The trade book was fixed first; the candle writer set WAL independently
    and was missed. Both writers touch the same bind-mounted storage."""
    import sqlite3 as sq
    from macd_trader.candle_store import LiveCandleWriter
    from macd_trader.models import Candle

    database = tmp_path / "historical.sqlite3"
    writer = LiveCandleWriter(str(database))
    writer.add(Candle("NSE:SBIN-EQ", 1_755_000_000, 1, 1, 1, 1, volume=10, closed=True))
    assert writer.flush_sync() == 1

    connection = sq.connect(database)
    try:
        mode = connection.execute("PRAGMA journal_mode;").fetchone()[0]
        assert mode.lower() == "delete", f"journal_mode must not be wal, got {mode}"
    finally:
        connection.close()
