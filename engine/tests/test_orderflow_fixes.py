"""15 Oct 2026 auction / order-flow fixes: stored profiles from raw ticks with the live day type, entry flow
logging, desk spread-crossing fills, and the index-only shadow desk's scope."""
from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from pytest import approx

from macd_trader.config import Settings
from macd_trader.entry_context import EntryFlow
from macd_trader.events import EventHub
from macd_trader.execution import ExecutionManager
from macd_trader.market_profile import ib_day_type
from macd_trader.models import Tick
from macd_trader.mp_engine import MPEngine, MPSettings
from macd_trader.portfolio import Portfolio
from macd_trader.profile_history import build_session, ensure_tables, save_sessions
from macd_trader.repository import TradeRepository
from macd_trader.tick_store import SCHEMA as TICK_SCHEMA

IST = ZoneInfo("Asia/Kolkata")
SPOT, OPTION = "NSE:SBIN-EQ", "NSE:SBIN26OCT800CE"


def _ticks(symbol: str, start: datetime, n: int, buy: bool):
    """A baseline tick, then n prints at the ask (buy) or the bid (sell)."""
    out = [Tick(symbol, 800.0, volume=1_000, bid=799.9, ask=800.1, timestamp=start)]
    for k in range(1, n + 1):
        price = 800.1 if buy else 799.9
        out.append(Tick(symbol, price, volume=1_000 + 100 * k, bid=799.9, ask=800.1, last_qty=100,
                        timestamp=start + timedelta(seconds=k)))
    return out


def test_entry_flow_signs_the_underlying_session_cvd_by_option_side():
    flow = EntryFlow()
    now = datetime(2026, 10, 15, 10, 30, tzinfo=IST)
    for tick in _ticks(SPOT, now.astimezone(UTC), 20, buy=True):
        flow.on_tick(tick, now)
    ce, pe = flow.context(OPTION), flow.context("NSE:SBIN26OCT800PE")
    assert ce["underlying"] == SPOT and ce["session_cvd"] > 0
    assert ce["session_cvd_share_signed"] > 0 and pe["session_cvd_share_signed"] == approx(-ce["session_cvd_share_signed"])
    flow.on_tick(Tick("NSE:NIFTY50-INDEX", 25000.0, timestamp=now.astimezone(UTC)), now)  # spots without volume are ignored
    assert "NSE:NIFTY50-INDEX" not in flow.tracker.states


def test_index_options_read_the_index_future():
    flow = EntryFlow()
    now = datetime(2026, 10, 15, 10, 30, tzinfo=IST)
    for tick in _ticks("NSE:NIFTY26OCTFUT", now.astimezone(UTC), 5, buy=False):
        flow.on_tick(tick, now)
    out = flow.context("NSE:NIFTY26O2025000PE")
    assert out["underlying"] == "NSE:NIFTY26OCTFUT" and out["session_cvd_share_signed"] > 0  # selling agrees with a put


def test_a_new_entry_records_its_flow_context(tmp_path):
    s = Settings(execution_mode="paper", symbols_csv=OPTION, tick_capture_enabled=False, slippage_bps=0)
    repo = TradeRepository(str(tmp_path / "b.sqlite3"))
    m = ExecutionManager(s, SimpleNamespace(name="t"), Portfolio(1_000_000), repo, EventHub())
    m.lot_sizes[OPTION] = 750
    m.entry_context = lambda symbol: {"underlying": SPOT, "side": 1, "session_cvd_share_signed": 0.2}
    m.set_quote(OPTION, 10.0, datetime.now(UTC))
    order = asyncio.run(m.submit(OPTION, "BUY", 750))
    asyncio.run(m.submit(OPTION, "BUY", 750))                        # an add is not a new entry
    rows = repo._connection.execute("SELECT order_id, payload FROM entry_context").fetchall()
    assert len(rows) == 1 and rows[0][0] == order.order_id
    assert json.loads(rows[0][1])["session_cvd_share_signed"] == 0.2


def test_desk_fills_cross_the_quoted_spread(tmp_path):
    mp = MPEngine(str(tmp_path / "mp.sqlite3"), settings=MPSettings(enabled=True, slippage_bps=25))
    mp.last_quotes[OPTION] = (10.0, 9.8, 10.2)
    assert mp._fill_price(OPTION, "BUY", 10.0) == approx(10.2)
    assert mp._fill_price(OPTION, "SELL", 10.0) == approx(9.8)
    mp.last_quotes[OPTION] = (10.1, 9.8, 10.2)                       # quote from another tick
    assert mp._fill_price(OPTION, "BUY", 10.0) == approx(10.025)
    mp.last_quotes[OPTION] = (10.0, 2.0, 30.0)                       # junk quote: capped at 5%
    assert mp._fill_price(OPTION, "BUY", 10.0) == approx(10.5)
    assert mp.fill_basis == {"quoted_spread": 3, "last_price_bps": 1}


def test_ib_day_type_is_the_live_rule():
    assert ib_day_type(110, 95, 105, 100) == "neutral_day"
    assert ib_day_type(112, 100, 105, 100) == "trend_day_up"
    assert ib_day_type(103, 96, 105, 100) == "normal_variation_down"
    assert ib_day_type(None, 1, 2, 3) == "forming"


def test_a_fresh_session_is_stored_from_raw_ticks_with_flow_and_the_live_day_type(tmp_path):
    ticks = sqlite3.connect(tmp_path / "ticks.sqlite3")
    ticks.executescript(TICK_SCHEMA)
    ticks.execute("INSERT INTO tick_symbols(id, symbol) VALUES (1, ?)", (SPOT,))
    day = "2026-10-14"
    start = datetime(2026, 10, 14, 9, 15, tzinfo=IST)
    rows, cum = [], 1_000
    for minute in range(0, 360):
        moment = start + timedelta(minutes=minute)
        price = 800 + (minute // 60)                                   # one rupee higher every hour
        for k, at_ask in enumerate((True, True, False)):
            cum += 100
            rows.append((1, int((moment + timedelta(seconds=10 * k)).timestamp() * 1000), price + (0.1 if at_ask else -0.1),
                         cum, 100, price - 0.1, price + 0.1, 500, 500, None, None))
    ticks.executemany("""INSERT INTO ticks(symbol_id, ts_ms, ltp, cum_volume, last_qty, bid, ask, bid_qty, ask_qty, tbq, tsq)
                         VALUES (?,?,?,?,?,?,?,?,?,?,?)""", rows)
    ticks.commit()
    profile = build_session(ticks, None, SPOT, day)
    assert profile.source == "ladder" and profile.buy_volume > profile.sell_volume > 0
    assert profile.ib_day_type == "trend_day_up"
    out = sqlite3.connect(tmp_path / "history.sqlite3")
    out.execute("CREATE TABLE session_profiles (symbol TEXT, day TEXT, open REAL, high REAL, low REAL, close REAL, poc REAL, "
                "vah REAL, val REAL, ib_high REAL, ib_low REAL, volume INTEGER NOT NULL DEFAULT 0, buy_volume INTEGER NOT NULL DEFAULT 0, "
                "sell_volume INTEGER NOT NULL DEFAULT 0, cumulative_delta INTEGER NOT NULL DEFAULT 0, imbalance REAL, "
                "trades INTEGER NOT NULL DEFAULT 0, day_type TEXT, open_type TEXT, single_prints TEXT, value_migration TEXT, "
                "levels INTEGER NOT NULL DEFAULT 0, source TEXT NOT NULL, built_at TEXT NOT NULL, PRIMARY KEY (symbol, day))")
    ensure_tables(out)                                               # widens the old table
    save_sessions(out, [profile])
    stored = out.execute("SELECT source, buy_volume, ib_day_type FROM session_profiles").fetchone()
    assert stored[0] == "ladder" and stored[1] > 0 and stored[2] == "trend_day_up"


def test_a_session_the_ticks_only_partly_caught_keeps_the_minute_bars(tmp_path):
    ticks = sqlite3.connect(tmp_path / "ticks.sqlite3")
    ticks.executescript(TICK_SCHEMA)
    ticks.execute("INSERT INTO tick_symbols(id, symbol) VALUES (1, ?)", (SPOT,))
    start = datetime(2026, 10, 8, 9, 15, tzinfo=IST)
    rows, cum = [], 1_000
    for minute in range(3):                                          # the host froze at 09:18
        for k in range(3):
            cum += 100
            rows.append((1, int((start + timedelta(minutes=minute, seconds=10 * k)).timestamp() * 1000), 800.1,
                         cum, 100, 799.9, 800.1, 500, 500, None, None))
    ticks.executemany("""INSERT INTO ticks(symbol_id, ts_ms, ltp, cum_volume, last_qty, bid, ask, bid_qty, ask_qty, tbq, tsq)
                         VALUES (?,?,?,?,?,?,?,?,?,?,?)""", rows)
    history = sqlite3.connect(tmp_path / "history.sqlite3")
    history.execute("CREATE TABLE historical_candles (symbol TEXT, timeframe_seconds INTEGER, timestamp INTEGER, open REAL, "
                    "high REAL, low REAL, close REAL, volume INTEGER)")
    history.executemany("INSERT INTO historical_candles VALUES (?,?,?,?,?,?,?,?)",
                        [(SPOT, 60, int((start + timedelta(minutes=m)).timestamp()), 800, 801, 799, 800 + m / 100, 1000)
                         for m in range(370)])
    profile = build_session(ticks, history, SPOT, "2026-10-08")
    assert profile.source == "candles" and profile.buy_volume == 0
