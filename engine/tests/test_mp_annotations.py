"""Market Profile annotations with their measured base rates, and the current period vs its baseline."""
import sqlite3
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from macd_trader import agentic_features as feat
from macd_trader import mp_annotations as mpa

IST = ZoneInfo("Asia/Kolkata")
DAY = date(2026, 10, 13)


def bar(hm, lo, hi, v=100.0):
    return (hm, lo, hi, lo, hi, v)


def test_poor_high_tails_and_single_prints():
    # periods A..C trade 100-101; D spikes alone to 103 (a selling tail); E and F both touch 101.9-102 (poor high? no:
    # the top is D's single print) -- then a session whose top is touched by two periods is a poor high
    bars = [bar(555 + i, 100.0, 101.0) for i in range(90)] + [bar(645 + i, 101.0, 103.0) for i in range(30)]
    t = mpa.tpo(bars)
    assert t["tail_high_rows"] >= 2 and not t["poor_high"]
    poor = [bar(555 + i, 100.0, 101.0) for i in range(60)] + [bar(615 + i, 100.5, 101.0) for i in range(30)]
    assert mpa.tpo(poor)["poor_high"] and mpa.tpo(poor)["tail_high_rows"] == 0
    # a gap through 101-102 traded by one period only, inside the profile: an internal single-print zone
    zone = [bar(555 + i, 100.0, 101.0) for i in range(30)] + [bar(585 + i, 100.0, 103.0) for i in range(2)] + \
           [bar(615 + i, 102.0, 103.0) for i in range(30)]
    assert mpa.tpo(zone)["single_print_zones"]


def test_eighty_rule_states():
    prior = {"vah": 105.0, "val": 100.0}
    inside_open = [bar(555 + i, 102.0, 103.0) for i in range(60)]
    assert mpa.eighty_rule(inside_open, prior)["state"].startswith("opened inside")
    above = [bar(555 + i, 106.0, 107.0) for i in range(30)] + [bar(585 + i, 103.0, 104.0) for i in range(60)] + [bar(645, 103.0, 103.5)]
    r = mpa.eighty_rule(above, prior)
    assert r["rule_active"] and "46%" in r["state"] and r["far_side"] == 100.0
    reached = above + [bar(646 + i, 99.5, 101.0) for i in range(5)]
    assert "reached" in mpa.eighty_rule(reached, prior)["state"]


def test_period_reading_flags_abnormal_volume_range_and_flow():
    now = datetime(2026, 10, 13, 10, 29, tzinfo=IST)          # period C, 15 minutes in
    bars = [bar(615 + i, 22500.0, 22500.0 + (40 if i == 10 else 5), 60_000.0) for i in range(15)]
    lo_ts = datetime(2026, 10, 13, 10, 15, tzinfo=IST).timestamp()
    flow = [[lo_ts + 60 * i, 22500.0, 60_000.0, 50_000.0, 10_000.0, 0.0] for i in range(15)]
    r = mpa.period_reading(bars, flow, "NSE:NIFTY26OCTFUT", atr=300.0, day_volume=2_800_000.0, now=now)
    assert r["period"] == "C" and r["volume_abnormal"] and r["rvol"] > r["rvol_p90"]
    assert r["delta_share"] > 0.6 and r["delta_abnormal"]
    assert r["range_usual"] == mpa.BASELINES["index_range"]["C"]["range_med"]


def test_annotate_reads_minute_bars_and_writes_odds_into_the_notes(tmp_path):
    db = sqlite3.connect(tmp_path / "h.sqlite3")
    db.execute("""CREATE TABLE historical_candles (symbol TEXT, timeframe_seconds INTEGER, timestamp INTEGER, open REAL,
                  high REAL, low REAL, close REAL, volume INTEGER)""")
    sym, rows, d = "NSE:NIFTY26OCTFUT", [], DAY
    days = []
    while len(days) < 22:
        d = feat.previous_session(d)
        days.append(d)
    for k, day in enumerate(sorted(days)):
        for i in range(375):
            p = 22000 + 10 * k + 100 * (i / 374)
            rows.append((sym, 60, int((datetime(day.year, day.month, day.day, 9, 15, tzinfo=IST) + timedelta(minutes=i)).timestamp()),
                         p, p + 3, p - 3, p, 1000))
    for i in range(90):                                          # today: opens well above yesterday's value
        p = 22400 + i
        rows.append((sym, 60, int((datetime(2026, 10, 13, 9, 15, tzinfo=IST) + timedelta(minutes=i)).timestamp()), p, p + 2, p - 2, p, 3000))
    db.executemany("INSERT INTO historical_candles VALUES (?,?,?,?,?,?,?,?)", rows)
    db.commit()
    out = mpa.annotate(str(tmp_path / "h.sqlite3"), sym, datetime(2026, 10, 13, 10, 44, tzinfo=IST))
    assert out["prior_value"] and out["atr"] and out["period"]["period"] == "C"
    assert out["eighty_rule"]["state"].startswith("opened above prior value")
    assert any(n.startswith("period C") for n in out["notes"]) and any("80% rule" in n for n in out["notes"])
    assert out["period"]["rvol"] is not None                    # 20 sessions of the symbol's own volume as the baseline


def test_a_straight_run_through_value_is_reported_as_reached():
    prior = {"vah": 105.0, "val": 100.0}
    through = [bar(555 + i, 106.0, 107.0) for i in range(30)] + [bar(585 + i, 98.0, 99.0) for i in range(30)]
    assert "traded through it to the far side" in mpa.eighty_rule(through, prior)["state"]
