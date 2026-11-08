"""Owner, 8 Nov 2026: MACD lane re-entry into a stock's ATM option while its premium trend is still running."""
import asyncio
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from macd_trader import reentry as reentry_mod
from macd_trader.config import Settings
from macd_trader.events import EventHub
from macd_trader.execution import ExecutionManager
from macd_trader.models import Signal
from macd_trader.portfolio import Portfolio
from macd_trader.reentry import REENTRY_KIND, ReentryTracker, is_reentry_kind, stock_key
from macd_trader.repository import TradeRepository

IST = ZoneInfo("Asia/Kolkata")
OLD_ATM = "NSE:FORCEMOT26NOV17000CE"
NEW_ATM = "NSE:FORCEMOT26NOV18000CE"
PUT = "NSE:FORCEMOT26NOV17000PE"
OTHER = "NSE:HAL26NOV4900CE"


class NoBroker:
    name = "fyers"

    async def place_order(self, _order):
        raise AssertionError("paper mode called the broker")


def _bar(hour: int, minute: int) -> int:
    """Start of a 30-min bar today (IST)."""
    return int(datetime.now(IST).replace(hour=hour, minute=minute, second=0, microsecond=0).timestamp())


def _manager(tmp_path: Path) -> tuple[ExecutionManager, ReentryTracker]:
    settings = Settings(execution_mode="paper", symbols_csv="NSE:SBIN-EQ", slippage_bps=0, auto_trade=True)
    manager = ExecutionManager(settings, NoBroker(), Portfolio(10_000_000), TradeRepository(str(tmp_path / "b.sqlite3")),
                               EventHub())
    manager.set_tradable_contracts({OLD_ATM: 25, NEW_ATM: 25, PUT: 25, OTHER: 150})
    manager.set_entry_universe({OLD_ATM, PUT, OTHER})
    for symbol in (OLD_ATM, NEW_ATM, PUT, OTHER):
        manager.set_quote(symbol, 100.0)
    tracker = ReentryTracker(str(tmp_path / "macd_reentry.json"))
    manager.reentry = tracker
    return manager, tracker


def _signal(symbol: str, kind: str = "PREMIUM_MACD_ZERO_CROSS_UP_INTRABAR") -> Signal:
    return Signal(symbol=symbol, side="BUY", kind=kind, price=100.0, macd=1.0, signal=0.5, histogram=0.5)


def _open_and_close(manager: ExecutionManager, symbol: str, reason: str, exit_price: float) -> None:
    asyncio.run(manager.submit(symbol, "BUY", lots=1, signal=_signal(symbol)))
    manager.set_quote(symbol, exit_price)
    manager.arm_exit(symbol, reason)
    asyncio.run(manager.submit(symbol, "SELL", manager.portfolio.positions[symbol].quantity))
    manager.set_quote(symbol, 100.0)


RISING = dict(macd=[0.5, 0.8, 1.2], highs=[101.0, 104.0], close=103.0)


def _check(tracker, symbol, universe, flat=True, bar=(13, 45), **over):
    args = {**RISING, **over}
    return tracker.check(symbol, _bar(*bar), 1800, args["macd"], args["highs"], args["close"], universe, flat)


def test_keys_and_kind():
    assert stock_key(OLD_ATM) == stock_key(NEW_ATM) == "FORCEMOT|CE"
    assert stock_key(PUT) == "FORCEMOT|PE"
    assert stock_key("NSE:FORCEMOT-EQ") is None
    assert is_reentry_kind(REENTRY_KIND) and not is_reentry_kind("PREMIUM_MACD_ZERO_CROSS_UP_INTRABAR")


def test_a_trailing_stop_exit_arms_the_stock_and_the_rolled_atm_contract_re_enters(tmp_path):
    manager, tracker = _manager(tmp_path)
    _open_and_close(manager, OLD_ATM, "TRAILING_STOP_25_PCT", 140.0)
    assert set(tracker.eligible) == {"FORCEMOT|CE"}
    # flat, the contract rolled to the new ATM strike: only that one is judged
    manager.set_entry_universe({NEW_ATM, PUT, OTHER})
    assert _check(tracker, OLD_ATM, manager.entry_universe) is None          # old strike: not today's ATM
    row = _check(tracker, NEW_ATM, manager.entry_universe)
    assert row and row["exit_reason"] == "TRAILING_STOP_25_PCT" and row["symbol"] == OLD_ATM
    signal = _signal(NEW_ATM, REENTRY_KIND)
    tracker.note_fired(signal.signal_id, NEW_ATM, row)
    assert not tracker.eligible, "one shot: the next bar cannot fire again"
    asyncio.run(manager.on_signal(signal))
    assert NEW_ATM in manager.portfolio.positions
    position_id = manager.portfolio.positions[NEW_ATM].position_id
    assert position_id in tracker.positions and tracker.snapshot()["open"] == 1
    # the re-entry's own exit does not re-arm the stock, and is tallied
    manager.set_quote(NEW_ATM, 70.0)
    manager.arm_exit(NEW_ATM, "HARD_STOP_30_PCT")
    asyncio.run(manager.submit(NEW_ATM, "SELL", manager.portfolio.positions[NEW_ATM].quantity))
    snap = tracker.snapshot()
    assert not tracker.eligible and snap["closed"] == 1 and snap["open"] == 0
    assert snap["realized_pnl"] == -750.0 and snap["avg_return_pct"] == -30.0 and snap["wins"] == 0
    assert snap["positions"][0]["exit_reason"] == "HARD_STOP_30_PCT"


def test_only_stop_and_trail_exits_arm(tmp_path):
    manager, tracker = _manager(tmp_path)
    for reason in ("MACD_INVALIDATED", "EXPIRY_DAY_FLATTEN", "EXTREME_BOOK_CALL_INDEX_1.5ATR_UP_EOD"):
        _open_and_close(manager, OLD_ATM, reason, 110.0)
        assert not tracker.eligible, reason
    _open_and_close(manager, OLD_ATM, "HARD_STOP_30_PCT", 70.0)
    assert set(tracker.eligible) == {"FORCEMOT|CE"}
    _open_and_close(manager, OLD_ATM, "MACD_INVALIDATED", 95.0)          # a normal entry + other exit clears it
    assert not tracker.eligible


def test_the_trigger_needs_macd_above_zero_rising_two_bars_and_a_higher_close(tmp_path):
    _, tracker = _manager(tmp_path)
    tracker.eligible["FORCEMOT|CE"] = {"symbol": OLD_ATM, "exit_reason": "TRAILING_STOP_25_PCT",
                                       "armed_on": datetime.now(IST).date().isoformat()}
    universe = {OLD_ATM}
    assert _check(tracker, OLD_ATM, universe, macd=[0.5, 1.2, 0.9]) is None        # MACD fell on this bar
    assert _check(tracker, OLD_ATM, universe, macd=[1.0, 0.8, 1.2]) is None        # not two rising bars
    assert _check(tracker, OLD_ATM, universe, close=100.9) is None                 # not above the previous high (101)
    assert _check(tracker, OLD_ATM, universe, flat=False) is None                  # a position on the stock
    assert _check(tracker, OLD_ATM, universe, bar=(15, 15)) is None                # closes 15:45, after 15:15
    assert _check(tracker, PUT, {PUT}) is None                                     # the other side is not armed
    assert _check(tracker, OLD_ATM, universe, bar=(14, 45)) is not None            # closes 15:15: allowed
    assert "FORCEMOT|CE" in tracker.eligible, "check() alone never consumes"


def test_macd_back_at_or_below_zero_ends_the_eligibility(tmp_path):
    _, tracker = _manager(tmp_path)
    tracker.eligible["FORCEMOT|CE"] = {"symbol": OLD_ATM, "exit_reason": "TRAILING_STOP_25_PCT",
                                       "armed_on": datetime.now(IST).date().isoformat()}
    assert _check(tracker, OLD_ATM, {OLD_ATM}, macd=[0.3, 0.1, -0.05]) is None
    assert not tracker.eligible and tracker.cleared == {"MACD back below zero": 1}


def test_eligibility_expires_after_five_regular_sessions(tmp_path, monkeypatch):
    _, tracker = _manager(tmp_path)
    monkeypatch.setattr(reentry_mod.market_calendar, "is_regular_trading_day", lambda d: d.weekday() < 5)
    today = datetime.now(IST).date()
    old = today - timedelta(days=10)
    tracker.eligible["FORCEMOT|CE"] = {"symbol": OLD_ATM, "exit_reason": "HARD_STOP_30_PCT", "armed_on": old.isoformat()}
    assert _check(tracker, OLD_ATM, {OLD_ATM}) is None and not tracker.eligible
    tracker.eligible["FORCEMOT|CE"] = {"symbol": OLD_ATM, "exit_reason": "HARD_STOP_30_PCT",
                                       "armed_on": (today - timedelta(days=1)).isoformat()}
    assert _check(tracker, OLD_ATM, {OLD_ATM}) is not None


def test_state_survives_a_restart(tmp_path):
    manager, tracker = _manager(tmp_path)
    _open_and_close(manager, OLD_ATM, "TRAILING_STOP_25_PCT", 140.0)
    again = ReentryTracker(str(tmp_path / "macd_reentry.json"))
    assert set(again.eligible) == {"FORCEMOT|CE"}


def test_execution_snapshot_carries_the_reentry_block(tmp_path):
    manager, tracker = _manager(tmp_path)
    snap = manager.snapshot()["reentry"]
    assert snap["kind"] == REENTRY_KIND and snap["eligible"] == [] and snap["closed"] == 0


def test_engine_hook_emits_a_tagged_signal_into_the_macd_lane_only(tmp_path):
    """engine._maybe_reenter: a closed ATM bar that qualifies saves a PREMIUM_MACD_REENTRY signal and enters the
    MACD lane directly (no entry gate, no smart lanes)."""
    from collections import deque
    from types import SimpleNamespace
    from macd_trader.engine import TradingEngine
    from macd_trader.models import Candle, IndicatorPoint

    manager, tracker = _manager(tmp_path)
    _open_and_close(manager, OLD_ATM, "TRAILING_STOP_25_PCT", 140.0)
    manager.set_entry_universe({NEW_ATM, PUT, OTHER})
    start = int(datetime.now().timestamp()) // 1800 * 1800
    points = deque(IndicatorPoint(NEW_ATM, start - 1800 * (2 - i), m, 0.0, m, None, None, None, None, None, None, None)
                   for i, m in enumerate((0.5, 0.8, 1.2)))
    candles = deque([Candle(NEW_ATM, start - 1800, 100, 101, 99, 100, 10, True),
                     Candle(NEW_ATM, start, 100, 104, 99, 103, 10, True)])
    saved, routed = [], []
    eng = SimpleNamespace(
        reentry=tracker, settings=manager.settings, execution=manager, portfolio=manager.portfolio,
        strategy=SimpleNamespace(indicators={NEW_ATM: SimpleNamespace(count=100)}, point_history={NEW_ATM: points},
                                 signals=[]),
        history={NEW_ATM: candles}, repository=SimpleNamespace(save_signal=lambda s: saved.append(s) or True),
        events=EventHub(), signals_emitted=0, _signal_bar_is_current=lambda s: True)
    eng._route_signal = lambda s: routed.append(s)
    # pin the bar inside the session so the 15:15 cut-off does not depend on when the test runs
    tracker_check = tracker.check
    tracker.check = lambda symbol, bar, *a: tracker_check(symbol, _bar(12, 15), *a)
    asyncio.run(TradingEngine._maybe_reenter(eng, candles[-1], points[-1]))
    assert tracker.error is None
    assert [s.kind for s in saved] == [REENTRY_KIND] and not routed
    assert NEW_ATM in manager.portfolio.positions and eng.signals_emitted == 1
    assert manager.portfolio.positions[NEW_ATM].position_id in tracker.positions
