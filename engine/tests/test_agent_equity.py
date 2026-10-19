"""Equity curves: marks, costs, open trades, drawdown, and the index plan's minute exits."""
from __future__ import annotations

import sqlite3
from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest

from macd_trader import agent_equity as eq
from macd_trader import option_agent as oa

IST = ZoneInfo("Asia/Kolkata")


def test_a_held_trade_is_marked_each_close_and_pays_costs_at_exit():
    trade = eq._hold_trade("2026-09-21", "X", 100.0, 10.0, [0.2, -0.1, 0.5], "ce")
    eq.settle([trade], "2026-09-29")
    out = eq.curve([trade], "2026-09-29")
    by_day = {p["day"]: p for p in out["points"]}
    assert by_day["2026-09-22"]["equity"] == pytest.approx(200.0)       # +20% on ₹1,000, still open
    assert by_day["2026-09-23"]["equity"] == pytest.approx(-100.0)
    net = 500.0 - eq.cost(1000.0, 1500.0)
    assert by_day["2026-09-24"]["equity"] == pytest.approx(net, abs=0.01)
    assert out["stats"]["final"] == pytest.approx(net, abs=0.01) and out["stats"]["closed"] == 1
    assert out["stats"]["max_drawdown"] == pytest.approx(-300.0)        # +200 -> -100


def test_a_trade_whose_sessions_have_not_closed_stays_open_at_its_last_mark():
    trade = eq._hold_trade("2026-09-28", "X", 10.0, 100.0, [0.1, None, None], "pe")
    eq.settle([trade], "2026-09-29")
    assert trade["reason"] == "open"
    out = eq.curve([trade], "2026-09-29")
    assert out["points"][-1]["equity"] == pytest.approx(100.0) and out["stats"]["open"] == 1
    assert out["stats"]["costs"] == 0


def _bars(connection, symbol, day, minutes):
    base = int(datetime.combine(datetime.fromisoformat(day).date(), time(9, 15), IST).timestamp())
    connection.executemany("INSERT INTO historical_candles VALUES (?, 60, ?, ?, ?, ?, ?, 0, 'x', NULL, 'x')",
                           [(symbol, base + m * 60, o, h, l, c) for m, (o, h, l, c) in minutes])


def test_the_plan_exits_on_the_first_index_touch_at_that_minutes_option_price(tmp_path):
    db = sqlite3.connect(tmp_path / "h.sqlite3")
    db.execute("""CREATE TABLE historical_candles (symbol TEXT, timeframe_seconds INTEGER, timestamp INTEGER,
                  open REAL, high REAL, low REAL, close REAL, volume INTEGER, asset_type TEXT, expiry TEXT, downloaded_at TEXT)""")
    spot, option = "NSE:NIFTY50-INDEX", "NSE:NIFTY26OCT25000CE"
    # Entry day after 14:45 (minute 330+): nothing touches. Next session: target touched at minute 10.
    _bars(db, spot, "2026-09-21", [(m, (25000, 25010, 24990, 25000)) for m in range(330, 375)])
    _bars(db, option, "2026-09-21", [(m, (100, 100, 100, 100)) for m in range(330, 375)])
    _bars(db, spot, "2026-09-22", [(m, (25100, 25400 if m == 10 else 25150, 25050, 25120)) for m in range(0, 30)])
    _bars(db, option, "2026-09-22", [(m, (120, 120, 120, 130 + m)) for m in range(0, 30)])
    trade = {"day": "2026-09-21", "underlying": "NIFTY", "side": 1, "symbol": option, "entry_price": 100.0,
             "stop_spot": 24800.0, "target_spot": 25300.0}
    result = eq._plan_trade(db, trade, 75)
    assert (result["reason"], result["exit_day"], result["exit"]) == ("target", "2026-09-22", 140.0)
    # Both levels inside one minute counts as the stop.
    trade_both = {**trade, "stop_spot": 25060.0}
    assert eq._plan_trade(db, trade_both, 75)["reason"] == "stop"


def test_books_price_every_book_from_the_agents_runs(tmp_path):
    from test_option_agent import _db
    from macd_trader import index_trades as it
    history = _db(tmp_path / "historical.sqlite3")
    oa.bootstrap(history, "2026-08-17", "2026-09-25")
    oa.train(history)
    it.train(history)
    for day in ("2026-09-01", "2026-09-02", "2026-09-03"):
        oa.emit(history, day, source="manual")
    out = eq.books(history, "2026-09-01", {"NSE:NIFTY26OCT25000CE": 75})
    assert out["as_of"] == "2026-09-03"
    assert {"ce", "pe", "ce_all", "pe_all", "ce_sized", "pe_sized", "ce_all_sized"} <= set(out["books"])
    assert out["books"]["ce_sized"]["stats"]["max_deployed"] <= eq.BOOK_CAP + 1
    ce = out["books"]["ce"]
    assert ce["points"][0]["day"] == "2026-09-01" and ce["points"][-1]["day"] == "2026-09-03"
    # Three emissions of up to ten picks at ₹10,000, none past its third session yet.
    assert ce["stats"]["open"] == ce["stats"]["trades"] and ce["stats"]["max_deployed"] <= 30 * eq.NOTIONAL
    history.close()


def test_volatility_sizing_gives_quiet_contracts_more_rupees_than_wild_ones():
    calm = eq.daily_premium_vol(400.0, 25000.0, 25000.0, "2026-10-27", 1, "2026-09-29")     # ATM, 4 weeks out
    wild = eq.daily_premium_vol(8.0, 25000.0, 25600.0, "2026-10-06", 1, "2026-09-29")       # far OTM, 1 week out
    assert calm and wild and wild > calm
    assert eq.pick_notional(wild) < eq.pick_notional(calm) <= eq.PICK_MAX
    assert eq.pick_notional(None) == eq.RISK_PER_PICK / eq.FALLBACK_VOL
    assert eq.PICK_MIN <= eq.pick_notional(50.0)


def test_index_lots_risk_about_the_budget_at_the_stop():
    # ₹60 a unit to the stop, 75 a lot: ₹4,500 a lot -> 0 lots at a ₹4,000 budget; ₹20 a unit -> 2 lots.
    assert eq.index_lots(150.0, 90.0, 75, None) == 0
    assert eq.index_lots(150.0, 130.0, 75, None) == 2
    assert eq.index_lots(150.0, None, 75, 0.1) == int(eq.RISK_PER_INDEX_TRADE // (150 * 1.5 * 0.1 * 75))


def test_a_days_entries_are_scaled_to_the_capital_left_under_the_cap():
    open_trade = eq._hold_trade("2026-09-21", "X", 1000.0, 200.0, [0.0, 0.0, 0.0], "ce_sized")   # ₹2 L open
    assert eq._open_value([open_trade], "2026-09-22") == 200_000.0
    assert eq._open_value([open_trade], "2026-09-25") == 0.0
    assert eq._scale_to_cap([60_000.0, 60_000.0], 200_000.0, 300_000.0) == pytest.approx(100_000 / 120_000)
    assert eq._scale_to_cap([10_000.0], 0.0, 300_000.0) == 1.0
    assert eq._scale_to_cap([10_000.0], 310_000.0, 300_000.0) == 0.0


def test_lot_sizes_fall_back_to_the_brokers_cache_never_to_one_unit(tmp_path):
    import json as _json
    snapshot = tmp_path / "atm_contracts.json"
    (tmp_path / "fyers_lot_sizes.json").write_text(_json.dumps({"date": "2026-09-29", "lot_sizes": {"NSE:NIFTY26OCT25000CE": 65}}))
    assert eq.known_lots({}, str(snapshot)) == {"NSE:NIFTY26OCT25000CE": 65}          # session down: the cache
    assert eq.known_lots({"NSE:NIFTY26OCT25000CE": 75}, str(snapshot))["NSE:NIFTY26OCT25000CE"] == 75   # live wins
    assert eq.known_lots({}, str(tmp_path / "missing" / "x.json")) == {}
    assert eq.lot_by_root(eq.known_lots({}, str(snapshot))) == {"NIFTY": 65}
