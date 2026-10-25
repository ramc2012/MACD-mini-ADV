"""Offline tests for the M3 GEX regime engine -- no network, no database.

Matches the live app's own test convention (Makefile: "mocks or gracefully
degrades Postgres, Redis and every broker, so it needs no database or
network"). Fixtures below are small hand-built option-chain snapshots shaped
exactly like what `load_snapshot_rows()` returns from the live query
(underlying, day, ts, expiry, strike, option_type, gamma, oi, lot_size,
spot, n_strikes) -- captured-shape, not captured-live-bytes, because the
source here is numeric query output, not a fetchable text file like M1's CSV.
"""
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from features.m3_gex import (  # noqa: E402
    bucket_regime,
    build_daily_series,
    compute_regime,
    gamma_flip_level,
    percentile_rank_trailing,
    signed_gex,
)


def _rows(pairs, day="2026-08-12", ts="2026-08-12T09:45:00+00:00", lot_size=50, spot=24000.0):
    """pairs: list of (strike, option_type, gamma, oi)."""
    records = [
        {
            "underlying": "TEST", "day": date.fromisoformat(day),
            "ts": pd.Timestamp(ts), "expiry": date(2026, 8, 26),
            "strike": strike, "option_type": opt_type, "gamma": gamma, "oi": oi,
            "lot_size": lot_size, "spot": spot, "n_strikes": len({p[0] for p in pairs}),
        }
        for strike, opt_type, gamma, oi in pairs
    ]
    return pd.DataFrame.from_records(records)


def test_signed_gex_calls_positive_puts_negative():
    """Documented convention: call-side contributes +, put-side contributes -."""
    rows = _rows([(24000, "CE", 0.001, 1000), (24000, "PE", 0.001, 1000)])
    gex = signed_gex(rows)
    call_gex, put_gex = gex.iloc[0], gex.iloc[1]
    assert call_gex > 0
    assert put_gex < 0
    assert call_gex == -put_gex  # identical magnitude inputs, opposite sign


def test_signed_gex_magnitude_matches_the_documented_formula():
    # gex_contract = gamma * OI * lot_size * spot**2 * 0.01
    rows = _rows([(24000, "CE", 0.002, 500)], lot_size=25, spot=100.0)
    expected = 0.002 * 500 * 25 * (100.0 ** 2) * 0.01
    assert abs(signed_gex(rows).iloc[0] - expected) < 1e-6


def test_net_gex_is_the_call_minus_put_sum():
    rows = _rows([
        (24000, "CE", 0.002, 1000),
        (24000, "PE", 0.001, 1000),
        (24100, "CE", 0.0015, 500),
    ])
    daily = build_daily_series(rows)
    assert len(daily) == 1
    call_sum = 0.002 * 1000 + 0.0015 * 500
    put_sum = 0.001 * 1000
    expected_net = (call_sum - put_sum) * 50 * (24000.0 ** 2) * 0.01
    assert abs(daily.iloc[0]["net_gex"] - expected_net) < 1e-3


def test_gamma_flip_level_interpolates_the_zero_crossing():
    # Strongly put-heavy (negative) below 24000, strongly call-heavy (positive) above --
    # cumulative signed GEX should cross zero somewhere between 24000 and 24100.
    rows = _rows([
        (23900, "PE", 0.002, 5000),
        (24000, "PE", 0.002, 5000),
        (24100, "CE", 0.002, 5000),
        (24200, "CE", 0.002, 5000),
    ])
    flip = gamma_flip_level(rows)
    assert flip is not None
    assert 23900 <= flip <= 24200


def test_gamma_flip_level_is_null_when_it_never_crosses():
    """All-call (all-positive) book: cumulative sum never returns to zero --
    doctrine: never extrapolate past observed strikes, store NULL."""
    rows = _rows([
        (24000, "CE", 0.002, 1000),
        (24100, "CE", 0.002, 1000),
        (24200, "CE", 0.002, 1000),
    ])
    assert gamma_flip_level(rows) is None


def test_gamma_flip_level_is_null_with_a_single_strike():
    rows = _rows([(24000, "CE", 0.002, 1000)])
    assert gamma_flip_level(rows) is None


def test_rows_missing_lot_size_or_spot_are_skipped_not_fabricated():
    """Doctrine #5: a symbol with no fo_underlying_catalog or
    underlying_spot_candles join must be dropped, never defaulted to 1 or 0."""
    good = _rows([(24000, "CE", 0.002, 1000)], day="2026-08-12")
    bad = _rows([(24000, "CE", 0.002, 1000)], day="2026-08-13")
    bad["lot_size"] = np.nan
    combined = pd.concat([good, bad], ignore_index=True)
    daily = build_daily_series(combined)
    assert len(daily) == 1
    assert daily.iloc[0]["day"] == date(2026, 8, 12)


def test_percentile_rank_trailing_is_exact_on_a_known_series():
    # Strictly increasing series -> each point's trailing percentile rank
    # (fraction of trailing window <= itself) is always 1.0 (it's the max so far).
    values = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    ranks = percentile_rank_trailing(values)
    # First 4 points have <5 trailing observations -> NaN (insufficient history).
    assert ranks.iloc[:4].isna().all()
    assert ranks.iloc[4] == 1.0  # window [1,2,3,4,5], value 5 is max -> rank 1.0
    assert ranks.iloc[5] == 1.0  # window [1,2,3,4,5,6], value 6 is max -> rank 1.0


def test_percentile_rank_trailing_no_lookahead():
    """A future spike must not affect a past session's percentile rank."""
    values = pd.Series([1.0, 1.0, 1.0, 1.0, 1.0, 1000.0])
    ranks = percentile_rank_trailing(values)
    # Index 4 (5th obs, before the spike) only sees [1,1,1,1,1] -> rank 1.0, unaffected by index 5.
    assert ranks.iloc[4] == 1.0
    assert ranks.iloc[5] == 1.0  # the spike itself is trivially the max of its own window


def test_percentile_rank_trailing_window_is_capped_at_60():
    """A window index i must never look further back than TRAILING_WINDOW
    observations, even with 100 available."""
    values = pd.Series(np.concatenate([np.full(40, 1.0), np.full(60, 2.0)]))
    ranks = percentile_rank_trailing(values)
    # At index 99 (last obs = 2.0), trailing window is values[40:100] = sixty 2.0's,
    # NOT values[39:100] which would include a 1.0. All 60 equal itself -> rank 1.0 either way,
    # so check window *size* directly via a case where inclusion of index 39 would change the result.
    values2 = pd.Series(np.concatenate([np.full(1, 0.0), np.full(59, 100.0), np.full(1, 50.0)]))
    ranks2 = percentile_rank_trailing(values2)
    last = ranks2.iloc[-1]
    # Trailing window for the last point = values2[1:61] i.e. indices 1..60 (60 obs):
    # fifty-nine 100.0's + the final 50.0 itself. 50.0 <= 50.0 counts once, none of the 100's do.
    assert abs(last - (1 / 60)) < 1e-9


def test_bucket_regime_matches_documented_cut_points():
    assert bucket_regime(0.0) == "STRONG_NEG"
    assert bucket_regime(0.20) == "STRONG_NEG"
    assert bucket_regime(0.21) == "NEG"
    assert bucket_regime(0.40) == "NEG"
    assert bucket_regime(0.50) == "NEUTRAL"
    assert bucket_regime(0.60) == "NEUTRAL"
    assert bucket_regime(0.61) == "POS"
    assert bucket_regime(0.80) == "POS"
    assert bucket_regime(0.81) == "STRONG_POS"
    assert bucket_regime(1.0) == "STRONG_POS"


def test_bucket_regime_is_none_for_nan():
    assert bucket_regime(float("nan")) is None


def test_compute_regime_end_to_end_on_a_multi_day_series():
    """Build a 6-session synthetic history for one symbol and check the last
    session (5 trailing obs -> first session with a regime bucket) lands in
    the bucket its own percentile implies."""
    frames = []
    for i, (day, gamma_ce, gamma_pe) in enumerate([
        ("2026-08-01", 0.0005, 0.0030),  # very put-heavy -> very negative net_gex
        ("2026-08-02", 0.0010, 0.0025),
        ("2026-08-03", 0.0015, 0.0020),
        ("2026-08-04", 0.0020, 0.0015),
        ("2026-08-05", 0.0025, 0.0010),
        ("2026-08-06", 0.0030, 0.0005),  # very call-heavy -> very positive net_gex, and the max
    ]):
        frames.append(_rows(
            [(24000, "CE", gamma_ce, 1000), (24000, "PE", gamma_pe, 1000)],
            day=day, ts=f"{day}T09:45:00+00:00",
        ))
    snapshot_rows = pd.concat(frames, ignore_index=True)
    daily = build_daily_series(snapshot_rows)
    assert len(daily) == 6
    regime = compute_regime(daily)
    last_row = regime.iloc[-1]
    assert last_row["net_gex"] == daily["net_gex"].max()  # most call-heavy day is the max
    assert last_row["regime"] == "STRONG_POS"  # it's the max of its own trailing window -> percentile 1.0
    # The first 4 sessions have <5 trailing observations -> no regime bucket.
    assert regime.iloc[:4]["regime"].isna().all()


def test_all_null_oi_session_is_skipped_not_zeroed():
    """Regression test for a real, critical bug: pandas .sum() defaults to
    skipna=True, so summing signed_gex() over a session where every contract
    row has NULL oi silently returned net_gex=0.0 -- a fabricated 'neutral,
    no dealer bias' reading built from zero real OI data (doctrine #5).
    Confirmed live: FINNIFTY 2026-08-04 09:15 UTC had exactly this shape (2
    contract rows, both oi=NULL) and got net_gex=0.0/regime=NEUTRAL stored."""
    rows = _rows([(24000, "CE", 0.001, None), (24000, "PE", 0.001, None)])
    daily = build_daily_series(rows)
    assert daily.empty, "a session with zero valid-oi rows must be skipped, not zeroed"


def test_partial_null_oi_rows_are_excluded_not_treated_as_zero_contribution():
    """One valid row (oi=1000) and one NULL-oi row (would-be oi=99999 if it
    had a real value) -- net_gex must reflect ONLY the valid row, not
    silently drop the NULL row's contribution while pretending the sum is
    complete."""
    rows = _rows([(24000, "CE", 0.001, 1000), (24100, "CE", 0.001, None)])
    daily = build_daily_series(rows)
    assert len(daily) == 1
    valid_only = _rows([(24000, "CE", 0.001, 1000)])
    expected = float(signed_gex(valid_only).sum())
    assert daily.iloc[0]["net_gex"] == pytest.approx(expected)
    assert daily.iloc[0]["n_strikes"] == 1, "the NULL-oi strike must not count toward n_strikes either"


def test_gamma_flip_level_finds_an_exact_zero_at_the_lowest_strike():
    """Regression test: the zero-crossing loop started at i=1 and never
    checked signs[0], so a cumulative sum that is EXACTLY zero at the
    lowest observed strike fell through to None instead of returning it.
    Needs >=2 distinct strikes (a single-strike group hits the module's own
    separate `len(by_strike) < 2` early-return, which is not this bug)."""
    # Strike 24000's own call/put GEX cancel exactly -> cumulative sum at
    # the lowest strike is 0.0. Strike 24100 adds a nonzero second point so
    # the group has >= 2 distinct strikes.
    rows = _rows([
        (24000, "CE", 0.001, 1000), (24000, "PE", 0.001, 1000),
        (24100, "CE", 0.001, 1000),
    ])
    flip = gamma_flip_level(rows)
    assert flip == pytest.approx(24000.0)


def test_gex_percentile_is_persisted_not_discarded():
    """Doctrine #1: net_gex/gamma_flip_level are raw and must not be the
    only stored reading -- the already-computed percentile (used for
    bucketing) must survive into the frame returned to the caller."""
    daily = pd.DataFrame({
        "symbol": ["TEST"] * 5, "day": pd.date_range("2026-08-01", periods=5),
        "ts": pd.date_range("2026-08-01", periods=5, tz="UTC"),
        "net_gex": [1.0, 2.0, 3.0, 4.0, 5.0], "gamma_flip_level": [None] * 5,
    })
    result = compute_regime(daily)
    assert "percentile" in result.columns
    assert result["percentile"].notna().any()
