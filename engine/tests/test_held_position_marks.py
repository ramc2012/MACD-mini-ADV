import asyncio
from datetime import UTC, datetime

from macd_trader.engine import TradingEngine, regular_session_open
from macd_trader.events import EventHub
from macd_trader.models import Position, Tick
from macd_trader.portfolio import Portfolio


def test_startup_quotes_mark_positions_without_calling_execution():
    symbol = "NSE:POWERINDIA26AUG35000CE"

    class QuoteBroker:
        async def quotes(self, symbols):
            assert symbols == [symbol]
            return {symbol: Tick(symbol, 1125.0)}

    engine = object.__new__(TradingEngine)
    engine.broker = QuoteBroker()
    engine.portfolio = Portfolio(100_000)
    engine.portfolio.positions[symbol] = Position(
        symbol, 25, 1330.57, 1330.57, datetime.now(UTC), lot_size=25,
    )
    engine.latest_ticks = {}
    engine.position_mark_errors = {}
    engine.events = EventHub()
    # Deliberately no execution manager: the method must only mark valuation.
    asyncio.run(engine._refresh_position_marks())

    position = engine.portfolio.positions[symbol]
    assert position.last_price == 1125.0
    assert engine.latest_ticks[symbol].ltp == 1125.0
    assert engine.position_mark_errors == {}
    # Valuation marks feed MFE/MAE; the session-gated peak that arms the
    # trailing stop is untouched.
    assert position.min_price == 1125.0
    assert position.min_return_pct < 0
    assert position.peak_price == 0.0
    assert position.trailing_stop is None


def test_execution_session_gate_rejects_premarket_and_after_hours():
    assert regular_session_open(datetime(2026, 8, 18, 9, 14, tzinfo=UTC)) is True
    assert regular_session_open(datetime(2026, 8, 18, 3, 29, tzinfo=UTC)) is False
    assert regular_session_open(datetime(2026, 8, 18, 10, 1, tzinfo=UTC)) is False
    assert regular_session_open(datetime(2026, 8, 23, 5, 0, tzinfo=UTC)) is False
