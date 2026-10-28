"""ATM straddle / OI series from stored chains: rolling ATM, per-contract volume deltas, anchored VWAP resets."""
import sqlite3
from datetime import date, datetime, timedelta

from macd_trader import straddle_views, whale
from macd_trader.straddle_views import IST


def _ts(day: str, hhmm: str) -> int:
    return int(datetime.fromisoformat(f"{day}T{hhmm}:00").replace(tzinfo=IST).timestamp())


def _db(tmp_path, snapshots):
    path = str(tmp_path / "h.sqlite3")
    c = sqlite3.connect(path)
    whale.ensure_schema(c)
    with c:
        for ts, spot, legs in snapshots:
            for (k, side), (ltp, vol, oi) in legs.items():
                c.execute("INSERT INTO chain_snapshots (ts, underlying, expiry, strike, option_type, symbol, ltp, volume, oi, spot)"
                          " VALUES (?,?,?,?,?,?,?,?,?,?)", (ts, "X", "2026-11-03", k, side, f"X{k:.0f}{side}", ltp, vol, oi, spot))
            c.execute("INSERT OR IGNORE INTO chain_days VALUES (?, 'X')", (datetime.fromtimestamp(ts, IST).date().isoformat(),))
    c.close()
    straddle_views._cache.clear()
    return path


def leg(ce, pe, vce, vpe, oce=100, ope=200):
    return {"CE": (ce, vce, oce), "PE": (pe, vpe, ope)}


def chain(spot, by_strike):
    return {(k, side): v for k, sides in by_strike.items() for side, v in sides.items()}


def test_rolling_atm_volume_deltas_and_day_vwap(tmp_path):
    d = "2026-10-28"
    path = _db(tmp_path, [
        (_ts(d, "09:16"), 100.4, chain(100.4, {100: leg(5, 4, 10, 10), 102: leg(2, 7, 5, 5)})),
        (_ts(d, "09:17"), 101.6, chain(101.6, {100: leg(7, 3, 30, 10), 102: leg(3, 6, 25, 15)})),  # ATM moves to 102
        (_ts(d, "09:18"), 101.7, chain(101.7, {100: leg(7, 3, 30, 10), 102: leg(3, 6, 25, 15)})),  # nothing traded
    ])
    out = straddle_views.series(path, "X", span="day", today=date(2026, 10, 28))
    p = out["points"]
    assert [x["strike"] for x in p] == [100, 102, 102]
    assert [x["volume"] for x in p] == [20, 30, 0]          # 102: CE 25-5, PE 15-5 since its own previous snapshot
    assert p[0]["vwap_day"] == 9 and p[1]["vwap_day"] == round((9 * 20 + 9 * 30) / 50, 2) == p[2]["vwap_day"]
    assert p[1]["ce_oi"] == 100 and p[1]["pe_oi"] == 200


def test_day_vwap_restarts_but_week_and_month_carry(tmp_path):
    path = _db(tmp_path, [
        (_ts("2026-10-27", "15:29"), 100, chain(100, {100: leg(6, 6, 10, 10)})),
        (_ts("2026-10-28", "09:16"), 100, chain(100, {100: leg(3, 3, 10, 10)})),
    ])
    p = straddle_views.series(path, "X", span="week", day="2026-10-28")["points"]
    assert p[-1]["vwap_day"] == 6 and p[-1]["vwap_week"] == 9 and p[-1]["vwap_month"] == 9
    p = straddle_views.series(path, "X", span="day", day="2026-10-28")["points"]
    assert len(p) == 1                                        # the day range shows the day; anchors still saw Monday


def test_fixed_strike_and_unknown_volume_after_a_gap(tmp_path):
    d = "2026-10-28"
    path = _db(tmp_path, [
        (_ts(d, "09:16"), 100, chain(100, {100: leg(5, 5, 10, 10)})),
        (_ts(d, "09:17"), 100, chain(100, {100: leg(5, 5, 20, 20), 103: leg(1, 4, 50, 50)})),   # 103 first seen mid-day
    ])
    p = straddle_views.series(path, "X", span="day", strike=103.0)["points"]
    assert len(p) == 1 and p[0]["volume"] == 0 and p[0]["vwap_day"] is None


def test_hysteresis_holds_the_atm_and_band_oi_is_smooth_across_a_switch(tmp_path):
    # Strikes step 10; the multiples of 20 carry 10x the OI. Spot 1010 -> 1016 (held) -> 1019.5 (past 3/4 step).
    d = "2026-10-28"
    def strikes(spot):
        return chain(spot, {k: leg(5, 5, 1, 1, oce=1000 if k % 20 == 0 else 100, ope=1000 if k % 20 == 0 else 100)
                            for k in range(900, 1120, 10)})        # a full index-size chain (30+ legs)
    path = _db(tmp_path, [(_ts(d, "09:16"), 1010.0, strikes(1010.0)), (_ts(d, "09:17"), 1016.0, strikes(1016.0)),
                          (_ts(d, "09:18"), 1019.5, strikes(1019.5))])
    pts = straddle_views.series(path, "X", span="day", day=d, today=date(2026, 10, 28))["points"]
    assert [p["strike"] for p in pts] == [1010, 1010, 1020]      # 1016 stays on 1010; 1019.5 is past 3/4 of a step
    assert pts[1]["ce_oi"] == 100 and pts[2]["ce_oi"] == 1000    # the single-strike line jumps 10x on the switch ...
    # ... the OI band is the session's opening ATM +-3 strikes (980-1040), fixed all day, so it does not move at all
    assert [p["band"] for p in pts] == [[980, 1040]] * 3 and {p["ce_oi_band"] for p in pts} == {1000 + 100 + 1000 + 100 + 1000 + 100 + 1000}


def test_a_partial_snapshot_is_skipped(tmp_path):
    d = "2026-10-28"
    full = chain(1010, {k: leg(5, 5, 1, 1) for k in range(980, 1050, 10)})
    partial = {key: v for key, v in full.items() if key[0] in (1000, 1010)}
    path = _db(tmp_path, [(_ts(d, "09:16"), 1010.0, full), (_ts(d, "09:17"), 1010.0, partial), (_ts(d, "09:18"), 1010.0, full)])
    out = straddle_views.series(path, "X", span="day", day=d, today=date(2026, 10, 28))
    assert len(out["points"]) == 2 and out["skipped_partial_snapshots"] == 1
