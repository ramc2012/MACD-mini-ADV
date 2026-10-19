"""Each instrument's own session (regime 2026-08: F&O to 15:40, cash to 15:15).

On 29 Sep 2026 one 09:15-15:30 clock dropped 107k F&O ticks after 15:30 and
let HDFCBANK's closing-auction match (9.5M shares at 15:29:23) into its profile
and CVD as one aggressive buy.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from macd_trader import sessions
from macd_trader.candles import CandleAggregator
from macd_trader.models import Tick
from macd_trader.orderflow import OrderFlowTracker
from macd_trader.tick_store import condense_ticks

IST = ZoneInfo("Asia/Kolkata")
DAY = "2026-09-29"


def at(hour, minute, second=0, day=29, month=9):
    return datetime(2026, month, day, hour, minute, second, tzinfo=IST)


def test_segments():
    assert sessions.segment("NSE:HDFCBANK-EQ") == "cash"
    assert sessions.segment("NSE:NIFTY26OCTFUT") == "fo"
    assert sessions.segment("NSE:NIFTY26O0622800CE") == "fo"
    assert sessions.segment("NSE:NIFTY50-INDEX") == "index"


def test_each_segment_closes_on_its_own_clock():
    assert sessions.in_session("NSE:NIFTY26OCTFUT", at(15, 39, 59))
    assert not sessions.in_session("NSE:NIFTY26OCTFUT", at(15, 40))
    assert sessions.in_session("NSE:HDFCBANK-EQ", at(15, 14, 59))
    assert not sessions.in_session("NSE:HDFCBANK-EQ", at(15, 15))
    assert sessions.in_session("NSE:NIFTY50-INDEX", at(15, 29, 59))
    assert not sessions.in_session("NSE:NIFTY50-INDEX", at(15, 30))
    assert sessions.in_closing_auction("NSE:HDFCBANK-EQ", at(15, 29, 23))
    assert not sessions.in_closing_auction("NSE:NIFTY26OCTFUT", at(15, 29, 23))
    # Before the regime: one 15:30 close, no auction.
    assert not sessions.in_session("NSE:NIFTY26JULFUT", at(15, 30, day=28, month=7))
    assert sessions.in_session("NSE:SBIN-EQ", at(15, 29, day=28, month=7))
    assert sessions.closing_auction("NSE:SBIN-EQ", "2026-07-28") is None
    assert sessions.market_close(DAY).strftime("%H:%M") == "15:40"


def test_fo_bars_run_to_1540_and_cash_bars_stop_at_1515():
    bars = CandleAggregator(60)
    future = Tick("NSE:NIFTY26OCTFUT", 22_800.0, 100, timestamp=at(15, 35))
    equity = Tick("NSE:HDFCBANK-EQ", 722.7, 100, timestamp=at(15, 20))
    assert bars.on_tick(future)[0] is not None
    assert bars.on_tick(equity) == (None, None)


def _cash_day():
    """Continuous prints to 15:14:59, then the closing-auction uncross."""
    ticks, cum = [], 1_000_000
    start = at(15, 0)
    for index in range(60):
        cum += 100
        price = 720.0 + (index % 5) * 0.05
        ticks.append(Tick("NSE:HDFCBANK-EQ", price, cum, timestamp=start + timedelta(seconds=15 * index),
                          bid=price - 0.05, ask=price, bid_qty=500, ask_qty=500, last_qty=100))
    # The match: 9.5M shares at one price, no aggressor.
    ticks.append(Tick("NSE:HDFCBANK-EQ", 722.7, cum + 9_516_452, timestamp=at(15, 29, 23),
                      bid=722.55, ask=722.7, bid_qty=10, ask_qty=10, last_qty=10))
    return ticks


def test_the_closing_auction_is_not_order_flow():
    ticks = _cash_day()
    tracker = OrderFlowTracker()
    for tick in ticks:
        if sessions.in_session(tick.symbol, tick.timestamp):
            tracker.on_tick(tick)
    live = tracker.states["NSE:HDFCBANK-EQ"]

    start, end = sessions.session_bounds("NSE:HDFCBANK-EQ", DAY)
    rows = [(1, int(t.timestamp.timestamp() * 1000), t.ltp, t.volume, t.last_qty, t.bid, t.ask,
             t.bid_qty, t.ask_qty, None, None) for t in ticks]
    minute, _ladder, _raw = condense_ticks(rows, {1: (start * 1000, end * 1000)})
    volume = sum(bucket["volume"] for bucket in minute.values())

    assert volume == 59 * 100                      # continuous prints only
    assert live.total_volume == volume
    assert (live.buy_volume, live.sell_volume) == (
        sum(b["buy_volume"] for b in minute.values()), sum(b["sell_volume"] for b in minute.values()))


def test_the_desk_records_the_auction_match_apart_from_flow():
    from macd_trader.mp_engine import MPEngine

    desk = MPEngine(":memory:")
    ticks = _cash_day()
    for tick in ticks[:-1]:
        desk.flow.on_tick(tick)
    desk._note_closing_auction(ticks[-1], ticks[-1].timestamp)
    auction = desk.closing_auctions["NSE:HDFCBANK-EQ"]
    assert auction["price"] == 722.7 and auction["volume"] == 9_516_452
