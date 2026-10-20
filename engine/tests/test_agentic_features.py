"""Computed inputs for the agentic lane: session shape, ATR, positioning, contract risk, implied move, track record."""
import sqlite3
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from pytest import approx

from macd_trader import agentic_features as feat

IST = ZoneInfo("Asia/Kolkata")
SPOT = "NSE:NIFTY50-INDEX"
FUT = "NSE:NIFTY26OCTFUT"
MON, TUE = date(2026, 10, 12), date(2026, 10, 13)          # two sessions


def at(day, hh, mm):
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=IST)


def make_db(path):
    db = sqlite3.connect(path)
    db.execute("""CREATE TABLE historical_candles (symbol TEXT, timeframe_seconds INTEGER, timestamp INTEGER, open REAL,
                  high REAL, low REAL, close REAL, volume INTEGER)""")
    db.execute("""CREATE TABLE chain_snapshots (ts INTEGER, underlying TEXT, expiry TEXT, strike REAL, option_type TEXT,
                  symbol TEXT, ltp REAL, volume INTEGER, oi INTEGER, spot REAL, bid REAL, ask REAL, prev_oi INTEGER,
                  fp REAL, vix REAL)""")
    rows = []
    # Monday: 22,900 -> 23,000, range 22,850-23,050; Tuesday gaps up to 23,100 and climbs 1 point a minute
    for i in range(375):
        t = at(MON, 9, 15) + timedelta(minutes=i)
        price = 22900 + i * 100 / 374
        rows.append((SPOT, 60, int(t.timestamp()), price, price + 2, price - 2, price, 0))
    rows.append((SPOT, 60, int((at(MON, 9, 15) + timedelta(minutes=30)).timestamp()) + 1, 0, 23050, 22850, 22940, 0))
    for i in range(120):                                      # Tuesday 09:15-11:14
        t = at(TUE, 9, 15) + timedelta(minutes=i)
        price = 23100 + i
        rows.append((SPOT, 60, int(t.timestamp()), price, price + 1, price - 1, price, 0))
        rows.append((FUT, 60, int(t.timestamp()), price + 20, price + 21, price + 19, price + 20, 1000))
    db.executemany("INSERT INTO historical_candles VALUES (?,?,?,?,?,?,?,?)", rows)
    snap = int(at(TUE, 11, 0).timestamp())
    legs = [(23000, "CE", 500, 400), (23200, "CE", 900, 600), (23300, "CE", 700, 700),
            (23000, "PE", 1200, 900), (22900, "PE", 800, 800), (23100, "PE", 300, 100)]
    db.executemany("INSERT INTO chain_snapshots (ts, underlying, expiry, strike, option_type, oi, prev_oi) VALUES (?,?,?,?,?,?,?)",
                   [(snap, "NIFTY", "2026-10-20", k, t, oi, p) for k, t, oi, p in legs])
    db.commit()
    return db


def test_session_shape_reads_gap_first_hour_hourly_closes_and_vwap(tmp_path):
    db = make_db(tmp_path / "h.sqlite3")
    shape = feat.session_shape(db, "NIFTY", SPOT, at(TUE, 11, 14), 23219.0)
    assert shape["prev_close"] == approx(23000, abs=0.01) and shape["prev_high"] == 23050 and shape["prev_low"] == 22850
    assert shape["gap_pct"] == approx((23100 / 23000 - 1) * 100, abs=0.01)
    assert shape["first_hour"] == {"high": 23160.0, "low": 23099.0, "now": "above"}
    assert [h[0] for h in shape["hourly_closes"]] == ["09:15", "10:15"] and shape["hourly_closes"][-1][1] == 23219
    assert shape["vs_futures_vwap_pct"] > 0                     # price rose all morning: last futures print above VWAP


def test_atr_positioning_risk_and_implied_move(tmp_path):
    db = make_db(tmp_path / "h.sqlite3")
    assert feat.daily_atr(db, SPOT, TUE) == {}                  # one session is not enough history
    pos = feat.positioning(db, "NIFTY", at(TUE, 11, 10))
    assert pos["pcr_oi"] == approx(2300 / 2100, abs=0.01) and pos["snapshot_age_min"] == 10
    assert pos["top_call_oi_strikes"][0] == [23200.0, 900] and pos["top_put_oi_strikes"][0] == [23000.0, 1200]
    assert pos["today_oi_change_calls"] == 400 and pos["today_oi_change_puts"] == 500

    risk = feat.contract_risk(120.0, 23200.0, 23200.0, "2026-10-20", True, 75, at(TUE, 11, 0))
    assert risk["breakeven"] == 23320.0 and risk["move_needed_pct"] == approx(120 / 23200 * 100, abs=0.01)
    assert 0.4 < risk["delta"] < 0.65 and risk["theta_rs_per_lot_day"] < 0 and risk["iv_pct"] > 0

    move = feat.implied_move([{"expiry": "2026-10-20", "strike": 23200.0, "type": "CE", "premium": 120},
                              {"expiry": "2026-10-20", "strike": 23200.0, "type": "PE", "premium": 110},
                              {"expiry": "2026-10-27", "strike": 23200.0, "type": "CE", "premium": 260}], 23210.0)
    assert move == {"expiry": "2026-10-20", "strike": 23200.0, "straddle": 230.0, "implied_move_pct": approx(0.99, abs=0.01)}


def test_track_record_merges_partial_exits_newest_first():
    closed = [
        {"position_id": "a", "symbol": "NSE:NIFTY26OCT23000PE", "entry_time": "2026-10-13T10:00", "exit_time": "2026-10-13T11:00",
         "realized_pnl": 3000, "entry_price": 100, "quantity": 75, "exit_reason": "AGENT_REDUCE"},
        {"position_id": "a", "symbol": "NSE:NIFTY26OCT23000PE", "entry_time": "2026-10-13T10:00", "exit_time": "2026-10-13T12:00",
         "realized_pnl": 1500, "entry_price": 100, "quantity": 75, "exit_reason": "AGENT_TARGET"},
        {"position_id": "b", "symbol": "NSE:BANKNIFTY26OCT55000CE", "entry_time": "2026-10-13T13:00", "exit_time": "2026-10-13T14:00",
         "realized_pnl": -2000, "entry_price": 200, "quantity": 30, "exit_reason": "AGENT_STOP"}]
    out = feat.track_record(closed)
    assert out["closed_positions"] == 2 and out["wins"] == 1 and out["net_pnl_rs"] == 2500
    first, second = out["recent"]
    assert first["contract"] == "BANKNIFTY26OCT55000CE" and first["exit"] == "AGENT_STOP"
    assert second["pnl_rs"] == 4500 and second["exit"] == "AGENT_TARGET" and second["return_pct"] == 30.0
