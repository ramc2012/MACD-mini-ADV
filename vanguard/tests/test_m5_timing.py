"""Offline tests for M5 timing -- no network, no database.

Matches the live app's own test convention (see tests/test_m1_participant_oi.py).
Exercises the pure functions directly, plus the developing-session walk and
the full pandas pipeline against synthetic bars (no DB fixture needed here:
unlike M1's NSE CSV, M5's only "external" input is OHLCV rows, which are
trivial to construct by hand and cheaper to reason about than a captured
query result).
"""
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from features.m5_timing import (  # noqa: E402
    add_daily_atr,
    add_rolling_seasonal_ratios,
    classify_beyond,
    classify_timing,
    compute_session_developing,
    compute_timing,
    initiative_or_responsive,
    opening_range_state,
    sector_direction_as_of,
    timing_score,
    va_position_of,
    value_area_from_bars,
)


# ---------------------------------------------------------------------------
# value_area_from_bars / va_position_of / classify_beyond
# ---------------------------------------------------------------------------

def test_value_area_single_bar_is_within_the_bars_own_range():
    # A single bar touches every bin uniformly, so the 70%-of-touches value
    # area is the middle ~70% of the bar's range, not the full range --
    # exercising this pins the alternating-expansion behaviour on a uniform
    # histogram (the simplest non-trivial case) rather than asserting a
    # coincidental equality.
    va_low, va_high, poc = value_area_from_bars([100.0], [102.0])
    assert 100.0 <= va_low < va_high <= 102.0
    assert (va_high - va_low) == pytest.approx(2.0 * 0.70, abs=0.07)
    assert va_low <= poc <= va_high


def test_value_area_zero_range_bars_degenerate_without_crashing():
    va_low, va_high, poc = value_area_from_bars([100.0, 100.0], [100.0, 100.0])
    assert va_low == va_high == poc == 100.0
    assert va_position_of(100.0, va_low, va_high) is None  # zero-width guard


def test_value_area_concentrates_around_the_most_touched_bin():
    # Ten bars all overlapping [100, 101], one wide outlier bar [90, 110] --
    # POC must sit inside the tight cluster, not the wide outlier's centre.
    lows = [100.0] * 10 + [90.0]
    highs = [101.0] * 10 + [110.0]
    va_low, va_high, poc = value_area_from_bars(lows, highs)
    assert 100.0 <= poc <= 101.0
    assert va_low <= 100.5 <= va_high


def test_va_position_inside_value_area_is_in_zero_one():
    pos = va_position_of(close=101.0, va_low=100.0, va_high=102.0)
    assert 0.0 <= pos <= 1.0
    assert pos == pytest.approx(0.5)


def test_va_position_above_vah_is_unclipped_signed_distance():
    # VA width = 2.0; close is 0.3 above VAH (102.0) -> 0.15 widths above.
    pos = va_position_of(close=102.3, va_low=100.0, va_high=102.0)
    assert pos == pytest.approx(1.15)
    assert classify_beyond(102.3, 100.0, 102.0) == "above"


def test_va_position_below_val_is_negative():
    pos = va_position_of(close=99.7, va_low=100.0, va_high=102.0)
    assert pos == pytest.approx(-0.15)
    assert classify_beyond(99.7, 100.0, 102.0) == "below"


# ---------------------------------------------------------------------------
# initiative / responsive
# ---------------------------------------------------------------------------

def test_initiative_when_beyond_value_and_volume_agrees():
    initiative, responsive = initiative_or_responsive("above", signed_volume=5000.0)
    assert initiative and not responsive


def test_responsive_when_beyond_value_but_volume_disagrees():
    initiative, responsive = initiative_or_responsive("above", signed_volume=-3000.0)
    assert responsive and not initiative


def test_neither_initiative_nor_responsive_when_inside_value():
    initiative, responsive = initiative_or_responsive("inside", signed_volume=9999.0)
    assert not initiative and not responsive


# ---------------------------------------------------------------------------
# opening_range_state
# ---------------------------------------------------------------------------

def test_opening_range_inside():
    state, atr_mult = opening_range_state(close=101.0, or_high=102.0, or_low=100.0, atr14=2.0)
    assert state == "inside" and atr_mult == 0.0


def test_opening_range_above_reports_atr_multiple():
    state, atr_mult = opening_range_state(close=104.0, or_high=102.0, or_low=100.0, atr14=2.0)
    assert state == "above"
    assert atr_mult == pytest.approx(1.0)  # 2.0 points beyond, ATR=2.0 -> 1 ATR


def test_opening_range_below_atr_multiple_is_negative():
    state, atr_mult = opening_range_state(close=99.0, or_high=102.0, or_low=100.0, atr14=2.0)
    assert state == "below"
    assert atr_mult == pytest.approx(-0.5)


def test_opening_range_atr_multiple_none_when_atr_unavailable():
    state, atr_mult = opening_range_state(close=104.0, or_high=102.0, or_low=100.0, atr14=None)
    assert state == "above" and atr_mult is None


# ---------------------------------------------------------------------------
# classify_timing (composite state, priority order)
# ---------------------------------------------------------------------------

def test_ignition_requires_all_four_legs():
    state = classify_timing(rvol=2.5, range_ratio=1.2, beyond="above",
                             initiative=True, responsive=False, sector_direction=1.0)
    assert state == "IGNITION"


def test_no_ignition_when_sector_direction_disagrees():
    state = classify_timing(rvol=2.5, range_ratio=1.2, beyond="above",
                             initiative=True, responsive=False, sector_direction=-1.0)
    assert state != "IGNITION"


def test_no_ignition_when_sector_direction_unknown():
    state = classify_timing(rvol=2.5, range_ratio=1.2, beyond="above",
                             initiative=True, responsive=False, sector_direction=None)
    assert state != "IGNITION", "missing sector_rs must not be treated as agreement"


def test_no_ignition_when_rvol_below_threshold():
    state = classify_timing(rvol=1.4, range_ratio=1.2, beyond="above",
                             initiative=True, responsive=False, sector_direction=1.0)
    assert state != "IGNITION"


def test_exhaust_on_high_volume_rejection():
    state = classify_timing(rvol=1.8, range_ratio=1.0, beyond="above",
                             initiative=False, responsive=True, sector_direction=1.0)
    assert state == "EXHAUST"


def test_exhaust_does_not_require_sector_agreement():
    state = classify_timing(rvol=1.8, range_ratio=1.0, beyond="above",
                             initiative=False, responsive=True, sector_direction=-1.0)
    assert state == "EXHAUST"


def test_compression_on_quiet_contained_bar():
    state = classify_timing(rvol=0.4, range_ratio=0.5, beyond="inside",
                             initiative=False, responsive=False, sector_direction=None)
    assert state == "COMPRESSION"


def test_balanced_is_the_default():
    state = classify_timing(rvol=1.0, range_ratio=1.0, beyond="inside",
                             initiative=False, responsive=False, sector_direction=None)
    assert state == "BALANCED"


def test_balanced_when_rvol_undefined_insufficient_history():
    state = classify_timing(rvol=None, range_ratio=None, beyond="above",
                             initiative=True, responsive=False, sector_direction=1.0)
    assert state == "BALANCED", "no fabricated state off undefined RVOL"


def test_ignition_takes_priority_over_exhaust_conditions():
    # Constructed so IGNITION's own predicate is satisfied; EXHAUST's
    # predicate (responsive) cannot also be true for the same bar since
    # initiative and responsive are mutually exclusive by construction --
    # this test documents that guarantee rather than an achievable conflict.
    state = classify_timing(rvol=3.0, range_ratio=1.5, beyond="above",
                             initiative=True, responsive=False, sector_direction=1.0)
    assert state == "IGNITION"


# ---------------------------------------------------------------------------
# timing_score
# ---------------------------------------------------------------------------

def test_timing_score_is_bounded_0_100():
    lo = timing_score(rvol=0.0, va_position=0.5, initiative=False, responsive=True, beyond="above")
    hi = timing_score(rvol=10.0, va_position=2.0, initiative=True, responsive=False, beyond="above")
    assert 0.0 <= lo <= 100.0
    assert 0.0 <= hi <= 100.0
    assert hi > lo


def test_timing_score_undefined_rvol_contributes_zero_not_a_crash():
    score = timing_score(rvol=None, va_position=0.5, initiative=False, responsive=False, beyond="inside")
    assert score == pytest.approx(15.0)  # location=0 (centre), activity=15 (inside/neutral)


def test_timing_score_ignition_like_bar_scores_higher_than_compression_like_bar():
    ignition_like = timing_score(rvol=2.5, va_position=1.2, initiative=True, responsive=False, beyond="above")
    compression_like = timing_score(rvol=0.4, va_position=0.5, initiative=False, responsive=False, beyond="inside")
    assert ignition_like > compression_like


# ---------------------------------------------------------------------------
# sector_direction_as_of -- no look-ahead
# ---------------------------------------------------------------------------

def test_sector_direction_uses_only_prior_session():
    sector_rs = pd.DataFrame({
        "ts": [date(2026, 8, 21), date(2026, 8, 24), date(2026, 8, 25)],
        "sector20": ["Metals & Mining"] * 3,
        "rs_z20": [1.0, 1.5, -9.0],   # today's own row (25th) is deliberately opposite sign
    })
    direction = sector_direction_as_of(sector_rs, "Metals & Mining", date(2026, 8, 25))
    assert direction == 1.0, "must use the 24th's row, never the 25th's own same-day row"


def test_sector_direction_none_when_no_prior_row():
    sector_rs = pd.DataFrame({"ts": [date(2026, 8, 25)], "sector20": ["X"], "rs_z20": [1.0]})
    assert sector_direction_as_of(sector_rs, "X", date(2026, 8, 20)) is None


def test_sector_direction_none_when_sector20_missing():
    sector_rs = pd.DataFrame({"ts": [date(2026, 8, 20)], "sector20": ["X"], "rs_z20": [1.0]})
    assert sector_direction_as_of(sector_rs, None, date(2026, 8, 25)) is None


# ---------------------------------------------------------------------------
# compute_session_developing -- no look-ahead within a session
# ---------------------------------------------------------------------------

def _bar(hour, minute, o, h, l, c, v, symbol="TEST", session_date=date(2026, 8, 25), atr14=2.0, rvol=1.0,
         range_ratio=1.0):
    return {
        "time": datetime(2026, 8, 25, hour, minute, tzinfo=timezone.utc), "symbol": symbol,
        "session_date": session_date, "tod": f"{hour:02d}:{minute:02d}",
        "open": o, "high": h, "low": l, "close": c, "volume": v, "atr14": atr14,
        "rvol": rvol, "range_ratio": range_ratio,
    }


def test_first_bar_of_session_is_forming_not_a_fabricated_reading():
    bars = pd.DataFrame([_bar(3, 45, 100, 102, 99, 101, 1000)])
    out = compute_session_developing(bars)
    assert out.iloc[0]["or_state"] == "forming"
    assert out.iloc[0]["or_atr_mult"] is None


def test_developing_value_area_only_uses_bars_seen_so_far():
    # Bar 1 stays in a tight band; bar 2 breaks sharply higher. Bar 1's own
    # value area/POC must be computed from bar 1 ALONE -- it cannot already
    # reflect bar 2's much higher range (that would be look-ahead).
    bars = pd.DataFrame([
        _bar(3, 45, 100, 101, 100, 100.5, 1000),
        _bar(4, 15, 100.5, 120, 100.5, 119, 5000),
    ])
    out = compute_session_developing(bars)
    first_va_high = out.iloc[0]["va_high"]
    assert first_va_high < 105, "bar 1's value area leaked information from bar 2"


def test_vwap_to_date_is_cumulative_not_whole_session():
    bars = pd.DataFrame([
        _bar(3, 45, 100, 100, 100, 100, 1000),
        _bar(4, 15, 100, 200, 100, 200, 1000),
    ])
    out = compute_session_developing(bars)
    # bar 1's vwap_to_date must equal bar 1's own typical price only.
    assert out.iloc[0]["vwap_to_date"] == pytest.approx(100.0)


def test_full_session_walk_runs_end_to_end_and_returns_one_row_per_bar():
    bars = pd.DataFrame([
        _bar(3, 45, 100, 101, 99, 100, 1000),
        _bar(4, 15, 100, 103, 99, 102, 1200),
        _bar(4, 45, 102, 108, 101, 107, 4000),
    ])
    out = compute_session_developing(bars)
    assert len(out) == 3
    assert list(out["or_state"]) == ["forming", out.iloc[1]["or_state"], out.iloc[2]["or_state"]]


# ---------------------------------------------------------------------------
# add_rolling_seasonal_ratios / add_daily_atr -- no look-ahead across sessions
# ---------------------------------------------------------------------------

def _make_flat_history(n_sessions: int, symbol="TEST", vol=1000.0, rng=2.0) -> pd.DataFrame:
    rows = []
    base = date(2026, 6, 1)
    for i in range(n_sessions):
        d = base + pd.Timedelta(days=i)
        rows.append({
            "time": datetime(d.year, d.month, d.day, 3, 45, tzinfo=timezone.utc),
            "symbol": symbol, "session_date": d, "tod": "09:15",
            "open": 100.0, "high": 100.0 + rng, "low": 100.0, "close": 100.0 + rng / 2,
            "volume": vol,
        })
    return pd.DataFrame(rows)


def test_rvol_undefined_before_minimum_history():
    df = add_rolling_seasonal_ratios(_make_flat_history(3))
    assert df["rvol"].isna().all()


def test_rvol_is_one_for_a_flat_history_new_bar_matching_the_mean():
    history = _make_flat_history(25)
    df = add_rolling_seasonal_ratios(history)
    last = df.sort_values("time").iloc[-1]
    assert last["rvol"] == pytest.approx(1.0)


def test_rvol_spikes_correctly_on_a_volume_surge():
    history = _make_flat_history(25)
    history = pd.concat([history, _make_flat_history(1, symbol="TEST")], ignore_index=True)
    history.iloc[-1, history.columns.get_loc("volume")] = 3000.0  # 3x the flat 1000 baseline
    history.iloc[-1, history.columns.get_loc("session_date")] = date(2026, 6, 1) + pd.Timedelta(days=25)
    history.iloc[-1, history.columns.get_loc("time")] = datetime(2026, 6, 26, 3, 45, tzinfo=timezone.utc)
    df = add_rolling_seasonal_ratios(history)
    last = df.sort_values("time").iloc[-1]
    assert last["rvol"] == pytest.approx(3.0, rel=0.05)


def test_atr_uses_only_prior_sessions_never_todays_own_range():
    # 14 quiet sessions (range=2), then a 15th session with a huge range.
    rows = []
    base = date(2026, 6, 1)
    for i in range(14):
        d = base + pd.Timedelta(days=i)
        rows.append({"symbol": "TEST", "session_date": d, "open": 100.0, "high": 101.0, "low": 99.0,
                      "close": 100.0, "time": datetime(d.year, d.month, d.day, 3, 45, tzinfo=timezone.utc),
                      "tod": "09:15", "volume": 1000.0})
    huge_day = base + pd.Timedelta(days=14)
    rows.append({"symbol": "TEST", "session_date": huge_day, "open": 100.0, "high": 150.0, "low": 50.0,
                 "close": 100.0, "time": datetime(huge_day.year, huge_day.month, huge_day.day, 3, 45,
                                                    tzinfo=timezone.utc), "tod": "09:15", "volume": 1000.0})
    df = pd.DataFrame(rows)
    df = add_daily_atr(df)
    huge_day_atr = df[df["session_date"] == huge_day]["atr14"].iloc[0]
    assert huge_day_atr < 10.0, "the 15th session's own 100-point range must not leak into its own ATR14"


# ---------------------------------------------------------------------------
# compute_timing -- offline via a monkeypatched DB layer would need a real
# connection; the sub-pieces above already cover the no-look-ahead
# guarantees. This one checks compute_timing's output contract using a
# fabricated in-memory frame path is out of scope without a DB, so we only
# assert the public pure-function surface here (matches the file's own
# "no network, no database" scope).
# ---------------------------------------------------------------------------

def test_pure_function_surface_is_importable_and_deterministic():
    args = (dict(rvol=2.0, range_ratio=1.0, beyond="above", initiative=True, responsive=False,
                  sector_direction=1.0),)
    assert classify_timing(**args[0]) == classify_timing(**args[0])


def test_timing_score_treats_real_pipeline_nan_the_same_as_none():
    """Regression test for a real, moderate bug: the actual pandas pipeline
    (compute_timing() via DataFrame.apply) passes numpy.nan for an undefined
    rvol/va_position, never Python None -- but the guards checked `is None`,
    which never fires on NaN, so the `else` branch computed
    min(nan/3.0, 1.0) = nan, poisoning the whole score to NaN. Confirmed
    live: 10.2% of stored rows (13,051/128,459) had a NULL timing_score
    before this fix. pd.isna() is the only guard that catches both."""
    import numpy as np

    none_score = timing_score(rvol=None, va_position=0.6, initiative=False, responsive=False, beyond="inside")
    nan_score = timing_score(rvol=float("nan"), va_position=0.6, initiative=False, responsive=False, beyond="inside")
    assert none_score == nan_score, "None and NaN must produce the identical, non-NaN score"
    assert not pd.isna(nan_score)

    both_nan = timing_score(rvol=np.nan, va_position=np.nan, initiative=False, responsive=False, beyond="inside")
    assert not pd.isna(both_nan)


# ── the session-grid filter (added 2026-08-27) ─────────────────────────────
#
# load_bars() takes a live connection, so these test the filter's ARITHMETIC
# against the constants it uses rather than round-tripping a fake cursor --
# the constants are what a future edit would get wrong.

from features.m5_timing import (  # noqa: E402
    SESSION_FIRST_BAR_MIN,
    SESSION_GRID_MINUTES,
    SESSION_LAST_BAR_MIN,
)


def _minutes(hh, mm):
    return hh * 60 + mm


def test_the_session_window_is_start_of_bar_0915_to_1515():
    """13 bars: 09:15 through 15:15, the last covering the 15:15-15:30 stub."""
    assert SESSION_FIRST_BAR_MIN == _minutes(9, 15)
    assert SESSION_LAST_BAR_MIN == _minutes(15, 15)
    span = (SESSION_LAST_BAR_MIN - SESSION_FIRST_BAR_MIN) // 30 + 1
    assert span == 13


def test_the_grid_is_15_and_45_because_the_session_opens_at_0915():
    """The second, 15-minute-offset grid in underlying_spot_candles is what
    made M6's evaluated universe swing between ~55 and ~208 symbols depending
    on which grid the latest bar happened to land on."""
    assert SESSION_GRID_MINUTES == (15, 45)


def test_every_real_session_bar_start_is_on_the_grid_and_in_the_window():
    starts = [SESSION_FIRST_BAR_MIN + 30 * i for i in range(13)]
    for minutes in starts:
        assert SESSION_FIRST_BAR_MIN <= minutes <= SESSION_LAST_BAR_MIN
        assert (minutes % 60) in SESSION_GRID_MINUTES


def test_the_offset_grid_and_off_hours_bars_are_both_excluded():
    for hh, mm in [(10, 0), (10, 30), (14, 0)]:           # the other grid
        assert (_minutes(hh, mm) % 60) not in SESSION_GRID_MINUTES
    for hh, mm in [(8, 45), (9, 0), (15, 45), (18, 15)]:  # outside the session
        minutes = _minutes(hh, mm)
        assert not (SESSION_FIRST_BAR_MIN <= minutes <= SESSION_LAST_BAR_MIN)
