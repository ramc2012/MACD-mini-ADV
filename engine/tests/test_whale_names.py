"""The whale name list: Layer A over every traded contract, with parameters.

On 29 Sep 2026 Layer A ran on three index futures only, and its top "large
print" (293,150 NIFTY contracts at 10:06:41) was the volume of a 64-second
feed gap arriving as one update. These pin the gap filter, the rupee floors
that keep a 1-share "lot" from making every clip a pattern, and the row a
name gets.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from macd_trader import whale
from macd_trader.models import Tick
from macd_trader.tick_store import TickStore

IST = ZoneInfo("Asia/Kolkata")
DAY = "2026-09-29"
STOCK = "NSE:HDFCBANK-EQ"
OPTION = "NSE:HDFCBANK26OCT720CE"


def at(hour, minute, second=0):
    return datetime(2026, 9, 29, hour, minute, second, tzinfo=IST)


def _heartbeat(store, start, end, skip=None):
    moment, cum = start, 0
    while moment < end:
        if not (skip and skip[0] <= moment < skip[1]):
            cum += 65
            store.add(Tick("NSE:NIFTY26OCTFUT", 22_800.0, cum, timestamp=moment,
                           bid=22_799.9, ask=22_800.0, bid_qty=100, ask_qty=100, last_qty=65))
        moment += timedelta(seconds=1)


def _stock(store, start, minutes, *, clip=100, iceberg=None, block=None, gap=None):
    """A print every 2 s; an optional repeated clip, a block, and a feed gap."""
    cum = 1_000_000
    moment = start
    index = 0
    while moment < start + timedelta(minutes=minutes):
        if gap and gap[0] <= moment < gap[1]:
            moment += timedelta(seconds=2)
            continue
        size = clip
        if iceberg and index % 7 == 0:
            size = iceberg
        if block and moment == block[0]:
            size = block[1]
        if gap and moment == gap[1]:
            size = clip * 32 + 250_000          # the volume traded while silent
        cum += size
        store.add(Tick(STOCK, 720.0, cum, timestamp=moment, bid=719.95, ask=720.0,
                       bid_qty=500, ask_qty=500, last_qty=clip))
        moment += timedelta(seconds=2)
        index += 1


@pytest.fixture()
def paths():
    with tempfile.TemporaryDirectory() as folder:
        yield os.path.join(folder, "ticks.sqlite3"), os.path.join(folder, "history.sqlite3")


def _events(ticks_path, lots=None):
    ticks = sqlite3.connect(ticks_path)
    try:
        gaps = whale.feed_gaps(ticks, DAY)
        return gaps, whale.scan(ticks, DAY, whale.traded_symbols(ticks, DAY), lots=lots, gaps=gaps)
    finally:
        ticks.close()


def test_a_feed_gap_catch_up_is_not_a_large_print(paths):
    ticks_path, _ = paths
    store = TickStore(ticks_path)
    gap = (at(10, 5, 38), at(10, 6, 42))
    _heartbeat(store, at(9, 15), at(11, 0), skip=gap)
    _stock(store, at(9, 15), 100, gap=gap, block=(at(10, 30), 250_000))
    store.flush_sync()
    gaps, events = _events(ticks_path)
    assert len(gaps) == 1 and gaps[0][1] - gaps[0][0] >= 60_000
    large = [event for event in events if event.symbol == STOCK and event.kind == "large_print"]
    # The block at 10:30 is real and far past the floor; the catch-up at 10:06:42 is not a print.
    assert [datetime.fromtimestamp(e.ts_ms / 1000, IST).strftime("%H:%M") for e in large] == ["10:30"]


def test_routine_clips_below_the_rupee_floor_are_not_patterns(paths):
    ticks_path, _ = paths
    store = TickStore(ticks_path)
    # A regular 600-share clip at Rs 720 is Rs 4.3 lakh, under the Rs 5 lakh child floor.
    _stock(store, at(9, 15), 60, iceberg=600)
    store.flush_sync()
    _gaps, events = _events(ticks_path)
    assert not [event for event in events if event.kind == "constant_child"]


def test_a_stock_iceberg_and_its_parameters(paths):
    ticks_path, history_path = paths
    store = TickStore(ticks_path)
    _stock(store, at(9, 15), 60, iceberg=1_500)          # Rs 10.8 lakh clips every 14 s
    store.flush_sync()
    _gaps, events = _events(ticks_path)
    children = [event for event in events if event.kind == "constant_child"]
    assert children and all(event.quantity == 1_500 for event in children)

    history = sqlite3.connect(history_path)
    whale.save_events(history, DAY, events)
    ticks = sqlite3.connect(ticks_path)
    rows = whale.names(history, ticks, DAY)
    row = next(item for item in rows if item["symbol"] == STOCK)
    assert row["kind"] == "cash" and row["underlying"] == "HDFCBANK"
    assert row["child"]["size"] == 1_500 and row["child"]["every_seconds"] == pytest.approx(14.0)
    assert row["child"]["notional"] == 1_080_000
    assert row["session_volume"] and row["patterns"][0].startswith("iceberg: 1500")


def test_option_symbols_are_described():
    assert whale.describe(OPTION) == {"kind": "option", "underlying": "HDFCBANK", "strike": 720.0, "right": "CE"}
    weekly = whale.describe("NSE:NIFTY26O0622800CE")
    assert (weekly["underlying"], weekly["strike"], weekly["right"]) == ("NIFTY", 22800.0, "CE")
    assert whale.describe("NSE:NIFTY26OCTFUT")["underlying"] == "NIFTY"
    assert whale.describe(STOCK)["kind"] == "cash"
