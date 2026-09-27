"""The auction desk is scoped independently of the MACD lane.

The MACD lane trades the full F&O universe. The Market Profile / Order Flow
desk is deliberately narrow because its trade-by-trade feed is capped at
roughly 15 instruments (5 symbols per connection, 3 connections). Narrowing
one must never narrow the other.
"""
from datetime import datetime

import pytest

from macd_trader.config import Settings

pytestmark = pytest.mark.usefixtures("fixed_auction_session_clock")

FIVE = "NSE:NIFTY50-INDEX,NSE:NIFTYBANK-INDEX,BSE:SENSEX-INDEX,NSE:ICICIBANK-EQ,NSE:BSE-EQ"


class TestGlobalUniverse:
    def test_macd_keeps_the_full_universe_when_only_the_desk_is_scoped(self):
        from macd_trader.universe import SPOT_SYMBOLS
        settings = Settings(symbols_csv="", mp_symbols_csv=FIVE)
        assert settings.symbols == list(SPOT_SYMBOLS)
        assert len(settings.symbols) > 200

    def test_symbols_csv_still_narrows_globally_when_deliberately_set(self):
        settings = Settings(symbols_csv="NSE:A-EQ,NSE:B-EQ")
        assert settings.symbols == ["NSE:A-EQ", "NSE:B-EQ"]


class TestDeskScope:
    class _Contract:
        def __init__(self, spot_symbol, underlying=None):
            self.spot_symbol = spot_symbol
            self.underlying = underlying

    def _engine(self, all_symbols, contracts, mp_csv):
        from types import SimpleNamespace

        from macd_trader.engine import TradingEngine
        obj = SimpleNamespace(
            all_symbols=all_symbols,
            settings=SimpleNamespace(mp_symbols_csv=mp_csv),
            contract_selector=SimpleNamespace(contracts=contracts),
        )
        obj.futures_rollover = {}
        obj.mp_configured_symbols = TradingEngine.mp_configured_symbols.__get__(obj)
        obj.mp_spot_symbols = TradingEngine.mp_spot_symbols.__get__(obj)
        obj.mp_universe = TradingEngine.mp_universe.__get__(obj)
        return obj

    def test_desk_gets_its_spots_and_their_atm_contracts_only(self):
        all_symbols = [
            "NSE:NIFTY50-INDEX", "NSE:ICICIBANK-EQ", "NSE:RELIANCE-EQ",
            "NSE:NIFTY26AUG24300CE", "NSE:ICICIBANK26AUG1420CE", "NSE:RELIANCE26AUG1400CE",
        ]
        contracts = {
            "NSE:NIFTY26AUG24300CE": self._Contract("NSE:NIFTY50-INDEX"),
            "NSE:ICICIBANK26AUG1420CE": self._Contract("NSE:ICICIBANK-EQ"),
            "NSE:RELIANCE26AUG1400CE": self._Contract("NSE:RELIANCE-EQ"),
        }
        engine = self._engine(all_symbols, contracts, "NSE:NIFTY50-INDEX,NSE:ICICIBANK-EQ")
        scope = engine.mp_universe()
        assert scope == ["NSE:NIFTY50-INDEX", "NSE:ICICIBANK-EQ",
                         "NSE:NIFTY26AUG24300CE", "NSE:ICICIBANK26AUG1420CE"]
        assert "NSE:RELIANCE-EQ" not in scope
        assert "NSE:RELIANCE26AUG1400CE" not in scope

    def test_empty_scope_means_everything_as_before(self):
        all_symbols = ["NSE:A-EQ", "NSE:B-EQ"]
        engine = self._engine(all_symbols, {}, "")
        assert engine.mp_universe() == all_symbols

    def test_five_underlyings_resolve_to_the_fifteen_symbol_budget(self):
        spots = FIVE.split(",")
        contracts, all_symbols = {}, list(spots)
        for spot in spots:                      # one ATM CE and PE each
            for side in ("CE", "PE"):
                sym = f"{spot}-{side}"
                contracts[sym] = self._Contract(spot)
                all_symbols.append(sym)
        engine = self._engine(all_symbols, contracts, FIVE)
        assert len(engine.mp_universe()) == 15


class TestDeskFiltersTicks:
    @staticmethod
    def _in_session(symbol, minute=560):
        """The desk drops off-session ticks, so stamp inside 09:15-15:30 IST."""
        from datetime import timedelta, timezone
        from zoneinfo import ZoneInfo

        from macd_trader.models import Tick
        ist = ZoneInfo("Asia/Kolkata")
        today = datetime.now(ist).date()
        moment = datetime(today.year, today.month, today.day, tzinfo=ist) + timedelta(minutes=minute)
        return Tick(symbol, 10.0, 5, timestamp=moment.astimezone(timezone.utc))

    def test_on_tick_ignores_symbols_outside_the_scope(self):
        import asyncio
        import os
        import tempfile

        from macd_trader.mp_engine import MPEngine, MPSettings

        with tempfile.TemporaryDirectory() as folder:
            desk = MPEngine(os.path.join(folder, "mp.sqlite3"),
                            settings=MPSettings(enabled=True, auto_trade=False))
            desk.set_universe(["NSE:WATCHED-EQ"])
            asyncio.run(desk.on_tick(self._in_session("NSE:IGNORED-EQ")))
            assert "NSE:IGNORED-EQ" not in desk.flow.states
            asyncio.run(desk.on_tick(self._in_session("NSE:WATCHED-EQ")))
            assert "NSE:WATCHED-EQ" in desk.flow.states

    def test_an_unscoped_desk_still_accepts_everything(self):
        import asyncio
        import os
        import tempfile

        from macd_trader.mp_engine import MPEngine, MPSettings

        with tempfile.TemporaryDirectory() as folder:
            desk = MPEngine(os.path.join(folder, "mp.sqlite3"),
                            settings=MPSettings(enabled=True, auto_trade=False))
            asyncio.run(desk.on_tick(self._in_session("NSE:ANYTHING-EQ")))
            assert "NSE:ANYTHING-EQ" in desk.flow.states


class TestIndexFutures:
    """Index SPOTS stream no volume, so the desk watches index FUTURES.

    Measured on 2026-08-20: NIFTY50-INDEX, NIFTYBANK-INDEX and SENSEX-INDEX
    each recorded 0 traded volume across 359-363 minute bars, while
    NIFTY26AUGFUT traded 3,658,525. Cumulative delta, footprint and absorption
    are all undefined without volume.
    """

    def test_underlying_is_recovered_from_a_futures_symbol(self):
        from macd_trader.universe import desk_underlying
        assert desk_underlying("NSE:NIFTY26AUGFUT") == "NIFTY"
        assert desk_underlying("NSE:BANKNIFTY26AUGFUT") == "BANKNIFTY"
        assert desk_underlying("BSE:SENSEX26AUGFUT") == "SENSEX"

    def test_underlying_is_recovered_from_equities_and_spots(self):
        from macd_trader.universe import desk_underlying
        assert desk_underlying("NSE:ICICIBANK-EQ") == "ICICIBANK"
        assert desk_underlying("NSE:NIFTY50-INDEX") == "NIFTY"
        assert desk_underlying("BSE:SENSEX-INDEX") == "SENSEX"

    def test_unknown_shapes_return_none_rather_than_guessing(self):
        from macd_trader.universe import desk_underlying
        assert desk_underlying("NSE:NIFTY26AUG24300CE") is None
        assert desk_underlying("garbage") is None

    def test_futures_scope_still_picks_up_the_spot_keyed_options(self):
        """The option contracts are keyed to the SPOT; the desk holds the FUTURE."""
        from types import SimpleNamespace

        from macd_trader.engine import TradingEngine

        class Contract:
            def __init__(self, spot_symbol, underlying):
                self.spot_symbol, self.underlying = spot_symbol, underlying

        all_symbols = [
            "NSE:NIFTY50-INDEX", "NSE:NIFTY26AUGFUT", "NSE:ICICIBANK-EQ", "NSE:RELIANCE-EQ",
            "NSE:NIFTY26AUG24300CE", "NSE:ICICIBANK26AUG1420CE", "NSE:RELIANCE26AUG1400CE",
        ]
        contracts = {
            "NSE:NIFTY26AUG24300CE": Contract("NSE:NIFTY50-INDEX", "NIFTY"),
            "NSE:ICICIBANK26AUG1420CE": Contract("NSE:ICICIBANK-EQ", "ICICIBANK"),
            "NSE:RELIANCE26AUG1400CE": Contract("NSE:RELIANCE-EQ", "RELIANCE"),
        }
        obj = SimpleNamespace(
            all_symbols=all_symbols,
            settings=SimpleNamespace(mp_symbols_csv="NSE:NIFTY26AUGFUT,NSE:ICICIBANK-EQ"),
            contract_selector=SimpleNamespace(contracts=contracts),
            futures_rollover={},
        )
        obj.mp_configured_symbols = TradingEngine.mp_configured_symbols.__get__(obj)
        obj.mp_spot_symbols = TradingEngine.mp_spot_symbols.__get__(obj)
        obj.mp_universe = TradingEngine.mp_universe.__get__(obj)
        scope = obj.mp_universe()

        assert "NSE:NIFTY26AUGFUT" in scope
        assert "NSE:NIFTY26AUG24300CE" in scope, "the NIFTY option must follow the future"
        assert "NSE:ICICIBANK26AUG1420CE" in scope
        assert "NSE:NIFTY50-INDEX" not in scope, "the volume-less spot is not watched"
        assert "NSE:RELIANCE-EQ" not in scope and "NSE:RELIANCE26AUG1400CE" not in scope


class TestFuturesSeriesRollover:
    """A configured futures ticker names a series; only one of them is alive.

    ``mp_symbols_csv`` held NSE:NIFTY26AUGFUT, NSE:BANKNIFTY26AUGFUT and
    BSE:SENSEX26AUGFUT. On 26 Aug 2026 none of those are in the feed any more,
    and subscribing to a symbol the feed does not carry is not an error -- it
    is just silence, which looks exactly like a quiet market.
    """

    def _engine(self, mp_csv, rollover):
        from types import SimpleNamespace

        from macd_trader.engine import TradingEngine
        obj = SimpleNamespace(
            all_symbols=[],
            settings=SimpleNamespace(mp_symbols_csv=mp_csv),
            contract_selector=SimpleNamespace(contracts={}),
            futures_rollover=rollover,
        )
        obj.mp_configured_symbols = TradingEngine.mp_configured_symbols.__get__(obj)
        obj.mp_spot_symbols = TradingEngine.mp_spot_symbols.__get__(obj)
        return obj

    def test_a_resolved_series_replaces_the_configured_one(self):
        engine = self._engine(
            "NSE:NIFTY26AUGFUT,BSE:SENSEX26AUGFUT,NSE:ICICIBANK-EQ",
            {"NSE:NIFTY26AUGFUT": "NSE:NIFTY26SEPFUT",
             "BSE:SENSEX26AUGFUT": "BSE:SENSEX26SEPFUT"},
        )
        assert engine.mp_spot_symbols() == [
            "NSE:NIFTY26SEPFUT", "BSE:SENSEX26SEPFUT", "NSE:ICICIBANK-EQ"]
        # The setting itself is untouched, so the roll is re-derived daily
        # rather than baked into the saved configuration.
        assert engine.mp_configured_symbols()[0] == "NSE:NIFTY26AUGFUT"

    def test_an_unresolved_symbol_is_left_exactly_as_configured(self):
        engine = self._engine("NSE:NIFTY26AUGFUT,NSE:ICICIBANK-EQ", {})
        assert engine.mp_spot_symbols() == ["NSE:NIFTY26AUGFUT", "NSE:ICICIBANK-EQ"]


class TestFuturesRootParsing:
    def test_series_and_series_free_forms_both_resolve(self):
        from macd_trader.rollover import futures_root, is_futures_symbol

        assert futures_root("NSE:NIFTY26AUGFUT") == ("NSE", "NIFTY")
        assert futures_root("NSE:NIFTY-FUT") == ("NSE", "NIFTY")
        assert futures_root("BSE:SENSEX26AUGFUT") == ("BSE", "SENSEX")
        assert futures_root("NSE:BAJAJ-AUTO26SEPFUT") == ("NSE", "BAJAJ-AUTO")
        assert futures_root("NSE:M&M-FUT") == ("NSE", "M&M")
        assert is_futures_symbol("NSE:ICICIBANK-EQ") is False
        assert is_futures_symbol("NSE:NIFTY50-INDEX") is False
        assert is_futures_symbol("NSE:SBIN26AUG1070CE") is False

    def test_the_desk_underlying_still_resolves_a_rolled_series(self):
        from macd_trader.universe import desk_underlying

        assert desk_underlying("NSE:NIFTY26SEPFUT") == "NIFTY"
        assert desk_underlying("BSE:SENSEX26SEPFUT") == "SENSEX"
