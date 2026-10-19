"""Equity periods count trading sessions, so a weekend or holiday never empties the curve."""
import sqlite3

from macd_trader.repository import TradeRepository


def test_sessions_and_buckets(tmp_path):
    repository = TradeRepository(str(tmp_path / "book.sqlite3"))
    rows = [("2026-09-29T04:00:05+00:00", 100.0), ("2026-09-29T04:00:50+00:00", 101.0),
            ("2026-09-30T04:00:10+00:00", 102.0), ("2026-10-01T09:59:00+00:00", 103.0)]
    with repository._connection:
        repository._connection.executemany(
            "INSERT INTO equity_points VALUES (?, ?, 0, 0, 0)", rows)
    assert repository.equity_days() == ["2026-09-29", "2026-09-30", "2026-10-01"]
    minute = repository.equity_buckets(None, 60)
    # two samples in one minute keep the later one, stamped at the bucket start
    assert [(r["timestamp"], r["equity"]) for r in minute][:1] == [("2026-09-29T04:00:00+00:00", 101.0)]
    latest = repository.equity_buckets("2026-10-01", 60)
    assert [r["equity"] for r in latest] == [103.0]
