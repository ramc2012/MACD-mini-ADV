"""Options lab and the spread mark (owner, 18 Nov 2026): IV scanner, strategy-builder chain, expiry exposure,
spread-aware second mark."""
from __future__ import annotations

import sqlite3
from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from macd_trader import expiry_exposure, iv_scanner, spread_mark, strategy_builder
from macd_trader.option_greeks import price as bs_price

IST = ZoneInfo("Asia/Kolkata")


def _ts(day: str, hh: int, mm: int) -> int:
    return int(datetime.fromisoformat(f"{day}T{hh:02d}:{mm:02d}:00").replace(tzinfo=IST).timestamp())


# ------------------------------------------------------------------ spread mark

def _ticks_db(path, rows):
    db = sqlite3.connect(path)
    db.executescript("""CREATE TABLE tick_symbols (id INTEGER PRIMARY KEY, symbol TEXT UNIQUE);
        CREATE TABLE tick_minute_flow (symbol_id INTEGER, minute_ts INTEGER, close REAL, avg_spread REAL,
                                       PRIMARY KEY (symbol_id, minute_ts));""")
    db.execute("INSERT INTO tick_symbols VALUES (1, 'NSE:ABC26NOV100CE')")
    db.executemany("INSERT INTO tick_minute_flow VALUES (1, ?, ?, ?)", rows)
    db.commit()
    db.close()


def test_a_fill_at_last_price_pays_half_the_quoted_spread_and_a_fill_at_the_quote_pays_nothing_more(tmp_path):
    entry, exit_ = _ts("2026-11-10", 10, 0), _ts("2026-11-10", 11, 0)
    _ticks_db(tmp_path / "t.sqlite3", [(entry, 10.0, 0.4), (exit_, 12.0, 0.6)])
    book = spread_mark.SpreadBook(str(tmp_path / "t.sqlite3"))
    base = {"symbol": "NSE:ABC26NOV100CE", "quantity": 100, "side": "LONG", "pnl": 200.0, "day": "2026-11-10",
            "entry_time": datetime.fromtimestamp(entry + 20, IST).isoformat(), "exit_time": datetime.fromtimestamp(exit_ + 5, IST).isoformat()}
    at_ltp = spread_mark.mark_trades([{**base, "entry_price": 10.0, "exit_price": 12.0}], book)
    at_quote = spread_mark.mark_trades([{**base, "entry_price": 10.2, "exit_price": 11.7, "pnl": 150.0}], book)
    book.close()
    s = spread_mark.summarize(at_ltp)
    assert s["extra"] == pytest.approx(0.2 * 100 + 0.3 * 100)        # half spreads, both legs
    assert s["net_at_quote"] == pytest.approx(200 - 50)
    assert s["coverage_pct"] == 100.0
    assert spread_mark.summarize(at_quote)["extra"] == pytest.approx(0.0)  # already crossed the quote


def test_a_fill_without_a_stored_quote_is_uncovered_and_not_charged(tmp_path):
    _ticks_db(tmp_path / "t.sqlite3", [(_ts("2026-11-10", 10, 0), 10.0, 0.4)])
    book = spread_mark.SpreadBook(str(tmp_path / "t.sqlite3"))
    rows = spread_mark.mark_trades([{"symbol": "NSE:ZZZ26NOV1CE", "quantity": 10, "pnl": 5.0, "day": "2026-11-10",
                                     "entry_time": "2026-11-10T04:30:00+00:00", "entry_price": 1.0,
                                     "exit_time": "2026-11-10T05:30:00+00:00", "exit_price": 1.5}], book)
    book.close()
    s = spread_mark.summarize(rows)
    assert s["covered_legs"] == 0 and s["extra"] == 0 and s["net_at_quote"] == 5.0


def test_a_short_entry_sells_at_the_bid_side():
    # SELL at 9.9 with close 10 and spread 0.4: paid 0.1 of the 0.2 half spread -> 0.1 more per unit
    charged, extra, pct = spread_mark.leg_cost(9.9, (10.0, 0.4), False, 10)
    assert charged == pytest.approx(1.0) and extra == pytest.approx(1.0) and pct == pytest.approx(4.0)


# ------------------------------------------------------------------ IV scanner

def _chain_rows(spot, expiries, vols, ts, strikes):
    rows = []
    for expiry, vol in zip(expiries, vols):
        years = iv_scanner._years(expiry, ts)
        for k in strikes:
            for right, call in (("CE", True), ("PE", False)):
                p = bs_price(spot, k, years, vol, call)
                rows.append((expiry, k, right, round(p, 2), round(p - 0.05, 2), round(p + 0.05, 2), spot, 1000))
    return rows


def test_atm_iv_is_recovered_and_interpolated_to_thirty_days():
    ts = _ts("2026-11-02", 15, 20)
    rows = _chain_rows(1000.0, ["2026-11-24", "2026-12-29"], [0.30, 0.20], ts, [980, 1000, 1020])
    point = iv_scanner.atm_point(rows, ts)
    assert point["strike"] == 1000 and point["tenor"] == "30d"
    # total variance interpolation lands between the two months' IVs
    assert 20.0 < point["iv"] < 30.0
    single = iv_scanner.atm_point([r for r in rows if r[0] == "2026-11-24"], ts)
    assert single["iv"] == pytest.approx(30.0, abs=0.6) and single["tenor"] == "22d"


def test_a_stale_next_month_is_ignored_and_expiry_day_is_never_used():
    ts = _ts("2026-11-18", 15, 20)
    rows = _chain_rows(1000.0, ["2026-11-23"], [0.25], ts, [1000])
    # next month: no quote and no trade today -> unusable
    rows += [("2026-12-29", 1000, "CE", 90.0, None, None, 1000.0, 0), ("2026-12-29", 1000, "PE", 5.0, None, None, 1000.0, 0)]
    point = iv_scanner.atm_point(rows, ts)
    assert point["expiry"] == "2026-11-23" and point["iv"] == pytest.approx(25.0, abs=1.0)
    expiry_day = _ts("2026-11-23", 15, 0)
    assert iv_scanner.atm_point(_chain_rows(1000.0, ["2026-11-23"], [0.25], expiry_day, [1000]), expiry_day) is None


def test_rank_percentile_and_hv20():
    assert iv_scanner.rank(30.0, [10.0, 20.0, 25.0, 40.0]) == (pytest.approx(66.7, abs=0.1), 75.0)
    assert iv_scanner.rank(30.0, [10.0]) == (None, None)            # too short a history
    flat = [100.0 * (1.01 if i % 2 else 1.0) for i in range(25)]
    assert iv_scanner.hv20(flat) > 10
    assert iv_scanner.hv20([100.0] * 5) is None


def _research_db(path, days, spot=1000.0):
    db = sqlite3.connect(path)
    db.executescript("""CREATE TABLE chain_snapshots (ts INTEGER, underlying TEXT, expiry TEXT, strike REAL, option_type TEXT,
        symbol TEXT, ltp REAL, volume INTEGER, oi INTEGER, spot REAL, bid REAL, ask REAL, prev_oi INTEGER, fp REAL, vix REAL,
        PRIMARY KEY (ts, symbol));
        CREATE INDEX idx_chain_underlying_ts ON chain_snapshots(underlying, ts);
        CREATE TABLE chain_days (day TEXT, underlying TEXT, PRIMARY KEY (day, underlying));
        CREATE TABLE daily_closes (symbol TEXT, day TEXT, close REAL, PRIMARY KEY (symbol, day));""")
    for i, (day, vol) in enumerate(days):
        ts = _ts(day, 15, 20)
        for e, k, right, ltp, bid, ask, s, volume in _chain_rows(spot, ["2026-12-29"], [vol], ts, [980, 1000, 1020]):
            db.execute("INSERT INTO chain_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (ts, "ABC", e, k, right, f"NSE:ABC26DEC{int(k)}{right}", ltp, volume, 5000, s, bid, ask, 4000, s + 2, 14.0))
        db.execute("INSERT INTO chain_days VALUES (?, 'ABC')", (day,))
        db.execute("INSERT INTO daily_closes VALUES ('NSE:ABC-EQ', ?, ?)", (day, spot * (1.01 if i % 2 else 0.99)))
    db.commit()
    db.close()


def test_the_scanner_ranks_today_against_the_cached_history(tmp_path):
    days = [("2026-11-10", 0.20), ("2026-11-11", 0.22), ("2026-11-12", 0.24), ("2026-11-13", 0.26), ("2026-11-16", 0.28),
            ("2026-11-17", 0.30)]
    _research_db(tmp_path / "h.sqlite3", days)
    scanner = iv_scanner.IvScanner(str(tmp_path / "h.sqlite3"), tmp_path / "iv_history.json")
    out = scanner.scan(datetime(2026, 11, 17, 15, 30, tzinfo=IST))
    row = out["rows"][0]
    assert row["underlying"] == "ABC" and row["kind"] == "stock"
    assert row["iv_rank"] == 100.0 and row["iv_pctl"] == 100.0 and row["sessions"] == 6
    assert row["iv_chg_1d"] == pytest.approx(2.0, abs=0.3)
    assert (tmp_path / "iv_history.json").exists()
    # a second scanner reads the history file instead of recomputing the past sessions
    again = iv_scanner.IvScanner(str(tmp_path / "h.sqlite3"), tmp_path / "iv_history.json")
    again._load()
    assert sorted(again.history["ABC"]) == [d for d, _ in days[:-1]]


# ------------------------------------------------------------------ strategy builder chain

def test_builder_chain_reads_the_latest_snapshot_with_iv_and_the_delivery_calendar(tmp_path):
    _research_db(tmp_path / "h.sqlite3", [("2026-11-17", 0.25)])
    out = strategy_builder.chain(str(tmp_path / "h.sqlite3"), "ABC")
    assert out["expiry"] == "2026-12-29" and [r["strike"] for r in out["rows"]] == [980.0, 1000.0, 1020.0]
    atm = out["rows"][1]
    assert atm["ce"]["iv"] == pytest.approx(25.0, abs=0.6) and atm["pe"]["iv"] == pytest.approx(25.0, abs=0.6)
    assert [s["stage"] for s in out["delivery"]] == ["E-5", "E-4", "E-3", "E-2", "E-1"]
    with pytest.raises(KeyError):
        strategy_builder.chain(str(tmp_path / "h.sqlite3"), "NOPE")


def test_stored_spots_use_the_latest_chain_spot_then_the_daily_close(tmp_path):
    path = tmp_path / "h.sqlite3"
    _research_db(path, [("2026-11-16", 0.25), ("2026-11-17", 0.25)], spot=1000.0)
    db = sqlite3.connect(path)
    db.execute("INSERT INTO daily_closes VALUES ('NSE:XYZ-EQ', '2026-11-17', 512.5)")
    db.commit()
    db.close()
    spot = strategy_builder.stored_spots(str(path))
    assert spot("NSE:ABC-EQ") == pytest.approx(1000.0)      # the latest chain's spot, not the daily close
    assert spot("NSE:XYZ-EQ") == pytest.approx(512.5)       # no chain: the latest close
    assert spot("NSE:NONE-EQ") is None


def test_the_november_2026_stock_expiry_moves_to_monday_and_margin_starts_on_tuesday_17th():
    # 24 Nov 2026 is an exchange holiday: the series expires Monday 23 Nov
    stages = {s["stage"]: s["day"] for s in strategy_builder.delivery_schedule("2026-11-23")}
    assert stages == {"E-5": "2026-11-16", "E-4": "2026-11-17", "E-3": "2026-11-18", "E-2": "2026-11-19", "E-1": "2026-11-20"}


# ------------------------------------------------------------------ expiry exposure

def _execution(positions):
    return SimpleNamespace(portfolio=SimpleNamespace(positions={
        sym: SimpleNamespace(quantity=q, average_price=avg, last_price=avg) for sym, q, avg in positions}))


def test_itm_long_options_and_futures_are_liable_and_otm_longs_and_index_options_are_not():
    lanes = [("macd", _execution([("NSE:RELIANCE26NOV1300CE", 500, 60.0),     # ITM call (spot 1360)
                                  ("NSE:RELIANCE26NOV1400CE", 500, 5.0),      # OTM call
                                  ("NSE:NIFTY26NOV25000CE", 75, 250.0)])),   # index: cash settled, not listed
             ("spot_macd", _execution([("NSE:RELIANCE26NOVFUT", -500, 1360.0)]))]
    out = expiry_exposure.report(lanes, lambda s: 1360.0 if s == "NSE:RELIANCE-EQ" else None,
                                 now=datetime(2026, 11, 18, 10, 0, tzinfo=IST))
    rows = {r["symbol"]: r for r in out["rows"]}
    assert set(rows) == {"NSE:RELIANCE26NOV1300CE", "NSE:RELIANCE26NOV1400CE", "NSE:RELIANCE26NOVFUT"}
    itm, otm, fut = rows["NSE:RELIANCE26NOV1300CE"], rows["NSE:RELIANCE26NOV1400CE"], rows["NSE:RELIANCE26NOVFUT"]
    assert itm["expiry"] == "2026-11-23" and itm["sessions_to_expiry"] == 3 and itm["stage_pct"] == 25
    assert itm["liable"] and itm["margin_today"] == pytest.approx(500 * 1360 * 0.20 * 0.25)
    assert not otm["liable"] and otm["margin_today"] == 0
    assert fut["kind"] == "FUT" and fut["liable"] and fut["stage_pct"] == 25
    assert "2026-11-23" in out["schedules"]
    assert expiry_exposure.sessions_before(date(2026, 11, 23), date(2026, 11, 16)) == 5


def test_a_contract_the_feed_never_streamed_falls_back_to_the_stored_chain_quote(tmp_path):
    _ticks_db(tmp_path / "t.sqlite3", [])
    _research_db(tmp_path / "h.sqlite3", [("2026-11-17", 0.25)])
    at = _ts("2026-11-17", 15, 22)
    book = spread_mark.SpreadBook(str(tmp_path / "t.sqlite3"), str(tmp_path / "h.sqlite3"))
    quote = book.quote("NSE:ABC26DEC1000CE", at)
    assert quote is not None and quote[1] == pytest.approx(0.10, abs=1e-6)   # stored bid/ask are price -/+ 0.05
    assert book.quote("NSE:ABC26DEC1000CE", at + 3600) is None              # nothing within five minutes
    book.close()
