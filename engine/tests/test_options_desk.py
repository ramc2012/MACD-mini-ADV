"""Options & flow desk: NTM IV, gamma exposure, spectrum, writers' P&L, IV range, MFLOW and smart candles."""
import math
import sqlite3
from datetime import date, datetime

import pytest

from macd_trader import oi_capture, option_greeks as og, options_desk as od, straddle_views, whale
from macd_trader.straddle_views import IST

EXPIRY = "2026-11-17"


def _ts(day: str, hhmm: str) -> int:
    return int(datetime.fromisoformat(f"{day}T{hhmm}:00").replace(tzinfo=IST).timestamp())


def _price(spot, strike, ts, vol, call, expiry=EXPIRY):
    years = og.years_to_expiry(expiry, datetime.fromtimestamp(ts, IST))
    return round(og.price(spot, strike, years, vol, call), 2)


def _chain_db(tmp_path, snapshots, expiry=EXPIRY):
    """snapshots: (ts, spot, vix, {(strike, side): (vol_iv, volume, oi, prev_oi)})"""
    path = str(tmp_path / "h.sqlite3")
    c = sqlite3.connect(path)
    whale.ensure_schema(c)
    with c:
        for ts, spot, vix, legs in snapshots:
            for (k, side), (iv, volume, oi, prev_oi) in legs.items():
                ltp = _price(spot, k, ts, iv, side == "CE", expiry)
                c.execute("""INSERT INTO chain_snapshots (ts, underlying, expiry, strike, option_type, symbol, ltp, volume,
                             oi, spot, bid, ask, prev_oi, fp, vix) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                          (ts, "NIFTY", expiry, k, side, f"N{k:.0f}{side}{expiry}", ltp, volume, oi, spot, ltp, ltp,
                           prev_oi, spot + 30, vix))
            c.execute("INSERT OR IGNORE INTO chain_days VALUES (?, 'NIFTY')",
                      (datetime.fromtimestamp(ts, IST).date().isoformat(),))
    c.close()
    straddle_views._cache.clear()
    return path


def _legs(strikes, iv=0.15, volume=1000, oi=10_000, prev_oi=10_000, **override):
    out = {(k, s): (iv, volume, oi, prev_oi) for k in strikes for s in ("CE", "PE")}
    out.update(override)
    return out


STRIKES = [24800 + 50 * i for i in range(9)]          # 24800 .. 25200, ATM 25000


def test_ntm_iv_recovers_the_volatility_the_premiums_were_priced_at(tmp_path):
    d = "2026-11-13"
    path = _chain_db(tmp_path, [(_ts(d, "09:16"), 25000.0, 16.0, _legs(STRIKES, iv=0.16)),
                                (_ts(d, "10:00"), 25010.0, 15.5, _legs(STRIKES, iv=0.13))])
    out = od.chain_view(path, "NIFTY", day=d, today=date(2026, 11, 13))
    assert [round(p["ntm_iv"]) for p in out["volx"]] == [16, 13]
    assert out["volx"][0]["vix"] == 16.0
    assert out["atm"] == 25000 and out["step"] == 50
    em = out["expected_move"]
    assert em["open"]["ntm_iv"] == pytest.approx(16, abs=0.1)
    # 09:16 is 1 minute in, 10:00 is 45 of 375: the band widens with sqrt(elapsed)
    width = em["band"][1]["upper"] - 25000
    assert width == pytest.approx(25000 * 0.16 * math.sqrt(1 / 365) * math.sqrt(45 / 375), rel=0.01)
    assert em["moves"]["1 day"]["points"] == pytest.approx(25010 * 0.13 * math.sqrt(1 / 365), rel=0.01)


def test_gamma_signs_walls_and_zero_gamma(tmp_path):
    d = "2026-11-13"
    legs = _legs(STRIKES, oi=1_000)
    legs[(25200, "CE")] = (0.15, 1000, 400_000, 400_000)   # big call OI above spot
    legs[(24800, "PE")] = (0.15, 1000, 400_000, 400_000)   # big put OI below spot
    path = _chain_db(tmp_path, [(_ts(d, "11:00"), 25000.0, 15.0, legs)])
    g = od.chain_view(path, "NIFTY", day=d)["gamma"]
    by = {c["strike"]: c for c in g["strikes"]}
    assert by[25200]["call"] > 0 and by[24800]["put"] < 0
    assert g["call_wall"] == 25200 and g["put_wall"] == 24800
    # symmetric walls: net GEX turns from negative (near the put wall) to positive (near the call wall) close to spot
    assert g["zero_gamma"] is not None and abs(g["zero_gamma"] - 25000) < 100
    assert g["timeline"][-1]["net"] == g["net"]


def test_spectrum_counts_volume_since_each_contracts_previous_snapshot(tmp_path):
    d = "2026-11-13"
    s1 = _legs(STRIKES[:3], volume=100)
    s2 = _legs(STRIKES[:3], volume=100)
    s2[(24800, "CE")] = (0.15, 400, 10_000, 10_000)            # +300 CE
    s2[(24850, "PE")] = (0.15, 150, 10_000, 10_000)            # +50 PE
    s2[(24950, "CE")] = (0.15, 9_999, 10_000, 10_000)          # first seen mid-day: adds nothing
    path = _chain_db(tmp_path, [(_ts(d, "09:16"), 24850.0, 15.0, s1), (_ts(d, "09:20"), 24850.0, 15.0, s2)])
    sp = od.chain_view(path, "NIFTY", day=d)["spectrum"]
    assert [(p["ce"], p["pe"]) for p in sp["points"]] == [(300, 300), (600, 350)]
    assert sp["pcr"] == round(350 / 600, 3)
    top_ce = [z for z in sp["zones"] if z["type"] == "CE"][0]
    assert top_ce["strike"] == 24950 and top_ce["volume"] == 9_999


def test_writers_pnl_on_added_oi_uses_the_average_premium_while_it_was_added(tmp_path):
    d = "2026-11-13"
    k = 25000
    def snap(hhmm, spot, oi):
        legs = _legs([k], oi=oi, prev_oi=1_000)
        return (_ts(d, hhmm), spot, 15.0, legs)
    path = _chain_db(tmp_path, [snap("09:16", 25000.0, 1_000), snap("09:30", 25000.0, 2_000),
                                snap("10:00", 24900.0, 3_000), snap("11:00", 25100.0, 3_000)])
    out = od.chain_view(path, "NIFTY", day=d)
    w = {r["strike"]: r for r in out["writers"]["strikes"]}[k]["CE"]
    p930 = _price(25000.0, k, _ts(d, "09:30"), 0.15, True)
    p1000 = _price(24900.0, k, _ts(d, "10:00"), 0.15, True)
    p1100 = _price(25100.0, k, _ts(d, "11:00"), 0.15, True)
    avg = (1000 * p930 + 1000 * p1000) / 2000
    assert w["net_added"] == 2_000 and w["avg_premium"] == pytest.approx(avg, abs=0.01)
    assert w["pnl"] == round(2_000 * (avg - p1100))                 # spot rallied: call writers are behind
    assert w["pnl"] < 0
    assert out["writers"]["ce_total"] == w["pnl"]


def test_table_has_greeks_and_oi_changes(tmp_path):
    d = "2026-11-13"
    s1 = _legs(STRIKES, oi=5_000, prev_oi=4_000)
    s2 = _legs(STRIKES, oi=7_000, prev_oi=4_000)
    path = _chain_db(tmp_path, [(_ts(d, "09:16"), 25000.0, 15.0, s1), (_ts(d, "12:00"), 25000.0, 15.0, s2)])
    rows = {r["strike"]: r for r in od.chain_view(path, "NIFTY", day=d)["table"]}
    ce, pe = rows[25000]["CE"], rows[25000]["PE"]
    assert ce["oi_change"] == 3_000 and ce["oi_change_today"] == 2_000
    assert ce["iv"] == pytest.approx(15, abs=0.1) and 0.45 < ce["delta"] < 0.6 and -0.55 < pe["delta"] < -0.4
    assert ce["theta"] < 0 and ce["vega"] > 0


def test_snapshots_after_the_close_are_left_out(tmp_path):
    d = "2026-11-13"
    path = _chain_db(tmp_path, [(_ts(d, "15:29"), 25000.0, 15.0, _legs(STRIKES)), (_ts(d, "15:38"), 25000.0, 15.0, _legs(STRIKES, iv=0.4))])
    out = od.chain_view(path, "NIFTY", day=d)
    assert out["as_of"] == _ts(d, "15:29") and len(out["volx"]) == 1


def test_at_picks_an_earlier_moment(tmp_path):
    d = "2026-11-13"
    path = _chain_db(tmp_path, [(_ts(d, "09:16"), 25000.0, 15.0, _legs(STRIKES)),
                                (_ts(d, "12:00"), 25100.0, 15.0, _legs(STRIKES))])
    out = od.chain_view(path, "NIFTY", day=d, at=_ts(d, "10:00"))
    assert out["as_of"] == _ts(d, "09:16") and out["spot"] == 25000.0
    assert len(out["volx"]) == 2                                   # the curves still show the whole session


def test_iv_range_takes_each_sessions_close_and_skips_expiry_day(tmp_path):
    snaps = []
    for d, iv in (("2026-11-10", 0.20), ("2026-11-11", 0.10), ("2026-11-12", 0.15)):
        snaps.append((_ts(d, "15:20"), 25000.0, 15.0, _legs(STRIKES, iv=iv)))
    snaps.append((_ts("2026-11-12", "15:29"), 25000.0, 15.0, _legs(STRIKES, iv=0.50)))   # after 15:25: ignored
    path = _chain_db(tmp_path, snaps)
    r = od.iv_range(path, "NIFTY", date(2026, 11, 12), spot=25000.0, days_to_expiry=5)
    assert [round(p["iv"]) for p in r["series"]] == [20, 10, 15]
    assert r["high"] == pytest.approx(20, abs=0.1) and r["low"] == pytest.approx(10, abs=0.1)
    assert r["percentile"] == pytest.approx(66.7, abs=0.1) and r["rank"] == pytest.approx(50, abs=1)
    assert r["projected"]["1 day"]["points"] == pytest.approx(25000 * 0.15 * math.sqrt(1 / 365), rel=0.01)
    # on its expiry day a series is not used (expiry > day)
    path = _chain_db(tmp_path, [(_ts("2026-11-17", "15:20"), 25000.0, 15.0, _legs(STRIKES))])     # adds the 17th
    assert od.iv_range(path, "NIFTY", date(2026, 11, 17))["series"][-1]["day"] == "2026-11-12"


def _minutes(day, bars):
    """bars: (hhmm, open, high, low, close, volume, delta)"""
    return [(_ts(day, t), o, h, l, c, v, (v + dl) // 2, (v - dl) // 2, dl) for t, o, h, l, c, v, dl in bars]


def test_mflow_assigns_volume_to_the_next_oi_change():
    d = "2026-11-13"
    start = _ts(d, "00:00")
    minutes = _minutes(d, [("09:15", 100, 101, 99, 100, 10, 0),
                           ("09:16", 100, 102, 100, 102, 20, 0),      # OI up, price up -> long build-up (10 + 20)
                           ("09:17", 102, 102, 100, 101, 5, 0),
                           ("09:18", 101, 101, 99, 99, 7, 0),        # OI down, price down -> long unwinding (12)
                           ("09:19", 99, 100, 99, 100, 3, 0)])       # no OI change: unassigned
    oi = [(_ts("2026-11-12", "15:29"), 1_000), (_ts(d, "09:16") + 30, 1_100), (_ts(d, "09:18") + 10, 1_050)]
    m = od.mflow(minutes, oi, start, tf=1)
    assert m["classes"] == {"long_buildup": 30, "short_buildup": 0, "short_covering": 0, "long_unwinding": 12}
    assert m["unassigned"] == 3 and m["new"] == 30 and m["exit"] == 12
    assert m["opening_oi"] == 1_000 and m["oi"] == 1_050 and m["oi_change"] == 50
    assert od.mflow(minutes, [], start, tf=1)["available"] is False


def test_smart_candles_ib_strength_and_balance_clusters():
    d = "2026-11-13"
    bars = [("09:15", 100, 110, 95, 108, 100, 60)]
    bars += [(f"10:{m:02d}", 105, 107, 104, 106, 50, -10) for m in range(20, 26)]   # rotation
    bars += [("10:26", 106, 125, 106, 124, 80, 70)]                                 # breakout
    c = od.smart_candles(_minutes(d, bars), tf=1)
    assert c["ib"]["high"] == 110 and c["ib"]["low"] == 95
    assert c["bars"][0]["strength"] == 0.6
    assert len(c["clusters"]) == 1 and c["clusters"][0]["bars"] == 6
    assert c["clusters"][0]["low"] == 104 and c["clusters"][0]["high"] == 107


def test_futures_flow_reads_the_tick_store_and_oi_minutes(tmp_path):
    d = "2026-11-13"
    ticks = str(tmp_path / "t.sqlite3")
    c = sqlite3.connect(ticks)
    c.executescript("""CREATE TABLE tick_symbols (id INTEGER PRIMARY KEY, symbol TEXT UNIQUE);
        CREATE TABLE tick_minute_flow (symbol_id INTEGER, minute_ts INTEGER, open REAL, high REAL, low REAL, close REAL,
        volume INTEGER, buy_volume INTEGER, sell_volume INTEGER, delta INTEGER);""")
    with c:
        c.execute("INSERT INTO tick_symbols VALUES (1, 'NSE:NIFTY26NOVFUT'), (2, 'NSE:NIFTY26NOV25000CE')")
        for i, row in enumerate(_minutes(d, [("09:15", 100, 101, 99, 100, 10, 2), ("09:16", 100, 103, 100, 103, 20, 8)])):
            c.execute("INSERT INTO tick_minute_flow VALUES (1,?,?,?,?,?,?,?,?,?)", row)
    c.close()
    research = str(tmp_path / "h.sqlite3")
    c = sqlite3.connect(research)
    oi_capture.ensure_schema(c)
    with c:
        c.execute("INSERT INTO oi_minutes (ts, symbol, oi) VALUES (?, 'NSE:NIFTY26NOVFUT', 500), (?, 'NSE:NIFTY26NOVFUT', 450)",
                  (_ts(d, "09:00"), _ts(d, "09:16") + 5))
    c.close()
    straddle_views._cache.clear()
    out = od.futures_flow(ticks, research, "nifty", day=d, tf=5)
    assert out["symbol"] == "NSE:NIFTY26NOVFUT"
    assert out["mflow"]["classes"]["short_covering"] == 30
    assert out["candles"]["bars"][0]["volume"] == 30 and out["candles"]["bars"][0]["delta"] == 10
    assert "note" in od.futures_flow(ticks, research, "FINNIFTY", day=d)


def test_fast_iv_matches_the_bisection_solver():
    import random
    rng = random.Random(4)
    for _ in range(3000):
        k, t = rng.uniform(22500, 27500), rng.choice([2e-5, 2e-4, 0.002, 0.01, 0.05, 0.3])
        call = rng.random() < 0.5
        premium = og.price(25000, k, t, rng.uniform(0.03, 1.5), call) * rng.uniform(0.8, 1.2)
        slow, fast = og.implied_vol(premium, 25000, k, t, call), od._iv(premium, 25000, k, t, call)
        assert (slow is None) == (fast is None)
        if slow is not None:
            assert fast == pytest.approx(slow, abs=1e-4)
            assert og.price(25000, k, t, fast, call) == pytest.approx(premium, abs=1e-6 * max(1.0, premium))


def test_a_refresh_solves_only_the_new_snapshots(tmp_path, monkeypatch):
    d = "2026-11-13"
    path = _chain_db(tmp_path, [(_ts(d, f"09:{m}"), 25000 + i, 15.0, _legs(STRIKES)) for i, m in enumerate((20, 25, 30))])
    od._MEMO.clear()
    calls = []
    solver = od._iv
    monkeypatch.setattr(od, "_iv", lambda *a: calls.append(1) or solver(*a))
    first = od.chain_view(path, "NIFTY", day=d)
    cold = len(calls)
    straddle_views._cache.clear()               # the 30-second cache has expired; no new snapshot yet
    calls.clear()
    again = od.chain_view(path, "NIFTY", day=d)
    assert again["volx"] == first["volx"] and again["gamma"] == first["gamma"]
    assert 0 < len(calls) < cold / 2            # only the moment's own gamma is solved again
    c = sqlite3.connect(path)
    with c:
        for (k, side), (iv, volume, oi, prev_oi) in _legs(STRIKES).items():
            ltp = _price(25010, k, _ts(d, "09:35"), iv, side == "CE")
            c.execute("INSERT INTO chain_snapshots (ts, underlying, expiry, strike, option_type, symbol, ltp, volume, oi, spot,"
                      " bid, ask, prev_oi, fp, vix) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (_ts(d, "09:35"), "NIFTY", EXPIRY, k, side, f"N{k:.0f}{side}{EXPIRY}", ltp, volume, oi, 25010, ltp, ltp,
                       prev_oi, 25040, 15.0))
    c.close()
    straddle_views._cache.clear()
    grown = od.chain_view(path, "NIFTY", day=d)
    assert len(grown["volx"]) == 4 and grown["volx"][:3] == first["volx"]
    assert grown["volx"][3]["ntm_iv"] == pytest.approx(15.0, abs=0.1)


def test_expiries_merge_earlier_sessions_with_todays_rows(tmp_path, monkeypatch):
    path = _chain_db(tmp_path, [(_ts("2026-11-12", "15:00"), 25000, 15.0, _legs(STRIKES)),
                                (_ts("2026-11-13", "09:20"), 25010, 15.0, _legs(STRIKES)),
                                (_ts("2026-11-13", "09:25"), 25020, 15.0, _legs(STRIKES))])
    monkeypatch.setattr(straddle_views.time, "time", lambda: _ts("2026-11-13", "09:30"))   # "today" is 13 Nov
    listed = straddle_views.expiries(path, "NIFTY")
    assert listed == [{"expiry": EXPIRY, "first_ts": _ts("2026-11-12", "15:00"), "last_ts": _ts("2026-11-13", "09:25"),
                       "snapshots": 3}]
    assert ("expiries-past", path, "NIFTY", _ts("2026-11-13", "00:00")) in straddle_views._cache


def test_warm_builds_views_and_survives_a_missing_store(tmp_path):
    d = "2026-11-13"
    path = _chain_db(tmp_path, [(_ts(d, "09:20"), 25000, 15.0, _legs(STRIKES))])
    od.warm(path, ("NIFTY", "NOSUCH"))
    assert any(k[0] == "chain_view" and k[2] == "NIFTY" for k in straddle_views._cache)
    od.warm(str(tmp_path / "missing.sqlite3"))            # no exception
