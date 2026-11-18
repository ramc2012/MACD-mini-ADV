"""Series roll (owner, 18 Nov 2026): November stock options closed on the quote side and re-opened at December's ATM
with the same rupees; quote-side paper fills (S1)."""
from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from macd_trader import execution as execution_module
from macd_trader import rollover, series_roll
from macd_trader.config import Settings
from macd_trader.events import EventHub
from macd_trader.execution import ExecutionManager
from macd_trader.models import Tick, Trade
from macd_trader.portfolio import Portfolio
from macd_trader.repository import TradeRepository

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 11, 18, 16, 30, tzinfo=IST)
TS = int(datetime(2026, 11, 18, 15, 26, tzinfo=IST).timestamp())
NOV_SYM, DEC_ATM = "NSE:ABC26NOV1000CE", "NSE:ABC26DEC1000CE"


@pytest.fixture(autouse=True)
def e5(monkeypatch):
    rollover.set_stock_exit_sessions(5, 5)
    monkeypatch.setattr(execution_module, "datetime",
                        type("D", (datetime,), {"now": staticmethod(lambda tz=None: NOW.astimezone(tz) if tz else NOW)}))
    yield
    rollover.set_stock_exit_sessions(5, 5)


def _research(path):
    db = sqlite3.connect(path)
    db.executescript("""CREATE TABLE chain_snapshots (ts INTEGER, underlying TEXT, expiry TEXT, strike REAL, option_type TEXT,
        symbol TEXT, ltp REAL, volume INTEGER, oi INTEGER, spot REAL, bid REAL, ask REAL, prev_oi INTEGER, fp REAL, vix REAL,
        PRIMARY KEY (ts, symbol));""")
    rows = []
    for expiry, mon, base in (("2026-11-23", "NOV", 10.0), ("2026-12-29", "DEC", 30.0)):
        for strike in (980, 1000, 1020):
            for right in ("CE", "PE"):
                ltp = base + (1000 - strike) / 10 if right == "CE" else base + (strike - 1000) / 10
                rows.append((TS, "ABC", expiry, strike, right, f"NSE:ABC26{mon}{strike}{right}", ltp, 100, 1000, 1004.0,
                             round(ltp - 0.5, 2), round(ltp + 0.5, 2), 900, 1005.0, 14.0))
    db.executemany("INSERT INTO chain_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    db.commit()
    db.close()


def _ticks(path):
    db = sqlite3.connect(path)
    db.executescript("""CREATE TABLE tick_symbols (id INTEGER PRIMARY KEY, symbol TEXT UNIQUE);
        CREATE TABLE tick_minute_flow (symbol_id INTEGER, minute_ts INTEGER, close REAL, avg_spread REAL);""")
    db.execute("INSERT INTO tick_symbols VALUES (1, ?)", (NOV_SYM,))
    # the last minute of the session (15:29 IST): close 10.2, spread 0.6 -> bid 9.9
    db.execute("INSERT INTO tick_minute_flow VALUES (1, ?, 10.2, 0.6)", (int(datetime(2026, 11, 18, 15, 29, tzinfo=IST).timestamp()),))
    db.commit()
    db.close()


def _engine(tmp_path):
    settings = Settings(execution_mode="paper", database_path=str(tmp_path / "m.sqlite3"),
                        research_database_path=str(tmp_path / "h.sqlite3"), tick_database_path=str(tmp_path / "t.sqlite3"),
                        runtime_settings_path=str(tmp_path / "settings.json"))
    _research(settings.research_database_path)
    _ticks(settings.tick_database_path)
    manager = ExecutionManager(settings, None, Portfolio(1_000_000), TradeRepository(settings.database_path), EventHub())
    manager.portfolio.apply_trade(Trade("o1", NOV_SYM, "BUY", 500, 8.0, lots=5, lot_size=100))
    manager.set_contract_expiries({NOV_SYM: "2026-11-23"})

    async def lot(_u, _chain):
        return 100

    return SimpleNamespace(settings=settings, execution=manager, blast=None, smart_lanes=None, agentic=None,
                           spot_lane=None, latest_ticks={}, events=EventHub(), _builder_lot=lot,
                           _expiry_of=lambda s: manager.contract_expiries.get(s))


def test_a_november_stock_option_is_closed_at_the_bid_and_reopened_at_decembers_atm_ask(tmp_path):
    engine = _engine(tmp_path)
    rows = asyncio.run(series_roll.plan(engine, NOW))
    assert len(rows) == 1 and rows[0]["status"] == "PLANNED"
    assert rows[0]["exit"]["price"] == pytest.approx(9.9) and rows[0]["exit"]["source"] == "tick_store"
    assert rows[0]["entry"]["symbol"] == DEC_ATM                          # strike nearest the stored spot 1004
    # rupees out: 500 x 9.9 = 4950; December ATM ask 30.5 x 100 = 3050 a lot -> 1 lot
    assert rows[0]["entry"]["lots"] == 1 and rows[0]["entry"]["price"] == pytest.approx(30.5)

    out = asyncio.run(series_roll.execute(engine, NOW, journal_dir=str(tmp_path)))
    assert out["counts"] == {"ROLLED": 1}
    book = engine.execution.portfolio
    assert NOV_SYM not in book.positions and book.positions[DEC_ATM].quantity == 100
    assert book.positions[DEC_ATM].average_price == pytest.approx(30.5)
    closed = engine.execution.closed_positions[0]
    assert closed["exit_reason"] == series_roll.ROLL_EXIT and closed["exit_price"] == pytest.approx(9.9)
    assert book.cash == pytest.approx(1_000_000 - 4000 + 4950 - 3050)
    assert (tmp_path / out["journal"].split("/")[-1]).exists()


def test_only_stock_series_past_their_last_day_roll():
    from datetime import date
    assert series_roll.series_closed("NSE:ABC26NOV1000CE", "2026-11-23", date(2026, 11, 16))     # E-5 itself
    assert not series_roll.series_closed("NSE:ABC26NOV1000CE", "2026-11-23", date(2026, 11, 13))
    assert not series_roll.series_closed("NSE:NIFTY26NOV26000CE", "2026-11-24", date(2026, 11, 20))  # index
    assert series_roll.series_closed("NSE:ABC26NOVFUT", "2026-11-23", date(2026, 11, 18))
    assert not series_roll.series_closed("NSE:NIFTY26NOVFUT", "2026-11-24", date(2026, 11, 20))


def test_paper_fills_take_the_quote_side_even_when_the_quote_did_not_ride_on_the_price_tick(tmp_path):
    settings = Settings(execution_mode="paper", database_path=str(tmp_path / "m.sqlite3"))
    manager = ExecutionManager(settings, None, Portfolio(1_000_000), TradeRepository(settings.database_path), EventHub())
    sym = "NSE:ABC26DEC1000CE"
    manager.quote_source = lambda s: Tick(s, 30.2, bid=29.8, ask=30.6, timestamp=datetime.now(UTC))
    assert manager.estimate_fill(sym, "BUY", 30.0) == pytest.approx(30.6)       # ltp 30.0 differs from the tick's 30.2
    assert manager.estimate_fill(sym, "SELL", 30.0) == pytest.approx(29.8)
    manager.quote_source = lambda s: None                                     # no quote now: the last seen half spread
    assert manager.estimate_fill(sym, "BUY", 30.0) == pytest.approx(30.0 * (1 + 0.4 / 30.2))
    assert manager.estimate_fill("NSE:XYZ26DEC500PE", "SELL", 10.0) == pytest.approx(10.0 * (1 - 0.007))   # default
    assert manager.estimate_fill("NSE:XYZ26DECFUT", "BUY", 1000.0) == pytest.approx(1000.0 * 1.0008)


def test_a_stale_deep_itm_leg_exits_at_todays_intrinsic_and_a_future_rolls_on_the_chains_futures_price(tmp_path):
    engine = _engine(tmp_path)
    book = engine.execution.portfolio
    deep, fut = "NSE:ABC26NOV900CE", "NSE:ABC26NOVFUT"
    book.apply_trade(Trade("o2", deep, "BUY", 100, 90.0, lots=1, lot_size=100))
    book.apply_trade(Trade("o3", fut, "BUY", 100, 990.0, lots=1, lot_size=100))
    engine.execution.set_contract_expiries({deep: "2026-11-23", fut: "2026-11-23"})
    db = sqlite3.connect(engine.settings.tick_database_path)
    db.execute("INSERT INTO tick_symbols VALUES (2, ?)", (deep,))
    db.execute("INSERT INTO tick_minute_flow VALUES (2, ?, 70.0, 1.0)",            # yesterday's last print, stale
               (int(datetime(2026, 11, 17, 13, 5, tzinfo=IST).timestamp()),))
    db.commit()
    db.close()
    rows = {r["symbol"]: r for r in asyncio.run(series_roll.plan(engine, NOW))}
    # today's stored spot 1004 -> intrinsic 104, less the 0.7% default half spread
    assert rows[deep]["exit"]["source"] == "intrinsic_today" and rows[deep]["exit"]["price"] == pytest.approx(104 * 0.993)
    assert rows[fut]["exit"]["source"] == "chain_futures_price"
    assert rows[fut]["exit"]["price"] == pytest.approx(1005.0 * (1 - 0.0008))
    assert rows[fut]["entry"]["symbol"] == "NSE:ABC26DECFUT" and rows[fut]["entry"]["lots"] == 1
    assert rows[fut]["entry"]["price"] == pytest.approx(1005.0 * 1.0008)
    out = asyncio.run(series_roll.execute(engine, NOW))
    assert out["counts"] == {"ROLLED": 3}
    assert book.positions["NSE:ABC26DECFUT"].quantity == 100 and fut not in book.positions and deep not in book.positions


def test_the_expiry_view_lists_each_book_once():
    from macd_trader.engine import TradingEngine
    spot = object()
    stub = SimpleNamespace(_books=lambda: [("macd", object()), ("spot_macd", spot), ("spot_macd", spot)])
    assert [name for name, _ in TradingEngine._option_lanes(stub)] == ["macd", "spot_macd"]
