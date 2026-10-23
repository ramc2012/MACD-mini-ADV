import asyncio
import sqlite3
from datetime import date
from types import SimpleNamespace

from macd_trader import oi_capture
from macd_trader.desk import DeskMixin
from macd_trader.rollover import Expiry


def _chain(expiry, expiries=()):
    entry = SimpleNamespace(strike=1200.0, option_type="CE", symbol=f"NSE:RELIANCE{expiry}1200CE", ltp=5.0, volume=10,
                            oi=1000, bid=4.9, ask=5.1, prev_oi=900)
    return SimpleNamespace(expiry=expiry, spot_price=1190.0, entries=[entry], expiries=list(expiries), fp=0.0, vix=None)


def test_save_oi_skips_contracts_without_an_answer(tmp_path):
    connection = sqlite3.connect(tmp_path / "h.sqlite3")
    stored = oi_capture.save_oi(connection, 60, {"NSE:NIFTY26OCTFUT": {"oi": 5, "prev_oi": 4, "ltp": 1.0, "volume": 2},
                                                 "NSE:BANKNIFTY26OCTFUT": None})
    assert stored == 1
    assert connection.execute("SELECT symbol, oi, prev_oi FROM oi_minutes").fetchall() == [("NSE:NIFTY26OCTFUT", 5, 4)]


def test_the_next_series_is_collected_only_in_the_front_series_last_week():
    listed = [Expiry("2026-10-27", "a"), Expiry("2026-11-24", "b"), Expiry("2026-12-29", "c")]
    chain = _chain("2026-10-27", listed)
    assert oi_capture.next_expiry_token(chain, "2026-10-27", 7, date(2026, 10, 23)) == "b"
    assert oi_capture.next_expiry_token(chain, "2026-10-27", 7, date(2026, 10, 12)) is None
    assert oi_capture.next_expiry_token(_chain("2026-10-27"), "2026-10-27", 7, date(2026, 10, 23)) is None


def test_a_stock_chain_stores_front_and_next_series_under_the_stock_name(tmp_path, monkeypatch):
    calls = []
    listed = [Expiry("2026-10-27", "a"), Expiry("2026-11-24", "b")]

    async def option_chain(symbol, expiry_token=None, strikecount=None):
        calls.append((symbol, expiry_token, strikecount))
        return _chain("2026-11-24" if expiry_token else "2026-10-27", listed)

    async def no_sleep(_seconds):
        return None

    desk = SimpleNamespace(broker=SimpleNamespace(option_chain=option_chain),
                           settings=SimpleNamespace(research_database_path=str(tmp_path / "h.sqlite3")))
    for name in ("_stock_chain", "_store_chain"):
        setattr(desk, name, getattr(DeskMixin, name).__get__(desk))
    monkeypatch.setattr("macd_trader.desk.asyncio.sleep", no_sleep)
    monkeypatch.setattr(oi_capture, "NEXT_SERIES_WITHIN_DAYS", 400)    # any test date is inside the window

    assert asyncio.run(desk._stock_chain("RELIANCE", "NSE:RELIANCE-EQ")) == 2
    assert calls == [("NSE:RELIANCE-EQ", None, oi_capture.STOCK_STRIKES), ("NSE:RELIANCE-EQ", "b", oi_capture.STOCK_STRIKES)]
    connection = sqlite3.connect(tmp_path / "h.sqlite3")
    assert sorted(connection.execute("SELECT underlying, expiry, oi FROM chain_snapshots")) == [
        ("RELIANCE", "2026-10-27", 1000), ("RELIANCE", "2026-11-24", 1000)]
