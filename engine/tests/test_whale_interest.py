"""Whale interest (owner, 3 Nov 2026): a whale-sized minute makes the underlying interesting, and a lane signal on
an interesting underlying goes first, takes the full ticket and, on a full smart book, the weakest holding's slot."""
import asyncio
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from macd_trader import entry_scorer as es
from macd_trader import smart_lane
from macd_trader.config import Settings
from macd_trader.events import EventHub
from macd_trader.models import Candle, Signal
from macd_trader.smart_lane import SmartLane
from macd_trader.whale_interest import WhaleInterest, from_file, root_of, write_export

IST = ZoneInfo("Asia/Kolkata")
DAY = "2026-11-03"


def at(hour, minute):
    return int(datetime(2026, 11, 3, hour, minute, tzinfo=IST).timestamp())


def interest(tmp_path, symbols=("NSE:SBIN-EQ", "NSE:SBIN26NOV800CE", "NSE:SBIN26NOVFUT")):
    path = tmp_path / "history.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE whale_minute_baselines (symbol TEXT, as_of TEXT, sessions INTEGER, slot_median TEXT, "
                       "p995 REAL, turnover REAL, range_median REAL, typical_move_pct REAL, PRIMARY KEY (symbol, as_of))")
    slots = json.dumps({str(slot): 1000 for slot in range(26)})
    with connection:
        for symbol in symbols:
            connection.execute("INSERT INTO whale_minute_baselines VALUES (?, '2026-11-02', 10, ?, 0, 0, 0, 0.3)",
                               (symbol, slots))
            connection.execute("INSERT INTO whale_minute_baselines VALUES (?, ?, 10, ?, 0, 0, 0, 0.3)",
                               (symbol, DAY, json.dumps({"0": 1})))       # tonight's study: not read today
    connection.close()
    tracker = WhaleInterest()
    assert tracker.load(str(path), DAY) == len(symbols)
    assert tracker.as_of == "2026-11-02"
    return tracker


def bar(symbol, ts, volume, close):
    return Candle(symbol, ts, close, close, close, close, volume, True)


def test_the_root_of_every_leg():
    assert root_of("NSE:RELIANCE-EQ") == "RELIANCE"
    assert root_of("NSE:RELIANCE26NOV1500CE") == "RELIANCE"
    assert root_of("NSE:BAJAJ-AUTO26NOVFUT") == "BAJAJ-AUTO"
    assert root_of("NSE:NIFTY50-INDEX") is None


def test_cash_needs_30x_its_slot_median_and_an_option_60x(tmp_path):
    tracker = interest(tmp_path)
    assert tracker.on_minute_bar(bar("NSE:SBIN-EQ", at(10, 0), 29_000, 800.0)) is None
    cash = tracker.on_minute_bar(bar("NSE:SBIN-EQ", at(10, 1), 31_000, 800.0))
    assert cash["root"] == "SBIN" and cash["multiple"] == 31.0 and cash["strength"] == pytest.approx(1.03, abs=0.01)
    assert tracker.on_minute_bar(bar("NSE:SBIN26NOV800CE", at(10, 2), 59_000, 20.0)) is None
    assert tracker.on_minute_bar(bar("NSE:SBIN26NOV800CE", at(10, 3), 61_000, 20.0))["leg"] == "option"
    assert tracker.on_minute_bar(bar("NSE:SBIN-EQ", at(10, 4), 31_000, 1.0)) is None     # under the rupee floor
    assert tracker.on_minute_bar(bar("NSE:OTHER-EQ", at(10, 5), 10**7, 800.0)) is None   # no baseline


def test_a_reading_lasts_an_hour_and_the_strongest_leads(tmp_path):
    tracker = interest(tmp_path)
    tracker.on_minute_bar(bar("NSE:SBIN-EQ", at(10, 0), 31_000, 800.0))
    tracker.on_minute_bar(bar("NSE:SBIN26NOV800CE", at(10, 10), 120_000, 20.0))
    now = datetime.fromtimestamp(at(10, 30), UTC)
    live = tracker.reading("NSE:SBIN26NOV820PE", now)
    assert live["symbol"] == "NSE:SBIN26NOV800CE" and live["strength"] == 2.0 and live["readings"] == 2
    assert tracker.reading("NSE:SBIN-EQ", datetime.fromtimestamp(at(11, 11), UTC)) is None
    assert [r["root"] for r in tracker.live(now)] == ["SBIN"]


def test_a_minute_where_dozens_of_names_spike_together_is_a_feed_catch_up(tmp_path):
    names = [f"NSE:N{i}-EQ" for i in range(30)]
    tracker = interest(tmp_path, names)
    for name in names:
        tracker.on_minute_bar(bar(name, at(10, 0), 40_000, 800.0))
    assert tracker.reading("N1", datetime.fromtimestamp(at(10, 5), UTC)) is None
    assert tracker.status(datetime.fromtimestamp(at(10, 5), UTC))["burst_minutes_ignored"] == 1


def test_other_processes_read_the_export_for_the_hour_before_their_bar(tmp_path):
    tracker = interest(tmp_path)
    tracker.on_minute_bar(bar("NSE:SBIN-EQ", at(13, 50), 40_000, 800.0))
    path = tmp_path / "shadow" / "whale_interest.json"
    write_export(path, tracker.export_payload())
    assert from_file(path, "SBIN", datetime.fromtimestamp(at(14, 45), UTC))["multiple"] == 40.0
    assert from_file(path, "SBIN", datetime.fromtimestamp(at(15, 0), UTC)) is None


def test_the_macd_gate_puts_whale_names_first_at_full_size(tmp_path):
    from test_entry_scorer import feats
    settings = SimpleNamespace(macd_entry_filter=True, macd_entry_skip_fraction=0.3, macd_entry_rank_seconds=0.0,
                               macd_entry_min_size_fraction=0.25)
    gate = es.EntryGate(settings, str(tmp_path / "h.sqlite3"))
    gate.whale = lambda symbol: {"symbol": "NSE:WHALE-EQ", "leg": "cash", "multiple": 45.0, "strength": 1.5,
                                 "at": 0} if symbol.startswith("NSE:WHALE") else None
    sig = lambda s: SimpleNamespace(symbol=s, signal_id=s)
    strong, whale = sig("NSE:STRONG26NOV100CE"), sig("NSE:WHALE26NOV100CE")
    assert gate.admit(strong, feats(expiry="2026-11-24", crowd_same=12)) is True
    assert gate.admit(whale, feats(expiry="2026-11-24", crowd_same=0, macd_preview=-0.5)) is True   # weaker score
    assert whale.size_fraction == 1.0

    class Book:
        def __init__(self):
            self.positions, self.asked = {}, []

        async def on_signal(self, signal):
            self.asked.append(signal.symbol)
    book = Book()
    asyncio.run(gate._drain(book, book))
    assert book.asked == ["NSE:WHALE26NOV100CE", "NSE:STRONG26NOV100CE"]
    gate._write(gate._journal)
    stored = sqlite3.connect(tmp_path / "h.sqlite3").execute(
        "SELECT features FROM macd_entry_scores WHERE symbol = 'NSE:WHALE26NOV100CE'").fetchone()[0]
    assert json.loads(stored)["whale"]["multiple"] == 45.0


class NoOrders:
    name = "fyers"

    async def place_order(self, _order):
        raise AssertionError("paper lanes must never reach the broker's order API")


@pytest.mark.usefixtures("no_stock_t2")
def test_a_full_smart_book_gives_the_weakest_slot_to_a_whale_signal(tmp_path, monkeypatch):
    settings = Settings(execution_mode="paper", slippage_bps=0, database_path=str(tmp_path / "macd.sqlite3"))
    lane = SmartLane("smart_test", 100_000, settings, NoOrders(), EventHub(), str(tmp_path / "lane.sqlite3"), slots=2)
    symbols = ["NSE:AAA26NOV100CE", "NSE:BBB26NOV100CE", "NSE:CCC26NOV100CE", "NSE:WHALE26NOV100CE"]
    lane.set_tradable_contracts({s: 100 for s in symbols})
    lane.set_contract_context({s: {"option_type": "CE", "expiry": "2099-12-30", "volume": 0, "oi": 0} for s in symbols})
    lane.set_resolvers(macd_confirm=lambda *_a: True, dispersion=lambda: None,
                       whale=lambda s: {"symbol": "NSE:WHALE-EQ", "leg": "cash", "multiple": 40.0, "strength": 1.33,
                                        "at": 0, "age_minutes": 3.0} if s.startswith("NSE:WHALE") else None)
    monkeypatch.setattr(smart_lane, "score", lambda features: 0.5)
    t0 = datetime(2026, 10, 5, 4, 30, tzinfo=UTC)

    def offer(symbol, minutes):
        when = t0 + timedelta(minutes=minutes)
        lane.on_macd_signal(Signal(symbol, "BUY", "TEST", 10.0, 0.1, 0.0, 0.1, evaluated_candle_timestamp=minutes),
                            "CE", {}, now=when)
        lane.execution.set_quote(symbol, 10.0)
        asyncio.run(lane.step(when + timedelta(seconds=330)))

    offer(symbols[0], 0)
    offer(symbols[1], 10)
    lane.execution.set_quote(symbols[0], 12.0)          # AAA +20%
    lane.execution.set_quote(symbols[1], 8.0)           # BBB -20%: the weakest
    offer(symbols[2], 20)                               # no whale: the full book turns it away
    assert lane.counts["POSITION_LIMIT"] == 1 and symbols[2] not in lane.portfolio.positions
    offer(symbols[3], 30)
    assert set(lane.held_symbols()) == {symbols[0], symbols[3]}
    assert lane.execution.exit_reasons[symbols[1]] == smart_lane.WHALE_REPLACE_EXIT
    lane.flush_journal()
    taken = [r for r in lane.journal_rows() if r["symbol"] == symbols[3]][0]
    assert taken["decision"] == "TAKEN" and taken["detail"]["replaced"] == symbols[1]
    assert taken["detail"]["replaced_return_pct"] == -20.0 and taken["detail"]["whale"]["multiple"] == 40.0
