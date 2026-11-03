"""Every paper book is scored the same way: trades to flat, weeks, exits by reason and hold, drawdown, book full."""
import json
import sqlite3

import pytest

from macd_trader.scoreboard import hold_bucket, max_drawdown, mp_book, score_book, shadow_book, week_of


def slice_(pid, entry, exit_, pnl, reason="HARD_STOP", partial=False):
    return {"position_id": pid, "entry_time": entry, "exit_time": exit_, "pnl": pnl, "exit_reason": reason, "partial": partial}


def test_trades_run_to_flat_and_weeks_are_ist_mondays():
    slices = [
        slice_("a", "2026-10-19T04:00:00+00:00", "2026-10-19T04:30:00+00:00", 300.0, "STAGED_PROFIT_EXIT_1", partial=True),
        slice_("a", "2026-10-19T04:00:00+00:00", "2026-10-22T08:00:00+00:00", 700.0, "TRAILING_STOP_25_PCT"),
        slice_("b", "2026-10-26T04:00:00+00:00", "2026-10-26T04:20:00+00:00", -400.0),
        slice_("c", "2026-10-27T04:00:00+00:00", "2026-10-27T05:00:00+00:00", -100.0, partial=True),   # still open
    ]
    board = score_book("x", "X", slices=slices, curve=[100_000, 101_000, 99_500, 100_500], capital=100_000,
                       now={"equity": 100_500, "cash": 50_000, "open": 1})
    assert board["closed"]["trades"] == 2 and board["closed"]["wins"] == 1
    assert board["closed"]["profit_factor"] == pytest.approx(1000 / 400)
    assert board["weekly"] == [{"week": "2026-10-19", "net": 1000.0}, {"week": "2026-10-26", "net": -500.0}]
    assert board["best_week"]["week"] == "2026-10-19" and board["net_without_best_week"] == -500.0
    assert board["max_drawdown"] == 1500 and board["return_pct"] == pytest.approx(0.5)
    exits = {(row["reason"], row["hold"]): row["net"] for row in board["exits"]}
    assert exits[("STAGED_PROFIT_EXIT_1", "<1h")] == 300.0 and exits[("TRAILING_STOP_25_PCT", ">2d")] == 700.0
    assert board["book_full"] is None and board["since"] == "2026-10-19T04:00:00+00:00"
    assert board["curve"] == []                       # bare values: drawdown only, nothing to chart


def test_book_full_from_slots_or_ticket():
    now = {"equity": 100_000, "cash": 5_000, "open": 3}
    assert score_book("x", "X", slices=[], curve=[], capital=100_000, now=now, slots=3)["book_full"] is True
    assert score_book("x", "X", slices=[], curve=[], capital=100_000, now=now, ticket=100_000)["book_full"] is True
    assert score_book("x", "X", slices=[], curve=[], capital=100_000, now={**now, "cash": 200_000, "open": 1},
                      ticket=100_000, slots=3)["book_full"] is False


def test_a_stamped_curve_is_returned_thinned():
    points = [(f"2026-10-{1 + i // 100:02d}T04:{i % 60:02d}:00+00:00", 100_000.0 + i) for i in range(1000)]
    board = score_book("x", "X", slices=[], curve=points, capital=100_000, now={"equity": 101_000, "cash": 0, "open": 0})
    assert len(board["curve"]) == 400 and board["curve"][-1] == {"at": points[-1][0], "equity": 100_999.0}
    assert board["max_drawdown"] == 0


def test_helpers():
    assert hold_bucket("2026-10-19T10:00:00+05:30", "2026-10-20T11:00:00+05:30") == "1-2d"
    assert hold_bucket(None, "2026-10-20T11:00:00+05:30") == "?"
    assert week_of("2026-11-01T20:00:00+00:00") == "2026-11-02"       # Monday 02:30 IST
    assert max_drawdown([90, 120, 60, 130], 100) == (60, 0.5)


def test_shadow_and_desk_books_read_their_own_schemas(tmp_path):
    shadow = tmp_path / "vanguard.sqlite3"
    c = sqlite3.connect(shadow)
    c.executescript("""
      CREATE TABLE account (id INTEGER, initial_paise INTEGER, cash_paise INTEGER, policy TEXT);
      CREATE TABLE positions (id TEXT, quantity INTEGER, entry REAL, opened_at TEXT, last_bid REAL, closed_at TEXT,
                              exit_reason TEXT, pnl_paise INTEGER);
      CREATE TABLE equity (at TEXT, equity_paise INTEGER);""")
    c.execute("INSERT INTO account VALUES (1, 500000000, 400000000, ?)", (json.dumps({"max_positions": 60}),))
    c.execute("INSERT INTO positions VALUES ('p1', 100, 50.0, '2026-10-28T10:00:00+05:30', NULL, '2026-10-28T11:00:00+05:30', 'STOP', -75000)")
    c.execute("INSERT INTO positions VALUES ('p2', 1000, 20.0, '2026-10-29T10:00:00+05:30', 25.0, NULL, NULL, NULL)")
    c.execute("INSERT INTO equity VALUES ('t', 500000000)")
    c.commit(); c.close()
    book = shadow_book(shadow)
    assert book["capital"] == 5_000_000 and book["slots"] == 60
    assert book["slices"][0]["pnl"] == -750.0
    assert book["now"] == {"equity": 4_025_000.0, "cash": 4_000_000.0, "realized": -750.0, "unrealized": 5_000.0, "open": 1}

    desk = tmp_path / "mp.sqlite3"
    c = sqlite3.connect(desk)
    c.executescript("""CREATE TABLE mp_closed_positions (id TEXT, exit_time TEXT, visible_until TEXT, payload TEXT);
      CREATE TABLE trades (trade_id TEXT, timestamp TEXT, payload TEXT);
      CREATE TABLE equity_points (timestamp TEXT, equity REAL, cash REAL, realized_pnl REAL, unrealized_pnl REAL);""")
    c.execute("INSERT INTO mp_closed_positions VALUES ('m1', 't', 't', ?)", (json.dumps(
        {"id": "m1", "symbol": "NSE:BANKNIFTY26NOV55000PE", "entry_time": "2026-11-02T04:45:25+00:00",
         "exit_time": "2026-11-02T11:15:26+05:30", "pnl": -2980.0, "exit_reason": "MP_TIME"}),))
    fills = [("2026-11-02T04:45:25.9+00:00", "BUY", 757.55), ("2026-11-02T05:45:26.2+00:00", "SELL", 733.05),
             ("2026-10-01T04:00:00+00:00", "BUY", 100.0)]                      # an older, still-open entry
    for k, (at, side, price) in enumerate(fills):
        c.execute("INSERT INTO trades VALUES (?, ?, ?)", (str(k), at, json.dumps(
            {"symbol": "NSE:BANKNIFTY26NOV55000PE" if k < 2 else "NSE:OLD26OCT1CE", "side": side, "quantity": 120,
             "price": price, "fees": 20.0, "timestamp": at})))
    c.execute("INSERT INTO equity_points VALUES ('2026-11-02T06:00:00+00:00', 997020, 997020, -2980, 0)")
    c.commit(); c.close()
    book = mp_book(desk, 1_000_000)
    (exit_,) = book["slices"]
    assert exit_["pnl"] == pytest.approx(-2980.0) and exit_["exit_reason"] == "MP_TIME" and exit_["partial"] is False
    assert book["now"]["equity"] == 997020 and book["curve"] == [("2026-11-02T06:00:00+00:00", 997020.0)]
    assert shadow_book(tmp_path / "missing.sqlite3") is None
