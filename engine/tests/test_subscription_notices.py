"""5 Oct: one expired contract in a 1,610-symbol subscription killed the feed all session."""
import asyncio
from datetime import date

from macd_trader.brokers import _is_partial_subscription_notice
from macd_trader.engine import TradingEngine, _drop_expired


def test_invalid_symbol_notice_is_not_fatal():
    notice = {"code": -300, "message": "Please provide a valid symbol", "s": "error", "type": "sub",
              "invalid_symbols": ["BSE:SENSEX26O0172900PE"]}
    assert _is_partial_subscription_notice(notice)


def test_index_depth_notice_is_not_fatal():
    assert _is_partial_subscription_notice(
        {"code": -300, "message": "Index does not have market depth", "s": "error", "type": "sub"})


def test_failed_lookup_stays_fatal():
    # Nothing is subscribed when the token lookup itself fails.
    assert not _is_partial_subscription_notice(
        {"code": -300, "message": "FYERS symbol-token lookup failed for batch 3", "s": "error", "type": "sub"})
    assert not _is_partial_subscription_notice("socket closed")
    assert not _is_partial_subscription_notice(None)


def test_expired_contracts_are_dropped_from_the_subscription():
    symbols = ["NSE:NIFTY50-INDEX", "NSE:SBIN-EQ", "NSE:NIFTY26OCTFUT",
               "BSE:SENSEX26O0172900PE",          # weekly, expired 1 Oct
               "NSE:NIFTY26O1322650PE",           # weekly, 13 Oct
               "NSE:TECHM26OCT1600CE",            # monthly, 27 Oct
               "NSE:ABC26SEP100CE"]               # known expiry passed
    kept = _drop_expired(symbols, {"NSE:ABC26SEP100CE": "2026-09-29"}, date(2026, 10, 5))
    assert kept == ["NSE:NIFTY50-INDEX", "NSE:SBIN-EQ", "NSE:NIFTY26OCTFUT",
                    "NSE:NIFTY26O1322650PE", "NSE:TECHM26OCT1600CE"]


def test_expiry_day_contract_stays_subscribed():
    assert _drop_expired(["BSE:SENSEX26O0172900PE"], {}, date(2026, 10, 1)) == ["BSE:SENSEX26O0172900PE"]


def test_stream_failure_is_recorded_even_when_already_in_error(tmp_path):
    from macd_trader.config import Settings

    engine = TradingEngine(Settings(
        symbols_csv="NSE:TEST-EQ", tick_capture_enabled=False,
        database_path=str(tmp_path / "book.sqlite3"),
        mp_database_path=str(tmp_path / "mp.sqlite3"),
        research_database_path=str(tmp_path / "history.sqlite3"),
        tick_database_path=str(tmp_path / "ticks.sqlite3"),
        contract_snapshot_path=str(tmp_path / "contracts.json"),
    ))

    async def failing_stream(symbols, handler):
        raise RuntimeError("Fyers subscription failed: boom")

    engine.broker.stream = failing_stream
    engine.status = "error"
    engine.error = None
    asyncio.run(engine._run_broker_stream())
    assert engine.status == "error"
    assert engine.error == "Fyers subscription failed: boom"
