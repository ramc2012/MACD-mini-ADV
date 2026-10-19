"""The index scorecard: indicators, the decision and its vetoes, selection, and the nightly history."""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta

from macd_trader import index_scorecard as sc
from macd_trader import index_trades

MODEL = {"name": "scorecard", "ce": ["last_hour", "rsi_h_follow", "gap_follow"], "pe": ["last_hour", "move_follow"], "min_net": 2}
NEUTRAL = {"last_hour": 0.0, "rsi_h": 50, "rsi_d": 50, "gap": 0.0, "move_atr": 0.0, "trend_h": 0, "trend_d": 0, "trend_w": 0,
           "u_ret_5d": 0.0, "u_ret_20d": 0.0, "u_range20_pos": 0.0}


def test_rsi_and_trend_match_their_definitions():
    rising = [100 + i for i in range(80)]
    assert sc.rsi(rising) == 100.0
    assert sc.trend(rising) == 1 and sc.trend(rising[::-1]) == -1
    assert sc.trend(rising[:40]) == 0          # fewer than 55 values: no trend read


def test_two_agreeing_conditions_buy_and_one_does_not():
    assert sc.decide({**NEUTRAL, "last_hour": 0.002}, MODEL)["side"] == 0
    call = sc.decide({**NEUTRAL, "last_hour": 0.002, "gap": 0.004}, MODEL)
    assert call["side"] == 1 and call["net"] == 2 and ["CE:gap_follow", 1] in call["drivers"]
    put = sc.decide({**NEUTRAL, "last_hour": -0.002, "move_atr": -0.8}, MODEL)
    assert put["side"] == -1 and put["net"] == -2


def test_puts_are_vetoed_against_an_oversold_day_or_an_overbought_hour():
    weak = {**NEUTRAL, "last_hour": -0.002, "move_atr": -0.8}
    assert "daily RSI" in sc.decide({**weak, "rsi_d": 25}, MODEL)["reason"]
    assert sc.decide({**weak, "rsi_h": 75}, MODEL)["side"] == 0
    assert sc.decide({**weak, "rsi_d": 25}, MODEL)["side"] == 0


def test_missing_features_are_no_trade_with_a_reason():
    assert sc.decide(None, MODEL) == {"side": 0, "net": None, "reason": sc.decide(None, MODEL)["reason"], "drivers": []}


def _history() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.execute("""CREATE TABLE historical_candles (symbol TEXT, timeframe_seconds INTEGER, timestamp INTEGER, open REAL,
                  high REAL, low REAL, close REAL, volume INTEGER, asset_type TEXT, expiry TEXT, downloaded_at TEXT,
                  PRIMARY KEY(symbol, timeframe_seconds, timestamp))""")
    sc.ensure_schema(db)
    return db


def test_selection_keeps_only_conditions_that_lifted_every_year():
    db = _history()
    rows = []
    for year in (2022, 2023):
        for i in range(500):
            day = (date(year, 1, 3) + timedelta(days=i % 360)).isoformat()
            up = i % 2 == 0                                  # last_hour up -> CE wins, every year
            f = {**NEUTRAL, "last_hour": 0.001 if up else -0.001, "gap": 0.004 if i % 4 == 1 else 0.0}
            rows.append(("NIFTY" if i < 250 else "SENSEX", day, json.dumps(f), 1.0 if up else -1.0, -1.0 if up else 1.0))
    with db:
        db.executemany("INSERT OR REPLACE INTO agent_index_days (underlying, day, features, ce_sel, pe_sel, ce_r, pe_r) VALUES (?,?,?,?,?,?,?)",
                       [(u, d, f, ce, pe, ce, pe) for u, d, f, ce, pe in rows])
    picked = sc.select(db, 2024)
    assert "last_hour" in picked["ce"] and "last_hour" in picked["pe"]
    assert "gap_follow" not in picked["ce"]                  # gaps fell on losing days: negative lift
    assert picked["years"] == [2022, 2023]


def _bars(db, symbol, day, prices):
    start = int(datetime.fromisoformat(f"{day}T09:15:00+05:30").timestamp())
    db.executemany("INSERT INTO historical_candles VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   [(symbol, 60, start + 60 * i, p, p + 1, p - 1, p, 0, "index", None, "t") for i, p in enumerate(prices)])


def test_history_extends_from_the_minute_store_and_labels_after_three_sessions():
    db = _history()
    days = [(date(2026, 6, 1) + timedelta(days=k)) for k in range(120)]
    days = [d.isoformat() for d in days if d.weekday() < 5]
    seeded, fresh = days[:70], days[70:75]
    with db:
        db.executemany("INSERT INTO agent_index_days (underlying, day, open, high, low, close) VALUES (?,?,?,?,?,?)",
                       [("NIFTY", d, 100 + i, 102 + i, 99 + i, 101 + i) for i, d in enumerate(seeded)])
    for k, d in enumerate(fresh):
        _bars(db, "NSE:NIFTY50-INDEX", d, [200 + k * 5 + i * 0.01 for i in range(375)])
    done = sc.update_history(db, today="2026-12-31")
    assert done["added"] == 5 and done["labelled"] == 2       # the last three have no third session yet
    row = db.execute("SELECT features, ce_path, ce_sel FROM agent_index_days WHERE day = ?", (fresh[0],)).fetchone()
    assert json.loads(row[0])["spot"] > 0 and row[1] in {"target", "stop", "time"} and row[2] is not None


def test_index_lane_uses_the_scorecard_side_and_the_atm_strike():
    rows = [{"underlying": "NIFTY", "side": s, "symbol": f"NIFTY{k}{'CE' if s > 0 else 'PE'}", "expiry": "2026-10-19",
             "entry_price": 100.0, "features": {"o_moneyness": m}} for k, (s, m) in enumerate([(1, 0.004), (1, 0.001), (-1, 0.002)])]
    out = index_trades.decide(rows, MODEL, {"NIFTY": "2026-10-13"},
                              {"NIFTY": {**NEUTRAL, "last_hour": 0.002, "rsi_h": 75}})
    assert out["NIFTY"]["side"] == 1 and out["NIFTY"]["row"]["symbol"] == "NIFTY1CE"
    assert out["BANKNIFTY"]["side"] == 0
