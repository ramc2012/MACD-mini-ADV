"""ATM heat map: period-to-date changes (current hour, 4H block, day, week, month)."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from pytest import approx

from macd_trader import atm_heatmap as hm

IST = ZoneInfo("Asia/Kolkata")


def at(day, hour, minute):
    return datetime(2026, 10, day, hour, minute, tzinfo=IST)


def test_outside_the_session_now_is_the_last_close():
    assert hm.session_anchor(at(17, 15, 0)) == at(16, 15, 30)          # Saturday
    assert hm.session_anchor(at(19, 8, 0)) == at(16, 15, 30)           # Monday before the open
    assert hm.session_anchor(at(16, 11, 0)) == at(16, 11, 0)


def test_periods_start_at_the_current_hour_block_day_week_and_month():
    now = at(14, 10, 40)                                                # Wednesday 10:40
    assert hm.period_start(now, "1H") == at(14, 10, 15)                 # hours aligned to the 09:15 open
    assert hm.period_start(now, "4H") == at(14, 9, 15)
    assert hm.period_start(at(14, 14, 0), "4H") == at(14, 13, 15)
    assert hm.period_start(now, "1D") == at(14, 9, 15)
    assert hm.period_start(now, "1W") == at(12, 9, 15)                  # Monday
    assert hm.period_start(now, "1M") == at(1, 9, 15)                   # 1 Oct, a Thursday session
    close = at(16, 15, 30)                                              # at the close: the block that just ended
    assert hm.period_start(close, "1H") == at(16, 15, 15)
    assert hm.period_start(close, "4H") == at(16, 13, 15)


def test_build_measures_from_the_period_s_first_trade_and_flags_late_starts(tmp_path):
    path = tmp_path / "h.sqlite3"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE historical_candles (symbol TEXT, timeframe_seconds INTEGER, timestamp INTEGER, open REAL, close REAL)")
    bars = {
        "NSE:SBIN-EQ": [(at(1, 9, 15), 880.0), (at(12, 9, 15), 890.0), (at(16, 9, 15), 895.0), (at(16, 13, 15), 897.0),
                        (at(16, 15, 15), 899.0), (at(16, 15, 29), 900.0)],
        "NSE:SBIN26OCT900CE": [(at(16, 9, 15), 16.0), (at(16, 15, 15), 21.0), (at(16, 15, 29), 22.0)],
        "NSE:SBIN26OCT900PE": [(at(14, 11, 0), 30.0), (at(16, 15, 29), 18.0)],   # first traded Wednesday mid-morning
    }
    db.executemany("INSERT INTO historical_candles VALUES (?,60,?,?,?)",
                   [(sym, int(t.timestamp()), price, price) for sym, rows in bars.items() for t, price in rows])
    db.commit(); db.close()
    contracts = [{"underlying": "SBIN", "spot_symbol": "NSE:SBIN-EQ", "option_type": side, "symbol": f"NSE:SBIN26OCT900{side}",
                  "strike": 900.0, "expiry": "2026-10-27"} for side in ("CE", "PE")]
    out = hm.build(str(path), contracts, {}, now=at(17, 12, 0))         # Saturday: Friday's periods
    row = out["rows"][0]
    assert out["references"]["1D"] == at(16, 9, 15).isoformat() and out["basis"] == "period to date"
    assert row["ce"]["change"]["1D"] == approx((22 / 16 - 1) * 100, abs=0.01) and row["ce"]["change"]["1H"] == approx((22 / 21 - 1) * 100, abs=0.01)
    assert set(row["ce"]["partial"]) == {"4H", "1W", "1M"}   # first trades: Friday, and 15:15 in the 13:15 block
    assert row["pe"]["change"]["1W"] == approx((18 / 30 - 1) * 100, abs=0.01)
    assert "1W" in row["pe"]["partial"] and "1M" in row["pe"]["partial"]    # its first trade came mid-week
    assert row["spot_change"]["1M"] == approx((900 / 880 - 1) * 100, abs=0.01) and row["spot_change"]["1W"] == approx((900 / 890 - 1) * 100, abs=0.01)


def test_sector_indices_appear_as_spot_only_rows_when_they_have_data(tmp_path):
    path = tmp_path / "h.sqlite3"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE historical_candles (symbol TEXT, timeframe_seconds INTEGER, timestamp INTEGER, open REAL, close REAL)")
    db.executemany("INSERT INTO historical_candles VALUES (?,60,?,?,?)",
                   [("NSE:NIFTYIT-INDEX", int(at(16, 9, 15).timestamp()), 35000.0, 35000.0),
                    ("NSE:NIFTYIT-INDEX", int(at(16, 15, 29).timestamp()), 35700.0, 35700.0)])
    db.commit(); db.close()
    out = hm.build(str(path), [], {}, now=at(17, 12, 0), context_spots=["NSE:NIFTYIT-INDEX", "NSE:NIFTYMEDIA-INDEX"])
    assert [r["underlying"] for r in out["rows"]] == ["NIFTY IT"]               # no data, no tile
    row = out["rows"][0]
    assert row["kind"] == "sector" and "ce" not in row and row["spot_change"]["1D"] == approx(2.0, abs=0.01)


def test_only_context_indices_the_broker_prices_are_subscribed(tmp_path):
    import asyncio
    from types import SimpleNamespace
    from macd_trader.config import Settings
    from macd_trader.engine import TradingEngine
    engine = TradingEngine(Settings(symbols_csv="NSE:SBIN-EQ", tick_capture_enabled=False,
                                    context_indices_csv="NSE:NIFTYIT-INDEX,NSE:NOSUCH-INDEX",
                                    database_path=str(tmp_path / "b.sqlite3"), mp_database_path=str(tmp_path / "mp.sqlite3"),
                                    research_database_path=str(tmp_path / "h.sqlite3"), tick_database_path=str(tmp_path / "t.sqlite3"),
                                    contract_snapshot_path=str(tmp_path / "c.json")))

    async def quotes(symbols):
        return {"NSE:NIFTYIT-INDEX": SimpleNamespace(ltp=35000.0)}
    engine.broker = SimpleNamespace(quotes=quotes)
    assert asyncio.run(engine._validated_context_indices()) == ["NSE:NIFTYIT-INDEX"]
    assert engine.context_indices_status["rejected"] == ["NSE:NOSUCH-INDEX"]

    async def broken(symbols):
        raise RuntimeError("token expired")
    engine.broker = SimpleNamespace(quotes=broken)
    engine._context_indices_checked = None
    assert asyncio.run(engine._validated_context_indices()) == []               # unknown: subscribe nothing extra
    engine.repository.close()


def test_historical_contracts_pick_the_day_s_traded_atm_pair_of_the_nearest_live_expiry(tmp_path):
    path = tmp_path / "h.sqlite3"
    db = sqlite3.connect(path)
    db.execute("""CREATE TABLE historical_candles (symbol TEXT, timeframe_seconds INTEGER, timestamp INTEGER, open REAL, close REAL,
                  PRIMARY KEY(symbol, timeframe_seconds, timestamp))""")
    day = at(1, 12, 0)
    rows = [("NSE:SBIN-EQ", day, 903.0),
            ("NSE:SBIN26SEP900CE", at(1, 10, 0), 9.0),     # expired before the day: never picked
            ("NSE:SBIN26OCT900CE", day, 20.0), ("NSE:SBIN26OCT900PE", day, 17.0),
            ("NSE:SBIN26OCT905CE", day - timedelta(days=3), 15.0), ("NSE:SBIN26OCT905PE", day - timedelta(days=3), 19.0),  # no trade that day
            ("NSE:SBIN26NOV900CE", day, 35.0), ("NSE:SBIN26NOV900PE", day, 30.0)]
    db.executemany("INSERT INTO historical_candles VALUES (?,60,?,?,?)", [(s, int(t.timestamp()), p, p) for s, t, p in rows])
    db.commit(); db.close()
    picks = hm.historical_contracts(str(path), day.date())
    assert sorted(c["symbol"] for c in picks) == ["NSE:SBIN26OCT900CE", "NSE:SBIN26OCT900PE"]
    assert {c["underlying"] for c in picks} == {"SBIN"} and picks[0]["spot_symbol"] == "NSE:SBIN-EQ"


def test_daily_comparisons_are_instrument_specific_and_use_the_prior_session(tmp_path):
    path = tmp_path / "h.sqlite3"
    spot, call, put = "NSE:SBIN-EQ", "NSE:SBIN26OCT900CE", "NSE:SBIN26OCT900PE"
    bars = [
        (spot, at(15, 15, 29), 870, 880), (spot, at(16, 9, 15), 900, 905),
        (spot, at(16, 15, 29), 919, 920),
        (call, at(15, 15, 29), 25, 28), (call, at(16, 9, 20), 30, 34),
        (call, at(16, 15, 29), 39, 40),
        (put, at(14, 15, 29), 55, 50), (put, at(16, 9, 15), 25, 22),
        (put, at(16, 15, 29), 12, 10),
    ]
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE historical_candles (symbol TEXT, timeframe_seconds INTEGER, timestamp INTEGER, open REAL, close REAL)")
        db.executemany("INSERT INTO historical_candles VALUES (?,60,?,?,?)",
                       [(symbol, int(t.timestamp()), opened, closed) for symbol, t, opened, closed in bars])
    contracts = [{"underlying": "SBIN", "spot_symbol": spot, "option_type": side, "symbol": symbol,
                  "strike": 900.0, "expiry": "2026-10-27"} for side, symbol in (("CE", call), ("PE", put))]
    close_view = hm.build(str(path), contracts, {}, now=at(17, 12, 0))
    assert close_view["references"][hm.PREVIOUS_CLOSE] == at(15, 15, 30).isoformat()
    row = close_view["rows"][0]
    assert row["sector"] == "Banks"
    assert row["spot_comparison"] == {"1D": 900, hm.PREVIOUS_CLOSE: 880}
    assert row["ce"]["comparison"] == {"1D": 30, hm.PREVIOUS_CLOSE: 28}
    assert row["ce"]["change"]["1D"] == approx(33.33)
    assert row["ce"]["change"][hm.PREVIOUS_CLOSE] == approx(42.86)
    assert row["ce"]["partial"]["1D"] == at(16, 9, 20).isoformat(timespec="minutes")
    assert row["pe"]["change"][hm.PREVIOUS_CLOSE] is None
    assert row["pe"]["change"]["1D"] == -60

    live = hm.build(str(path), contracts, {call: 42}, now=at(16, 15, 30), previous_closes={call: 30})
    assert live["rows"][0]["ce"]["change"][hm.PREVIOUS_CLOSE] == 40

    new_session = hm.build(str(path), contracts, {}, now=at(19, 9, 30))
    assert new_session["rows"][0]["ce"]["change"][hm.PREVIOUS_CLOSE] is None
    assert new_session["rows"][0]["ce"]["price"] is None
