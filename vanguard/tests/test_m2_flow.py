"""Offline tests for M2 -- no network, no database.

Fixtures below are shaped exactly like the live rows pulled from
`option_premium_candles` / `underlying_spot_candles` / `fo_option_chain_metrics`
on 2026-08-26 (see the module docstring for the live queries that produced
the real numbers these are modeled on -- e.g. the TATASTEEL 2026-07-28
185-strike CE/PE rows, put delta sign convention negative).
"""
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from features.m2_flow import (  # noqa: E402
    _rolling_percentile,
    _session_ivs_skew_os,
    _zscore,
    build_session_ingredients,
    classify_oi_state,
    compute_flow_score,
    compute_time_series,
)


def _chain_row(underlying, expiry, strike, option_type, iv, delta, underlying_price, volume, dt):
    return {"underlying": underlying, "expiry": expiry, "strike": strike,
            "option_type": option_type, "iv": iv, "delta": delta,
            "underlying_price": underlying_price, "volume": volume, "dt": dt}


# --------------------------------------------------------------------------
# classify_oi_state -- ingredient 4, pure function, no live source calls it
# --------------------------------------------------------------------------
def test_long_buildup_is_oi_up_price_up():
    assert classify_oi_state(delta_oi=1000, delta_price=5.0) == "long_buildup"


def test_short_covering_is_oi_down_price_up():
    assert classify_oi_state(delta_oi=-1000, delta_price=5.0) == "short_covering"


def test_long_unwind_is_oi_down_price_down():
    assert classify_oi_state(delta_oi=-1000, delta_price=-5.0) == "long_unwind"


def test_short_buildup_is_oi_up_price_down():
    assert classify_oi_state(delta_oi=1000, delta_price=-5.0) == "short_buildup"


def test_missing_inputs_classify_as_none():
    assert classify_oi_state(None, 5.0) is None
    assert classify_oi_state(1000, None) is None
    assert classify_oi_state(0, 5.0) is None
    assert classify_oi_state(1000, 0) is None


# --------------------------------------------------------------------------
# _session_ivs_skew_os -- IVS, SKEW, delta-weighted O/S raw from one chain
# --------------------------------------------------------------------------
def test_ivs_is_mean_near_atm_call_iv_minus_put_iv():
    dt = date(2026, 7, 28)
    rows = [
        _chain_row("TATASTEEL", dt, 182.5, "CE", 0.25, 0.55, 184.0, 1000, dt),
        _chain_row("TATASTEEL", dt, 182.5, "PE", 0.20, -0.45, 184.0, 1000, dt),
        _chain_row("TATASTEEL", dt, 185.0, "CE", 0.30, 0.35, 184.0, 1000, dt),
        _chain_row("TATASTEEL", dt, 185.0, "PE", 0.22, -0.65, 184.0, 1000, dt),
    ]
    group = pd.DataFrame(rows)
    result = _session_ivs_skew_os(group, spot_close=184.0)
    # both strikes fall within +-2 of ATM(185) given only two strikes exist
    assert result["ivs"] == pytest.approx((0.25 + 0.30) / 2 - (0.20 + 0.22) / 2)


def test_skew_uses_nearest_25_delta_put_and_call_not_atm():
    dt = date(2026, 7, 28)
    rows = [
        _chain_row("TATASTEEL", dt, 180.0, "PE", 0.40, -0.25, 184.0, 500, dt),   # nearest -0.25
        _chain_row("TATASTEEL", dt, 175.0, "PE", 0.55, -0.60, 184.0, 500, dt),   # far from -0.25
        _chain_row("TATASTEEL", dt, 190.0, "CE", 0.28, 0.26, 184.0, 500, dt),    # nearest +0.25
        _chain_row("TATASTEEL", dt, 200.0, "CE", 0.20, 0.05, 184.0, 500, dt),    # far from +0.25
    ]
    group = pd.DataFrame(rows)
    result = _session_ivs_skew_os(group, spot_close=184.0)
    assert result["skew"] == pytest.approx(0.40 - 0.28)


def test_os_raw_is_delta_weighted_volume_summed_across_delta_notna_rows():
    dt = date(2026, 7, 28)
    rows = [
        _chain_row("TATASTEEL", dt, 185.0, "CE", 0.30, 0.40, 184.0, 1000, dt),
        _chain_row("TATASTEEL", dt, 185.0, "PE", 0.22, -0.60, 184.0, 2000, dt),
        _chain_row("TATASTEEL", dt, 190.0, "CE", None, None, 184.0, 5000, dt),  # no delta -- excluded
    ]
    group = pd.DataFrame(rows)
    result = _session_ivs_skew_os(group, spot_close=184.0)
    assert result["os_raw"] == pytest.approx(0.40 * 1000 + 0.60 * 2000)
    assert result["n_delta_rows"] == 2


def test_front_expiry_is_the_nearest_expiry_present_that_day():
    dt = date(2026, 7, 28)
    rows = [
        _chain_row("TATASTEEL", date(2026, 7, 28), 185.0, "CE", 0.30, 0.40, 184.0, 1000, dt),
        _chain_row("TATASTEEL", date(2026, 7, 28), 185.0, "PE", 0.22, -0.60, 184.0, 1000, dt),
        # a further-dated expiry present the same session must be ignored
        _chain_row("TATASTEEL", date(2026, 8, 25), 185.0, "CE", 0.99, 0.99, 184.0, 999999, dt),
        _chain_row("TATASTEEL", date(2026, 8, 25), 185.0, "PE", 0.99, -0.99, 184.0, 999999, dt),
    ]
    group = pd.DataFrame(rows)
    result = _session_ivs_skew_os(group, spot_close=184.0)
    assert result["ivs"] == pytest.approx(0.30 - 0.22)


def test_ivs_is_nan_when_only_one_side_has_iv_near_atm():
    dt = date(2026, 7, 28)
    rows = [
        _chain_row("TATASTEEL", dt, 185.0, "CE", 0.30, 0.40, 184.0, 1000, dt),
        _chain_row("TATASTEEL", dt, 185.0, "PE", None, -0.60, 184.0, 1000, dt),
    ]
    group = pd.DataFrame(rows)
    result = _session_ivs_skew_os(group, spot_close=184.0)
    assert np.isnan(result["ivs"])


def test_underlying_price_falls_back_to_spot_close_when_option_row_lacks_it():
    dt = date(2026, 7, 28)
    # enough strikes between 100 and 500 that a +-2-strike window around the
    # true ATM (100) cannot reach the decoy strikes near 500
    rows = [
        _chain_row("TATASTEEL", dt, strike, side, iv, delta, None, 1000, dt)
        for strike, side, iv, delta in [
            (90.0, "CE", 0.40, 0.70), (90.0, "PE", 0.35, -0.30),
            (95.0, "CE", 0.32, 0.60), (95.0, "PE", 0.28, -0.40),
            (100.0, "CE", 0.30, 0.50), (100.0, "PE", 0.22, -0.50),
            (105.0, "CE", 0.29, 0.40), (105.0, "PE", 0.24, -0.60),
            (110.0, "CE", 0.27, 0.30), (110.0, "PE", 0.26, -0.70),
            (500.0, "CE", 0.99, 0.99), (500.0, "PE", 0.88, -0.99),
        ]
    ]
    group = pd.DataFrame(rows)
    # spot close near strike 100 -> ATM window should pick strikes 90..110, not 500
    result = _session_ivs_skew_os(group, spot_close=101.0)
    expected_ce = (0.40 + 0.32 + 0.30 + 0.29 + 0.27) / 5
    expected_pe = (0.35 + 0.28 + 0.22 + 0.24 + 0.26) / 5
    assert result["ivs"] == pytest.approx(expected_ce - expected_pe)


# --------------------------------------------------------------------------
# build_session_ingredients -- groups a multi-day, multi-symbol chain frame
# --------------------------------------------------------------------------
def test_build_session_ingredients_produces_one_row_per_underlying_dt():
    d1, d2 = date(2026, 7, 27), date(2026, 7, 28)
    rows = [
        _chain_row("TATASTEEL", d1, 185.0, "CE", 0.30, 0.40, 184.0, 1000, d1),
        _chain_row("TATASTEEL", d1, 185.0, "PE", 0.22, -0.60, 184.0, 1000, d1),
        _chain_row("TATASTEEL", d2, 185.0, "CE", 0.28, 0.38, 183.0, 1200, d2),
        _chain_row("TATASTEEL", d2, 185.0, "PE", 0.24, -0.62, 183.0, 1200, d2),
        _chain_row("RELIANCE", d2, 1300.0, "CE", 0.18, 0.50, 1310.0, 800, d2),
        _chain_row("RELIANCE", d2, 1300.0, "PE", 0.20, -0.50, 1310.0, 800, d2),
    ]
    chain = pd.DataFrame(rows)
    spot = pd.DataFrame([])
    result = build_session_ingredients(chain, spot)
    assert len(result) == 3
    assert set(zip(result["underlying"], result["dt"])) == {
        ("TATASTEEL", d1), ("TATASTEEL", d2), ("RELIANCE", d2)}


# --------------------------------------------------------------------------
# rolling z-score / percentile helpers -- causal, no look-ahead
# --------------------------------------------------------------------------
def test_zscore_matches_hand_computed_value():
    series = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    z = _zscore(series, window=5, min_periods=5)
    mean, std = series.mean(), series.std(ddof=0)
    assert z.iloc[-1] == pytest.approx((5.0 - mean) / std)


def test_zscore_is_nan_before_min_periods_reached():
    series = pd.Series([1.0, 2.0])
    z = _zscore(series, window=20, min_periods=5)
    assert z.isna().all()


def test_zscore_at_row_i_never_uses_data_after_row_i_no_lookahead():
    series = pd.Series([1.0, 2.0, 3.0, 100.0, 5.0])  # a future spike at index 3
    z = _zscore(series, window=20, min_periods=3)
    # z at index 2 (value 3.0) must be identical whether or not the future
    # spike at index 3 exists in the series at all
    truncated = _zscore(series.iloc[:3], window=20, min_periods=3)
    assert z.iloc[2] == pytest.approx(truncated.iloc[2])


def test_rolling_percentile_of_last_is_max_when_current_is_the_highest():
    series = pd.Series([1.0, 2.0, 3.0, 4.0, 10.0])
    pct = _rolling_percentile(series, window=5, min_periods=3)
    assert pct.iloc[-1] == pytest.approx(1.0)


def test_rolling_percentile_of_last_is_min_when_current_is_the_lowest():
    series = pd.Series([5.0, 4.0, 3.0, 2.0, 0.5])
    pct = _rolling_percentile(series, window=5, min_periods=3)
    assert pct.iloc[-1] == pytest.approx(0.2)  # 1 of 5 values <= itself


# --------------------------------------------------------------------------
# compute_flow_score -- weighted composite with renormalization over NULLs
# --------------------------------------------------------------------------
def test_flow_score_all_ingredients_present_uses_full_weights():
    score, meta = compute_flow_score(ivs_z=3.0, skew_z=3.0, os_pctile=1.0,
                                      oi_state="long_buildup", pcr_z=3.0)
    # every component maxes to +1 -> weighted average is +1 -> score = 100
    assert score == pytest.approx(100.0)
    assert meta["n_ingredients"] == 5


def test_flow_score_all_ingredients_bearish_extreme_is_minus_100():
    score, _ = compute_flow_score(ivs_z=-3.0, skew_z=-3.0, os_pctile=0.0,
                                   oi_state="short_buildup", pcr_z=-3.0)
    assert score == pytest.approx(-100.0)


def test_flow_score_renormalizes_weights_when_ingredients_are_null():
    # only IVS (weight 30) and PCR (weight 10) present, both maxed bullish
    score, meta = compute_flow_score(ivs_z=3.0, skew_z=None, os_pctile=None,
                                      oi_state=None, pcr_z=3.0)
    assert score == pytest.approx(100.0)  # renormalized weights still sum to 1
    assert meta["n_ingredients"] == 2
    assert meta["weights_used"]["ivs"] == pytest.approx(30 / 40)
    assert meta["weights_used"]["pcr"] == pytest.approx(10 / 40)


def test_flow_score_is_none_when_every_ingredient_is_null():
    score, meta = compute_flow_score(None, None, None, None, None)
    assert score is None
    assert meta["n_ingredients"] == 0


def test_flow_score_null_never_contributes_zero_into_a_100pct_weight_sum():
    """A name with only a strongly bullish IVS and nothing else must not be
    dragged toward 0 by the missing ingredients -- that would be treating
    NULL as 0 baked into an un-renormalized 100%-weight sum."""
    score, _ = compute_flow_score(ivs_z=3.0, skew_z=None, os_pctile=None,
                                   oi_state=None, pcr_z=None)
    assert score == pytest.approx(100.0)  # not 30.0, which is what a
    # NULL-as-zero, un-renormalized (weight/100) sum would have produced


def test_flow_score_clips_extreme_z_scores_rather_than_exceeding_the_scale():
    score, _ = compute_flow_score(ivs_z=50.0, skew_z=None, os_pctile=None,
                                   oi_state=None, pcr_z=None)
    assert score == pytest.approx(100.0)  # clipped at Z_CLIP, not 1666.7


# --------------------------------------------------------------------------
# compute_time_series -- wiring ingredients + daily volume + pcr into series
# --------------------------------------------------------------------------
def test_compute_time_series_os_ratio_divides_by_matching_day_stock_volume():
    ingredients = pd.DataFrame([
        {"underlying": "TATASTEEL", "dt": date(2026, 7, 27), "ivs": 0.05, "skew": 0.02,
         "os_raw": 500.0, "n_strikes": 4, "n_delta_rows": 4},
        {"underlying": "TATASTEEL", "dt": date(2026, 7, 28), "ivs": 0.04, "skew": 0.03,
         "os_raw": 1000.0, "n_strikes": 4, "n_delta_rows": 4},
    ])
    daily_volume = pd.DataFrame([
        {"underlying": "TATASTEEL", "dt": date(2026, 7, 27), "close": 180.0, "volume": 5000.0},
        {"underlying": "TATASTEEL", "dt": date(2026, 7, 28), "close": 182.0, "volume": 2000.0},
    ])
    pcr = pd.DataFrame(columns=["underlying", "dt", "oi_pcr"])
    series, _ = compute_time_series(ingredients, daily_volume, pcr)
    row = series[series["dt"] == date(2026, 7, 28)].iloc[0]
    assert row["os_ratio"] == pytest.approx(1000.0 / 2000.0)


def test_compute_time_series_pcr_z_scores_the_day_over_day_change():
    pcr = pd.DataFrame([
        {"underlying": "TATASTEEL", "dt": date(2026, 7, d), "oi_pcr": v}
        for d, v in zip(range(20, 28), [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 2.0])
    ])
    ingredients = pd.DataFrame(columns=["underlying", "dt", "ivs", "skew", "os_raw",
                                         "n_strikes", "n_delta_rows"])
    daily_volume = pd.DataFrame(columns=["underlying", "dt", "close", "volume"])
    _, pcr_series = compute_time_series(ingredients, daily_volume, pcr)
    last = pcr_series[pcr_series["dt"] == date(2026, 7, 27)].iloc[0]
    assert last["pcr_change"] == pytest.approx(1.0)  # 2.0 - 1.0
    assert last["pcr_z"] > 0  # the one real jump should score positive vs a flat baseline


def test_rolling_percentile_returns_nan_not_zero_when_current_value_is_missing():
    """Regression test for a real, critical bug: NaN comparisons are always
    False in NumPy, so `(values <= values[-1]).mean()` on a window whose
    LAST value is NaN silently returned 0.0 -- the single most bearish
    reading possible -- for a name with literally no data that session.
    Confirmed live: 113/210 stored rows hit this, 20 driving flow_score to
    a fabricated -100.0 from nothing. Doctrine #5 requires NULL here."""
    series = pd.Series([10.0, 20.0, 30.0, np.nan])
    pct = _rolling_percentile(series, window=4, min_periods=3)
    assert pd.isna(pct.iloc[-1]), "current value missing -> must be NaN, not 0.0"


def test_rolling_percentile_denominator_excludes_nan_gaps_in_the_window():
    """Regression test: the denominator must be the count of VALID
    comparisons, not the raw window length. [NaN, 10, 20, 30, 5] has 4 valid
    entries; 5.0 is the smallest of those 4, so its percentile is 1/4=0.25,
    not 1/5=0.20 (what dividing by raw window length silently produced)."""
    series = pd.Series([np.nan, 10.0, 20.0, 30.0, 5.0])
    pct = _rolling_percentile(series, window=5, min_periods=3)
    assert pct.iloc[-1] == pytest.approx(0.25)


def test_rolling_percentile_nan_with_no_valid_history_is_nan_not_a_crash():
    series = pd.Series([np.nan, np.nan, np.nan])
    pct = _rolling_percentile(series, window=3, min_periods=1)
    assert pd.isna(pct.iloc[-1])
