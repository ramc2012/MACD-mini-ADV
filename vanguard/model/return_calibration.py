"""Experimental validation-only payoff calibration; ranks are not returns.

Uncertainty is measured across source sessions, never across correlated
contracts or horizons. A frozen model without sufficient calibration evidence
can still rank paper observations, but cannot claim a positive expected edge.
"""
from __future__ import annotations

import numpy as np

MIN_SESSIONS = 20


def calibrate_returns(scores, net_returns, sessions, horizons, *, bins=5):
    scores, net_returns = np.asarray(scores), np.asarray(net_returns)
    sessions, horizons = np.asarray(sessions).astype(str), np.asarray(horizons)
    result = {"basis": "validation_only_net_option_return", "min_sessions": MIN_SESSIONS,
              "bins": []}
    # Keep D+1/D+2/D+3 calibration separate; mixing horizons makes a score
    # look profitable simply because a longer holding period had more time.
    for horizon in (1, 2, 3):
        eligible = (horizons == horizon) & np.isfinite(scores) & np.isfinite(net_returns)
        if not eligible.any():
            continue
        boundaries = np.unique(np.quantile(scores[eligible], np.linspace(0, 1, bins + 1)))
        if len(boundaries) < 2:
            continue
        for number, (lo, hi) in enumerate(zip(boundaries[:-1], boundaries[1:])):
            mask = eligible & (scores >= lo) & ((scores <= hi) if number == len(boundaries)-2 else (scores < hi))
            daily = [float(np.mean(net_returns[mask & (sessions == day)]))
                     for day in sorted(set(sessions[mask]))]
            n = len(daily)
            mean = float(np.mean(daily)) if n else None
            se = float(np.std(daily, ddof=1) / np.sqrt(n)) if n > 1 else None
            result["bins"].append(dict(horizon=horizon, lo=float(lo), hi=float(hi),
                sessions=n, expected_net_return=mean,
                expected_net_lower=mean - 1.96 * se if n >= MIN_SESSIONS else None))
    return result


def expected_net_return(calibration, score, horizon):
    refusal = {"expected_net_return": None, "expected_net_lower": None,
               "return_refusal": "net option return calibration unavailable or insufficient"}
    if not calibration or calibration.get("basis") != "validation_only_net_option_return":
        return refusal
    for bucket in calibration.get("bins", []):
        if bucket["horizon"] != horizon or not bucket["lo"] <= score <= bucket["hi"]:
            continue
        lower = bucket.get("expected_net_lower")
        if bucket.get("sessions", 0) < MIN_SESSIONS or lower is None or not np.isfinite(lower):
            return refusal
        return {k: bucket[k] for k in ("expected_net_return", "expected_net_lower")} | {
            "return_refusal": None if lower > 0 else "expected net return lower bound is not positive"}
    return refusal  # no extrapolation outside the calibrated score range
