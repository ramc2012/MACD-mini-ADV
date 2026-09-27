"""Encoding, snapshot, history retention and health payload costs."""

import asyncio
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from macd_trader import engine as engine_module
from macd_trader.candle_store import prune_history
from macd_trader.config import Settings
from macd_trader.contracts import OptionContract
from macd_trader.engine import TradingEngine
from macd_trader.events import EventHub, Frame, _json_value_slow, frame_text, json_value
from macd_trader.models import Order, Tick


@dataclass
class _Nested:
    when: datetime
    rows: list = field(default_factory=list)
    by_strike: dict = field(default_factory=dict)


def test_fast_json_value_matches_the_recursive_walk():
    moment = datetime(2026, 9, 28, 4, 0, 0, 250000, tzinfo=UTC)
    value = {
        "order": Order("NSE:X", "BUY", 50, lots=1, lot_size=50),
        "nested": _Nested(moment, rows=[(1, 2.5), moment], by_strike={"24100": 1.5}),
        "plain": [1, "a", None, True],
    }
    assert json_value(value) == _json_value_slow(value)
    assert json_value(moment) == moment.isoformat()


def test_non_finite_numbers_and_numeric_keys_become_valid_json():
    value = {"iv": float("nan"), "gex": float("inf"), "ladder": {24100: 3}}
    assert json_value(value) == {"iv": None, "gex": None, "ladder": {"24100": 3}}


def test_frames_carry_their_text_once_and_it_matches_their_data():
    async def scenario():
        hub = EventHub()
        first, second = await hub.subscribe(), await hub.subscribe()
        hub.publish("tick", Tick("NSE:X", 101.5, volume=7, timestamp=datetime(2026, 9, 28, 4, 0, tzinfo=UTC)))
        a, b = first.get_nowait(), second.get_nowait()
        assert a is b, "one frame object is shared by every subscriber"
        assert a["data"]["ltp"] == 101.5 and a["data"]["timestamp"] == "2026-09-28T04:00:00+00:00"
        assert json.loads(frame_text(a)) == dict(a)
        assert a.text() is a.text()
    asyncio.run(scenario())


def test_snapshot_frame_text_is_encoded_from_plain_values():
    frame = Frame(9, "snapshot", {"broker": {"status": "connected"}, "rows": [{"iv": None}]})
    assert json.loads(frame.text()) == {"seq": 9, "type": "snapshot", "data": dict(frame)["data"]}


def _history(path, rows):
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE historical_candles (symbol TEXT NOT NULL, timeframe_seconds INTEGER NOT NULL,
          timestamp INTEGER NOT NULL, open REAL, high REAL, low REAL, close REAL, volume INTEGER,
          asset_type TEXT, expiry TEXT, downloaded_at TEXT, PRIMARY KEY(symbol, timeframe_seconds, timestamp));
        CREATE TABLE historical_indicators (symbol TEXT NOT NULL, timeframe_seconds INTEGER NOT NULL,
          timestamp INTEGER NOT NULL, macd REAL, PRIMARY KEY(symbol, timeframe_seconds, timestamp));
    """)
    for symbol, ts, expiry in rows:
        connection.execute("INSERT INTO historical_candles VALUES (?,60,?,1,1,1,1,0,'option',?,'x')", (symbol, ts, expiry))
        connection.execute("INSERT INTO historical_indicators VALUES (?,60,?,0)", (symbol, ts))
    connection.commit()
    connection.close()


def test_history_prune_drops_expired_contracts_and_old_bars_only(tmp_path):
    path = str(tmp_path / "historical.sqlite3")
    now = datetime(2026, 9, 28, 17, 0, tzinfo=UTC)
    recent = int((now - timedelta(days=2)).timestamp())
    ancient = int((now - timedelta(days=200)).timestamp())
    _history(path, [
        ("NSE:LONGEXPIRED26AUG", recent, "2026-08-20"),   # 39 days past expiry
        ("NSE:JUSTEXPIRED26SEP", recent, "2026-09-10"),   # 18 days past expiry: kept
        ("NSE:LIVE26OCT", recent, "2026-10-27"),
        ("NSE:SBIN-EQ", recent, None),
        ("NSE:SBIN-EQ", ancient, None),                   # older than retention
    ])
    result = prune_history(path, now=now, keep_days=120, expired_keep_days=30)
    assert result == {"candles": 2, "indicators": 2, "vacuumed": 0}
    connection = sqlite3.connect(path)
    kept = sorted(connection.execute("SELECT symbol, timestamp FROM historical_candles").fetchall())
    indicators = sorted(connection.execute("SELECT symbol, timestamp FROM historical_indicators").fetchall())
    connection.close()
    assert kept == indicators == sorted([
        ("NSE:JUSTEXPIRED26SEP", recent), ("NSE:LIVE26OCT", recent), ("NSE:SBIN-EQ", recent)])


def test_history_prune_can_be_disabled_and_tolerates_a_missing_file(tmp_path):
    path = str(tmp_path / "historical.sqlite3")
    now = datetime(2026, 9, 28, tzinfo=UTC)
    _history(path, [("NSE:OLD", int((now - timedelta(days=999)).timestamp()), "2024-01-01")])
    assert prune_history(path, now=now, keep_days=0, expired_keep_days=0)["candles"] == 0
    assert prune_history(str(tmp_path / "absent.sqlite3"), now=now, keep_days=1, expired_keep_days=1)["candles"] == 0


def _engine(tmp_path):
    settings = Settings(
        symbols_csv="NSE:NIFTY50-INDEX", feed_mode="simulation", execution_mode="paper",
        database_path=str(tmp_path / "e.sqlite3"), research_database_path=str(tmp_path / "r.sqlite3"),
        mp_database_path=str(tmp_path / "mp.sqlite3"), blast_database_path=str(tmp_path / "b.sqlite3"),
        tick_database_path=str(tmp_path / "t.sqlite3"), tick_capture_enabled=False,
    )
    return TradingEngine(settings)


def test_option_greeks_are_reused_while_marks_are_unchanged(tmp_path, monkeypatch):
    engine = _engine(tmp_path)
    calls = []
    monkeypatch.setattr(engine_module, "contract_gex", lambda **kw: calls.append(kw) or {"iv": 0.2, "gamma": 0.1, "gex": 5})
    contract = OptionContract("NIFTY", "NSE:NIFTY50-INDEX", "CE", "NSE:NIFTY26OCT24100CE", 24100, "2026-10-27", 80, oi=10, lot_size=75)
    engine.latest_ticks["NSE:NIFTY50-INDEX"] = Tick("NSE:NIFTY50-INDEX", 24100)
    engine.latest_ticks[contract.symbol] = Tick(contract.symbol, 85)
    assert engine.option_greeks(contract)["iv"] == 0.2
    assert engine.option_greeks(contract)["iv"] == 0.2
    assert len(calls) == 1
    engine.latest_ticks[contract.symbol] = Tick(contract.symbol, 86)  # moved, but within the window
    engine.option_greeks(contract)
    assert len(calls) == 1
    engine._greeks_cache[contract.symbol] = (0.0, *engine._greeks_cache[contract.symbol][1:])  # window over
    engine.option_greeks(contract)
    assert len(calls) == 2
    engine.repository.close()


def test_health_broker_status_omits_the_symbol_list(tmp_path):
    engine = _engine(tmp_path)
    engine.all_symbols = [f"NSE:S{i}" for i in range(1500)]
    slim = engine.broker_status(include_symbols=False)
    assert "symbols" not in slim and slim["symbol_count"] == 1500
    assert len(engine.broker_status()["symbols"]) == 1500  # the snapshot still carries them
    assert len(json.dumps(slim)) < 2_000
    engine.repository.close()
