"""The auction page, the order-flow page and the ladder read one session one way.

On 29 Sep 2026 the three disagreed: the auction flow chart read only
condensed minutes (written five days later) and showed a cumulative delta of
0 beside an order-flow page with 302k prints; a footprint opened at 15:29
began at 15:05 because it was seeded from the tracker's last 400 prints; and
the profile was a 191-row tick ladder in one view and a 90-row sample in the
other. These pin the fixes: raw-tier flow equals the condensed record, a
footprint opened late starts at the open, and the profile rows are
ATR-scaled aggregates that never drop volume.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from macd_trader import auction_views, profile_scale
from macd_trader.market_profile import Profile
from macd_trader.models import Tick
from macd_trader.orderflow import Print
from macd_trader.replay import merge_seed_prints, session_prints
from macd_trader.tick_store import TickStore

IST = ZoneInfo("Asia/Kolkata")
DAY = "2026-08-20"
SYMBOL = "NSE:HDFCBANK-EQ"


def _session_ticks(symbol=SYMBOL, minutes=40):
    """Prints alternating at the ask and the bid, with book changes between."""
    start = datetime(2026, 8, 20, 9, 15, tzinfo=IST)
    ticks, cum = [], 0
    for index in range(minutes * 6):
        when = start + timedelta(seconds=10 * index)
        price = round(700 + (index % 37) * 0.05 - (index % 11) * 0.1, 2)
        at_ask = index % 3 != 0
        cum += 25
        ticks.append(Tick(symbol=symbol, ltp=price, volume=cum, timestamp=when,
                          bid=round(price - 0.05, 2) if at_ask else price,
                          ask=price if at_ask else round(price + 0.05, 2),
                          bid_qty=400 + index % 50, ask_qty=380 + index % 40, last_qty=25))
    return ticks


@pytest.fixture()
def store():
    with tempfile.TemporaryDirectory() as folder:
        tick_store = TickStore(os.path.join(folder, "ticks.sqlite3"), retention_days=2)
        for tick in _session_ticks():
            tick_store.add(tick)
        tick_store.flush_sync()
        yield tick_store


class TestAuctionFlow:
    def test_raw_day_reads_the_same_as_after_condensing(self, store):
        raw = auction_views.flow(store._path, SYMBOL, DAY)
        assert raw["source"] == "raw"
        assert raw["minutes"] == 40
        assert raw["cumulative_delta"] != 0

        store.condense_day(DAY)
        condensed = auction_views.flow(store._path, SYMBOL, DAY)
        assert condensed["source"] == "condensed"
        assert condensed["series"] == raw["series"]
        assert condensed["cumulative_delta"] == raw["cumulative_delta"]
        assert condensed["cumulative_ofi"] == raw["cumulative_ofi"]

    def test_raw_days_are_offered(self, store):
        assert auction_views.flow_days(store._path, SYMBOL) == [DAY]

    def test_a_day_with_nothing_recorded_says_so(self, store):
        empty = auction_views.flow(store._path, SYMBOL, "2026-08-21")
        assert empty["minutes"] == 0 and empty["source"] == "none"


class TestFootprintSeed:
    def test_session_prints_start_at_the_open(self, store):
        prints = session_prints(store._path, SYMBOL, DAY)
        first = datetime.fromtimestamp(prints[0].timestamp, IST)
        assert (first.hour, first.minute) == (9, 15)
        # Every recorded tick after the first carries size: all are prints.
        assert len(prints) >= 40 * 6 - 1

    def test_merge_prefers_recent_for_the_shared_second(self):
        stored = [Print(float(t), 1.0, 1, 1, "quote") for t in (10, 11, 12, 12)]
        recent = [Print(float(t), 2.0, 1, 1, "quote") for t in (12, 12, 12, 13)]
        merged = merge_seed_prints(stored, recent)
        assert [(p.timestamp, p.price) for p in merged] == [
            (10, 1.0), (11, 1.0), (12, 2.0), (12, 2.0), (12, 2.0), (13, 2.0)]

    def test_merge_keeps_the_store_when_recent_starts_later(self):
        stored = [Print(float(t), 1.0, 1, 1, "quote") for t in (10, 11)]
        recent = [Print(float(t), 2.0, 1, 1, "quote") for t in (15, 16)]
        assert [p.timestamp for p in merge_seed_prints(stored, recent)] == [10, 11, 15, 16]

    def test_merge_without_a_store_is_the_tracker_window(self):
        recent = [Print(5.0, 1.0, 1, 1, "quote")]
        assert merge_seed_prints([], recent) == recent


class TestAtrRows:
    def test_row_ticks_snap_up_the_ladder(self):
        # 12 rupees of ATR over 60 rows is 0.20: 2 ticks leaves 120 rows, 5 fits.
        assert profile_scale.row_ticks_for_range(12.0, 0.05) == 5
        assert profile_scale.row_ticks_for_range(200.0, 0.05) == 100
        assert profile_scale.row_ticks_for_range(0.5, 0.05) == 1
        assert profile_scale.row_ticks_for_range(0.0, 0.05) == 1

    def test_true_range_counts_the_gap(self):
        sessions = [(105.0, 100.0, 104.0), (112.0, 108.0, 110.0)]
        # Second session: high-low 4, but the gap from 104 makes it 8.
        assert profile_scale.average_true_range(sessions) == pytest.approx((5.0 + 8.0) / 2)

    def test_scale_from_stored_minute_bars_and_futures_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "history.sqlite3")
            connection = sqlite3.connect(path)
            connection.execute("""CREATE TABLE historical_candles (symbol TEXT, timeframe_seconds INTEGER,
                timestamp INTEGER, open REAL, high REAL, low REAL, close REAL, volume INTEGER)""")
            rows = []
            for offset in range(1, 11):
                day = datetime(2026, 9, 29, 9, 15, tzinfo=IST) - timedelta(days=offset)
                base = 22_700.0
                rows.append(("NSE:NIFTY50-INDEX", 60, int(day.timestamp()), base, base + 100, base - 100, base, 0))
                rows.append(("NSE:NIFTY50-INDEX", 60, int((day + timedelta(minutes=300)).timestamp()),
                             base, base + 50, base - 50, base, 0))
            connection.executemany("INSERT INTO historical_candles VALUES (?,?,?,?,?,?,?,?)", rows)
            connection.commit()
            connection.close()

            spot = profile_scale.profile_scale(path, "NSE:NIFTY50-INDEX", "2026-09-29", 0.05)
            assert spot["basis"] == "daily_atr" and spot["atr"] == pytest.approx(200.0)
            assert spot["row_size"] == 5.0          # 200 / 60 = 3.3 -> 100 ticks (40 rows)
            # A freshly rolled future with no bars of its own scales by its spot.
            future = profile_scale.profile_scale(path, "NSE:NIFTY26OCTFUT", "2026-09-29", 0.05)
            assert future["atr_symbol"] == "NSE:NIFTY50-INDEX" and future["row_size"] == 5.0
            assert profile_scale.cached_scale(path, "NSE:NIFTY26OCTFUT", "2026-09-29", 0.05) == future
            # An option with no history falls back to today's range, uncached.
            option = profile_scale.profile_scale(path, "NSE:NIFTY26O0622800CE", "2026-09-29", 0.05, 6.0)
            assert option["basis"] == "session_range" and option["row_ticks"] == 2
            assert profile_scale.cached_scale(path, "NSE:NIFTY26O0622800CE", "2026-09-29", 0.05) is None


class TestAggregatedProfile:
    @staticmethod
    def _build():
        profile = Profile(SYMBOL, DAY, 0.05)
        for tick in _session_ticks(minutes=120):
            local = tick.timestamp.astimezone(IST)
            profile.add(tick.ltp, 25, local.hour * 60 + local.minute)
        return profile

    def test_rows_sum_volume_and_union_letters_without_sampling(self):
        profile = self._build()
        tick_view = profile.snapshot(max_levels=None)
        rows = profile.snapshot(max_levels=90, row_size=0.5)
        assert rows["levels_sampled"] is False
        assert rows["row_size"] == 0.5
        assert sum(level["volume"] for level in rows["levels"]) == \
            sum(level["volume"] for level in tick_view["levels"])
        assert len(rows["levels"]) < len(tick_view["levels"])
        for row in rows["levels"]:
            members = [level for level in tick_view["levels"]
                       if row["price"] - 1e-9 <= level["price"] < row["price"] + 0.5 - 1e-9]
            assert members, row
            letters = set("".join(level["letters"] for level in members))
            assert set(row["letters"]) == letters and row["tpo"] == len(letters)

    def test_trading_levels_stay_tick_precise(self):
        profile = self._build()
        tick_view = profile.snapshot(max_levels=None)
        rows = profile.snapshot(row_size=0.5)
        for key in ("poc", "vah", "val", "ib_high", "ib_low", "high", "low", "open_type", "day_type"):
            assert rows[key] == tick_view[key], key

    def test_a_row_no_wider_than_the_tick_is_the_tick_ladder(self):
        profile = self._build()
        assert profile.snapshot(max_levels=None, row_size=0.05)["levels"] == \
            profile.snapshot(max_levels=None)["levels"]


def _fyers_like_ticks(symbol=SYMBOL):
    """Fyers SymbolUpdate shape: every update repeats the last trade's qty,
    quote-only updates leave cumulative volume unchanged, and one update can
    carry several trades' worth of volume."""
    start = datetime(2026, 8, 20, 9, 15, tzinfo=IST)
    ticks, cum, price = [], 1_000_000, 700.0
    for index in range(600):
        when = start + timedelta(seconds=index)
        quote_only = index % 3 == 2
        if not quote_only:
            price = round(price + (0.05 if index % 7 < 4 else -0.05), 2)
            cum += 25 * (1 + index % 5)          # 1-5 trades since the last update
        at_ask = index % 4 != 0
        ticks.append(Tick(symbol=symbol, ltp=price, volume=cum, timestamp=when,
                          bid=round(price - 0.05, 2) if at_ask else price,
                          ask=price if at_ask else round(price + 0.05, 2),
                          bid_qty=500 + index % 17, ask_qty=480 + index % 13, last_qty=25,
                          total_buy_qty=90_000 + index * 3, total_sell_qty=95_000 - index * 2))
    return ticks


def test_the_condenser_and_the_live_tracker_measure_the_same_flow():
    from macd_trader.orderflow import OrderFlowTracker
    from macd_trader.tick_store import condense_ticks

    ticks = _fyers_like_ticks()
    tracker = OrderFlowTracker()
    for tick in ticks:
        tracker.on_tick(tick)
    live = tracker.states[SYMBOL]

    rows = [(1, int(t.timestamp.timestamp() * 1000), t.ltp, t.volume, t.last_qty, t.bid, t.ask,
             t.bid_qty, t.ask_qty, t.total_buy_qty, t.total_sell_qty) for t in ticks]
    minute, _ladder, _raw = condense_ticks(rows)
    buy = sum(b["buy_volume"] for b in minute.values())
    sell = sum(b["sell_volume"] for b in minute.values())
    volume = sum(b["volume"] for b in minute.values())
    trades = sum(b["trades"] for b in minute.values())

    # Cumulative volume moved by exactly this much after the baseline tick.
    assert volume == ticks[-1].volume - ticks[0].volume
    assert trades == live.trades
    assert (buy, sell) == (live.buy_volume, live.sell_volume)
    assert buy - sell == live.cumulative_delta
