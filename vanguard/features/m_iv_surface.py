"""Per-underlying IV surface, aggregated from Vanguard's own computed IVs.

Reads `option_iv` (quality='good' rows only) and produces one row per
(symbol, session): ATM IV, the Cremers-Weinbaum call-minus-put spread, a
25-delta risk reversal WHERE ONE GENUINELY EXISTS, and the trailing context
that turns a raw vol level into something a signal can use.

WHY skew_25d IS USUALLY NULL, AND WHY THAT IS THE HONEST ANSWER
--------------------------------------------------------------------------
Measured across the whole collected history on 2026-08-27, contracts per
symbol per day peaked at 6.6 in early July and had fallen to 1.2 by late
August. A 25-delta contract sits well out on the wing; locating one needs a
chain spanning perhaps ten to twenty strikes. Three to six strikes clustered
around the money do not contain one, and never did.

m2_flow.py computes its skew with `idxmin(|delta - 0.25|)` and no tolerance
band, so it silently accepted whatever strike was nearest -- a near-ATM
contract. Its SKEW ingredient (25% of the composite) was therefore measuring
approximately the same thing as its IVS ingredient (30%), which means 55% of
the flow score was one quantity counted twice.

This module refuses that substitution. `skew_25d` is populated only when
contracts within SKEW_DELTA_TOLERANCE of +/-0.25 exist on BOTH wings, and
`skew_reason` names what was missing otherwise. `n_strikes` and `delta_span`
travel with every row so nobody has to guess how much chain is behind a
number.

    python vanguard/features/m_iv_surface.py --lookback-days 120 --write
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras

DEFAULT_DSN = ""

NEAR_ATM_STRIKES = 2          # +/- this many strikes around the money
SKEW_TARGET_DELTA = 0.25
SKEW_DELTA_TOLERANCE = 0.08   # a 0.17-0.33 delta still describes the wing;
                              # anything nearer the money is not a risk reversal
IV_WINDOW = 60                # sessions of trailing context
IV_MIN_PERIODS = 10


def load_good_ivs(connection, start: date, end: date) -> pd.DataFrame:
    query = """
        SELECT dt, symbol, expiry, strike, option_type, iv, delta, spot, oi, volume
        FROM option_iv
        WHERE quality = 'good' AND iv IS NOT NULL
          AND dt >= %(start)s AND dt <= %(end)s
    """
    frame = pd.read_sql(query, connection, params={"start": start, "end": end})
    for col in ("strike", "iv", "delta", "spot"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    return frame


def surface_for_session(group: pd.DataFrame) -> dict:
    """One (symbol, session) chain -> one surface row.

    The front series is the smallest expiry present. It is chosen per session
    rather than per symbol so a roll shows up as a roll, not as a vol jump.
    """
    front_expiry = group["expiry"].min()
    chain = group[group["expiry"] == front_expiry]
    spot = float(chain["spot"].iloc[0]) if len(chain) else np.nan

    strikes = sorted(chain["strike"].dropna().unique())
    result: dict = {
        "expiry": front_expiry, "spot": spot,
        "n_strikes": len(strikes), "n_good": len(chain),
        "atm_iv": np.nan, "atm_strike": np.nan,
        "call_iv": np.nan, "put_iv": np.nan, "ivs": np.nan,
        "skew_25d": np.nan, "skew_reason": None,
        "delta_span": np.nan,
    }
    if not strikes or not np.isfinite(spot):
        result["skew_reason"] = "no chain"
        return result

    deltas = chain["delta"].dropna()
    if not deltas.empty:
        result["delta_span"] = float(deltas.max() - deltas.min())

    atm_strike = min(strikes, key=lambda s: abs(s - spot))
    index = strikes.index(atm_strike)
    near = set(strikes[max(0, index - NEAR_ATM_STRIKES): index + NEAR_ATM_STRIKES + 1])
    near_chain = chain[chain["strike"].isin(near)]
    call_iv = near_chain.loc[near_chain["option_type"] == "CE", "iv"].dropna()
    put_iv = near_chain.loc[near_chain["option_type"] == "PE", "iv"].dropna()

    result["atm_strike"] = float(atm_strike)
    atm_rows = chain[chain["strike"] == atm_strike]["iv"].dropna()
    if not atm_rows.empty:
        result["atm_iv"] = float(atm_rows.mean())
    if not call_iv.empty:
        result["call_iv"] = float(call_iv.mean())
    if not put_iv.empty:
        result["put_iv"] = float(put_iv.mean())
    # IVS needs BOTH sides. A one-sided "spread" is a level.
    if not call_iv.empty and not put_iv.empty:
        result["ivs"] = float(call_iv.mean() - put_iv.mean())

    result.update(_risk_reversal(chain))
    return result


def _risk_reversal(chain: pd.DataFrame) -> dict:
    """25-delta put IV minus 25-delta call IV, or a reason it does not exist."""
    puts = chain[(chain["option_type"] == "PE") & chain["delta"].notna() & chain["iv"].notna()]
    calls = chain[(chain["option_type"] == "CE") & chain["delta"].notna() & chain["iv"].notna()]
    if puts.empty or calls.empty:
        return {"skew_25d": np.nan, "skew_reason": "one wing has no priced contracts"}

    put_gap = (puts["delta"] - (-SKEW_TARGET_DELTA)).abs()
    call_gap = (calls["delta"] - SKEW_TARGET_DELTA).abs()
    best_put = float(put_gap.min())
    best_call = float(call_gap.min())
    if best_put > SKEW_DELTA_TOLERANCE or best_call > SKEW_DELTA_TOLERANCE:
        return {
            "skew_25d": np.nan,
            "skew_reason": (
                f"no contract within {SKEW_DELTA_TOLERANCE:.2f} of 25-delta "
                f"(nearest put {best_put:.2f} away, call {best_call:.2f} away) — "
                "the collected chain does not reach the wings"
            ),
        }
    put_iv = float(puts.loc[put_gap.idxmin(), "iv"])
    call_iv = float(calls.loc[call_gap.idxmin(), "iv"])
    return {"skew_25d": put_iv - call_iv, "skew_reason": None}


def build(ivs: pd.DataFrame) -> pd.DataFrame:
    if ivs.empty:
        return pd.DataFrame()
    rows = []
    for (symbol, dt), group in ivs.groupby(["symbol", "dt"]):
        row = surface_for_session(group)
        row.update(symbol=symbol, dt=dt)
        rows.append(row)
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame

    out = []
    for symbol, group in frame.sort_values("dt").groupby("symbol"):
        group = group.copy()
        atm = group["atm_iv"]
        # Percentile of the CURRENT value among the window's valid values, and
        # NaN when the current value is itself missing -- the same trap
        # m2_flow's rolling percentile fell into, where an all-False comparison
        # against NaN silently returned 0.0, the most extreme possible reading.
        def pct_of_last(values: np.ndarray) -> float:
            current = values[-1]
            if np.isnan(current):
                return np.nan
            valid = values[~np.isnan(values)]
            return np.nan if valid.size == 0 else float((valid <= current).mean())

        group["iv_percentile"] = atm.rolling(IV_WINDOW, min_periods=IV_MIN_PERIODS).apply(
            pct_of_last, raw=True)
        low = atm.rolling(IV_WINDOW, min_periods=IV_MIN_PERIODS).min()
        high = atm.rolling(IV_WINDOW, min_periods=IV_MIN_PERIODS).max()
        span = (high - low).replace(0, np.nan)
        group["iv_rank"] = (atm - low) / span
        group["d_atm_iv"] = atm.diff()
        out.append(group)
    return pd.concat(out, ignore_index=True)


COLUMNS = ("dt", "symbol", "expiry", "atm_iv", "atm_strike", "spot", "call_iv", "put_iv",
           "ivs", "skew_25d", "skew_reason", "iv_percentile", "iv_rank", "d_atm_iv",
           "n_strikes", "n_good", "delta_span")
TEXT_OR_DATE = {"dt", "symbol", "expiry", "skew_reason"}
INT_COLUMNS = {"n_strikes", "n_good"}


def _cell(value, column):
    if column in TEXT_OR_DATE:
        return None if (value is None or (isinstance(value, float) and np.isnan(value))) else value
    if value is None or pd.isna(value):
        return None
    return int(value) if column in INT_COLUMNS else float(value)


def upsert(connection, frame: pd.DataFrame) -> int:
    if frame.empty:
        return 0
    rows = [tuple(_cell(row[c], c) for c in COLUMNS) for _, row in frame.iterrows()]
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in COLUMNS if c not in ("dt", "symbol"))
    with connection.cursor() as cursor:
        psycopg2.extras.execute_values(
            cursor,
            f"""INSERT INTO iv_surface ({", ".join(COLUMNS)}) VALUES %s
                ON CONFLICT (dt, symbol) DO UPDATE SET {updates}, computed_at = now()""",
            rows, page_size=1000,
        )
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lookback-days", type=int, default=120)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--dsn", default=os.environ.get("VANGUARD_DATABASE_URL", DEFAULT_DSN))
    args = parser.parse_args()

    connection = psycopg2.connect(args.dsn)
    connection.autocommit = True
    try:
        end = date.today()
        start = end - timedelta(days=args.lookback_days)
        ivs = load_good_ivs(connection, start, end)
        print(f"good contract IVs in window: {len(ivs):,}")
        if ivs.empty:
            print("nothing to aggregate — run features/m_implied_vol.py first")
            return 1

        frame = build(ivs)
        print(f"surface rows: {len(frame):,}  {frame['dt'].min()} .. {frame['dt'].max()}")
        print(f"\natm_iv       non-NULL for {frame['atm_iv'].notna().sum():,}/{len(frame):,}")
        print(f"ivs          non-NULL for {frame['ivs'].notna().sum():,}/{len(frame):,}")
        print(f"skew_25d     non-NULL for {frame['skew_25d'].notna().sum():,}/{len(frame):,}")
        if frame["skew_25d"].isna().any():
            reasons = frame.loc[frame["skew_25d"].isna(), "skew_reason"].dropna()
            if not reasons.empty:
                top = reasons.str.split("(").str[0].value_counts().head(3)
                print("  why skew is NULL:")
                for reason, count in top.items():
                    print(f"    {count:>6,}  {reason.strip()}")
        print(f"\nmedian strikes per chain: {frame['n_strikes'].median():.0f}"
              f"   median delta span: {frame['delta_span'].median():.2f}")
        print(f"median ATM IV: {frame['atm_iv'].median():.4f}")

        if args.write:
            print(f"\nwrote {upsert(connection, frame):,} rows to iv_surface")
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
