"""Raw tick capture and its condensation.

Ticks are the one dataset that cannot be recovered after the fact: Fyers' finest
history is 5-second OHLCV (the API lists `5S, 10S, 15S, 30S, 45S, 1, 2, ...`)
and there is no trade-by-trade endpoint. So the capture path must not lose rows,
and the condensation must keep exactly what a candle cannot reproduce.
"""
import os
import tempfile
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from macd_trader.models import Tick
from macd_trader.tick_store import TickStore, classify

IST = ZoneInfo("Asia/Kolkata")


def _store(folder, retention_days=2):
    return TickStore(os.path.join(folder, "ticks.sqlite3"), retention_days=retention_days)


def _tick(symbol, ltp, when, *, volume=None, last_qty=None, bid=None, ask=None, oi=None):
    return Tick(symbol=symbol, ltp=ltp, volume=volume or 0, timestamp=when,
                bid=bid, ask=ask, last_qty=last_qty, open_interest=oi)


def _ist(day, hour, minute, second=0):
    return datetime(2026, 8, day, hour, minute, second, tzinfo=IST)


class TestCapture:
    def test_ticks_survive_a_flush(self):
        with tempfile.TemporaryDirectory() as folder:
            store = _store(folder)
            now = _ist(20, 9, 20)
            for i in range(5):
                store.add(_tick("NSE:X", 100.0 + i, now + timedelta(seconds=i), volume=10 * i))
            assert store.flush_sync() == 5
            assert store.status()["raw_ticks"] == 5
            assert store.status()["symbols"] == 1

    def test_symbols_are_interned_not_repeated(self):
        with tempfile.TemporaryDirectory() as folder:
            store = _store(folder)
            now = _ist(20, 9, 20)
            for i in range(20):
                store.add(_tick("NSE:SAME", 100.0, now + timedelta(seconds=i)))
            store.flush_sync()
            assert store.status()["symbols"] == 1

    def test_a_malformed_timestamp_does_not_raise(self):
        with tempfile.TemporaryDirectory() as folder:
            store = _store(folder)

            class Bad:
                symbol, ltp, volume = "NSE:X", 1.0, 0
                timestamp = "not-a-datetime"

            store.add(Bad())          # must not raise
            assert store.flush_sync() == 1

    def test_optional_microstructure_fields_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            store = _store(folder)
            store.add(_tick("NSE:X", 100.0, _ist(20, 9, 20), volume=500, last_qty=25,
                            bid=99.95, ask=100.05, oi=1234))
            store.flush_sync()
            import sqlite3
            row = sqlite3.connect(os.path.join(folder, "ticks.sqlite3")).execute(
                "SELECT ltp, cum_volume, last_qty, bid, ask, oi FROM ticks").fetchone()
            assert row == (100.0, 500, 25, 99.95, 100.05, 1234)

    def test_flush_of_an_empty_buffer_is_a_noop(self):
        with tempfile.TemporaryDirectory() as folder:
            assert _store(folder).flush_sync() == 0


class TestClassification:
    def test_quote_rule_wins_at_the_touch(self):
        assert classify(100.05, 99.95, 100.05, None) == (1, "quote")
        assert classify(99.95, 99.95, 100.05, None) == (-1, "quote")

    def test_mid_fallback_inside_the_spread(self):
        assert classify(100.02, 99.95, 100.05, None)[1] == "mid"

    def test_tick_rule_when_there_is_no_book(self):
        assert classify(101.0, None, None, 100.0) == (1, "tick")
        assert classify(99.0, None, None, 100.0) == (-1, "tick")

    def test_unknowable_is_reported_not_guessed(self):
        assert classify(100.0, None, None, 100.0) == (0, "tick")


class TestCondensation:
    """The retention window and what survives it."""

    def _day_of_ticks(self, store, day=20):
        # Two minutes of trading, an explicit book, growing cumulative volume.
        rows = [
            (_ist(day, 9, 20, 0),  100.00, 100, 99.95, 100.05),   # at ask -> buy
            (_ist(day, 9, 20, 30), 100.05, 250, 99.95, 100.05),   # at ask -> buy
            (_ist(day, 9, 20, 45),  99.95, 400, 99.95, 100.05),   # at bid -> sell
            (_ist(day, 9, 21, 10), 100.00, 550, 99.95, 100.05),   # mid -> buy
        ]
        for when, ltp, cum, bid, ask in rows:
            store.add(_tick("NSE:X", ltp, when, volume=cum, bid=bid, ask=ask))
        store.flush_sync()

    def test_a_day_is_condensed_the_night_after_not_on_the_day(self):
        with tempfile.TemporaryDirectory() as folder:
            store = _store(folder, retention_days=2)
            self._day_of_ticks(store, day=20)
            # Same day: the session may still be writing.
            assert store.days_pending_condensation(now=_ist(20, 16, 0)) == []
            # The next day: condensed, whatever the raw window (raw ticks stay until archived).
            assert store.days_pending_condensation(now=_ist(21, 16, 0)) == ["2026-08-20"]

    def test_a_day_past_the_window_becomes_pending(self):
        with tempfile.TemporaryDirectory() as folder:
            store = _store(folder, retention_days=2)
            self._day_of_ticks(store, day=20)
            assert store.days_pending_condensation(now=_ist(22, 16, 0)) == ["2026-08-20"]

    def test_condensing_writes_flow_and_keeps_the_raw(self):
        with tempfile.TemporaryDirectory() as folder:
            store = _store(folder, retention_days=2)
            self._day_of_ticks(store, day=20)
            assert store.status()["raw_ticks"] == 4

            result = store.condense_day("2026-08-20")
            assert result["raw_ticks"] == 4
            assert result["minute_rows"] == 2          # 09:20 and 09:21
            assert store.status()["raw_ticks"] == 4    # raw stays until the archive holds it
            assert store.status()["minute_rows"] == 2

    def test_aggressor_split_survives_the_condensation(self):
        with tempfile.TemporaryDirectory() as folder:
            store = _store(folder, retention_days=2)
            self._day_of_ticks(store, day=20)
            store.condense_day("2026-08-20")
            import sqlite3
            rows = sqlite3.connect(os.path.join(folder, "ticks.sqlite3")).execute(
                """SELECT buy_volume, sell_volume, delta, trades FROM tick_minute_flow
                   ORDER BY minute_ts""").fetchall()
            # 09:20 -- first tick only sets the volume baseline, so sizes are
            # 150 bought at the ask and 150 sold at the bid.
            assert rows[0] == (150, 150, 0, 2)
            # 09:21 -- 150 more, mid-priced above the midpoint, so a buy.
            assert rows[1] == (150, 0, 150, 1)

    def test_the_volume_profile_ladder_is_kept(self):
        with tempfile.TemporaryDirectory() as folder:
            store = _store(folder, retention_days=2)
            self._day_of_ticks(store, day=20)
            store.condense_day("2026-08-20")
            import sqlite3
            ladder = dict(sqlite3.connect(os.path.join(folder, "ticks.sqlite3")).execute(
                """SELECT price, buy_volume + sell_volume FROM tick_session_ladder
                   WHERE day = '2026-08-20'""").fetchall())
            assert ladder == {100.05: 150, 99.95: 150, 100.0: 150}

    def test_condensation_is_idempotent_and_recorded(self):
        with tempfile.TemporaryDirectory() as folder:
            store = _store(folder, retention_days=2)
            self._day_of_ticks(store, day=20)
            store.condense_day("2026-08-20")
            assert store.days_pending_condensation(now=_ist(22, 16, 0)) == []
            again = store.condense_day("2026-08-20")   # the same rows again: same flow, no duplicates
            assert (again["raw_ticks"], again["minute_rows"]) == (4, 2)
            assert store.status()["minute_rows"] == 2
            assert store.status()["condensed_days"] == 1

    def test_only_the_targeted_day_is_consumed(self):
        with tempfile.TemporaryDirectory() as folder:
            store = _store(folder, retention_days=2)
            self._day_of_ticks(store, day=20)
            self._day_of_ticks(store, day=21)
            store.condense_day("2026-08-20")
            assert store.status()["minute_rows"] == 2  # the 21st is untouched
            assert store.status()["condensed_days"] == 1

    def test_vacuum_runs_clean(self):
        with tempfile.TemporaryDirectory() as folder:
            store = _store(folder, retention_days=2)
            self._day_of_ticks(store, day=20)
            store.condense_day("2026-08-20")
            store.vacuum()
            assert store.status()["minute_rows"] == 2


def test_raw_ticks_go_only_once_the_archive_holds_them():
    """Condensed the night after; raw kept past the hot window until the archive covers the day.
    1980 stamps (the feed's "never traded") are artefacts and always go."""
    from macd_trader import archive as arc
    with tempfile.TemporaryDirectory() as folder:
        store = _store(folder, retention_days=2)
        store.add(_tick("NSE:X", 100.0, _ist(20, 9, 20), volume=100))
        store.flush_sync()
        assert [r["day"] for r in store.condense_pending(now=_ist(21, 16, 0))] == ["2026-08-20"]
        store.add(_tick("NSE:IDLE", 5.0, _ist(20, 15, 29), volume=7))         # a stale stamp, after condensing
        store.add(_tick("NSE:NEVER", 1.0, datetime(1980, 1, 1, 5, 30, tzinfo=IST)))
        store.add(_tick("NSE:X", 101.0, _ist(24, 9, 20), volume=200))
        store.flush_sync()

        store.condense_pending(now=_ist(24, 16, 0))                           # no archive: nothing real goes
        assert store.last_purge["days_held"] == ["2026-08-20"]
        assert store.status()["raw_ticks"] == 3                               # 1980 went

        archive = arc.Archive(os.path.join(folder, "archive"))
        report = arc.seal_pending(archive, history_path="", ticks_path=os.path.join(folder, "ticks.sqlite3"),
                                  today=datetime(2026, 8, 24).date(), tick_retention_days=0)
        assert report["errors"] == [] and archive.rows("ticks", "2026-08-20") == 2
        store.condense_pending(now=_ist(24, 16, 0), archive=archive)
        assert store.last_purge["days_held"] == [] and store.status()["raw_ticks"] == 1   # only the 24th is left
