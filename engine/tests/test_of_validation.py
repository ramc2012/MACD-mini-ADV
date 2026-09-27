"""Tests for research/of_validation.py.

The research script is not importable as a package module (it lives outside
src/ and is designed to be piped into the container), so it is loaded by path.
"""
from __future__ import annotations

import importlib.util
import random
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

IST = timezone(timedelta(hours=5, minutes=30))
_PATH = Path(__file__).resolve().parents[1] / "research" / "of_validation.py"


def _load():
    spec = importlib.util.spec_from_file_location("of_validation", _PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ofv = _load()


def _ts(day: str, hh: int, mm: int) -> int:
    y, mo, d = (int(x) for x in day.split("-"))
    return int(datetime(y, mo, d, hh, mm, tzinfo=IST).timestamp())


def _bar(ts, close, *, open_=None, volume=1000, delta=0, trades=10,
         quote_n=None, unclassified=0, spread=0.05, vwap=None, sid=1):
    buy = (volume + delta) // 2
    sell = volume - buy
    return {
        "sid": sid, "ts": ts, "open": close if open_ is None else open_,
        "close": close, "vwap": close if vwap is None else vwap,
        "volume": volume, "buy": buy, "sell": sell, "delta": delta,
        "trades": trades, "quote_n": trades if quote_n is None else quote_n,
        "mid_n": 0, "tick_n": 0, "unclassified": unclassified, "spread": spread,
    }


# --------------------------------------------------------------------------
# statistics
# --------------------------------------------------------------------------

def test_percentile_interpolates_and_refuses_empty():
    assert ofv.percentile([], 0.5) is None
    assert ofv.percentile([7.0], 0.25) == 7.0
    assert ofv.median([1.0, 2.0, 3.0, 4.0]) == 2.5
    assert ofv.percentile([0.0, 10.0], 0.25) == 2.5


def test_spearman_is_rank_based_not_level_based():
    xs = [1.0, 2.0, 3.0, 4.0, 5.0]
    ys = [1.0, 4.0, 9.0, 16.0, 25.0]      # monotone but very non-linear
    assert ofv.spearman(xs, ys) == pytest.approx(1.0)
    assert ofv.spearman(xs, [-y for y in ys]) == pytest.approx(-1.0)


def test_spearman_returns_none_for_a_constant_series():
    """A flat feature has no rank order; that must be absent, not zero."""
    assert ofv.spearman([1.0] * 8, list(range(8))) is None


def test_partial_spearman_removes_the_control():
    """x and y share only their common driver z -> partial ~ 0, raw does not."""
    rng = random.Random(7)
    zs = [rng.gauss(0, 1) for _ in range(400)]
    xs = [z + rng.gauss(0, 0.4) for z in zs]
    ys = [z + rng.gauss(0, 0.4) for z in zs]
    assert ofv.spearman(xs, ys) > 0.7          # they look strongly related
    assert abs(ofv.partial_spearman(xs, ys, zs)) < 0.12   # ...but only via z


def test_partial_spearman_is_absent_when_it_cannot_be_computed():
    """A control that explains a side perfectly leaves nothing to partial out;
    that must come back as None, never as a zero that reads as a result."""
    zs = [float(i) for i in range(60)]
    assert ofv.partial_spearman([z * 3 for z in zs], [z * 2 for z in zs], zs) is None


def test_mean_se_t_withholds_a_t_stat_it_cannot_compute():
    mu, sd, se, t = ofv.mean_se_t([0.5])
    assert mu == 0.5 and sd is None and se is None and t is None


# --------------------------------------------------------------------------
# feature construction: causality and gap handling
# --------------------------------------------------------------------------

def _straight_session(n=40, day="2026-08-21"):
    return [_bar(_ts(day, 9, 15) + 60 * i, 100.0 + i, delta=100 * (i % 3 - 1))
            for i in range(n)]


def test_targets_never_span_a_gap_in_the_tape():
    bars = _straight_session(40)
    del bars[30]                                  # one minute never traded
    pairs = ofv.build_pairs(bars, horizon=1)
    stamps = {p["ts"] for p in pairs}
    missing = _ts("2026-08-21", 9, 15) + 60 * 30
    # the bar before the hole has no bar exactly one minute later, so it
    # cannot produce a pair; stretching it to the next print would silently
    # turn a 1-minute target into a 2-minute one.
    assert missing - 60 not in stamps
    assert missing not in stamps


def test_inactive_bars_take_no_part():
    bars = _straight_session(40)
    bars[10]["volume"] = 0
    bars[10]["trades"] = 0
    pairs = ofv.build_pairs(bars, horizon=1)
    assert bars[10]["ts"] not in {p["ts"] for p in pairs}


def test_expanding_stats_are_causal():
    """delta_z at bar t must not move when a LATER bar changes."""
    bars = _straight_session(40)
    first = {p["ts"]: p["delta_z"] for p in ofv.build_pairs(bars, 1)}
    bars[35]["delta"] = 10 ** 9                   # a huge print, late in the day
    second = {p["ts"]: p["delta_z"] for p in ofv.build_pairs(bars, 1)}
    early = _ts("2026-08-21", 9, 15) + 60 * 25
    assert first[early] == second[early]


def test_expanding_stats_are_absent_before_their_floor():
    pairs = ofv.build_pairs(_straight_session(40), 1)
    early = [p for p in pairs
             if p["ts"] < _ts("2026-08-21", 9, 15) + 60 * ofv.MIN_PRIOR_BARS]
    assert early and all(p["delta_z"] is None and p["rvol"] is None
                         for p in early)


def test_the_three_targets_use_the_prices_they_claim_to():
    day = "2026-08-21"
    bars = [_bar(_ts(day, 9, 15) + 60 * i, 100.0, open_=100.0, vwap=100.0)
            for i in range(40)]
    bars[30] = _bar(bars[30]["ts"], 110.0, open_=105.0, vwap=107.0)
    pairs = {p["ts"]: p for p in ofv.build_pairs(bars, horizon=1)}
    p = pairs[bars[29]["ts"]]
    assert p["close"] == pytest.approx(110.0 / 100.0 - 1)   # close(t+1)/close(t)
    assert p["oc"] == pytest.approx(110.0 / 105.0 - 1)      # close(t+1)/open(t+1)
    assert p["vwap"] == pytest.approx(107.0 / 100.0 - 1)    # vwap(t+1)/vwap(t)


def test_oc_target_excludes_the_feature_bar_close():
    """The tradeable target must not contain close(t) -- that is the whole
    point of it: close(t) is where the bid-ask bounce enters."""
    day = "2026-08-21"
    bars = [_bar(_ts(day, 9, 15) + 60 * i, 100.0, open_=100.0) for i in range(40)]
    baseline = {p["ts"]: p["oc"] for p in ofv.build_pairs(bars, 1)}
    bars[29]["close"] = 90.0                       # move ONLY close(t)
    moved = {p["ts"]: p["oc"] for p in ofv.build_pairs(bars, 1)}
    assert moved[bars[29]["ts"]] == baseline[bars[29]["ts"]]


# --------------------------------------------------------------------------
# per-symbol summary: thin symbols must not fake a measurement
# --------------------------------------------------------------------------

def test_thin_symbol_publishes_none_not_zero():
    bars = [_bar(_ts("2026-08-21", 9, 15) + 60 * i, 100.0 + i) for i in range(4)]
    s = ofv.symbol_summary("NSE:THIN26AUG100CE", {"2026-08-21": bars})
    assert s["bars"] == 4
    assert s["volume"]["median"] is not None       # 4 >= MIN_BARS_FOR_MEDIAN
    assert s["volume"]["p25"] is None              # 4 <  MIN_BARS_FOR_IQR
    assert s["volume"]["p75"] is None
    assert s["instrument"] == "OPT"


def test_summary_reports_classification_quality_shares():
    bars = [_bar(_ts("2026-08-21", 9, 15) + 60 * i, 100.0, volume=1000,
                 delta=0, trades=10, quote_n=6, unclassified=2)
            for i in range(30)]
    # _bar splits volume evenly, so buy+sell == volume -> fully classified
    s = ofv.symbol_summary("NSE:X-EQ", {"2026-08-21": bars})
    assert s["quote_share"] == pytest.approx(0.6)
    assert s["classified_share"] == pytest.approx(1.0)
    assert s["unclassified_trade_share"] == pytest.approx(0.2)


def test_summary_return_sd_skips_non_adjacent_minutes():
    day = "2026-08-21"
    bars = [_bar(_ts(day, 9, 15) + 60 * i, 100.0) for i in range(10)]
    bars.append(_bar(_ts(day, 14, 0), 500.0))      # hours later, huge jump
    s = ofv.symbol_summary("NSE:X-EQ", {day: bars})
    assert s["ret_sd"] == pytest.approx(0.0)       # the jump is not a 1-min return


def test_instrument_class():
    assert ofv.instrument_class("NSE:HDFCBANK-EQ") == "EQ"
    assert ofv.instrument_class("BSE:SENSEX26AUGFUT") == "FUT"
    assert ofv.instrument_class("NSE:NIFTY26AUG24200PE") == "OPT"
    assert ofv.instrument_class("NSE:NIFTY50-INDEX") == "INDEX"


# --------------------------------------------------------------------------
# baseline persistence
# --------------------------------------------------------------------------

def _summaries():
    day = "2026-08-21"
    bars = [_bar(_ts(day, 9, 15) + 60 * i, 100.0 + (i % 5),
                 volume=1000 + i, delta=10 * i, trades=10, quote_n=9)
            for i in range(60)]
    return [ofv.symbol_summary("NSE:X-EQ", {day: bars})]


def test_write_baselines_is_additive_and_idempotent(tmp_path):
    db = str(tmp_path / "ticks.sqlite3")
    # a pre-existing table must survive untouched: the script is only ever
    # allowed to ADD to the research store.
    pre = sqlite3.connect(db)
    pre.execute("CREATE TABLE tick_minute_flow (symbol_id INTEGER)")
    pre.execute("INSERT INTO tick_minute_flow VALUES (7)")
    pre.commit()
    pre.close()

    n = ofv.write_baselines(db, _summaries(), "2026-08-21", ofv.LEGACY_ESTIMATOR)
    assert n == 1
    again = ofv.write_baselines(db, _summaries(), "2026-08-21", ofv.LEGACY_ESTIMATOR)
    assert again == 1

    conn = sqlite3.connect(db)
    assert conn.execute("SELECT symbol_id FROM tick_minute_flow").fetchall() == [(7,)]
    rows = conn.execute("SELECT symbol, bars, estimator FROM of_symbol_baseline").fetchall()
    assert rows == [("NSE:X-EQ", 60, ofv.LEGACY_ESTIMATOR)]   # replaced, not doubled
    cols = [c[1] for c in conn.execute("PRAGMA table_info(of_symbol_baseline)")]
    assert cols == ofv.BASELINE_COLUMNS
    conn.close()


def test_baseline_latest_view_keeps_only_the_newest_day(tmp_path):
    db = str(tmp_path / "ticks.sqlite3")
    ofv.write_baselines(db, _summaries(), "2026-08-21", ofv.LEGACY_ESTIMATOR)
    ofv.write_baselines(db, _summaries(), "2026-08-25", ofv.LEGACY_ESTIMATOR)
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM of_symbol_baseline").fetchone()[0] == 2
    latest = conn.execute(
        "SELECT symbol, as_of FROM of_symbol_baseline_latest").fetchall()
    assert latest == [("NSE:X-EQ", "2026-08-25")]
    conn.close()


def test_baseline_row_width_matches_the_declared_columns(tmp_path):
    """A silent column shift would corrupt every baseline the desk reads."""
    db = str(tmp_path / "ticks.sqlite3")
    ofv.write_baselines(db, _summaries(), "2026-08-21", ofv.CURRENT_ESTIMATOR)
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT * FROM of_symbol_baseline").fetchone()
    assert len(row) == len(ofv.BASELINE_COLUMNS)
    conn.close()


# --------------------------------------------------------------------------
# day selection and loading
# --------------------------------------------------------------------------

def test_usable_days_rejects_condensation_stubs(tmp_path):
    db = str(tmp_path / "ticks.sqlite3")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE tick_minute_flow (symbol_id INTEGER, minute_ts INTEGER)")
    real = _ts("2026-08-21", 9, 15)
    # many symbols in the same minute -- row COUNT is what the threshold reads,
    # and spacing them a minute apart would run the day over into the next one.
    conn.executemany(
        "INSERT INTO tick_minute_flow VALUES (?,?)",
        [(sid, real) for sid in range(ofv.MIN_DAY_ROWS + 10)]
        + [(sid, _ts("2026-08-20", 9, 15)) for sid in range(3)])
    conn.commit()
    assert ofv.usable_days(conn) == ["2026-08-21"]
    conn.close()


def test_estimator_label_follows_the_session_date():
    assert ofv.CLASSIFIER_FIX_DAY == "2026-08-27"
    assert ("2026-08-25" < ofv.CLASSIFIER_FIX_DAY)   # legacy side
    assert not ("2026-08-27" < ofv.CLASSIFIER_FIX_DAY)


# --------------------------------------------------------------------------
# reporting must never print a stand-in number
# --------------------------------------------------------------------------

def test_fmt_prints_na_for_the_unmeasured():
    assert ofv.fmt(None).strip() == "n/a"
    assert "0" not in ofv.fmt(None)
    assert ofv.fmt(0.5, 2, 6).strip() == "0.50"
