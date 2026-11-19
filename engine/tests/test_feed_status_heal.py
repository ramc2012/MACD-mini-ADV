"""19 Nov 2026: the header said "Fyers error" all day while ticks arrived at 80/s, and a morning login took ~3 minutes
to show "connected" because each contract's chain marks were read with a full-table scan."""
import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta

from macd_trader import engine as engine_module
from macd_trader.config import Settings
from macd_trader.engine import TradingEngine
from macd_trader.models import Tick


def _engine(tmp_path):
    return TradingEngine(Settings(
        symbols_csv="NSE:TEST-EQ", tick_capture_enabled=False,
        database_path=str(tmp_path / "book.sqlite3"),
        mp_database_path=str(tmp_path / "mp.sqlite3"),
        research_database_path=str(tmp_path / "history.sqlite3"),
        tick_database_path=str(tmp_path / "ticks.sqlite3"),
        contract_snapshot_path=str(tmp_path / "contracts.json"),
    ))


def test_a_timed_out_recovery_turns_green_again_once_ticks_flow(tmp_path, monkeypatch):
    monkeypatch.setattr(engine_module, "FEED_STEP_TIMEOUT", 0.01)
    monkeypatch.setattr(engine_module, "regular_session_open", lambda *a: True)
    engine = _engine(tmp_path)

    async def not_refreshed():
        return False

    async def restart_hangs():
        await asyncio.sleep(10)

    engine._preopen_refresh = not_refreshed
    engine.restart_stream = restart_hangs
    engine.status = "connected"
    engine.seconds_since_last_tick = lambda: 9999.0
    asyncio.run(engine._watchdog_sweep())
    assert engine.status == "error" and engine.error == "feed recovery failed: TimeoutError"
    # The SDK's own reconnect brings ticks back; nothing restarts the stream, yet the feed works.
    engine.seconds_since_last_tick = lambda: 0.2
    engine.restart_stream = lambda: (_ for _ in ()).throw(AssertionError("no restart needed"))
    asyncio.run(engine._watchdog_sweep())
    assert engine.status == "connected" and engine.error is None and engine.feed_alive()
    assert engine.last_feed_error["error"] == "feed recovery failed: TimeoutError"


def test_ticks_from_the_old_socket_do_not_hide_a_failed_login_or_an_expired_token(tmp_path):
    engine = _engine(tmp_path)
    engine.seconds_since_last_tick = lambda: 0.2
    engine.status, engine.error, engine._socket_error = "error", "Held contracts could not be restored", False
    assert not engine._heal_feed_status() and engine.status == "error"          # a connect_feed failure stays red
    engine._socket_error = True
    engine.token_expired = lambda: True
    assert not engine._heal_feed_status() and engine.status == "error"


def test_grok_market_frame_has_index_vwap_from_the_future_and_live_cash_breadth(tmp_path):
    engine = _engine(tmp_path)
    now = datetime.now(UTC)
    engine.latest_ticks = {
        "NSE:NIFTY50-INDEX": Tick("NSE:NIFTY50-INDEX", 25300.0, timestamp=now, prev_close=25250.0),
        "NSE:NIFTY26NOVFUT": Tick("NSE:NIFTY26NOVFUT", 25400.0, timestamp=now, avg_trade_price=25349.3),
        "NSE:NIFTY26DECFUT": Tick("NSE:NIFTY26DECFUT", 25500.0, timestamp=now - timedelta(minutes=5),
                                  avg_trade_price=25000.0),
        "NSE:NIFTYNXT5026NOVFUT": Tick("NSE:NIFTYNXT5026NOVFUT", 70000.0, timestamp=now, avg_trade_price=1.0),
        "NSE:SBIN-EQ": Tick("NSE:SBIN-EQ", 810.0, timestamp=now, prev_close=800.0, avg_trade_price=805.0),
        "NSE:TCS-EQ": Tick("NSE:TCS-EQ", 3900.0, timestamp=now, prev_close=4000.0, avg_trade_price=3950.0),
        "NSE:INFY-EQ": Tick("NSE:INFY-EQ", 1510.0, timestamp=now, prev_close=1500.0, avg_trade_price=1500.0),
    }
    frame = engine._smart_market_frame(now)
    assert frame["indices"]["NIFTY"]["fut_vs_vwap_pct"] == 0.2                # the near series, not NIFTYNXT50
    assert "vs_vwap_pct" not in frame["indices"]["NIFTY"]
    assert frame["cash_breadth"] == {"stocks": 3, "up": 2, "down": 1, "above_vwap": 2, "below_vwap": 1,
                                     "median_move_pct": 0.67}


def test_chain_marks_are_read_by_contract_through_an_index(tmp_path):
    from macd_trader import whale
    path = tmp_path / "history.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(whale.SCHEMA)
    plan = connection.execute("EXPLAIN QUERY PLAN SELECT ts, ltp, volume FROM chain_snapshots "
                              "WHERE symbol = ? AND ltp > 0 ORDER BY ts DESC LIMIT 10", ("X",)).fetchall()
    assert any("idx_chain_symbol_ts" in str(row) for row in plan)
