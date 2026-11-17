"""Self-healing after outages: missed-session repair, expired-position settlement, nightly completeness, markers."""
from __future__ import annotations

import asyncio
import os
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from pytest import approx

from macd_trader import self_heal
from macd_trader.config import Settings
from macd_trader.events import EventHub
from macd_trader.execution import ExecutionManager
from macd_trader.models import Candle
from macd_trader.portfolio import Portfolio
from macd_trader.repository import TradeRepository

IST = ZoneInfo("Asia/Kolkata")
THU = date(2026, 10, 15)


def _history(path: Path, rows):
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE historical_candles (symbol TEXT, timeframe_seconds INTEGER, timestamp INTEGER, open REAL, "
               "high REAL, low REAL, close REAL, volume INTEGER, asset_type TEXT, expiry TEXT, downloaded_at TEXT, "
               "PRIMARY KEY(symbol, timeframe_seconds, timestamp))")
    db.executemany("INSERT INTO historical_candles VALUES (?,60,?,?,?,?,?,0,'x',NULL,'')",
                   [(s, int(t.timestamp()), c, c, c, c) for s, t, c in rows])
    db.commit(); db.close()


def test_missed_windows_cover_only_the_session_minutes_the_app_was_down():
    last = datetime(2026, 10, 15, 12, 52, tzinfo=IST)
    now = datetime(2026, 10, 15, 19, 15, tzinfo=IST)
    windows = self_heal.missed_windows(last, now)
    assert windows == [(datetime(2026, 10, 15, 12, 53, tzinfo=IST), datetime(2026, 10, 15, 15, 30, tzinfo=IST))]
    assert self_heal.missed_minutes(windows) == 157
    # a crash Friday afternoon, restart Monday morning: Friday's tail and Monday so far (the weekend is no session)
    w = self_heal.missed_windows(datetime(2026, 10, 16, 14, 0, tzinfo=IST), datetime(2026, 10, 19, 10, 0, tzinfo=IST))
    assert [lo.date() for lo, _ in w] == [date(2026, 10, 16), date(2026, 10, 19)]
    assert self_heal.missed_windows(None, now) == []


class HistoryBroker:
    def __init__(self):
        self.asked = []

    async def history_range(self, symbol, timeframe, start, end):
        self.asked.append((symbol, start, end))
        if symbol == "NSE:GONE26SEP100CE":
            raise RuntimeError("expired contract")
        return [Candle(symbol, int(start.timestamp()) + 60, 1, 1, 1, 1)]


def test_an_outage_is_backfilled_before_warm_up_and_a_short_one_is_not(tmp_path):
    path = tmp_path / "h.sqlite3"
    _history(path, [("NSE:NIFTY50-INDEX", datetime(2026, 10, 15, 12, 52, tzinfo=IST), 25000.0)])
    broker = HistoryBroker()
    stored = []
    report = asyncio.run(self_heal.repair_outage(
        broker, str(path), ["NSE:NIFTY50-INDEX", "NSE:SBIN-EQ", "NSE:GONE26SEP100CE"],
        now=datetime(2026, 10, 15, 19, 15, tzinfo=IST), store=lambda p, rows: stored.extend(rows) or len(rows)))
    assert report["missed_minutes"] == 157 and report["symbols"] == 2 and report["errors"] == 1 and report["rows"] == 2
    assert broker.asked[0][1] == datetime(2026, 10, 15, 12, 52, tzinfo=IST)   # one minute of overlap
    quiet = asyncio.run(self_heal.repair_outage(
        HistoryBroker(), str(path), ["NSE:SBIN-EQ"], now=datetime(2026, 10, 15, 12, 55, tzinfo=IST), store=lambda p, r: 0))
    assert quiet["skipped"] == "no missed session minutes"


def _manager(tmp_path, symbol, qty, price):
    s = Settings(execution_mode="paper", symbols_csv="NSE:SBIN-EQ", tick_capture_enabled=False, slippage_bps=0)
    m = ExecutionManager(s, SimpleNamespace(name="t"), Portfolio(1_000_000), TradeRepository(str(tmp_path / f"{qty}.sqlite3")), EventHub())
    m.tradable_symbols.add(symbol); m.lot_sizes[symbol] = qty
    m.set_quote(symbol, price, datetime.now(UTC))
    asyncio.run(m.submit(symbol, "BUY", qty))
    m.tradable_symbols.discard(symbol)                                  # next day: off the watchlist
    return m


def test_a_contract_that_expired_offline_is_settled_at_intrinsic(tmp_path):
    path = tmp_path / "h.sqlite3"
    _history(path, [("NSE:SBIN-EQ", datetime(2026, 9, 29, 15, 29, tzinfo=IST), 830.0)])
    itm = _manager(tmp_path, "NSE:SBIN26SEP800CE", 750, 20.0)
    otm = _manager(tmp_path, "NSE:SBIN26SEP800PE", 750, 5.0)
    done = asyncio.run(self_heal.settle_expired_books([("macd", itm), ("smart", otm)], str(path),
                                                      now=datetime(2026, 10, 1, 9, 20, tzinfo=IST)))
    assert {d["symbol"]: d["price"] for d in done} == {"NSE:SBIN26SEP800CE": 30.0, "NSE:SBIN26SEP800PE": 0.0}
    assert not itm.portfolio.positions and not otm.portfolio.positions
    assert itm.exit_reasons["NSE:SBIN26SEP800CE"] == "EXPIRED_SETTLED_WHILE_OFFLINE"
    reason = itm.repository._connection.execute("SELECT json_extract(payload,'$.exit_reason') FROM closed_positions").fetchone()[0]
    assert reason == "EXPIRED_SETTLED_WHILE_OFFLINE"


def test_a_contract_expiring_today_is_left_alone_until_the_close(tmp_path):
    path = tmp_path / "h.sqlite3"
    _history(path, [("NSE:SBIN-EQ", datetime(2026, 10, 27, 15, 29, tzinfo=IST), 830.0)])
    m = _manager(tmp_path, "NSE:SBIN26OCT800CE", 750, 20.0)
    before = asyncio.run(self_heal.settle_expired_books([("macd", m)], str(path), now=datetime(2026, 10, 27, 14, 0, tzinfo=IST)))
    assert before == [] and m.portfolio.positions
    after = asyncio.run(self_heal.settle_expired_books([("macd", m)], str(path), now=datetime(2026, 10, 27, 15, 40, tzinfo=IST)))
    assert after[0]["price"] == 30.0 and not m.portfolio.positions


def test_the_nightly_waits_for_the_session_to_be_complete(tmp_path):
    path = tmp_path / "h.sqlite3"
    open_ = datetime(2026, 10, 15, 9, 15, tzinfo=IST)
    _history(path, [("NSE:NIFTY50-INDEX", open_ + timedelta(minutes=m), 25000.0) for m in range(218)])
    assert not self_heal.session_history_complete(str(path), THU)
    _history(tmp_path / "full.sqlite3", [("NSE:NIFTY50-INDEX", open_ + timedelta(minutes=m), 25000.0) for m in range(375)])
    assert self_heal.session_history_complete(str(tmp_path / "full.sqlite3"), THU)


def test_a_clean_stop_leaves_the_doctor_its_marker(tmp_path):
    self_heal.mark_clean_shutdown(str(tmp_path / "macd_trader.sqlite3"), "strategy")
    assert (tmp_path / ".clean_shutdown.strategy").exists()


def test_the_watchdog_reports_its_beat():
    async def run():
        dog = self_heal.LoopWatchdog("test", stall_seconds=10_000)
        dog._task = asyncio.create_task(dog._beat())                   # the loop half only; no exiting thread in tests
        await asyncio.sleep(0.05)
        status = dog.status()
        dog._task.cancel()
        return status
    assert asyncio.run(run())["last_beat_age"] < 1


def test_the_boot_report_says_where_the_start_spent_its_time(tmp_path):
    backups = tmp_path / "backups"; backups.mkdir()
    (backups / "doctor.log").write_text(
        "2026-10-16T02:28:20Z [strategy] unclean shutdown: checking the stores\n"
        "2026-10-16T02:31:27Z [strategy] ticks.sqlite3 DAMAGED: malformed\n"
        "2026-10-16T02:31:40Z [strategy] ticks.sqlite3 recovered and swapped in (original in x)\n"
        "2026-10-16T02:31:53Z [strategy] check done\n"
        "2026-10-16T02:31:53Z [desk] stores checked 0s ago by the other process; skipped\n")
    doctor = self_heal.doctor_summary(str(tmp_path / "macd_trader.sqlite3"), "strategy")
    assert doctor["started"] == "2026-10-16T02:28:20Z" and doctor["finished"] == "2026-10-16T02:31:53Z"
    assert doctor["recovered"] == 1 and len(doctor["damaged"]) == 1
    finished = (self_heal.PROCESS_STARTED + timedelta(seconds=140)).isoformat()
    report = self_heal.boot_report(str(tmp_path / "macd_trader.sqlite3"), "strategy", {"required": 10, "with_indicators": 10},
                                   {"finished_at": finished, "seconds": 120.0, "downloaded": 3, "selection": {"seconds": 0.9}},
                                   {"pending": 0}, "connected")
    assert report["ready"] and report["process_to_ready_seconds"] == 140 and report["warmup_downloads"] == 3
    assert not self_heal.boot_report(str(tmp_path / "x.sqlite3"), "strategy", {"required": 10, "with_indicators": 9},
                                     None, None, "connected")["ready"]


def test_a_quiet_contract_is_not_downloaded_again_once_checked(tmp_path):
    from macd_trader.engine import TradingEngine
    engine = TradingEngine(Settings(symbols_csv="NSE:SBIN-EQ", tick_capture_enabled=False,
                                    database_path=str(tmp_path / "b.sqlite3"), mp_database_path=str(tmp_path / "mp.sqlite3"),
                                    research_database_path=str(tmp_path / "h.sqlite3"),
                                    tick_database_path=str(tmp_path / "t.sqlite3"),
                                    contract_snapshot_path=str(tmp_path / "c.json")))
    symbol = "NSE:SBIN26OCT960CE"
    assert not engine._history_checked_current(symbol)
    engine._mark_history_checked(symbol)
    assert engine._history_checked_current(symbol)
    engine._history_checked_cache = None                                 # survives a restart
    assert engine._history_checked_current(symbol)
    engine.repository.close()


def test_settlement_rejects_morning_spot_and_old_option_premium(tmp_path):
    path = tmp_path / 'h.sqlite3'
    _history(path, [
        ('NSE:SBIN-EQ', datetime(2026, 9, 29, 10, 0, tzinfo=IST), 830),
        ('NSE:SBIN26SEP800CE', datetime(2026, 9, 28, 15, 29, tzinfo=IST), 40)])
    parsed = {'expiry': '2026-09-29', 'spot': 'NSE:SBIN-EQ', 'strike': 800, 'side': 1}
    price, reason = self_heal.settlement_price(str(path), 'NSE:SBIN26SEP800CE', parsed)
    assert price is None and 'unresolved' in reason
    manager = _manager(tmp_path, 'NSE:SBIN26SEP800CE', 750, 20)
    before = len(manager.repository.rows('trades', 100))
    result = asyncio.run(self_heal.settle_expired_books([('macd', manager)], str(path),
                         datetime(2026, 10, 1, 10, 0, tzinfo=IST)))
    assert result[0]['error'] and manager.portfolio.positions
    assert len(manager.repository.rows('trades', 100)) == before


def test_nightly_requires_all_symbols_and_all_minutes_and_read_errors_block(tmp_path):
    path = tmp_path / 'h.sqlite3'
    opened = datetime(2026, 10, 15, 9, 15, tzinfo=IST)
    rows = [('NSE:NIFTY50-INDEX', opened + timedelta(minutes=m), 100) for m in range(375)]
    rows += [('NSE:SBIN-EQ', opened + timedelta(minutes=m), 100) for m in range(359)]
    _history(path, rows)
    result = self_heal.session_history_coverage(str(path), THU, ['NSE:NIFTY50-INDEX', 'NSE:SBIN-EQ'])
    assert not result['complete'] and result['missing'] == {'NSE:SBIN-EQ': 1}
    assert not self_heal.session_history_complete(str(tmp_path / 'missing.sqlite3'), THU)


def test_nightly_uses_each_instruments_continuous_session(tmp_path):
    opened = datetime(2026, 10, 15, 9, 15, tzinfo=IST)
    lengths = {'NSE:SBIN-EQ': 360, 'NSE:NIFTY50-INDEX': 375, 'NSE:NIFTY26OCTFUT': 385}
    rows = [(symbol, opened + timedelta(minutes=m), 100)
            for symbol, length in lengths.items() for m in range(length)]
    path = tmp_path / 'sessions.sqlite3'
    _history(path, rows)
    result = self_heal.session_history_coverage(str(path), THU, list(lengths))
    assert result['complete'] and result['missing'] == {}
    assert result['expected_minutes_by_symbol'] == lengths
    # A complete index day cannot conceal a missing derivative closing minute.
    with sqlite3.connect(path) as db:
        db.execute('DELETE FROM historical_candles WHERE symbol=? AND timestamp=?',
                   ('NSE:NIFTY26OCTFUT', int((opened + timedelta(minutes=384)).timestamp())))
    result = self_heal.session_history_coverage(str(path), THU, list(lengths))
    assert not result['complete'] and result['missing'] == {'NSE:NIFTY26OCTFUT': 1}


def test_doctor_blocks_corruption_without_replacing_any_original(tmp_path):
    import shutil
    import subprocess
    import pytest
    if not shutil.which('sqlite3'):
        pytest.skip('sqlite3 command line required')
    path = tmp_path / 'macd_trader.sqlite3'
    original = b'not a sqlite database'
    path.write_bytes(original)
    healthy = tmp_path / 'smart_50l.sqlite3'
    with sqlite3.connect(healthy) as db:
        db.execute('CREATE TABLE protected (value TEXT)')
        db.execute("INSERT INTO protected VALUES ('keep me')")
    env = {**os.environ, 'MACD_DATABASE_PATH': str(path), 'MACD_ENGINE_ROLE': 'strategy'}
    doctor = Path(__file__).resolve().parents[1] / 'db_doctor.sh'
    run = subprocess.run(['sh', str(doctor)], env=env, capture_output=True, text=True)
    assert run.returncode != 0 and 'BLOCKED' in run.stdout
    assert path.read_bytes() == original
    with sqlite3.connect(healthy) as db:
        assert db.execute('SELECT value FROM protected').fetchone()[0] == 'keep me'
    assert not list(tmp_path.glob('*.recovering'))


# --------------------------------------------------------------------------- mid-session feed gaps (owner, 17 Nov 2026)

def _minutes(symbol, day, start, end, close=100.0):
    t, rows = datetime.combine(day, start, IST), []
    while t < datetime.combine(day, end, IST):
        rows.append((symbol, t, close)); t += timedelta(minutes=1)
    return rows


def test_a_mid_session_feed_gap_is_found_once_the_feed_is_back(tmp_path):
    from datetime import time as clock
    day, path = date(2026, 11, 16), tmp_path / "h.sqlite3"
    # 16 Nov 2026: bars to 09:35, nothing 09:36-11:03, bars again from 11:04; a 3-minute blip at 12:00 is ignored
    _history(path, [*_minutes(self_heal.NIFTY, day, clock(9, 15), clock(9, 36)),
                    *_minutes(self_heal.NIFTY, day, clock(11, 4), clock(12, 0)),
                    *_minutes(self_heal.NIFTY, day, clock(12, 3), clock(13, 0))])
    at = lambda h, m: datetime(2026, 11, 16, h, m, tzinfo=IST)
    assert self_heal.session_gaps(str(path), day, at(10, 30)) == []           # feed still down: not yet
    assert self_heal.session_gaps(str(path), day, at(11, 5)) == []            # back, but not settled
    assert self_heal.session_gaps(str(path), day, at(12, 30)) == [(at(9, 36), at(11, 4))]
    # the stretch after the last stored bar is the live edge (or an outage still running), never a closed gap
    assert self_heal.session_gaps(str(path), day, at(15, 50)) == [(at(9, 36), at(11, 4))]
    assert self_heal.session_gaps(str(path), date(2026, 11, 17), datetime(2026, 11, 17, 12, 0, tzinfo=IST)) == []


class GapBroker:
    def __init__(self):
        self.asked = []

    async def history_range(self, symbol, timeframe, start, end):
        from macd_trader.brokers import HISTORY_HOLD_EXEMPT
        self.asked.append((symbol, HISTORY_HOLD_EXEMPT.get()))
        if symbol == "NSE:GONE26SEP100CE":
            raise RuntimeError("expired contract")
        t, out = start, []
        while t <= end:
            out.append(Candle(symbol, int(t.timestamp()), 2, 2, 2, 2)); t += timedelta(minutes=1)
        return out


def test_a_gap_repair_writes_only_missing_minutes_and_passes_the_hold(tmp_path):
    from datetime import time as clock
    day, path = date(2026, 11, 16), tmp_path / "h.sqlite3"
    _history(path, [*_minutes(self_heal.NIFTY, day, clock(9, 15), clock(9, 36)),
                    *_minutes(self_heal.NIFTY, day, clock(11, 4), clock(11, 30)),
                    *_minutes("NSE:SBIN-EQ", day, clock(9, 15), clock(9, 36)),
                    ("NSE:SBIN-EQ", datetime(2026, 11, 16, 10, 0, tzinfo=IST), 100.0),     # one minute did arrive
                    *_minutes("MCX:GOLD26DECFUT", day, clock(9, 15), clock(9, 36))])
    at = lambda h, m: datetime(2026, 11, 16, h, m, tzinfo=IST)
    windows = self_heal.session_gaps(str(path), day, at(11, 30))
    symbols = self_heal.gap_symbols(str(path), windows[0][0])
    assert set(symbols) == {self_heal.NIFTY, "NSE:SBIN-EQ"}                      # MCX has its own store
    symbols["NSE:GONE26SEP100CE"] = None
    stored: dict[str, list] = {}
    broker = GapBroker()
    report = asyncio.run(self_heal.repair_session_gaps(
        broker, str(path), windows, symbols, pace=0,
        store=lambda p, rows, expiry: stored.setdefault(rows[0].symbol, []).extend(rows) or len(rows)))
    assert all(exempt for _, exempt in broker.asked)                             # through the hold, this task only
    from macd_trader.brokers import HISTORY_HOLD_EXEMPT
    assert HISTORY_HOLD_EXEMPT.get() is False
    nifty = sorted(c.timestamp for c in stored[self_heal.NIFTY])
    assert nifty[0] == int(at(9, 36).timestamp()) and nifty[-1] == int(at(11, 3).timestamp()) and len(nifty) == 88
    assert len(stored["NSE:SBIN-EQ"]) == 87                                      # the stored 10:00 bar is kept
    assert int(at(10, 0).timestamp()) not in {c.timestamp for c in stored["NSE:SBIN-EQ"]}
    assert report["rows"] == 175 and report["filled_symbols"] == 2 and report["errors"] == 1 and report["minutes"] == 88
