"""Stock options leave a series on T-2 (operator's rule, 23 Oct 2026): new risk goes to the next series and
held positions are closed by the flatten time that day. Index options keep the expiry-day rules."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from macd_trader import rollover
from macd_trader.config import Settings
from macd_trader.events import EventHub
from macd_trader.execution import ExecutionManager
from macd_trader.portfolio import Portfolio
from macd_trader.repository import TradeRepository
from macd_trader.rollover import Expiry

IST = ZoneInfo("Asia/Kolkata")
OCT, NOV = "2026-10-27", "2026-11-24"


@pytest.fixture(autouse=True)
def t2():
    rollover.set_stock_exit_sessions(2)
    yield
    rollover.set_stock_exit_sessions(2)


def test_t2_of_a_tuesday_expiry_is_the_friday_before():
    assert rollover.stock_last_day(OCT) == date(2026, 10, 23)
    assert rollover.is_stock_option("NSE:SBIN26OCT800CE") and not rollover.is_stock_option("NSE:NIFTY26O2722700PE")
    assert not rollover.is_stock_option("NSE:BANKNIFTY26OCT55000CE") and not rollover.is_stock_option("BSE:SENSEX26O2973000CE")


def test_selection_moves_stock_options_to_the_next_series_from_t2_only():
    expiries = [Expiry(OCT), Expiry(NOV)]
    assert rollover.first_tradable(expiries, 1, date(2026, 10, 22), stock=True).date == OCT
    assert rollover.first_tradable(expiries, 1, date(2026, 10, 23), stock=True).date == NOV
    assert rollover.first_tradable(expiries, 1, date(2026, 10, 23), stock=False).date == OCT    # an index keeps it
    rollover.set_stock_exit_sessions(0)
    assert rollover.first_tradable(expiries, 1, date(2026, 10, 23), stock=True).date == OCT     # rule switched off


def test_held_stock_options_are_due_out_at_the_flatten_time_on_t2_and_at_once_after():
    sym = "NSE:SBIN26OCT800CE"
    assert not rollover.stock_exit_due(sym, OCT, datetime(2026, 10, 23, 14, 0, tzinfo=IST))
    assert rollover.stock_exit_due(sym, OCT, datetime(2026, 10, 23, 15, 10, tzinfo=IST))
    assert rollover.stock_exit_due(sym, OCT, datetime(2026, 10, 26, 9, 16, tzinfo=IST))
    assert not rollover.stock_exit_due("NSE:NIFTY26OCT23000PE", OCT, datetime(2026, 10, 26, 9, 16, tzinfo=IST))
    assert rollover.stock_entry_closed(sym, OCT, date(2026, 10, 23)) and not rollover.stock_entry_closed(sym, OCT, date(2026, 10, 22))


def test_a_buy_or_add_in_the_outgoing_stock_series_is_blocked(tmp_path, monkeypatch):
    settings = Settings(execution_mode="paper", database_path=str(tmp_path / "m.sqlite3"))
    manager = ExecutionManager(settings, None, Portfolio(1_000_000), TradeRepository(str(tmp_path / "m.sqlite3")), EventHub())
    manager.set_contract_expiries({"NSE:SBIN26OCT800CE": OCT, "NSE:SBIN26NOV800CE": NOV, "NSE:NIFTY26OCT23000PE": OCT})
    from macd_trader import execution
    monkeypatch.setattr(execution, "datetime", type("D", (datetime,), {"now": staticmethod(lambda tz=None: datetime(2026, 10, 23, 10, 0, tzinfo=IST))}))
    with pytest.raises(ValueError, match="next series"):
        manager._check_buy_expiry("NSE:SBIN26OCT800CE")
    manager._check_buy_expiry("NSE:SBIN26NOV800CE")
    manager._check_buy_expiry("NSE:NIFTY26OCT23000PE")
