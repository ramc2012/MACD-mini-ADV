"""Causal option-premium and IV ratios for the nonlinear selector.

One row is written per (30-minute timestamp, underlying, front expiry).  The
chain uses one timestamp and one expiry throughout; mixing expiries or later
prints would make a visually plausible but non-causal ratio.

25-delta wings are quality gated.  The nearest contract is accepted only when
it is within 0.08 delta of +/-0.25.  Thin chains therefore produce NULL wing
ratios, which the neural model receives with an explicit missingness flag.
Raw ITM/ATM/OTM premium ratios are represented by *extrinsic* value ratios so
spot movement is not counted again as option richness.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from features.m_implied_vol import solve_frame  # noqa: E402

DEFAULT_DSN = ""
IST = ZoneInfo("Asia/Kolkata")
WING_TARGET = 0.25
WING_TOLERANCE = 0.08
METHOD = (
    "front-expiry same-bar chain; nearest spot-ATM common strike; nearest ITM/OTM "
    "extrinsic ratios; quality-good 25d wings within 0.08; premium turnover PCR"
)

COLUMNS = (
    "ts", "symbol", "expiry", "spot", "atm_strike", "atm_call", "atm_put",
    "atm_iv", "straddle_to_spot", "normalized_straddle",
    "atm_put_call_premium_ratio", "atm_call_put_extrinsic_ratio",
    "call_itm_atm_extrinsic_ratio", "call_otm_atm_extrinsic_ratio",
    "put_itm_atm_extrinsic_ratio", "put_otm_atm_extrinsic_ratio",
    "call_wing_iv_ratio", "put_wing_iv_ratio", "strangle_straddle_ratio",
    "premium_pcr", "wing_valid", "call_wing_delta_gap", "put_wing_delta_gap",
    "n_strikes", "n_contracts", "method",
)


def _finite(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None


def _ratio(numerator, denominator) -> float | None:
    a, b = _finite(numerator), _finite(denominator)
    return None if a is None or b is None or b <= 0 else a / b


def _extrinsic(row: pd.Series, spot: float) -> float | None:
    premium = _finite(row.get("premium"))
    strike = _finite(row.get("strike"))
    if premium is None or strike is None:
        return None
    intrinsic = max(spot - strike, 0.0) if row["option_type"] == "CE" else max(strike - spot, 0.0)
    return max(0.0, premium - intrinsic)


def _one_at(chain: pd.DataFrame, side: str, strike: float) -> pd.Series | None:
    rows = chain[(chain["option_type"] == side) & np.isclose(chain["strike"], strike)]
    return None if rows.empty else rows.iloc[0]


def _nearest(chain: pd.DataFrame, side: str, spot: float, relation: str) -> pd.Series | None:
    rows = chain[chain["option_type"] == side]
    if relation == "below":
        rows = rows[rows["strike"] < spot]
    elif relation == "above":
        rows = rows[rows["strike"] > spot]
    if rows.empty:
        return None
    return rows.loc[(rows["strike"] - spot).abs().idxmin()]


def _wing(chain: pd.DataFrame, side: str, target: float) -> tuple[pd.Series | None, float | None]:
    rows = chain[(chain["option_type"] == side) & (chain["quality"] == "good")
                 & chain["delta"].notna() & chain["iv"].notna()]
    if rows.empty:
        return None, None
    gaps = (rows["delta"] - target).abs()
    index = gaps.idxmin()
    gap = float(gaps.loc[index])
    return (rows.loc[index] if gap <= WING_TOLERANCE else None), gap


def ratios_for_snapshot(frame: pd.DataFrame) -> dict | None:
    """Compute one timestamp/underlying row from a solved option chain."""
    if frame.empty:
        return None
    local_day = frame["dt"].iloc[0]
    valid = frame[pd.to_datetime(frame["expiry"]).dt.date > local_day]
    if valid.empty:
        return None
    expiry = valid["expiry"].min()
    chain = valid[valid["expiry"] == expiry].copy()
    spot_values = pd.to_numeric(chain["spot"], errors="coerce").dropna()
    if spot_values.empty:
        return None
    spot = float(spot_values.median())

    common = set(chain.loc[chain["option_type"] == "CE", "strike"]) & set(
        chain.loc[chain["option_type"] == "PE", "strike"])
    if not common:
        return None
    atm_strike = min(common, key=lambda strike: abs(float(strike) - spot))
    call = _one_at(chain, "CE", atm_strike)
    put = _one_at(chain, "PE", atm_strike)
    if call is None or put is None:
        return None
    call_price, put_price = _finite(call["premium"]), _finite(put["premium"])
    if call_price is None or put_price is None or call_price <= 0 or put_price <= 0:
        return None
    straddle = call_price + put_price
    dte = max(1.0, float((pd.Timestamp(expiry).date() - local_day).days))

    call_ext = _extrinsic(call, spot)
    put_ext = _extrinsic(put, spot)
    call_itm = _nearest(chain, "CE", spot, "below")
    call_otm = _nearest(chain, "CE", spot, "above")
    put_itm = _nearest(chain, "PE", spot, "above")
    put_otm = _nearest(chain, "PE", spot, "below")

    call_wing, call_gap = _wing(chain, "CE", WING_TARGET)
    put_wing, put_gap = _wing(chain, "PE", -WING_TARGET)
    wing_valid = call_wing is not None and put_wing is not None
    atm_ivs = [_finite(call.get("iv")), _finite(put.get("iv"))]
    atm_ivs = [value for value in atm_ivs if value is not None and value > 0]
    atm_iv = float(np.mean(atm_ivs)) if atm_ivs else None
    call_wing_iv = _finite(call_wing.get("iv")) if call_wing is not None else None
    put_wing_iv = _finite(put_wing.get("iv")) if put_wing is not None else None

    call_turnover = float((chain.loc[chain["option_type"] == "CE", "premium"]
                           * chain.loc[chain["option_type"] == "CE", "volume"].fillna(0)).sum())
    put_turnover = float((chain.loc[chain["option_type"] == "PE", "premium"]
                          * chain.loc[chain["option_type"] == "PE", "volume"].fillna(0)).sum())
    return {
        "ts": frame["ts"].iloc[0], "symbol": frame["symbol"].iloc[0],
        "expiry": expiry, "spot": spot, "atm_strike": float(atm_strike),
        "atm_call": call_price, "atm_put": put_price, "atm_iv": atm_iv,
        "straddle_to_spot": straddle / spot,
        "normalized_straddle": (straddle / spot) / np.sqrt(dte / 365.0),
        "atm_put_call_premium_ratio": put_price / call_price,
        "atm_call_put_extrinsic_ratio": _ratio(call_ext, put_ext),
        "call_itm_atm_extrinsic_ratio": _ratio(
            _extrinsic(call_itm, spot) if call_itm is not None else None, call_ext),
        "call_otm_atm_extrinsic_ratio": _ratio(
            _extrinsic(call_otm, spot) if call_otm is not None else None, call_ext),
        "put_itm_atm_extrinsic_ratio": _ratio(
            _extrinsic(put_itm, spot) if put_itm is not None else None, put_ext),
        "put_otm_atm_extrinsic_ratio": _ratio(
            _extrinsic(put_otm, spot) if put_otm is not None else None, put_ext),
        "call_wing_iv_ratio": _ratio(call_wing_iv, atm_iv),
        "put_wing_iv_ratio": _ratio(put_wing_iv, atm_iv),
        "strangle_straddle_ratio": (
            (float(call_wing["premium"]) + float(put_wing["premium"])) / straddle
            if wing_valid else None),
        "premium_pcr": _ratio(put_turnover, call_turnover),
        "wing_valid": wing_valid, "call_wing_delta_gap": call_gap,
        "put_wing_delta_gap": put_gap,
        "n_strikes": int(chain["strike"].nunique()),
        "n_contracts": int(len(chain)), "method": METHOD,
    }


def load_day(connection, day: date) -> pd.DataFrame:
    start = datetime.combine(day, time.min, IST).astimezone(timezone.utc)
    end = start + timedelta(days=1)
    query = """
        SELECT o.time AS ts, o.underlying AS symbol, o.expiry, o.strike,
               o.option_type, o.close AS premium, o.oi, o.volume, s.close AS spot
        FROM option_premium_candles o
        JOIN underlying_spot_candles s
          ON s.time=o.time AND s.underlying=o.underlying AND s.interval='30minute'
        WHERE o.interval='30minute' AND o.time >= %(start)s AND o.time < %(end)s
          AND o.close IS NOT NULL AND o.expiry > %(day)s
          AND o.option_type IN ('CE','PE')
    """
    with connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
        cursor.execute(query, {"start": start, "end": end, "day": day})
        frame = pd.DataFrame(cursor.fetchall())
    if frame.empty:
        return frame
    frame["dt"] = day
    for column in ("strike", "premium", "oi", "volume", "spot"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return solve_frame(frame)


def build_day(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=COLUMNS)
    rows = []
    for _, group in frame.groupby(["ts", "symbol"], sort=False):
        row = ratios_for_snapshot(group)
        if row is not None:
            rows.append(row)
    return pd.DataFrame(rows, columns=COLUMNS)


def _cell(value):
    if value is None or (isinstance(value, (float, np.floating)) and not np.isfinite(value)):
        return None
    return value.item() if isinstance(value, np.generic) else value


def upsert(connection, frame: pd.DataFrame) -> int:
    if frame.empty:
        return 0
    rows = [tuple(_cell(row[column]) for column in COLUMNS) for _, row in frame.iterrows()]
    updates = ", ".join(
        f"{column}=EXCLUDED.{column}" for column in COLUMNS
        if column not in ("ts", "symbol", "expiry")
    )
    with connection.cursor() as cursor:
        psycopg2.extras.execute_values(
            cursor,
            f"""INSERT INTO option_premium_ratios ({', '.join(COLUMNS)}) VALUES %s
                ON CONFLICT (ts, symbol, expiry) DO UPDATE SET
                {updates}, computed_at=now()""",
            rows, page_size=1000,
        )
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lookback-days", type=int, default=3)
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--end", type=date.fromisoformat)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--dsn", default=os.environ.get("VANGUARD_DATABASE_URL", DEFAULT_DSN))
    args = parser.parse_args()
    end = args.end or date.today()
    start = args.start or end - timedelta(days=args.lookback_days - 1)
    connection = psycopg2.connect(args.dsn)
    connection.autocommit = True
    total = written = 0
    try:
        day = start
        while day <= end:
            solved = load_day(connection, day)
            ratios = build_day(solved)
            total += len(ratios)
            if args.write:
                written += upsert(connection, ratios)
            if not ratios.empty:
                print(
                    f"{day}: {len(solved):,} contracts -> {len(ratios):,} snapshots; "
                    f"wings {int(ratios['wing_valid'].sum()):,}", flush=True)
            day += timedelta(days=1)
    finally:
        connection.close()
    print(f"ratio snapshots={total:,} written={written:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
