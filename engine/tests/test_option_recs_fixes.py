"""15 Oct 2026 fixes: spread-crossing paper fills, Black-Scholes Greeks, blast shadow fields, OI in condensed minutes."""
from __future__ import annotations

import asyncio
import math
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from pytest import approx

from macd_trader.blast_engine import BlastEngine
from macd_trader.config import Settings
from macd_trader.events import EventHub
from macd_trader.execution import ExecutionManager
from macd_trader.models import Tick
from macd_trader.option_greeks import book_greeks, greeks, implied_vol, price
from macd_trader.portfolio import Portfolio
from macd_trader.repository import TradeRepository
from macd_trader.tick_store import condense_ticks

IST = ZoneInfo("Asia/Kolkata")
OPTION = "NSE:SBIN26OCT800CE"


class NoOrders:
    name = "test"

    async def place_order(self, _order):
        raise AssertionError("paper mode called the broker")


def _manager(tmp_path: Path, **settings):
    s = Settings(execution_mode="paper", symbols_csv=OPTION, tick_capture_enabled=False, **settings)
    m = ExecutionManager(s, NoOrders(), Portfolio(1_000_000), TradeRepository(str(tmp_path / "b.sqlite3")), EventHub())
    m.lot_sizes[OPTION] = 750
    return m


def _book(ticks: dict):
    return lambda symbol: ticks.get(symbol)


def test_paper_buy_fills_at_the_ask_and_sell_at_the_bid(tmp_path):
    m = _manager(tmp_path)
    m.quote_source = _book({OPTION: Tick(OPTION, 10.0, bid=9.8, ask=10.2)})
    m.set_quote(OPTION, 10.0, datetime.now(UTC))
    assert m._paper_price(OPTION, "BUY", 10.0) == approx(10.2)
    assert m._paper_price(OPTION, "SELL", 10.0) == approx(9.8)
    assert m.paper_fill_basis == {"quoted_spread": 2, "last_price_bps": 0}
    order = asyncio.run(m.submit(OPTION, "BUY", 750))
    assert order.status == "FILLED" and order.fill_price == approx(10.2)


def test_a_quote_from_another_tick_or_none_falls_back_to_bps(tmp_path):
    m = _manager(tmp_path, slippage_bps=5)
    assert m._paper_price(OPTION, "BUY", 10.0) == approx(10.005)              # no quote source (tests, desk)
    m.quote_source = _book({OPTION: Tick(OPTION, 10.1, bid=9.9, ask=10.3)})   # quote rode a different price
    assert m._paper_price(OPTION, "BUY", 10.0) == approx(10.005)
    m.quote_source = _book({OPTION: Tick(OPTION, 10.0, bid=0, ask=10.3)})     # one-sided
    assert m._paper_price(OPTION, "SELL", 10.0) == approx(9.995)
    assert m.paper_fill_basis["last_price_bps"] == 3


def test_a_junk_wide_quote_is_capped_and_the_switch_turns_it_off(tmp_path):
    m = _manager(tmp_path, paper_max_half_spread_pct=5.0)
    m.quote_source = _book({OPTION: Tick(OPTION, 10.0, bid=2.0, ask=30.0)})
    assert m._paper_price(OPTION, "BUY", 10.0) == approx(10.5)
    assert m._paper_price(OPTION, "SELL", 10.0) == approx(9.5)
    off = _manager(tmp_path, paper_cross_spread=False, slippage_bps=0)
    off.quote_source = m.quote_source
    assert off._paper_price(OPTION, "BUY", 10.0) == approx(10.0)


def test_black_scholes_round_trip_and_parity():
    spot, strike, years, vol = 800.0, 820.0, 12 / 365, 0.28
    call, put = price(spot, strike, years, vol, True), price(spot, strike, years, vol, False)
    assert call - put == approx(spot - strike * math.exp(-0.065 * years), abs=1e-9)
    assert implied_vol(call, spot, strike, years, True) == approx(vol, abs=1e-6)
    g_call, g_put = greeks(call, spot, strike, years, True), greeks(put, spot, strike, years, False)
    assert g_call["delta"] - g_put["delta"] == approx(1.0, abs=1e-6)
    assert g_call["theta_day"] < 0 and g_call["vega_point"] > 0
    assert implied_vol(0.0, spot, strike, years, True) is None
    assert implied_vol(1.0, spot, 700.0, years, True) is None                  # below intrinsic


def test_book_greeks_sum_delta_in_rupees_and_split_by_side():
    now = datetime(2026, 10, 14, 10, 0, tzinfo=IST)
    years = (datetime(2026, 10, 27, 15, 30, tzinfo=IST) - now).total_seconds() / (365 * 86400)
    ce = price(800, 800, years, 0.3, True); pe = price(800, 780, years, 0.3, False)
    positions = [("NSE:SBIN26OCT800CE", SimpleNamespace(quantity=750, last_price=ce, average_price=ce)),
                 ("NSE:SBIN26OCT780PE", SimpleNamespace(quantity=750, last_price=pe, average_price=pe)),
                 ("NSE:XYZ26OCT10CE", SimpleNamespace(quantity=100, last_price=1.0, average_price=1.0))]
    parsed = {"NSE:SBIN26OCT800CE": {"root": "SBIN", "strike": 800.0, "side": 1, "expiry": "2026-10-27"},
              "NSE:SBIN26OCT780PE": {"root": "SBIN", "strike": 780.0, "side": -1, "expiry": "2026-10-27"}}
    out = book_greeks(positions, 1_000_000, lambda s: (800.0, parsed[s]) if s in parsed else None, now)
    d_ce = greeks(ce, 800, 800, years, True)["delta"] * 750 * 800
    d_pe = greeks(pe, 800, 780, years, False)["delta"] * 750 * 800
    assert out["positions"] == 3 and out["priced"] == 2 and out["unpriced"] == ["NSE:XYZ26OCT10CE"]
    assert out["net_delta"] == approx(d_ce + d_pe) and out["gross_delta"] == approx(abs(d_ce) + abs(d_pe))
    assert out["calls"]["n"] == 1 and out["puts"]["delta"] == approx(d_pe)
    assert out["one_pct_down"] == approx(-0.01 * (d_ce + d_pe))
    assert out["net_delta_pct_equity"] == approx(100 * (d_ce + d_pe) / 1_000_000)


def test_blast_shadow_fields_measure_greeks_rv_and_spread(tmp_path):
    history = tmp_path / "h.sqlite3"
    db = sqlite3.connect(history)
    db.execute("CREATE TABLE historical_candles (symbol TEXT, timeframe_seconds INTEGER, timestamp INTEGER, close REAL)")
    today = datetime.now(IST).date()
    rows, d, k = [], today - timedelta(days=40), 0
    while d < today:
        if d.weekday() < 5:
            stamp = int(datetime(d.year, d.month, d.day, 15, 29, tzinfo=IST).timestamp())
            rows.append(("NSE:SBIN-EQ", 60, stamp, 800 * (1.01 if k % 2 else 0.99)))
            k += 1
        d += timedelta(days=1)
    db.executemany("INSERT INTO historical_candles VALUES (?,?,?,?)", rows); db.commit(); db.close()
    settings = Settings(symbols_csv=OPTION, tick_capture_enabled=False, blast_database_path=str(tmp_path / "blast.sqlite3"))
    blast = BlastEngine(settings.blast_database_path, settings, NoOrders(), EventHub())
    blast.history_path = str(history)
    blast.quote_source = _book({OPTION: Tick(OPTION, 12.0, bid=11.8, ask=12.2)})
    expiry = (today + timedelta(days=12)).isoformat()
    out = blast.shadow_features(OPTION, "CE", 12.0, 790.0, {"strike": 800.0, "expiry": expiry}, "NSE:SBIN-EQ")
    assert out["dte"] == 12 and 0 < out["delta"] < 0.5 and out["iv"] > 0
    assert out["rv20"] > 0.2 and out["iv_rv"] == approx(out["iv"] / out["rv20"], rel=1e-3)
    assert out["spread_pct"] == approx(0.4 / 12.0 * 100, abs=1e-4)
    # a broken input is journalled as an error, never raised into the screen
    assert "error" in blast.shadow_features(OPTION, "CE", 12.0, 790.0, {"strike": "x", "expiry": expiry}, None)


def test_condensed_minutes_keep_the_last_open_interest():
    base = 1_791_000_000_000
    rows = [(1, base, 10.0, 100, 10, 9.9, 10.1, 5, 5, 50, 50, 1200),
            (1, base + 20_000, 10.2, 150, 50, 10.1, 10.3, 5, 5, 50, 50, None),
            (1, base + 40_000, 10.1, 160, 10, 10.0, 10.2, 5, 5, 50, 50, 1350)]
    minute, _ladder, raw = condense_ticks(rows)
    assert raw == 3 and [b["oi"] for b in minute.values()] == [1350]
    legacy, _l, _r = condense_ticks([row[:11] for row in rows])           # older callers send 11 fields
    assert [b["oi"] for b in legacy.values()] == [None]
