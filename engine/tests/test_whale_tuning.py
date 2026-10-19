"""Per-instrument whale thresholds, features, outcomes and the calibration loop."""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from macd_trader import whale, whale_tuning as tuning
from macd_trader.models import Tick
from macd_trader.tick_store import TickStore

IST = ZoneInfo("Asia/Kolkata")
DAY = "2026-09-29"


def at(hour, minute, second=0, day=29):
    return datetime(2026, 9, day, hour, minute, second, tzinfo=IST)


def _history(path):
    connection = sqlite3.connect(path)
    connection.execute("""CREATE TABLE historical_candles (symbol TEXT, timeframe_seconds INTEGER,
        timestamp INTEGER, open REAL, high REAL, low REAL, close REAL, volume INTEGER)""")
    whale.ensure_schema(connection)
    tuning.ensure_schema(connection)
    return connection


def _prints(sizes, price=100.0):
    start = int(at(10, 0).timestamp() * 1000)
    return [(start + i * 1000, price, qty, 1) for i, qty in enumerate(sizes)]


def test_fingerprint_percentiles():
    fp = tuning.fingerprint(_prints(list(range(1, 1001))))
    assert fp["prints"] == 1000 and fp["p50"] == 501 and fp["p90"] == 901 and fp["p999"] == 1000
    assert fp["notional"] == 100 * sum(range(1, 1001))
    assert tuning.fingerprint(_prints([5] * 10)) is None       # too few prints to describe


def test_each_instrument_gets_thresholds_from_its_own_scale():
    liquid = {"sessions": 20, "p90": 2_000, "p999": 50_000, "notional": 30_000_000_000}
    thin = {"sessions": 20, "p90": 20, "p999": 400, "notional": 50_000_000}
    a = tuning.fit_params("NSE:HDFCBANK-EQ", DAY, liquid)
    b = tuning.fit_params("NSE:IEX-EQ", DAY, thin)
    assert a["large_min_qty"] > 40 * b["large_min_qty"]
    assert a["large_floor"] > b["large_floor"] >= 900_000              # thin names still clear the minimum
    assert a["source"] == b["source"] == "fitted"
    # Few sessions shrink toward the defaults; none means defaults.
    one = tuning.fit_params("NSE:HDFCBANK-EQ", DAY, {**liquid, "sessions": 1})
    assert one["large_min_qty"] == round(0.25 * 50_000)
    assert tuning.fit_params("NSE:HDFCBANK-EQ", DAY, {"sessions": 0})["source"] == "default"
    # The index futures keep the tracker's rule; an override always wins.
    assert tuning.fit_params("NSE:NIFTY26OCTFUT", DAY, liquid)["large_min_qty"] == 0
    pinned = tuning.fit_params("NSE:IEX-EQ", DAY, thin, override={"large_min_qty": 9_999, "bogus": 1})
    assert pinned["large_min_qty"] == 9_999 and "bogus" not in pinned and pinned["source"] == "fitted+override"


def test_tuned_thresholds_change_what_is_detected():
    sizes = [100] * 60 + [2_000] + [100] * 5
    prints = _prints(sizes, price=5_000.0)        # Rs 1 Cr: clears the default Rs 50 L floor
    assert [e.kind for e in whale.detect("NSE:X-EQ", DAY, prints)] == ["large_print"]
    assert whale.detect("NSE:X-EQ", DAY, prints, params={"large_min_qty": 5_000}) == []
    event = whale.detect("NSE:X-EQ", DAY, prints)[0]
    assert event.features["median"] == 100 and event.features["notional"] == 10_000_000


def test_sensitivity_steers_toward_the_target_rate(tmp_path):
    history = _history(str(tmp_path / "h.sqlite3"))
    stamp = int(at(10, 0).timestamp() * 1000)
    whale.save_events(history, DAY, [whale.WhaleEvent("NSE:X-EQ", stamp + i, "large_print", 1, 10, 1.0, 1.0, "")
                                     for i in range(20)])
    assert tuning.next_sensitivity(history, "NSE:X-EQ", DAY, 1.0) == 1.25       # 20/day: too noisy
    assert tuning.next_sensitivity(history, "NSE:Y-EQ", DAY, 1.0) == 1.0        # no history: unchanged
    assert tuning.next_sensitivity(history, "NSE:X-EQ", DAY, 4.0) == 4.0        # bounded


def _bars(history, symbol, start, closes):
    history.executemany("INSERT INTO historical_candles VALUES (?,?,?,?,?,?,?,?)",
                        [(symbol, 60, int((start + timedelta(minutes=i)).timestamp()), c, c, c, c, 1)
                         for i, c in enumerate(closes)])
    history.commit()


def test_outcomes_are_judged_against_the_instruments_own_move(tmp_path):
    history = _history(str(tmp_path / "h.sqlite3"))
    # A prior session with a steady 1% per 30 minutes: typical move ~1%.
    _bars(history, "NSE:X-EQ", at(9, 15, day=28), [100 * (1.01 ** (i / 30)) for i in range(375)])
    _bars(history, "NSE:X-EQ", at(10, 0), [100.0] * 30 + [100.8] * 90)     # +0.8% by 30 minutes
    buy = whale.WhaleEvent("NSE:X-EQ", int(at(10, 0).timestamp() * 1000), "large_print", 1, 1, 100.0, 1.0, "",
                           features={"notional": 1})
    sell = whale.WhaleEvent("NSE:X-EQ", int(at(10, 0).timestamp() * 1000), "constant_child", -1, 1, 100.0, 1.0, "",
                            features={"notional": 1})
    tuning.save_features(history, DAY, [buy, sell])
    assert tuning.label_outcomes(history, DAY) == 2
    rows = dict(history.execute("SELECT kind, hit FROM whale_event_features").fetchall())
    assert rows == {"large_print": 1, "constant_child": 0}
    ret30, typical = history.execute(
        "SELECT ret30, typical_move FROM whale_event_features WHERE kind='large_print'").fetchone()
    assert ret30 == pytest.approx(0.8) and typical == pytest.approx(1.0, rel=0.05)


def test_calibration_prefers_the_bin_that_worked_and_shrinks_small_ones(tmp_path):
    history = _history(str(tmp_path / "h.sqlite3"))
    rows = []
    for i in range(200):
        size = "5x+" if i % 2 else "<1x"
        hit = 1 if (size == "5x+" and i % 10 < 8) or (size == "<1x" and i % 10 < 3) else 0
        bins = {"kind": "large_print", "size_vs_p999": size, "time": "rare" if i == 0 else "mid"}
        rows.append(("NSE:X-EQ", DAY, i, "large_print", 1, "{}", json.dumps(bins), 0.1, 0.2, 0.3, 1.0, hit, "x"))
    history.executemany("INSERT INTO whale_event_features VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    history.commit()
    report = tuning.calibrate(history, DAY)
    weights = report["features"]["size_vs_p999"]
    assert weights["5x+"]["weight"] > 0 > weights["<1x"]["weight"]
    assert abs(report["features"]["time"]["rare"]["weight"]) < 0.1          # one event: shrunk
    w, base, run = tuning.latest_calibration(history)
    assert run == report["run_id"] and base == pytest.approx(0.6)     # 80% and 40% bins
    assert tuning.probability({"size_vs_p999": "5x+"}, w, base) > base > tuning.probability({"size_vs_p999": "<1x"}, w, base)
    assert report["top_quintile_hit_rate"] >= 0.7


def test_the_loop_end_to_end(tmp_path):
    with tempfile.TemporaryDirectory() as folder:
        store = TickStore(os.path.join(folder, "ticks.sqlite3"))
        cum, moment = 1_000_000, at(9, 15)
        for i in range(1200):
            size = 100 if i % 150 else 50_000
            cum += size
            store.add(Tick("NSE:X-EQ", 1_000.0, cum, timestamp=moment, bid=999.95, ask=1_000.0,
                           bid_qty=500, ask_qty=500, last_qty=size))
            moment += timedelta(seconds=2)
        store.flush_sync()
        ticks = sqlite3.connect(os.path.join(folder, "ticks.sqlite3"))
        history = _history(str(tmp_path / "h.sqlite3"))
        first = tuning.run(ticks, history, DAY)
        assert first["fingerprints"] == 1 and first["events"] >= 1 and first["refitted"] == 1
        params = tuning.load_params(history)["NSE:X-EQ"]
        assert params["source"] == "fitted" and params["large_min_qty"] > 0 and params["baseline_sessions"] == 1
        view = tuning.symbol_view(history, "NSE:X-EQ", DAY)
        assert view["fingerprints"][0]["p999"] == 50_000 and view["events"]
        assert tuning.overview(history)["runs"][0]["run_id"] == first["calibration_run"]
        pinned = tuning.set_override(history, "NSE:X-EQ", {"sensitivity": 2.0}, DAY)
        assert pinned["params"]["sensitivity"] == 2.0 and pinned["override"] == {"sensitivity": 2.0}


def test_a_stock_is_judged_net_of_the_market(tmp_path):
    history = _history(str(tmp_path / "h.sqlite3"))
    _bars(history, "NSE:X-EQ", at(9, 15, day=28), [100 * (1.01 ** (i / 30)) for i in range(375)])
    _bars(history, "NSE:X-EQ", at(10, 0), [100.0] * 30 + [101.0] * 90)            # +1.0%
    _bars(history, "NSE:NIFTY50-INDEX", at(10, 0), [100.0] * 30 + [101.2] * 90)   # the market +1.2%
    buy = whale.WhaleEvent("NSE:X-EQ", int(at(10, 0).timestamp() * 1000), "large_print", 1, 1, 100.0, 1.0, "",
                           features={"notional": 1})
    tuning.save_features(history, DAY, [buy])
    tuning.label_outcomes(history, DAY)
    ret30, hit = history.execute("SELECT ret30, hit FROM whale_event_features").fetchone()
    assert ret30 == pytest.approx(-0.2) and hit == 0      # rose, but less than the market
