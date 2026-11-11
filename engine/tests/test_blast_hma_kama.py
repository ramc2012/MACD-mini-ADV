"""Blast lane on the HMA(20) x KAMA(10,2,30) strategy (owner, 28 Oct 2026): enter on the cross up, leave on HMA below
KAMA, a 30% stop or the 4th session; no trail."""
import asyncio
import math
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def fixed_blast_session_clock(monkeypatch):
    from macd_trader import blast_engine, engine, execution, models

    class SessionClock(datetime):
        @classmethod
        def now(cls, tz=None):
            moment = datetime(2026, 9, 11, 5, 30, tzinfo=UTC)          # 11:00 IST, a Friday
            return moment.astimezone(tz) if tz else moment.replace(tzinfo=None)

    for module in (blast_engine, engine, execution, models):
        monkeypatch.setattr(module, "datetime", SessionClock)
    monkeypatch.setattr(sys.modules[__name__], "datetime", SessionClock)


from macd_trader import blast_engine
from macd_trader.blast_engine import EXIT_HK, HMA_KAMA, TAKEN, BlastEngine, held_sessions
from macd_trader.config import Settings
from macd_trader.events import EventHub
from macd_trader.indicators import IncrementalHMA
from macd_trader.models import Candle

pytestmark = pytest.mark.usefixtures("no_stock_t2")

SYMBOL = "NSE:SBIN26SEP800CE"
SPOT = "NSE:SBIN-EQ"


class NoOrders:
    name = "fyers"

    async def place_order(self, _order):
        raise AssertionError("paper mode called the live broker order API")


def build(tmp_path: Path, **overrides) -> BlastEngine:
    settings = Settings(**{"execution_mode": "paper", "symbols_csv": SPOT, "slippage_bps": 0, "blast_auto_trade": True,
                           "blast_target_notional": 100_000, "blast_hard_stop_pct": 0.30, "blast_strategy": HMA_KAMA,
                           **overrides})
    lane = BlastEngine(str(tmp_path / "blast.sqlite3"), settings, NoOrders(), EventHub())
    lane.set_tradable_contracts({SYMBOL: 100})
    lane.set_contract_context({SYMBOL: {"spot_symbol": SPOT, "option_type": "CE", "strike": 800, "expiry": "2026-09-29"}})
    lane.set_resolvers(spot_price=lambda _s: 800.0)
    return lane


def reference_hma(values, n):
    def wma(xs, k):
        out = [None] * len(xs)
        for i in range(k - 1, len(xs)):
            window = xs[i - k + 1:i + 1]
            if None in window:
                continue
            out[i] = sum(v * (j + 1) for j, v in enumerate(window)) / (k * (k + 1) / 2)
        return out
    half, full = wma(values, n // 2), wma(values, n)
    return wma([None if a is None or b is None else 2 * a - b for a, b in zip(half, full)], int(math.sqrt(n)))


def test_incremental_hma_matches_the_research_definition():
    closes = [10 + math.sin(i / 3) + i * 0.05 for i in range(60)]
    hma = IncrementalHMA(20)
    live = [hma.update(c) for c in closes]
    expected = reference_hma(closes, 20)
    assert all((a is None and b is None) or abs(a - b) < 1e-9 for a, b in zip(live, expected))
    assert live[21] is None and live[22] is not None                  # first value at the (n + sqrt(n) - 1)th price


def drive(lane, closes, start=0):
    """Feed closed signal bars; return the journal rows produced."""
    rows = []
    for i, close in enumerate(closes, start=start):
        candle = Candle(SYMBOL, 1_000_000 + i * 1800, close, close, close, close, 100, True)
        point = lane._indicator(candle)
        lane.execution.set_quote(SYMBOL, close)
        row = asyncio.run(lane._hma_kama_bar(candle, point, late=False))
        if row:
            rows.append(row)
    return rows


def test_enters_on_the_cross_up_and_leaves_when_hma_falls_below_kama(tmp_path):
    lane = build(tmp_path)
    falling = [20 - i * 0.25 for i in range(40)]                     # HMA below KAMA throughout the decline
    rows = drive(lane, falling)
    assert not rows and SYMBOL not in lane.portfolio.positions
    rising = [falling[-1] + i * 0.6 for i in range(1, 9)]
    rows = drive(lane, rising, start=40)
    taken = [r for r in rows if r["reason"] == TAKEN]
    assert len(taken) == 1 and taken[0]["hma"] > taken[0]["kama"] and taken[0]["strategy"] == HMA_KAMA
    assert lane.portfolio.positions[SYMBOL].quantity > 0
    peak = rising[-1]
    # The turn: HMA lags a sharp top by a few bars, then drops through KAMA. (On a long straight fall KAMA can
    # overshoot below HMA and re-cross: the strategy may re-enter then, as it did in the backtest.)
    drive(lane, [peak - i * 0.9 for i in range(1, 12)], start=48)
    closed = lane.execution.portfolio_snapshot()["closed_positions"]
    assert closed and closed[0]["exit_reason"] == EXIT_HK and closed[0]["exit_price"] < peak


def test_no_trail_a_30_percent_stop_and_a_four_session_cap(tmp_path):
    lane = build(tmp_path)
    drive(lane, [20 - i * 0.25 for i in range(40)])
    drive(lane, [10 + i * 0.6 for i in range(1, 9)], start=40)
    position = lane.portfolio.positions[SYMBOL]
    entry = position.average_price
    asyncio.run(lane.on_tick(SYMBOL, entry * 2.0))                   # +100%: the old lane would arm a trail here
    asyncio.run(lane.on_tick(SYMBOL, entry * 1.2))                   # -40% from the peak: no trail, still held
    assert lane.portfolio.positions[SYMBOL].trailing_stop is None
    asyncio.run(lane.on_tick(SYMBOL, entry * 0.69))                  # below the 30% stop
    assert SYMBOL not in lane.portfolio.positions
    assert lane.execution.portfolio_snapshot()["closed_positions"][-1]["exit_reason"] == "BLAST_HARD_STOP_30_PCT"


def test_held_sessions_counts_trading_days_and_the_exit_time():
    monday = datetime(2026, 10, 5, 5, 0, tzinfo=UTC)                 # Mon 10:30 IST
    assert held_sessions(monday, datetime(2026, 10, 5, 9, 31, tzinfo=UTC)) == 0   # the entry day never counts
    assert held_sessions(monday, datetime(2026, 10, 6, 9, 0, tzinfo=UTC)) == 0    # Tue 14:30 IST: not yet
    assert held_sessions(monday, datetime(2026, 10, 6, 9, 31, tzinfo=UTC)) == 1   # Tue 15:01 IST
    assert held_sessions(monday, datetime(2026, 10, 9, 9, 31, tzinfo=UTC)) == 4   # Fri 15:01 IST: out


IST_ZONE = blast_engine.IST


def minute(day: int, hour: int, minute_: int) -> int:
    return int(datetime(2026, 10, day, hour, minute_, tzinfo=IST_ZONE).timestamp())


def history_db(tmp_path: Path, stored, marks) -> str:
    import sqlite3
    path = str(tmp_path / "history.sqlite3")
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE historical_candles (symbol TEXT, timeframe_seconds INTEGER, timestamp INTEGER, open REAL,"
                   " high REAL, low REAL, close REAL, volume INTEGER)")
        db.execute("CREATE TABLE chain_snapshots (symbol TEXT, ts INTEGER, ltp REAL, volume INTEGER)")
        db.executemany("INSERT INTO historical_candles VALUES (?, 60, ?, ?, ?, ?, ?, 10)",
                       [(SYMBOL, ts, px, px, px, px) for ts, px in stored])
        db.executemany("INSERT INTO chain_snapshots VALUES (?, ?, ?, 0)", [(SYMBOL, ts, px) for ts, px in marks])
    return path


def test_chain_marks_fill_only_the_stretches_the_contract_was_not_streamed(tmp_path):
    """29 Oct 2026: BLUESTARCO 1600CE had no stored minutes for 26-28 Oct (not on the feed), so its 30-minute HMA/KAMA
    skipped the 28 Oct rally and crossed a day after the exchange chart did."""
    stored = [(minute(23, 10, m), 20.0) for m in range(0, 30)] + [(minute(28, 11, m), 60.0) for m in range(0, 10)]
    marks = [(minute(23, 12, 0), 99.0),                       # a streamed day: never overwritten
             (minute(26, 10, 0), 25.0), (minute(27, 13, 0), 30.0),   # days off the feed
             (minute(28, 9, 30), 40.0),                       # before its first streamed minute that day
             (minute(28, 14, 0), 99.0)]                       # after it: a quiet streamed bucket stays empty
    path = history_db(tmp_path, stored, marks)
    read = blast_engine.read_stored_minute_bars(path, {SYMBOL: -1}, 10_000)
    filled = blast_engine.fill_unstreamed_minutes(path, {SYMBOL: -1}, read, 10_000)[SYMBOL]
    added = sorted({c.timestamp: c.close for c in filled}.items())
    assert [(ts, px) for ts, px in added if px != 20.0 and px != 60.0] == [
        (minute(26, 10, 0), 25.0), (minute(27, 13, 0), 30.0), (minute(28, 9, 30), 40.0)]
    assert [c.timestamp for c in filled] == sorted(c.timestamp for c in filled)
    # Re-warm from a lane that already holds 28 Oct 11:05: that day was streamed, nothing is added to it.
    later = blast_engine.read_stored_minute_bars(path, {SYMBOL: minute(28, 11, 5)}, 10_000)
    assert blast_engine.fill_unstreamed_minutes(path, {SYMBOL: minute(28, 11, 5)}, later, 10_000)[SYMBOL] == later[SYMBOL]


def test_an_intraday_re_warm_of_a_contract_streamed_today_reads_no_chain_marks(tmp_path, monkeypatch):
    """2 Nov 2026: every ladder refresh re-read the chain for all 546 contracts (~43 s) with the feed down."""
    stored = [(minute(28, 11, m), 60.0) for m in range(0, 10)]
    path = history_db(tmp_path, stored, [(minute(27, 13, 0), 30.0)])
    read = blast_engine.read_stored_minute_bars(path, {SYMBOL: minute(28, 11, 5)}, 10_000)

    def no_read(*_):
        raise AssertionError("a contract already streamed today has no unstreamed stretch to fill")

    monkeypatch.setattr(blast_engine, "chain_minutes", no_read)
    today = datetime(2026, 10, 28, tzinfo=IST_ZONE).date()
    assert blast_engine.fill_unstreamed_minutes(path, {SYMBOL: minute(28, 11, 5)}, read, 10_000, today)[SYMBOL] == read[SYMBOL]


def test_warm_up_folds_the_chain_filled_days_on_the_hma_kama_strategy(tmp_path):
    stored = [(minute(28, 9, 15) + i * 60, 50.0 + i * 0.01) for i in range(30)]
    marks = [(minute(day, 9, 20) + k * 900, 10.0 + day + k * 0.1) for day in (26, 27) for k in range(25)]
    path = history_db(tmp_path, stored, marks)
    lane = build(tmp_path, blast_signal_bar_seconds=1800)
    asyncio.run(lane.warm_from_store(path, [SYMBOL]))
    hma, kama = lane._hk_value[SYMBOL]
    assert hma is not None and kama is not None      # 26-27 Oct buckets came from the chain marks
    (tmp_path / "macd").mkdir()
    macd_lane = build(tmp_path / "macd", blast_signal_bar_seconds=1800, blast_strategy="macd_screen")
    asyncio.run(macd_lane.warm_from_store(path, [SYMBOL]))
    assert macd_lane._hk_value.get(SYMBOL, (None, None))[0] is None   # the MACD screen keeps reading stored minutes only


def _enter(lane):
    drive(lane, [20 - i * 0.25 for i in range(40)])
    drive(lane, [10 + i * 0.6 for i in range(1, 9)], start=40)
    assert lane.portfolio.positions[SYMBOL].quantity > 0


def test_a_refused_hma_exit_is_retried_on_the_next_trade(tmp_path, monkeypatch):
    """11 Nov 2026: the exit was refused for a stale price and never retried, so the position stayed open."""
    from macd_trader import execution as execution_module
    lane = build(tmp_path)
    _enter(lane)
    now = datetime.now(UTC)
    lane.execution.set_quote(SYMBOL, 14.0, now - timedelta(minutes=5))       # a thin contract's last trade
    assert asyncio.run(lane.flatten(SYMBOL, EXIT_HK)) is False
    assert lane.health()["pending_exits"] == {SYMBOL: EXIT_HK}
    assert lane.execution.exit_failures[SYMBOL]["count"] == 1
    clock = [execution_module.time.monotonic() + 20]                         # past the 15 s back-off
    monkeypatch.setattr(execution_module.time, "monotonic", lambda: clock[0])
    asyncio.run(lane.on_tick(SYMBOL, 14.2, now))                             # the next trade
    assert SYMBOL not in lane.portfolio.positions
    closed = lane.execution.portfolio_snapshot()["closed_positions"]
    assert closed[-1]["exit_reason"] == EXIT_HK and abs(closed[-1]["exit_price"] - 14.2) < 1e-6
    assert lane.health()["pending_exits"] == {} and lane.health()["pending_exit_fills"] == 1
    assert SYMBOL not in lane.execution.exit_failures                        # the header chip clears


def test_a_pending_exit_is_dropped_when_the_position_closes_another_way(tmp_path):
    lane = build(tmp_path)
    _enter(lane)
    now = datetime.now(UTC)
    lane.execution.set_quote(SYMBOL, 14.0, now - timedelta(minutes=5))
    asyncio.run(lane.flatten(SYMBOL, EXIT_HK))
    lane.portfolio.positions.pop(SYMBOL)                                     # e.g. the owner closed it
    assert asyncio.run(lane.retry_pending_exit(SYMBOL)) is False
    assert lane.health()["pending_exits"] == {}


def test_other_refused_exits_are_not_kept_pending(tmp_path):
    lane = build(tmp_path)
    _enter(lane)
    lane.execution.set_quote(SYMBOL, 14.0, datetime.now(UTC) - timedelta(minutes=5))
    asyncio.run(lane.flatten(SYMBOL, "EXPIRY_EXIT_15_20_IST"))              # re-judged on every tick anyway
    assert lane.health()["pending_exits"] == {}
