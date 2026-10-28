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
