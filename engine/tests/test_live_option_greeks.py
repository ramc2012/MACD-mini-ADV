"""Watchlist Greeks require each leg's own current exchange quote."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from macd_trader import engine as engine_module
from macd_trader.contracts import OptionContract
from macd_trader.engine import TradingEngine
from macd_trader.models import Tick


NOW = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)


@pytest.fixture
def live_engine(monkeypatch):
    class FixedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)

    monkeypatch.setattr(engine_module, "datetime", FixedClock)
    engine = TradingEngine.__new__(TradingEngine)
    engine.settings = SimpleNamespace(order_quote_max_age_seconds=10, risk_free_rate=0.065)
    engine.latest_ticks = {}
    engine._greeks_cache = {}
    contract = OptionContract("NIFTY", "NSE:NIFTY50-INDEX", "CE", "NSE:NIFTY26OCT24100CE",
                              24100, "2026-10-27", 24100, oi=10, lot_size=75)
    engine.latest_ticks[contract.spot_symbol] = Tick(contract.spot_symbol, 24100, timestamp=NOW)
    engine.latest_ticks[contract.symbol] = Tick(contract.symbol, 85, timestamp=NOW, open_interest=100)
    return engine, contract


def test_missing_option_quote_never_uses_the_underlying_selection_reference(live_engine, monkeypatch):
    engine, contract = live_engine
    del engine.latest_ticks[contract.symbol]
    calls = []
    monkeypatch.setattr(engine_module, "contract_gex", lambda **kw: calls.append(kw) or {"iv": 99})
    assert engine.option_greeks(contract) == {"iv": None, "gamma": None, "gex": None}
    assert calls == []


@pytest.mark.parametrize("leg", ["option", "spot"])
@pytest.mark.parametrize("invalid", ["missing", "stale", "future", "non_finite", "non_positive"])
def test_expired_or_invalid_inputs_cannot_reuse_a_populated_cache(live_engine, leg, invalid):
    engine, contract = live_engine
    assert engine.option_greeks(contract)["iv"] is not None
    symbol = contract.symbol if leg == "option" else contract.spot_symbol
    if invalid == "missing":
        del engine.latest_ticks[symbol]
    elif invalid == "stale":
        engine.latest_ticks[symbol].timestamp = NOW - timedelta(seconds=11)
    elif invalid == "future":
        engine.latest_ticks[symbol].timestamp = NOW + timedelta(seconds=6)
    elif invalid == "non_finite":
        engine.latest_ticks[symbol].ltp = float("nan")
    else:
        engine.latest_ticks[symbol].ltp = 0.0
    assert engine.option_greeks(contract) == {"iv": None, "gamma": None, "gex": None}
    assert contract.symbol not in engine._greeks_cache


def test_valid_quotes_use_the_option_premium_and_live_open_interest(live_engine, monkeypatch):
    engine, contract = live_engine
    calls = []
    monkeypatch.setattr(engine_module, "contract_gex", lambda **kw: calls.append(kw) or {"iv": 20, "gamma": 0.1, "gex": 5})
    assert engine.option_greeks(contract)["iv"] == 20
    assert calls[0]["premium"] == 85 and calls[0]["spot"] == 24100 and calls[0]["open_interest"] == 100
    assert calls[0]["now"] == NOW


def test_unchanged_fresh_prices_do_not_cache_time_to_expiry_forever(live_engine, monkeypatch):
    engine, contract = live_engine
    elapsed, calls = [100.0], []
    monkeypatch.setattr(engine_module.time, "monotonic", lambda: elapsed[0])
    monkeypatch.setattr(engine_module, "contract_gex", lambda **kw: calls.append(kw) or {"iv": len(calls)})
    assert engine.option_greeks(contract)["iv"] == 1
    elapsed[0] += 1
    assert engine.option_greeks(contract)["iv"] == 1
    elapsed[0] += engine_module.GREEKS_CACHE_SECONDS
    assert engine.option_greeks(contract)["iv"] == 2
    assert len(calls) == 2
