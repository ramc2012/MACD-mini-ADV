"""The auction desk's concurrency limit is a setting, not a defect.

On 4 Sep 2026 the desk held exactly four positions with last_order_rejection
None, and mp_engine was refusing new ones with "max_positions reached": four
positions at the 1 lakh notional cap commit only ~4 lakh of its 10 lakh book.
The limit is now 8 by default and editable from the settings panel, so the
value the owner saves has to reach the running desk without a restart.
"""
import asyncio
from types import SimpleNamespace

from macd_trader.config import Settings
from macd_trader.engine import TradingEngine
from macd_trader.events import EventHub
from macd_trader.mp_engine import MPSettings


class TestDefault:
    def test_the_desk_may_hold_eight_positions_by_default(self):
        assert Settings().mp_max_positions == 8

    def test_a_persisted_value_still_wins_over_the_default(self):
        # runtime/settings.json holds the live desk's own copy; raising the
        # default cannot silently override what the operator has saved.
        assert Settings(mp_max_positions=4).mp_max_positions == 4


class TestPropagation:
    def _engine(self, current: Settings) -> SimpleNamespace:
        obj = SimpleNamespace(
            settings=current,
            events=EventHub(),
            execution=SimpleNamespace(settings=current),
            broker=object(),
            strategy=None,
            candles=None,
            history={},
            history_errors={},
            option_symbols=[],
            all_symbols=[],
            portfolio=SimpleNamespace(initial_capital=current.initial_capital, cash=0.0),
            mp=SimpleNamespace(settings=MPSettings(
                max_positions=current.mp_max_positions,
                max_trades_per_day=current.mp_max_trades_per_day,
            )),
            blast=SimpleNamespace(applied=[], apply_settings=lambda s: obj.blast.applied.append(s)),
            _reconfigure_lock=asyncio.Lock(),
        )
        obj.reconfigure_strategy = TradingEngine.reconfigure_strategy.__get__(obj)
        return obj

    def test_a_saved_limit_reaches_the_running_desk(self):
        current = Settings(mp_max_positions=4, mp_max_trades_per_day=12)
        engine = self._engine(current)

        asyncio.run(engine.reconfigure_strategy(
            current.model_copy(update={"mp_max_positions": 8, "mp_max_trades_per_day": 20})))

        assert engine.mp.settings.max_positions == 8
        assert engine.mp.settings.max_trades_per_day == 20
        # The third book is reconfigured on the same path, so a saved change
        # cannot reach two lanes and quietly skip the other.
        assert engine.blast.applied[-1].mp_max_positions == 8
