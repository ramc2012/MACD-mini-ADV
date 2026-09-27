"""Whale Layers B-E: per-strike delta-weighted OI builds, the futures leg
beside the option book, the close against the previous close, and the
composite that only speaks once it has a distribution to speak from."""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import statistics
from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace

from macd_trader import engine as engine_module
from zoneinfo import ZoneInfo

from macd_trader import alerts, auction_views, whale
from macd_trader.engine import TradingEngine, fo_session_open
from macd_trader.greeks import black_scholes, contract_delta, delta, years_to_expiry
from macd_trader.models import Tick
from macd_trader.tick_store import TickStore

IST = ZoneInfo("Asia/Kolkata")
DAY = "2026-09-03"
EXPIRY = "2026-10-06"
FUT = "NSE:NIFTY26SEPFUT"


def _ts(hour: int, minute: int, second: int = 0, day: str = DAY) -> int:
    midnight = datetime.combine(date.fromisoformat(day), time(0, 0), IST)
    return int((midnight + timedelta(hours=hour, minutes=minute, seconds=second)).timestamp())


def _db(tmp_path, name="h.sqlite3") -> sqlite3.Connection:
    connection = sqlite3.connect(str(tmp_path / name))
    whale.ensure_schema(connection)
    return connection


class _Entry:
    def __init__(self, strike, kind, oi, volume, ltp, prev_oi=None, bid=0.0, ask=0.0):
        self.strike, self.option_type, self.oi, self.volume, self.ltp = strike, kind, oi, volume, ltp
        self.prev_oi, self.bid, self.ask = prev_oi, bid, ask
        self.symbol = f"NSE:NIFTY26OCT{int(strike)}{kind}"


def _chain(connection, ts, spot, entries, fp=0.0, vix=None, expiry=EXPIRY, underlying="NIFTY"):
    return whale.save_chain(connection, ts, underlying, expiry, spot, entries, fp=fp, vix=vix)


def _premium(underlying: float, strike: float, ts: int, is_call: bool, sigma=0.2, rate=0.0) -> float:
    years = years_to_expiry(EXPIRY, datetime.fromtimestamp(ts, UTC))
    return black_scholes(underlying, strike, years, rate, sigma, is_call)


def _leg(strike, kind, sign, dn, d_oi=1000, d_volume=0, delta_=0.5) -> whale.StrikeWindow:
    return whale.StrikeWindow(strike, kind, f"S{int(strike)}{kind}", EXPIRY, 10_000 + d_oi, 10_000,
                              d_oi, d_volume, 100.0, 1.0, 0.2, delta_, "model", sign,
                              "prints" if sign else "none", dn, delta_ * abs(d_oi) * sign, 0, [])


def _flow(connection, symbol, minute_ts, cvd, ofi_cum, oi, ltp=24000.0, total=0.0, trades=0,
          source="live", day=DAY):
    row = (symbol, minute_ts, day, ltp, cvd, max(cvd, 0.0), max(-cvd, 0.0), total, trades,
           ofi_cum, 10, 500.0, oi, source)
    return whale.save_flow_minutes(connection, [row], source)


def _window_row(connection, underlying, day, slot, ts, **columns):
    names = ["underlying", "ts", "day", "slot", "window_seconds", "source", *columns]
    values = [underlying, ts, day, slot, 180, "nightly", *columns.values()]
    with connection:
        connection.execute(
            f"INSERT OR REPLACE INTO whale_windows ({', '.join(names)}) VALUES ({', '.join('?' * len(names))})",
            values)


def _seed_history(connection, underlying, days: int, slot: int, score_b=100.0, d_volume=1000):
    """Prior sessions with one window row and one strike row at ``slot``,
    plus a decoy strike row at the next slot that a same-slot read must skip."""
    for index in range(days):
        day = (date(2026, 8, 1) + timedelta(days=index)).isoformat()
        ts = _ts(9, 15 + slot * 15 + 5, day=day)
        value = score_b(index) if callable(score_b) else score_b
        _window_row(connection, underlying, day, slot, ts, score_a=1.0, score_b=value, score_c=0.0)
        with connection:
            connection.executemany(
                """INSERT OR REPLACE INTO whale_strike_windows
                   (underlying, ts, day, slot, strike, option_type, bucket, d_volume, d_oi, dn, flow_sign)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                [(underlying, ts, day, slot, 24000.0, "CE", 0, d_volume, 100, 1e6, 1),
                 (underlying, ts + 900, day, slot + 1, 24000.0, "CE", 0, 9 * d_volume, 100, 1e6, 1)])


# ---------------------------------------------------------------------------
# Delta
# ---------------------------------------------------------------------------

def test_delta_matches_the_finite_difference_of_the_price():
    spot, strike, years, rate, sigma, h = 24000.0, 24100.0, 0.05, 0.065, 0.2, 0.5
    numeric = (black_scholes(spot + h, strike, years, rate, sigma, True)
               - black_scholes(spot - h, strike, years, rate, sigma, True)) / (2 * h)
    call = delta(spot, strike, years, rate, sigma, True)
    assert abs(call - numeric) < 1e-4
    assert abs(delta(spot, strike, years, rate, sigma, False) - (call - 1.0)) < 1e-9


def test_contract_delta_falls_back_to_intrinsic_when_the_solver_refuses():
    common = dict(expiry=EXPIRY, now=datetime.fromtimestamp(_ts(10, 0), UTC))
    deep_itm = contract_delta(premium=1.0, underlying=24500.0, strike=24000.0, option_type="CE", **common)
    assert deep_itm == (1.0, None, "intrinsic")
    untraded_otm = contract_delta(premium=0.0, underlying=24000.0, strike=26000.0, option_type="CE", **common)
    assert untraded_otm == (0.0, None, "intrinsic")
    itm_put = contract_delta(premium=1.0, underlying=23000.0, strike=24000.0, option_type="PE", **common)
    assert itm_put[0] == -1.0
    expired = contract_delta(premium=5.0, underlying=24000.0, strike=24000.0, expiry="2026-01-01",
                             option_type="CE", now=common["now"])
    assert expired == (0.0, None, "none")
    solved = contract_delta(premium=_premium(24000.0, 24000.0, _ts(10, 0), True), underlying=24000.0,
                            strike=24000.0, option_type="CE", rate=0.0, **common)
    assert solved[2] == "model" and abs(solved[1] - 0.2) < 1e-3 and 0.45 < solved[0] < 0.6


def test_expiry_day_past_1530_is_intrinsic_not_a_blank_delta():
    """The F&O session runs to 15:40 and greeks.EXPIRY_TIME is 15:30. Those ten
    minutes are the expiry-day unwind: every strike must keep a delta."""
    common = dict(expiry=DAY, option_type="CE", underlying=24500.0)
    before = contract_delta(premium=120.0, strike=24500.0,
                            now=datetime.fromtimestamp(_ts(15, 25), UTC), **common)
    assert before[2] == "model" and 0.4 < before[0] < 0.6

    late = datetime.fromtimestamp(_ts(15, 31), UTC)
    at_the_money = contract_delta(premium=120.0, strike=24500.0, now=late, **common)
    assert at_the_money == (0.0, None, "intrinsic")
    itm = contract_delta(premium=505.0, strike=24000.0, now=late, **common)
    assert itm == (1.0, None, "intrinsic")
    itm_put = contract_delta(premium=505.0, underlying=24500.0, strike=25000.0, expiry=DAY,
                             option_type="PE", now=late)
    assert itm_put == (-1.0, None, "intrinsic")
    # A date that has genuinely passed still has no delta to give.
    assert contract_delta(premium=5.0, underlying=24500.0, strike=24500.0, expiry="2026-09-02",
                          option_type="CE", now=late) == (0.0, None, "none")


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def test_ensure_schema_widens_an_existing_chain_table(tmp_path):
    connection = sqlite3.connect(str(tmp_path / "old.sqlite3"))
    connection.executescript("""
        CREATE TABLE chain_snapshots (
          ts INTEGER NOT NULL, underlying TEXT NOT NULL, expiry TEXT NOT NULL, strike REAL NOT NULL,
          option_type TEXT NOT NULL, symbol TEXT NOT NULL, ltp REAL, volume INTEGER, oi INTEGER, spot REAL,
          PRIMARY KEY (ts, symbol));""")
    connection.execute("INSERT INTO chain_snapshots VALUES (?,?,?,?,?,?,?,?,?,?)",
                       (_ts(10, 0), "NIFTY", EXPIRY, 24000.0, "CE", "S", 1.0, 0, 1, 24000.0))

    whale.ensure_schema(connection)
    whale.ensure_schema(connection)

    columns = [row[1] for row in connection.execute("PRAGMA table_info(chain_snapshots)")]
    for name in ("bid", "ask", "prev_oi", "fp", "vix"):
        assert columns.count(name) == 1
    for name in ("fut_symbol", "d_oi_skipped"):
        assert [row[1] for row in connection.execute("PRAGMA table_info(whale_eod)")].count(name) == 1
    # The day ledger is backfilled once, from the chain that was already there.
    assert [tuple(row) for row in connection.execute("SELECT day, underlying FROM chain_days")] == [(DAY, "NIFTY")]

    class Legacy:
        def __init__(self):
            self.strike, self.option_type, self.oi, self.volume, self.ltp = 24000.0, "CE", 10, 5, 1.0
            self.symbol = "NSE:NIFTY26OCT24000CE"

    assert whale.save_chain(connection, 1000, "NIFTY", EXPIRY, 24000.0, [Legacy()]) == 1
    assert connection.execute("SELECT bid, fp, prev_oi FROM chain_snapshots WHERE ts = 1000"
                              ).fetchone() == (0.0, 0.0, None)


# ---------------------------------------------------------------------------
# Layer B
# ---------------------------------------------------------------------------

def test_strike_window_delta_notional_uses_the_solved_delta_against_the_future(tmp_path):
    connection = _db(tmp_path)
    then, now = _ts(10, 0), _ts(10, 3)
    fp = 24050.0
    _chain(connection, then, 24000.0, [
        _Entry(24000, "CE", 10_000, 1000, _premium(fp, 24000, then, True)),
        _Entry(24000, "PE", 8_000, 1000, _premium(fp, 24000, then, False))], fp=fp)
    _chain(connection, now, 24000.0, [
        _Entry(24000, "CE", 11_300, 6000, _premium(fp, 24000, now, True)),
        _Entry(24000, "PE", 8_000, 1000, _premium(fp, 24000, now, False))], fp=fp)

    meta, legs = whale.strike_windows(connection, "NIFTY", now + 30)

    assert meta["status"] == "ok" and meta["underlying_price"] == fp and meta["fut"] == fp
    top = legs[0]
    years = years_to_expiry(EXPIRY, datetime.fromtimestamp(now, UTC))
    expected = delta(fp, 24000.0, years, 0.0, 0.2, True)
    assert top.option_type == "CE" and top.d_oi == 1300 and top.d_oi // 65 == 20
    assert top.delta_source == "model" and abs(top.delta - expected) < 1e-3
    assert abs(top.dn - 1300 * abs(expected) * fp) / top.dn < 0.01
    assert legs[1].d_oi == 0 and legs[1].dn == 0


def test_a_window_refuses_a_gap_and_a_day_boundary(tmp_path):
    connection = _db(tmp_path)
    assert whale.strike_windows(connection, "NIFTY", _ts(10, 0))[0]["status"] == "no_snapshot"
    _chain(connection, _ts(10, 0), 24000.0, [_Entry(24000, "CE", 10, 0, 1.0)])
    assert whale.strike_windows(connection, "NIFTY", _ts(10, 0))[0]["status"] == "no_window"
    # 500 s since the previous snapshot is a feed hole, not a window.
    _chain(connection, _ts(10, 8, 20), 24000.0, [_Entry(24000, "CE", 10, 0, 1.0)])
    assert whale.strike_windows(connection, "NIFTY", _ts(10, 9))[0]["status"] == "no_window"
    # 180 s apart but across IST midnight: OI does not carry across a session.
    _chain(connection, _ts(23, 59, day="2026-09-02"), 24000.0, [_Entry(24000, "PE", 10, 0, 1.0)], underlying="BANKNIFTY")
    _chain(connection, _ts(0, 2, day="2026-09-03"), 24000.0, [_Entry(24000, "PE", 10, 0, 1.0)], underlying="BANKNIFTY")
    assert whale.strike_windows(connection, "BANKNIFTY", _ts(0, 3))[0]["status"] == "no_window"


def test_flow_sign_from_premium_is_net_of_the_underlying_move(tmp_path):
    connection = _db(tmp_path)
    then, now = _ts(10, 0), _ts(10, 3)
    base = _premium(24000.0, 24000.0, then, True, rate=0.065)
    rows_then = [_Entry(24000, "CE", 10_000, 0, base), _Entry(24100, "CE", 10_000, 0, base),
                 _Entry(24200, "CE", 10_000, 0, base), _Entry(26000, "CE", 10_000, 0, 0.0)]
    rows_now = [_Entry(24000, "CE", 11_000, 500, base + 12.0),   # rose, but less than a 0.5 delta x 30 says
                _Entry(24100, "CE", 11_000, 500, base + 25.0),   # rose more than the move explains
                _Entry(24200, "CE", 11_000, 500, base - 5.0),
                _Entry(26000, "CE", 10_000, 0, 0.0)]             # never traded: no delta, no sign
    _chain(connection, then, 24000.0, rows_then)
    _chain(connection, now, 24030.0, rows_now)

    _, legs = whale.strike_windows(connection, "NIFTY", now, flow_signs={"NSE:NIFTY26OCT24200CE": 1})
    by = {leg.strike: leg for leg in legs}

    assert (by[24000.0].flow_sign, by[24000.0].flow_source) == (-1, "premium")
    assert (by[24100.0].flow_sign, by[24100.0].flow_source) == (1, "premium")
    assert (by[24200.0].flow_sign, by[24200.0].flow_source) == (1, "prints")
    assert (by[26000.0].flow_sign, by[26000.0].flow_source) == (0, "none")
    assert by[26000.0].delta_source == "intrinsic"


def test_unusual_volume_ge_oi_fires_without_history_and_the_median_rule_waits(tmp_path):
    connection = _db(tmp_path)
    then, now = _ts(10, 0), _ts(10, 3)
    ltp = _premium(24000.0, 24000.0, then, True)

    def window(oi_now, volume_now, medians=None):
        connection.execute("DELETE FROM chain_snapshots")
        _chain(connection, then, 24000.0, [_Entry(24000, "CE", 4000, 0, ltp)])
        _chain(connection, now, 24000.0, [_Entry(24000, "CE", oi_now, volume_now, ltp)])
        return whale.strike_windows(connection, "NIFTY", now, medians=medians)[1][0]

    assert window(6000, 5000).unusual == ["volume_ge_oi"]
    assert window(4100, 5000).unusual == []                                  # churn, not positioning
    assert window(6000, 5000, medians=None).unusual == ["volume_ge_oi"]
    assert window(5000, 3500, medians={("CE", 0): 1000}).unusual == ["volume_3x_median"]


def test_bucket_medians_report_insufficient_below_twenty_days(tmp_path):
    connection = _db(tmp_path)
    _seed_history(connection, "NIFTY", 19, slot=3)
    assert whale.bucket_medians(connection, "NIFTY", 3, DAY) == (None, 19)
    _seed_history(connection, "NIFTY", 20, slot=3)
    medians, days = whale.bucket_medians(connection, "NIFTY", 3, DAY)
    assert days == 20 and medians == {("CE", 0): 1000}


def test_structures_classify_the_shapes():
    step = 50.0
    rr = whale.structures([_leg(24200, "CE", -1, 1e8), _leg(23800, "PE", 1, 1e8)], step)
    assert [(s["kind"], s["direction"]) for s in rr] == [("risk_reversal", -1)]
    straddle = whale.structures([_leg(24000, "CE", 1, 1e8), _leg(24000, "PE", 1, 1e8)], step)
    assert [(s["kind"], s["direction"]) for s in straddle] == [("straddle", 0)]
    synthetic = whale.structures([_leg(24000, "CE", 1, 1e8), _leg(24000, "PE", -1, 1e8)], step)
    assert [(s["kind"], s["direction"]) for s in synthetic] == [("synthetic_future", 1)]
    vertical = whale.structures([_leg(24000, "CE", 1, 1e8), _leg(24050, "CE", -1, 1e8)], step)
    assert [(s["kind"], s["direction"]) for s in vertical] == [("vertical", 1)]
    strangle = whale.structures([_leg(24200, "CE", 1, 1e8), _leg(23800, "PE", 1, 1e8)], step)
    assert [s["kind"] for s in strangle] == ["strangle"]
    # A leg under the floor is not half of anything, and a 3:1 pair is two trades.
    assert whale.structures([_leg(24000, "CE", 1, 1e8), _leg(24000, "PE", 1, 1e6)], step) == []
    assert whale.structures([_leg(24000, "CE", 1, 3e8), _leg(24000, "PE", 1, 1e8)], step) == []
    assert whale.structures([], step) == []


def test_net_option_delta_sign_convention():
    legs = [_leg(24000, "CE", 1, 1e8, delta_=0.5),      # bought calls: long
            _leg(23800, "PE", -1, 1e8, delta_=-0.4),    # written puts: long
            _leg(23900, "PE", 1, 1e8, delta_=-0.4),     # bought puts: short
            _leg(24500, "CE", 0, 1e6, d_oi=200)]
    out = whale.net_option_delta(legs, 24000.0)
    assert out["units"] == 500.0 and out["dn"] == 500 * 24000 and out["sign"] == 1
    assert (out["signed_legs"], out["unsigned_legs"]) == (3, 1)


def test_walls_pcr_jump_and_migration(tmp_path):
    connection = _db(tmp_path)
    opened, now = _ts(9, 16), _ts(10, 3)
    _chain(connection, opened, 24000.0, [_Entry(24000, "CE", 10_000, 0, 1.0), _Entry(23800, "PE", 7000, 0, 1.0),
                                         _Entry(23900, "PE", 5000, 0, 1.0)])
    _chain(connection, now, 24000.0, [_Entry(24000, "CE", 10_000, 0, 1.0), _Entry(23800, "PE", 6000, 0, 1.0),
                                      _Entry(23900, "PE", 6700, 0, 1.0)])

    out = whale.walls_and_pcr(connection, "NIFTY", now, opened, opened)

    assert (out["pcr_oi_then"], out["pcr_oi"], out["pcr_jump"], out["pcr_jumped"]) == (1.2, 1.27, 0.07, True)
    assert out["migration"]["PE"] == {"from": 23800.0, "to": 23900.0}
    assert out["walls"]["PE"][0] == {"strike": 23900.0, "oi": 6700}
    assert whale.pcr_volume_window([_leg(24000, "CE", 1, 1e8, d_volume=100),
                                    _leg(24000, "PE", 1, 1e8, d_volume=150)]) == 1.5


# ---------------------------------------------------------------------------
# Layer C
# ---------------------------------------------------------------------------

def test_futures_window_and_the_majority_sign(tmp_path):
    connection = _db(tmp_path)
    t0 = _ts(10, 0)
    _flow(connection, FUT, t0, cvd=1000, ofi_cum=0, oi=100_000, total=5000, trades=100)
    _flow(connection, FUT, t0 + 180, cvd=1650, ofi_cum=-300, oi=100_650, total=8000, trades=160)

    fut = whale.futures_window(connection, FUT, t0 + 200)

    assert (fut["delta_units"], fut["ofi"], fut["d_oi"], fut["dn"]) == (650, -300, 650, 650 * 24000)
    assert (fut["volume"], fut["trades"], fut["ofi_normalised"]) == (3000, 60, -0.6)
    assert whale.futures_sign(fut, a_net=-2.0) == -1
    assert whale.futures_sign(fut, a_net=1.0) == 1
    assert whale.futures_window(connection, FUT, t0 + 60) is None


def test_divergence_needs_opposite_signs_five_x_and_a_real_futures_leg():
    fut = {"delta_units": -100, "ofi": -5, "d_oi": 10, "dn": -1e8}
    flagged = whale.divergence({"dn": 6e8, "sign": 1}, fut, 0.0)
    assert flagged["divergence"] is True and flagged["ratio"] == 6.0 and flagged["fresh_futures"]
    assert whale.divergence({"dn": -6e8, "sign": -1}, fut, 0.0)["divergence"] is False
    flat = whale.divergence({"dn": 6e8, "sign": 1}, {**fut, "dn": -5e6}, 0.0)
    assert (flat["status"], flat["divergence"], flat["ratio"]) == ("futures_flat", False, None)
    assert whale.divergence({"dn": 4e7, "sign": 1}, fut, 0.0)["divergence"] is False
    assert whale.divergence({"dn": 6e8, "sign": 1}, None, 0.0)["status"] == "no_futures_window"


def test_minute_flow_from_ticks_matches_the_condenser(tmp_path):
    store = TickStore(str(tmp_path / "ticks.sqlite3"), retention_days=1)
    start = datetime.combine(date.fromisoformat(DAY), time(10, 0), IST)
    for index in range(40):
        price = 24000.0 + (index % 3) - 1
        store.add(Tick(FUT, price, volume=100 * (index + 1), timestamp=start + timedelta(seconds=5 * index),
                       bid=price - 0.5, ask=price + 0.5, bid_qty=100 + 10 * (index % 4),
                       ask_qty=120 + 5 * (index % 5), last_qty=100))
    store.flush_sync()
    ticks = sqlite3.connect(str(tmp_path / "ticks.sqlite3"))

    rebuilt = whale.minute_flow_from_ticks(ticks, FUT, DAY)
    store.condense_day(DAY)
    condensed = ticks.execute(
        """SELECT minute_ts, volume, trades, delta, ofi FROM tick_minute_flow ORDER BY minute_ts""").fetchall()

    assert len(rebuilt) == len(condensed) == 4
    volume = trades = delta_sum = ofi_sum = 0.0
    for row, (minute_ts, minute_volume, minute_trades, minute_delta, minute_ofi) in zip(rebuilt, condensed):
        volume += minute_volume; trades += minute_trades; delta_sum += minute_delta; ofi_sum += minute_ofi
        assert row[1] == minute_ts and row[13] == "raw"
        assert (row[7], row[8], row[4]) == (volume, trades, delta_sum)
        assert abs(row[9] - ofi_sum) < 0.05 * len(rebuilt)
    assert rebuilt[-1][9] != 0 and rebuilt[-1][4] != 0


def test_a_rebuild_never_displaces_a_live_flow_row(tmp_path):
    connection = _db(tmp_path)
    t0 = _ts(10, 0)
    _flow(connection, FUT, t0, cvd=1000, ofi_cum=0, oi=100_000, source="live")
    _flow(connection, FUT, t0, cvd=999, ofi_cum=9, oi=None, source="raw")
    _flow(connection, FUT, t0 + 60, cvd=5, ofi_cum=5, oi=None, source="raw")
    _flow(connection, FUT, t0 + 60, cvd=6, ofi_cum=6, oi=None, source="condensed")
    rows = connection.execute("SELECT cvd, oi, source FROM whale_flow_minutes ORDER BY minute_ts").fetchall()
    assert rows == [(1000.0, 100_000, "live"), (6.0, None, "condensed")]


# ---------------------------------------------------------------------------
# Layer E
# ---------------------------------------------------------------------------

def test_zscore_is_none_until_twenty_days_then_uses_the_same_slot_only(tmp_path):
    connection = _db(tmp_path)
    _seed_history(connection, "NIFTY", 19, slot=3)
    assert whale.zscore(connection, "NIFTY", "score_b", 3, DAY, 150.0) == (None, 19)
    _seed_history(connection, "NIFTY", 20, slot=3)
    assert whale.zscore(connection, "NIFTY", "score_b", 3, DAY, 150.0) == (0.0, 20)     # zero variance
    _seed_history(connection, "NIFTY", 20, slot=3, score_b=lambda i: 100.0 if i % 2 else 200.0)
    # Decoys at slot 4 would drag the mean if they were counted.
    for index in range(20):
        day = (date(2026, 8, 1) + timedelta(days=index)).isoformat()
        _window_row(connection, "NIFTY", day, 4, _ts(10, 20, day=day), score_b=1e9)
    z, days = whale.zscore(connection, "NIFTY", "score_b", 3, DAY, 250.0)
    sample = [100.0 if i % 2 else 200.0 for i in range(20)]
    assert days == 20 and abs(z - (250.0 - statistics.mean(sample)) / statistics.stdev(sample)) < 1e-3


def test_composite_weights_and_a_missing_d():
    assert whale.composite({"a": 3.0, "b": 3.0, "c": 3.0, "d": None}) == 3.0
    assert whale.composite({"a": 3.0, "b": 3.0, "c": 3.0, "d": 0.0}) == 2.7
    assert whale.composite({"a": None, "b": 3.0, "c": 3.0, "d": 0.0}) is None


def test_decayed_composite_halves_in_fifteen_minutes(tmp_path):
    connection = _db(tmp_path)
    t = _ts(10, 0)
    _window_row(connection, "NIFTY", DAY, 3, t, composite=4.0)
    assert whale.decayed_composite(connection, "NIFTY", DAY, t + 900) == 4.0
    _window_row(connection, "NIFTY", DAY, 4, t + 900, composite=0.0)
    assert whale.decayed_composite(connection, "NIFTY", DAY, t + 900) == round(2.0 / 1.5, 3)
    assert whale.decayed_composite(connection, "BANKNIFTY", DAY, t) is None


def test_alert_threshold_and_one_per_fifteen_minutes(tmp_path):
    connection = _db(tmp_path)
    t = _ts(10, 0)
    assert whale.maybe_alert(connection, "NIFTY", t, DAY, 2.49, 1, 24000.0, None, {}) is None
    first = whale.maybe_alert(connection, "NIFTY", t, DAY, 2.5, 1, 24000.0, None, {"why": [1]})
    assert first is not None
    assert whale.maybe_alert(connection, "NIFTY", t + 600, DAY, 3.0, 1, 24000.0, None, {}) is None
    assert whale.maybe_alert(connection, "NIFTY", t + 900, DAY, 3.0, -1, 24000.0, None, {}) == first + 1
    evidence = connection.execute("SELECT evidence FROM whale_alerts WHERE id = ?", (first,)).fetchone()[0]
    assert json.loads(evidence) == {"why": [1]}


def test_outcomes_fill_from_chain_spot_and_stay_null_past_the_close(tmp_path):
    connection = _db(tmp_path)
    t = _ts(10, 0)
    alert = whale.maybe_alert(connection, "NIFTY", t, DAY, 3.0, 1, 24000.0, None, {})
    _chain(connection, _ts(10, 15), 24040.0, [_Entry(24000, "CE", 1, 0, 1.0)])
    _chain(connection, _ts(10, 30), 23990.0, [_Entry(24000, "CE", 1, 0, 1.0)])

    assert whale.fill_outcomes(connection, _ts(10, 31)) == 2
    row = whale.alert_payload(dict(zip(
        ("next_15", "next_30", "next_60"),
        connection.execute("SELECT next_15, next_30, next_60 FROM whale_alerts WHERE id = ?", (alert,)).fetchone())))
    assert (row["next_15"]["move_pts"], row["next_15"]["agreed"]) == (40.0, True)
    assert (row["next_30"]["move_pts"], row["next_30"]["agreed"]) == (-10.0, False)
    assert row["next_60"] is None

    late = whale.maybe_alert(connection, "NIFTY", _ts(15, 25), DAY, 3.0, 1, 24000.0, None, {})
    assert whale.fill_outcomes(connection, _ts(16, 40)) == 0
    assert connection.execute("SELECT next_15, next_60 FROM whale_alerts WHERE id = ?", (late,)).fetchone() == (None, None)


def test_detect_recent_dedupes_on_replace(tmp_path):
    ticks = sqlite3.connect(str(tmp_path / "ticks.sqlite3"))
    ticks.executescript(
        """CREATE TABLE tick_symbols (id INTEGER PRIMARY KEY, symbol TEXT UNIQUE);
           CREATE TABLE ticks (symbol_id INTEGER, ts_ms INTEGER, ltp REAL, cum_volume INTEGER,
             last_qty INTEGER, bid REAL, ask REAL, bid_qty INTEGER, ask_qty INTEGER, oi INTEGER,
             tbq INTEGER, tsq INTEGER);""")
    ticks.execute("INSERT INTO tick_symbols VALUES (1, ?)", (FUT,))
    start = _ts(10, 0) * 1000
    rows = [(1, start + i * 1000, 24001.0, 65 * (i + 1), 65, 23999.0, 24001.0, 100, 100, None, None, None)
            for i in range(40)]
    rows += [(1, start + 45_000 + i * 5000, 24001.0, 0, 1800, 23999.0, 24001.0, 100, 100, None, None, None)
             for i in range(3)]
    ticks.executemany("INSERT INTO ticks VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    ticks.commit()
    history = _db(tmp_path)

    first = whale.save_events(history, DAY, whale.detect_recent(ticks, FUT, DAY, start + 120_000))
    again = whale.save_events(history, DAY, whale.detect_recent(ticks, FUT, DAY, start + 180_000))

    assert first == again > 0
    assert history.execute("SELECT COUNT(*) FROM whale_events").fetchone()[0] == first
    kinds = {row["kind"] for row in whale.events_for(history, DAY, FUT)}
    assert {"freeze_print", "slicer"} <= kinds


def test_evaluate_window_persists_and_a_nightly_pass_keeps_the_live_row(tmp_path):
    history = _db(tmp_path)
    then, now = _ts(10, 0), _ts(10, 3)
    fp = 24050.0
    # Window volume 5000 on a prior OI of 4000 with 1300 of fresh OI: the
    # absolute "volume at or above OI" notice, which needs no history.
    _chain(history, _ts(9, 16), 24000.0, [_Entry(24000, "CE", 3_000, 0, _premium(fp, 24000, then, True)),
                                          _Entry(24000, "PE", 8_000, 0, _premium(fp, 24000, then, False))], fp=fp)
    _chain(history, then, 24000.0, [_Entry(24000, "CE", 4_000, 1000, _premium(fp, 24000, then, True)),
                                    _Entry(24000, "PE", 8_000, 1000, _premium(fp, 24000, then, False))], fp=fp)
    _chain(history, now, 24000.0, [_Entry(24000, "CE", 5_300, 6000, _premium(fp, 24000, now, True) + 5),
                                   _Entry(24000, "PE", 8_000, 1000, _premium(fp, 24000, now, False))], fp=fp, vix=12.5)
    _flow(history, FUT, then, cvd=1000, ofi_cum=0, oi=100_000)
    _flow(history, FUT, now, cvd=1650, ofi_cum=-300, oi=100_650)

    live = whale.evaluate_window(history, "NIFTY", FUT, now)

    assert live["status"] == "ok" and live["source"] == "live" and live["lot"] == 65
    assert live["composite"] is None and live["alert_id"] is None
    assert live["history"] == {"days": 0, "required": 20, "status": "insufficient", "regime_breaks": []}
    assert live["strikes"][0]["d_oi"] == 1300 and live["strikes"][0]["flow_sign"] == 1
    assert live["net_option_delta"]["sign"] == 1 and live["futures"]["delta_units"] == 650
    assert live["divergence"]["status"] == "ok" and live["vix"] == 12.5
    assert history.execute("SELECT COUNT(*) FROM whale_strike_windows").fetchone()[0] == 2
    assert history.execute("SELECT source, composite_decayed FROM whale_windows").fetchone() == ("live", None)

    nightly = whale.evaluate_window(history, "NIFTY", FUT, now, source="nightly")
    assert nightly["source"] == "live" and "strikes" not in nightly
    assert history.execute("SELECT source FROM whale_windows").fetchone() == ("live",)

    replayed = whale.window_payload(history, dict(zip(
        [d[0] for d in history.execute("SELECT * FROM whale_windows").description],
        history.execute("SELECT * FROM whale_windows").fetchone())))
    assert replayed["strikes"][0]["d_oi"] == 1300 and replayed["strikes"][0]["unusual"] == ["volume_ge_oi"]
    assert replayed["net_option_delta"]["sign"] == 1 and replayed["futures"]["d_oi"] == 650
    assert replayed["divergence"]["status"] == "ok" and replayed["migration"]["CE"]["to"] == 24000.0


# ---------------------------------------------------------------------------
# Layer D
# ---------------------------------------------------------------------------

def test_eod_uses_prev_oi_on_the_first_day_then_its_own_prior_close(tmp_path):
    history = _db(tmp_path)
    day1, day2 = "2026-09-02", "2026-09-03"
    _chain(history, _ts(10, 0, day=day1), 24000.0, [_Entry(24000, "CE", 11_000, 0, 1.0, prev_oi=10_000)])
    _chain(history, _ts(15, 39, day=day1), 24000.0, [_Entry(24000, "CE", 12_000, 0, 1.0, prev_oi=10_000),
                                                     _Entry(24000, "PE", 9_000, 0, 1.0, prev_oi=10_000)], fp=24040.0)
    _flow(history, FUT, _ts(15, 39, day=day1), cvd=0, ofi_cum=0, oi=200_000, total=6500, trades=100, day=day1)

    first = whale.eod(history, day1, "NIFTY", FUT)

    assert first["status"] == "ok" and (first["d_call_oi"], first["d_put_oi"]) == (2000, -1000)
    assert (first["pcr_oi"], first["prior_pcr_oi"], first["fut"]) == (0.75, None, 24040.0)
    assert (first["fut_oi"], first["fut_pdoi"], first["fut_avg_trade"], first["fut_avg_trade_pct"]) == (200_000, None, 65.0, None)
    assert first["score"] is None and first["history_days"] == 0

    # The band re-centred: 24100PE is new to the snapshot and Fyers gave no
    # prior close for it. Unknown, not a 100-unit build out of nothing.
    _chain(history, _ts(15, 39, day=day2), 24100.0, [_Entry(24000, "CE", 13_000, 0, 1.0, prev_oi=12_000),
                                                     _Entry(24000, "PE", 9_500, 0, 1.0, prev_oi=9_000),
                                                     _Entry(24100, "PE", 100, 0, 1.0, prev_oi=0)])
    _flow(history, FUT, _ts(15, 39, day=day2), cvd=0, ofi_cum=0, oi=201_000, day=day2)

    second = whale.eod(history, day2, "NIFTY", FUT)

    assert (second["d_call_oi"], second["d_put_oi"], second["d_oi_skipped"]) == (1000, 500, 1)
    assert [row for row in second["top_d_oi"] if row[0] == 24100.0] == []
    assert (second["prior_pcr_oi"], second["fut_pdoi"], second["history_days"]) == (0.75, 200_000, 1)
    assert [row[0] for row in second["top_oi"]][:2] == [24000.0, 24000.0] and len(second["top_oi"]) <= 10
    assert history.execute("SELECT COUNT(*) FROM whale_eod").fetchone()[0] == 2
    assert whale.prior_eod_score(history, "NIFTY", "2026-09-04") is None
    assert whale.eod(history, "2026-09-04", "NIFTY", FUT)["status"] == "no_snapshots"


# ---------------------------------------------------------------------------
# Engine hooks
# ---------------------------------------------------------------------------

def test_fo_session_open_uses_the_regime_close():
    assert fo_session_open(datetime(2026, 9, 3, 15, 35, tzinfo=IST))
    assert fo_session_open(datetime(2026, 9, 3, 15, 40, tzinfo=IST))
    assert not fo_session_open(datetime(2026, 9, 3, 15, 41, tzinfo=IST))
    assert not fo_session_open(datetime(2026, 7, 15, 15, 35, tzinfo=IST))     # session_end 15:30 then
    assert not fo_session_open(datetime(2026, 9, 5, 10, 0, tzinfo=IST))       # Saturday


class _Broker:
    def __init__(self):
        self.chain_calls: list[str] = []
        self.quote_calls: list[list[str]] = []

    async def option_chain(self, symbol, expiry_token=None):
        self.chain_calls.append(symbol)
        return SimpleNamespace(expiry=EXPIRY, spot_price=24000.0, expiries=[], fp=24050.0, vix=12.5,
                               entries=[_Entry(24000, "CE", 10_000, 100, 120.0, prev_oi=9_000, bid=119.5, ask=120.5)])

    async def quotes(self, symbols):
        self.quote_calls.append(list(symbols))
        return {symbol: Tick(symbol, 24010.0, open_interest=123_456) for symbol in symbols}


def _engine(tmp_path):
    engine = SimpleNamespace(
        settings=SimpleNamespace(research_database_path=str(tmp_path / "h.sqlite3"),
                                 tick_database_path=str(tmp_path / "missing.sqlite3"),
                                 tick_capture_enabled=True,
                                 mp_symbols_csv="NSE:NIFTY26SEPFUT,BSE:SENSEX26SEPFUT"),
        futures_rollover={}, broker=_Broker(), mp=SimpleNamespace(flow=SimpleNamespace(states={})),
        contract_selector=SimpleNamespace(contracts={}), published=[],
        chain_status={"day": None, "snapshots": 0, "last_at": None, "error": None, "windows": 0,
                      "alerts_today": 0, "history_days": 0, "composite": {}, "whale_error": None},
        whale_live=whale.LiveFlow([FUT]), whale_alert_queue=[],
        # The chain minute stands the futures-OI call down after a 429.
        _quotes_blocked_until=0.0, _quotes_backoff=engine_module.QUOTES_BACKOFF_SECONDS)
    engine.events = SimpleNamespace(publish=lambda kind, data: engine.published.append((kind, data)))
    for name in ("_chain_minute", "_whale_roots", "_store_chain", "_whale_minute", "_option_flow_signs",
                 "mp_spot_symbols", "mp_configured_symbols"):
        setattr(engine, name, getattr(TradingEngine, name).__get__(engine))
    return engine


def test_chain_minute_requests_the_index_spot_and_samples_futures_oi(tmp_path):
    engine = _engine(tmp_path)
    stamp = _ts(10, 5)

    asyncio.run(engine._chain_minute(stamp))

    # The chain is asked for by the INDEX, once per root the tracker covers.
    assert engine.broker.chain_calls == ["NSE:NIFTY50-INDEX"]
    assert engine.broker.quote_calls == [[FUT]]
    connection = sqlite3.connect(engine.settings.research_database_path)
    assert connection.execute("SELECT fp, vix, prev_oi, bid FROM chain_snapshots").fetchone() == (24050.0, 12.5, 9_000, 119.5)
    assert connection.execute("SELECT oi, source, day FROM whale_flow_minutes").fetchone() == (123_456, "live", DAY)
    assert engine.chain_status["snapshots"] == 1 and engine.chain_status["error"] is None
    # No tick archive: Layer A reports it, the chain evaluation still runs.
    assert engine.chain_status["whale_error"].startswith("layer A")
    kind, payload = engine.published[0]
    assert kind == "whale" and payload["NIFTY"]["status"] == "no_window"
    assert engine.chain_status["composite"] == {"NIFTY": None}

    # 'snapshots' and 'windows' are per-day counts, like 'alerts_today' beside
    # them — a process that stays up across sessions must not report a total.
    engine.chain_status["windows"] = 1500
    asyncio.run(engine._chain_minute(_ts(10, 5, day="2026-09-04")))
    assert engine.chain_status["day"] == "2026-09-04"
    assert (engine.chain_status["snapshots"], engine.chain_status["windows"]) == (1, 0)


# ---------------------------------------------------------------------------
# Views and Telegram
# ---------------------------------------------------------------------------

def test_whale_view_is_empty_before_any_table_exists(tmp_path):
    path = str(tmp_path / "blank.sqlite3")
    sqlite3.connect(path).close()
    view = auction_views.whale(path, DAY)
    assert (view["windows"], view["alerts"], view["eod"]) == ({}, [], {})
    assert view["history"] == {"chain_days": 0, "required": 20}
    assert auction_views.whale_windows(path, "NIFTY", DAY)["series"] == []
    assert auction_views.whale_alerts(path, DAY) == {
        "day": DAY, "alerts": [], "sessions": 0,
        "precision": {c: {"agreed": 0, "resolved": 0} for c in ("next_15", "next_30", "next_60")}}


def test_whale_view_carries_windows_alerts_eod_and_history(tmp_path):
    path = str(tmp_path / "h.sqlite3")
    history = sqlite3.connect(path)
    whale.ensure_schema(history)
    then, now = _ts(10, 0), _ts(10, 3)
    _chain(history, then, 24000.0, [_Entry(24000, "CE", 4_000, 1000, _premium(24000, 24000, then, True))])
    _chain(history, now, 24000.0, [_Entry(24000, "CE", 5_300, 6000, _premium(24000, 24000, now, True) + 5)])
    _chain(history, _ts(15, 39, day="2026-09-02"), 23900.0, [_Entry(24000, "CE", 3_000, 0, 1.0)])
    whale.evaluate_window(history, "NIFTY", FUT, now)
    whale.eod(history, "2026-09-02", "NIFTY", FUT)
    alert = whale.maybe_alert(history, "NIFTY", now, DAY, 3.0, 1, 24000.0, None, {"strikes": []})
    _chain(history, _ts(10, 18), 24020.0, [_Entry(24000, "CE", 11_300, 6000, 1.0)])
    whale.fill_outcomes(history, _ts(10, 19))
    history.close()

    view = auction_views.whale(path, DAY)

    window = view["windows"]["NIFTY"]
    assert window["status"] == "ok" and window["as_of"] == now and window["alert_id"] == alert
    assert window["strikes"][0]["d_oi"] == 1300 and window["strikes"][0]["unusual"] == ["volume_ge_oi"]
    assert window["history"]["status"] == "insufficient" and "BANKNIFTY" not in view["windows"]
    assert view["eod"]["NIFTY"]["day"] == "2026-09-02" and view["eod"]["NIFTY"]["top_oi"][0][0] == 24000.0
    assert view["alerts"][0]["next_15"]["move_pts"] == 20.0 and view["alerts"][0]["next_60"] is None
    assert view["alerts"][0]["evidence"] == {"strikes": []}
    assert view["history"] == {"chain_days": 2, "required": 20}
    series = auction_views.whale_windows(path, "NIFTY", DAY)["series"]
    assert len(series) == 1 and series[0]["ts"] == now and series[0]["composite"] is None
    outcomes = auction_views.whale_alerts(path, DAY)
    assert outcomes["sessions"] == 1 and outcomes["precision"]["next_15"] == {"agreed": 1, "resolved": 1}
    assert outcomes["precision"]["next_60"] == {"agreed": 0, "resolved": 0}


def _queued(underlying, as_of, alert_id=None):
    return {"underlying": underlying, "as_of": as_of, "alert_id": alert_id, "composite_decayed": 2.7,
            "lot": 65, "net_option_delta": {"dn": 6.2e7, "sign": 1},
            "divergence": {"divergence": True, "ratio": 6.1},
            "strikes": [{"strike": 24000.0, "option_type": "CE", "d_oi": 1300}],
            "structures": [{"kind": "risk_reversal"}]}


def test_whale_queue_is_drained_into_telegram_keyed_per_underlying(tmp_path):
    path = str(tmp_path / "h.sqlite3")
    history = sqlite3.connect(path)
    whale.ensure_schema(history)
    alert = whale.maybe_alert(history, "NIFTY", _ts(10, 0), DAY, 3.0, 1, 24000.0, None, {})
    history.close()
    engine = SimpleNamespace(
        settings=SimpleNamespace(telegram_bot_token="t", telegram_chat_id="c", day_loss_alert_rupees=0,
                                 research_database_path=path),
        whale_alert_queue=[_queued("NIFTY", _ts(10, 0), alert), _queued("NIFTY", _ts(10, 15)),
                           _queued("BANKNIFTY", _ts(10, 15))],
        health=lambda: {"status": "connected", "last_tick_age_seconds": 1.0, "loop_lag_ms": {}},
        day_baseline_equity=lambda: None, token_expired=lambda: False)
    manager = alerts.AlertManager(engine)
    sent: list[tuple[str, str]] = []

    async def fake_alert(key, text):
        sent.append((key, text))
        return key
    manager._alert = fake_alert

    fired = asyncio.run(manager.check_once(datetime(2026, 9, 3, 8, 0, tzinfo=IST)))

    # One key per underlying, so the 30-minute cooldown has something to bite
    # on — a per-window suffix would mint a key that never collides.
    assert [key for key, _ in sent] == ["whale:NIFTY", "whale:NIFTY", "whale:BANKNIFTY"]
    assert fired == [key for key, _ in sent]
    assert all(text.endswith("Context, not a signal.") for _, text in sent)
    assert "option Δ-notional ₹6.2 cr long; futures opposed x6.1; 24000CE +20L; risk reversal" in sent[0][1]
    assert engine.whale_alert_queue == []
    assert sqlite3.connect(path).execute("SELECT sent FROM whale_alerts WHERE id = ?", (alert,)).fetchone() == (1,)

    # Unconfigured: nothing is sent, but the queue still empties.
    engine.settings.telegram_bot_token = ""
    engine.whale_alert_queue.append(_queued("NIFTY", _ts(11, 0)))
    assert asyncio.run(manager.check_once()) == [] and engine.whale_alert_queue == []
    assert len(sent) == 3


def test_a_quotes_rate_limit_stands_the_futures_oi_call_down(tmp_path):
    """A 429 retried every minute keeps the bucket full and sustains itself;
    futures OI is one input to Layer C and is worth less than the quota."""
    engine = _engine(tmp_path)
    attempts = []

    async def limited(symbols):
        attempts.append(symbols)
        raise RuntimeError("Client error '429 Too Many Requests' for url ...")

    engine.broker.quotes = limited
    asyncio.run(engine._chain_minute(_ts(9, 20)))
    assert len(attempts) == 1
    assert engine._quotes_blocked_until > 0
    assert engine._quotes_backoff == engine_module.QUOTES_BACKOFF_SECONDS * 2

    # A layer-A fault outranks it in the status line, so silence that to read
    # the stand-down message itself.
    engine._whale_minute = lambda *a, **k: ({}, None)
    asyncio.run(engine._chain_minute(_ts(9, 21)))
    assert len(attempts) == 1, "the stood-down minute must not spend a call"
    assert "stood down" in engine.chain_status["whale_error"]


# ---------------------------------------------------------------------------
# Restarts, rollovers and the tables that outlive them
# ---------------------------------------------------------------------------

def test_a_mid_session_restart_is_not_a_sell_and_the_rebuild_repairs_it(tmp_path):
    """cvd/ofi/volume/trades are day-cumulative and start again at zero when
    the process does. Differencing across that would book a three-minute
    window of negative volume and negative trades as a large sell."""
    connection = _db(tmp_path)
    t0 = _ts(11, 0)
    for index, (cvd, total, trades) in enumerate(((40_000, 500_000, 5_000), (41_000, 520_000, 5_200),
                                                  (42_000, 540_000, 5_400), (43_000, 560_000, 5_600))):
        _flow(connection, FUT, t0 + index * 60, cvd=cvd, ofi_cum=100 * index, oi=100_000,
              total=total, trades=trades)
    # 11:04 and 11:05: the counters restarted with the process.
    _flow(connection, FUT, t0 + 240, cvd=300, ofi_cum=5, oi=100_000, total=4_000, trades=40)
    _flow(connection, FUT, t0 + 300, cvd=700, ofi_cum=9, oi=100_000, total=9_000, trades=90)

    assert whale.futures_window(connection, FUT, t0 + 180)["delta_units"] == 3000
    assert whale.futures_window(connection, FUT, t0 + 300) is None
    assert whale.restarted_minutes(connection, FUT, DAY) == [t0 + 240, t0 + 300]

    # The nightly rebuild counts from the open throughout, and the live-wins
    # rule steps aside for exactly the rows it can prove are broken.
    rebuilt = [(FUT, t0 + 240, DAY, 24000.0, 44_000, 44_000, 0.0, 580_000, 5_800, 400.0, 10, 500.0, None, "raw"),
               (FUT, t0 + 300, DAY, 24000.0, 45_000, 45_000, 0.0, 600_000, 6_000, 500.0, 10, 500.0, None, "raw")]
    assert whale.save_flow_minutes(connection, rebuilt, "raw") == 2
    assert whale.futures_window(connection, FUT, t0 + 300) is None      # live rows still win

    assert whale.clear_restarted_minutes(connection, FUT, DAY) == 2
    whale.save_flow_minutes(connection, rebuilt, "raw")
    repaired = whale.futures_window(connection, FUT, t0 + 300)

    assert (repaired["delta_units"], repaired["volume"], repaired["trades"]) == (3000, 60_000, 600)
    assert whale.restarted_minutes(connection, FUT, DAY) == []


def test_day_totals_sum_increments_across_a_restart(tmp_path):
    connection = _db(tmp_path)
    t0 = _ts(11, 0)
    _flow(connection, FUT, t0, cvd=0, ofi_cum=0, oi=1, total=1000, trades=10)
    _flow(connection, FUT, t0 + 60, cvd=0, ofi_cum=0, oi=1, total=1500, trades=15)
    _flow(connection, FUT, t0 + 120, cvd=0, ofi_cum=0, oi=1, total=400, trades=4)     # restart
    _flow(connection, FUT, t0 + 180, cvd=0, ofi_cum=0, oi=1, total=900, trades=9)

    # MAX() would have said 1500/15 — only the larger side of the restart.
    assert whale._day_totals(connection, FUT, DAY) == (2400.0, 24)


def test_live_flow_stops_sampling_a_series_that_rolled_off(tmp_path):
    connection = _db(tmp_path)
    live = whale.LiveFlow([FUT])

    live.watch(["NSE:NIFTY26OCTFUT"])

    assert list(live.ofi) == ["NSE:NIFTY26OCTFUT"]
    live.sample(connection, _ts(10, 0), {}, {})
    assert [row[0] for row in connection.execute(
        "SELECT symbol FROM whale_flow_minutes")] == ["NSE:NIFTY26OCTFUT"]


def test_eod_will_not_difference_two_different_futures_series(tmp_path):
    history = _db(tmp_path)
    day1, day2 = "2026-09-02", "2026-09-03"
    september, october = FUT, "NSE:NIFTY26OCTFUT"
    for day, symbol, oi in ((day1, september, 200_000), (day2, october, 30_000)):
        _chain(history, _ts(15, 39, day=day), 24000.0,
               [_Entry(24000, "CE", 12_000, 0, 1.0, prev_oi=11_000)])
        _flow(history, symbol, _ts(15, 39, day=day), cvd=0, ofi_cum=0, oi=oi, day=day)
        whale.eod(history, day, "NIFTY", symbol)

    rows = {row[0]: (row[1], row[2], row[3]) for row in history.execute(
        "SELECT day, fut_symbol, fut_oi, fut_pdoi FROM whale_eod ORDER BY day")}

    assert rows[day1] == (september, 200_000, None)
    # The new near month against the expiring one would be a -170,000 unwind.
    assert rows[day2] == (october, 30_000, None)


def test_fill_outcomes_only_scans_the_session_it_is_given(tmp_path):
    connection = _db(tmp_path)
    yesterday = "2026-09-02"
    stale = whale.maybe_alert(connection, "NIFTY", _ts(15, 25, day=yesterday), yesterday,
                              3.0, 1, 24000.0, None, {})
    whale.maybe_alert(connection, "NIFTY", _ts(10, 0), DAY, 3.0, 1, 24000.0, None, {})
    _chain(connection, _ts(15, 40, day=yesterday), 24500.0, [_Entry(24000, "CE", 1, 0, 1.0)])
    _chain(connection, _ts(10, 15), 24040.0, [_Entry(24000, "CE", 1, 0, 1.0)])

    # Yesterday's 15:25 alert can never answer its 60-minute horizon, so
    # without the bound it is re-queried on every window of every session.
    assert whale.fill_outcomes(connection, _ts(10, 16)) == 1
    assert connection.execute("SELECT next_15 FROM whale_alerts WHERE id = ?", (stale,)).fetchone()[0] is None


def test_chain_days_is_a_ledger_and_old_sessions_keep_only_their_close(tmp_path):
    connection = _db(tmp_path)
    old_day = "2026-01-05"
    for minute in (30, 31, 32):
        _chain(connection, _ts(15, minute, day=old_day), 24000.0, [_Entry(24000, "CE", 1, 0, 1.0)])
    _chain(connection, _ts(10, 0), 24000.0, [_Entry(24000, "CE", 1, 0, 1.0)])

    assert connection.execute("SELECT COUNT(DISTINCT day) FROM chain_days").fetchone()[0] == 2

    removed = whale.condense_chain_snapshots(connection, DAY, keep_days=30)

    assert removed == 2
    assert [row[0] for row in connection.execute("SELECT ts FROM chain_snapshots ORDER BY ts")] == [
        _ts(15, 32, day=old_day), _ts(10, 0)]
    # The ledger is the history count, and condensation does not erase a day.
    assert connection.execute("SELECT COUNT(DISTINCT day) FROM chain_days").fetchone()[0] == 2
