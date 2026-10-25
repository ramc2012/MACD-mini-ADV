"""Implied volatility and greeks, computed rather than sourced.

WHY COMPUTE INSTEAD OF ASKING A BROKER
--------------------------------------------------------------------------
`option_premium_candles.iv` and `.delta` stopped being populated for the
equity universe on 2026-07-28 and for gamma around 2026-06-23. M2's first
three ingredients (IVS, 25-delta skew, and the delta-weighted O/S ratio) all
read those columns, so the lane's entire options-flow feature has been
uncomputable for a month. Checked 2026-08-26: 5,856 contract rows, every one
with a `close`, not one with an `iv` or a `delta`.

Fyers can return an option chain with greeks, and this repo can reach one
through a sibling project's token. It is still the wrong answer here:

  1. Its `optionchain` endpoint resolves LIVE contracts only. It cannot
     backfill 2026-07-28 to now, and it cannot produce a single historical
     IV -- so the cross-sectional IC study, the backtest and every
     trailing-window z-score in M2 would remain unserviceable.
  2. It would make a core feature depend on another project's daily OAuth
     token, which is a shared-fate coupling this repo has already been
     bitten by.
  3. Everything required to solve for IV is already sitting in Postgres,
     for years back, with no auth of any kind.

So this module solves it. A broker's IV is itself a model output; there is
no observable "true" IV to prefer it over.

MODEL, AND ITS ASSUMPTIONS STATED PLAINLY
--------------------------------------------------------------------------
Black-Scholes-Merton, European exercise. NSE index AND stock options have
been European-style cash-settled since 2011, so this is the right model
family rather than a convenient approximation.

  * Forward is taken as S*exp(rT). There is no reliable per-stock futures
    price in this schema, so the basis a real desk would use is unavailable.
  * DIVIDENDS ARE NOT MODELLED. For a stock going ex-dividend inside the
    contract's life this biases call IV down and put IV up. It is flagged
    per row (`quality` gains `no_div_adj` only where a dividend is known to
    be missing -- which is never, today, because no dividend calendar is
    ingested) and is stated here rather than buried.
  * RISK_FREE_RATE is a constant. At Indian short rates a 100bp error moves
    a 30-day ATM IV by well under a vol point; it matters for deep ITM
    options, which the quality gate excludes anyway.

QUALITY IS A FIRST-CLASS OUTPUT, NOT AN AFTERTHOUGHT
--------------------------------------------------------------------------
An IV solved from a stale last-trade print on an illiquid strike is a
number, not a measurement. Every row carries `quality` (good | weak |
unusable) and `quality_flags` naming exactly what was wrong, and downstream
aggregation uses only `good` rows. The gates:

  no_solution   the price is outside the no-arbitrage band, so no sigma
                reproduces it -- almost always a stale or crossed print
  below_tick    premium at or under MIN_PREMIUM, where one tick is a large
                fraction of the price and IV is mostly quantisation
  thin_oi       open interest under MIN_OI
  no_volume     the contract did not trade in the session
  far_otm       |log(K/F)| beyond MAX_LOG_MONEYNESS
  vol_not_identified
                one price tick spans more than MAX_IV_UNCERTAINTY of sigma at
                the solution, so the price does not pin a volatility down
  extreme_iv    solved outside [IV_FLOOR, IV_CEILING]
  near_expiry   under MIN_DAYS_TO_EXPIRY, where T is small enough that
                small price errors explode into large IV errors

Writes to Vanguard's own `option_iv`. It never updates
`option_premium_candles` -- that table belongs to the live application, and
back-filling a column the live app owns is exactly the kind of cross-writing
this lane is built to avoid.

    python vanguard/features/m_implied_vol.py --lookback-days 5 --write
    python vanguard/features/m_implied_vol.py --start 2026-05-29 --end 2026-08-26 --write
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

# India short rate. See the docstring for why a constant is defensible here.
RISK_FREE_RATE = 0.065
TRADING_DAYS = 365.0          # calendar-time convention, matching NSE quotes

MIN_PREMIUM = 0.50            # rupees; below this a tick dominates the price
MIN_OI = 100                  # contracts
MAX_LOG_MONEYNESS = 0.35      # |ln(K/F)| beyond which vega is negligible
MIN_DAYS_TO_EXPIRY = 1.0
IV_FLOOR = 0.01
IV_CEILING = 3.00

# NSE quotes option premia in 5-paise ticks. That tick sets a hard floor on how
# precisely any price can pin down sigma:
#
#     sigma uncertainty  ~=  TICK_SIZE / vega
#
# Far from the money and at low vol, vega collapses -- a 15%-OTM contract at 8%
# vol on a 0.15y expiry has vega 2.7e-05, so one tick of price is worth roughly
# 1,800 vol points of sigma. The price simply does not identify sigma there,
# and a solver that answers anyway returns a plausible-looking wrong number:
# measured here, a true 8% vol came back as 9.3%. That is the quiet failure
# mode an IV solver has, so the solver refuses instead, and reports the
# uncertainty alongside every IV it does return.
TICK_SIZE = 0.05
MAX_IV_UNCERTAINTY = 0.05     # 5 vol points of tick-implied uncertainty
NEWTON_STEPS = 12
NEWTON_TOL = 1e-6
BISECTION_STEPS = 60


# --------------------------------------------------------------------------
# Black-Scholes (vectorised; every function takes and returns numpy arrays)
# --------------------------------------------------------------------------
def _norm_cdf(x: np.ndarray) -> np.ndarray:
    """Standard normal CDF via the error function.

    scipy.stats.norm.cdf is ~40x slower per call and this runs over hundreds
    of thousands of contracts; the identity is exact, not an approximation.
    """
    from scipy.special import erf
    return 0.5 * (1.0 + erf(x / np.sqrt(2.0)))


def _norm_pdf(x: np.ndarray) -> np.ndarray:
    return np.exp(-0.5 * x * x) / np.sqrt(2.0 * np.pi)


def bs_price(spot, strike, t_years, sigma, is_call, rate=RISK_FREE_RATE):
    """Black-Scholes price. Degenerate inputs return intrinsic, never NaN."""
    spot = np.asarray(spot, dtype=float)
    strike = np.asarray(strike, dtype=float)
    t_years = np.asarray(t_years, dtype=float)
    sigma = np.asarray(sigma, dtype=float)
    is_call = np.asarray(is_call, dtype=bool)

    valid = (t_years > 0) & (sigma > 0) & (spot > 0) & (strike > 0)
    with np.errstate(divide="ignore", invalid="ignore"):
        sqrt_t = np.sqrt(np.where(valid, t_years, 1.0))
        d1 = (np.log(np.where(valid, spot / strike, 1.0))
              + (rate + 0.5 * sigma ** 2) * np.where(valid, t_years, 1.0)) / (sigma * sqrt_t)
        d2 = d1 - sigma * sqrt_t
        discount = np.exp(-rate * np.where(valid, t_years, 0.0))
        call = spot * _norm_cdf(d1) - strike * discount * _norm_cdf(d2)
        put = strike * discount * _norm_cdf(-d2) - spot * _norm_cdf(-d1)
        price = np.where(is_call, call, put)

    intrinsic = np.where(is_call, np.maximum(spot - strike, 0.0), np.maximum(strike - spot, 0.0))
    return np.where(valid, price, intrinsic)


def bs_vega(spot, strike, t_years, sigma, rate=RISK_FREE_RATE):
    """dPrice/dSigma. Identical for calls and puts."""
    spot = np.asarray(spot, dtype=float)
    strike = np.asarray(strike, dtype=float)
    t_years = np.asarray(t_years, dtype=float)
    sigma = np.asarray(sigma, dtype=float)
    valid = (t_years > 0) & (sigma > 0) & (spot > 0) & (strike > 0)
    with np.errstate(divide="ignore", invalid="ignore"):
        sqrt_t = np.sqrt(np.where(valid, t_years, 1.0))
        d1 = (np.log(np.where(valid, spot / strike, 1.0))
              + (rate + 0.5 * sigma ** 2) * np.where(valid, t_years, 1.0)) / (sigma * sqrt_t)
        vega = spot * _norm_pdf(d1) * sqrt_t
    return np.where(valid, vega, 0.0)


def bs_greeks(spot, strike, t_years, sigma, is_call, rate=RISK_FREE_RATE) -> dict:
    """Delta / gamma / vega / theta, analytic, once sigma is known.

    Delta is what the 25-delta skew needs and what the O/S ratio weights by;
    both have been unavailable since the broker's own greeks stopped.
    """
    spot = np.asarray(spot, dtype=float)
    strike = np.asarray(strike, dtype=float)
    t_years = np.asarray(t_years, dtype=float)
    sigma = np.asarray(sigma, dtype=float)
    is_call = np.asarray(is_call, dtype=bool)
    valid = (t_years > 0) & (sigma > 0) & (spot > 0) & (strike > 0)

    with np.errstate(divide="ignore", invalid="ignore"):
        sqrt_t = np.sqrt(np.where(valid, t_years, 1.0))
        d1 = (np.log(np.where(valid, spot / strike, 1.0))
              + (rate + 0.5 * sigma ** 2) * np.where(valid, t_years, 1.0)) / (sigma * sqrt_t)
        d2 = d1 - sigma * sqrt_t
        discount = np.exp(-rate * np.where(valid, t_years, 0.0))
        pdf = _norm_pdf(d1)

        delta = np.where(is_call, _norm_cdf(d1), _norm_cdf(d1) - 1.0)
        gamma = pdf / (spot * sigma * sqrt_t)
        vega = spot * pdf * sqrt_t / 100.0                       # per 1 vol point
        theta_common = -(spot * pdf * sigma) / (2.0 * sqrt_t)
        theta_call = theta_common - rate * strike * discount * _norm_cdf(d2)
        theta_put = theta_common + rate * strike * discount * _norm_cdf(-d2)
        theta = np.where(is_call, theta_call, theta_put) / TRADING_DAYS   # per day

    nan = np.full(spot.shape, np.nan)
    return {
        "delta": np.where(valid, delta, nan),
        "gamma": np.where(valid, gamma, nan),
        "vega": np.where(valid, vega, nan),
        "theta": np.where(valid, theta, nan),
    }


def implied_vol(price, spot, strike, t_years, is_call, rate=RISK_FREE_RATE):
    """Solve for sigma. Newton on vega, then bisection for whatever it misses.

    Newton alone is not safe here: vega collapses toward zero for deep
    in/out-of-the-money contracts, so a Newton step divides by something near
    nothing and can leave the bracket entirely. Newton does the work on the
    well-conditioned majority; every row that has not converged falls through
    to bisection, which cannot diverge. Rows whose price sits outside the
    no-arbitrage band get NaN -- there is no sigma that reproduces them, and
    returning a boundary value would fabricate one.

    Returns (sigma, uncertainty). `uncertainty` is the sigma range one price
    tick spans at the solution, and sigma is NaN wherever that exceeds
    MAX_IV_UNCERTAINTY -- the price did not identify a volatility, so none is
    reported. The uncertainty itself is always returned, including for rejected
    rows, so the reason for a NaN is inspectable rather than mysterious.
    """
    price = np.asarray(price, dtype=float)
    spot = np.asarray(spot, dtype=float)
    strike = np.asarray(strike, dtype=float)
    t_years = np.asarray(t_years, dtype=float)
    is_call = np.asarray(is_call, dtype=bool)

    discount = np.exp(-rate * np.maximum(t_years, 0.0))
    intrinsic = np.where(is_call,
                         np.maximum(spot - strike * discount, 0.0),
                         np.maximum(strike * discount - spot, 0.0))
    upper_bound = np.where(is_call, spot, strike * discount)

    solvable = (
        (t_years > 0) & (spot > 0) & (strike > 0) & np.isfinite(price)
        & (price > intrinsic + 1e-9) & (price < upper_bound - 1e-9)
    )

    sigma = np.full(price.shape, 0.35)          # a sane ATM starting point
    for _ in range(NEWTON_STEPS):
        diff = bs_price(spot, strike, t_years, sigma, is_call, rate) - price
        vega = bs_vega(spot, strike, t_years, sigma, rate)
        with np.errstate(divide="ignore", invalid="ignore"):
            step = np.where(vega > 1e-8, diff / np.where(vega > 1e-8, vega, 1.0), 0.0)
        step = np.clip(step, -0.5, 0.5)          # no wild jumps out of the bracket
        sigma = np.clip(sigma - step, IV_FLOOR, IV_CEILING)
        if np.all(np.abs(diff[solvable]) < NEWTON_TOL) if solvable.any() else True:
            break

    residual = np.abs(bs_price(spot, strike, t_years, sigma, is_call, rate) - price)
    needs_bisection = solvable & ~(residual < 1e-4)
    if needs_bisection.any():
        low = np.full(price.shape, IV_FLOOR)
        high = np.full(price.shape, IV_CEILING)
        for _ in range(BISECTION_STEPS):
            mid = 0.5 * (low + high)
            value = bs_price(spot, strike, t_years, mid, is_call, rate)
            too_low = value < price
            low = np.where(too_low, mid, low)
            high = np.where(too_low, high, mid)
        sigma = np.where(needs_bisection, 0.5 * (low + high), sigma)

    sigma = np.where(solvable, sigma, np.nan)

    # Conditioning gate. vega is evaluated AT the solved sigma, which is the
    # only place the sensitivity is the one that actually applied.
    vega_at_solution = bs_vega(spot, strike, t_years, sigma, rate)
    with np.errstate(divide="ignore", invalid="ignore"):
        uncertainty = np.where(vega_at_solution > 0, TICK_SIZE / vega_at_solution, np.inf)
    identified = np.isfinite(uncertainty) & (uncertainty <= MAX_IV_UNCERTAINTY)
    return np.where(identified, sigma, np.nan), np.where(np.isfinite(uncertainty), uncertainty, np.nan)


# --------------------------------------------------------------------------
# Quality gating
# --------------------------------------------------------------------------
def assess_quality(frame: pd.DataFrame) -> pd.DataFrame:
    """Attach `quality` and `quality_flags` to a solved frame.

    An IV solved off a stale last-trade print on an untraded strike is a
    number, not a measurement. The earlier review of this lane called out
    exactly this -- "IV from last-trade on illiquid names is stale/garbage;
    need an iv_quality flag, and do not score names that fail it" -- and the
    aggregation below honours it by using `good` rows only.
    """
    flags = [[] for _ in range(len(frame))]

    def mark(mask: pd.Series, label: str) -> None:
        for index in np.flatnonzero(np.asarray(mask, dtype=bool)):
            flags[index].append(label)

    # An unidentified IV and an unsolvable price are different failures needing
    # different responses, so they are flagged separately rather than both
    # landing under "no_solution".
    unidentified = frame["iv"].isna() & frame.get(
        "iv_uncertainty", pd.Series(np.nan, index=frame.index)).notna()
    mark(frame["iv"].isna() & ~unidentified, "no_solution")
    mark(unidentified, "vol_not_identified")
    mark(frame["premium"] <= MIN_PREMIUM, "below_tick")
    mark(frame["oi"].fillna(0) < MIN_OI, "thin_oi")
    mark(frame["volume"].fillna(0) <= 0, "no_volume")
    mark(frame["log_moneyness"].abs() > MAX_LOG_MONEYNESS, "far_otm")
    mark((frame["iv"] <= IV_FLOOR * 1.01) | (frame["iv"] >= IV_CEILING * 0.99), "extreme_iv")
    mark(frame["days_to_expiry"] < MIN_DAYS_TO_EXPIRY, "near_expiry")

    frame = frame.copy()
    frame["quality_flags"] = [",".join(f) for f in flags]

    # "unusable" means the number is not a measurement of anything.
    # "weak" means it is real but thin -- kept, visible, and excluded from
    # aggregates. Only "good" feeds a feature.
    unusable = frame["quality_flags"].str.contains(
        "no_solution|vol_not_identified|extreme_iv|below_tick|near_expiry", regex=True)
    weak = frame["quality_flags"].str.len() > 0
    frame["quality"] = np.where(unusable, "unusable", np.where(weak, "weak", "good"))
    return frame


# --------------------------------------------------------------------------
# Loaders
# --------------------------------------------------------------------------
def load_universe(connection) -> list[str]:
    frame = pd.read_sql("SELECT symbol FROM sector_taxonomy", connection)
    return sorted(frame["symbol"].unique())


def load_chain(connection, symbols: list[str], start: date, end: date) -> pd.DataFrame:
    """End-of-session contract prints, with spot joined from the same session.

    Spot comes from `underlying_spot_candles` rather than
    `option_premium_candles.underlying_price`: that column stopped being
    populated entirely after 2026-07-30 and would silently NULL out every
    recent row.

    NO GRID FILTER ON THE SPOT LEG, deliberately. NSE STOCKS bar on the
    exchange's 09:15-anchored :15/:45 grid, but the INDICES in this table bar
    on :00/:30 -- verified 2026-08-27: NIFTY and BANKNIFTY have 115 of 128
    recent bars at :00/:30 while RELIANCE has none there at all. Applying
    m5_timing's grid rule here therefore threw away the index spot entirely and
    left NIFTY with a solved chain on 8 sessions and a joinable spot on 1, so
    `market_sentiment.index_atm_iv` was almost always NULL.
    That grid rule exists to keep BAR SEQUENCES consistent for RVOL and VWAP.
    A daily closing spot needs no such thing -- only that the print is inside
    the session, which is all this bounds.
    """
    query = """
        WITH ranked AS (
            SELECT underlying, expiry, strike, option_type, close AS premium,
                   oi, volume,
                   date(time AT TIME ZONE 'Asia/Kolkata') AS dt,
                   ROW_NUMBER() OVER (
                       PARTITION BY underlying, expiry, strike, option_type,
                                    date(time AT TIME ZONE 'Asia/Kolkata')
                       ORDER BY time DESC, (close IS NULL) ASC) AS rn
            FROM option_premium_candles
            WHERE underlying = ANY(%(symbols)s) AND interval = '30minute'
              AND time >= %(start)s AND time < %(end)s
              AND close IS NOT NULL
        ),
        spot AS (
            SELECT DISTINCT ON (underlying, date(time AT TIME ZONE 'Asia/Kolkata'))
                   underlying,
                   date(time AT TIME ZONE 'Asia/Kolkata') AS dt,
                   close AS spot
            FROM underlying_spot_candles
            WHERE underlying = ANY(%(symbols)s) AND interval = '30minute'
              AND time >= %(start)s AND time < %(end)s
              AND (time AT TIME ZONE 'Asia/Kolkata')::time
                  BETWEEN TIME '09:15' AND TIME '15:30'
            ORDER BY underlying, date(time AT TIME ZONE 'Asia/Kolkata'), time DESC
        )
        SELECT r.dt, r.underlying AS symbol, r.expiry, r.strike, r.option_type,
               r.premium, r.oi, r.volume, s.spot
        FROM ranked r
        JOIN spot s ON s.underlying = r.underlying AND s.dt = r.dt
        WHERE r.rn = 1 AND r.expiry > r.dt
    """
    frame = pd.read_sql(query, connection, params={
        "symbols": symbols, "start": start, "end": end + timedelta(days=1)})
    for col in ("strike", "premium", "spot"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    for col in ("oi", "volume"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    return frame


def solve_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Add days_to_expiry, log_moneyness, iv, greeks and quality to a chain."""
    if frame.empty:
        return frame
    out = frame.copy()
    out["days_to_expiry"] = (pd.to_datetime(out["expiry"]) - pd.to_datetime(out["dt"])).dt.days.astype(float)
    t_years = out["days_to_expiry"] / TRADING_DAYS
    is_call = (out["option_type"] == "CE").to_numpy()

    forward = out["spot"].to_numpy() * np.exp(RISK_FREE_RATE * t_years.to_numpy())
    with np.errstate(divide="ignore", invalid="ignore"):
        out["log_moneyness"] = np.log(out["strike"].to_numpy() / forward)

    iv, uncertainty = implied_vol(out["premium"].to_numpy(), out["spot"].to_numpy(),
                                   out["strike"].to_numpy(), t_years.to_numpy(), is_call)
    out["iv"] = iv
    out["iv_uncertainty"] = uncertainty
    greeks = bs_greeks(out["spot"].to_numpy(), out["strike"].to_numpy(),
                       t_years.to_numpy(), out["iv"].to_numpy(), is_call)
    for name, values in greeks.items():
        out[name] = values
    return assess_quality(out)


COLUMNS = ("dt", "symbol", "expiry", "strike", "option_type", "premium", "spot",
           "oi", "volume", "days_to_expiry", "log_moneyness",
           "iv", "iv_uncertainty", "delta", "gamma", "vega", "theta",
           "quality", "quality_flags")


def _cell(value, column):
    if column in ("dt", "symbol", "expiry", "option_type", "quality", "quality_flags"):
        return value
    if value is None or (isinstance(value, float) and (np.isnan(value) or np.isinf(value))):
        return None
    if pd.isna(value):
        return None
    if column in ("oi", "volume"):
        return int(value)
    return float(value)


def upsert(connection, frame: pd.DataFrame) -> int:
    if frame.empty:
        return 0
    rows = [tuple(_cell(row[c], c) for c in COLUMNS) for _, row in frame.iterrows()]
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in COLUMNS
                        if c not in ("dt", "symbol", "expiry", "strike", "option_type"))
    with connection.cursor() as cursor:
        psycopg2.extras.execute_values(
            cursor,
            f"""INSERT INTO option_iv ({", ".join(COLUMNS)}) VALUES %s
                ON CONFLICT (dt, symbol, expiry, strike, option_type)
                DO UPDATE SET {updates}, computed_at = now()""",
            rows, page_size=2000,
        )
    return len(rows)


def run(dsn: str, start: date, end: date, write: bool, chunk_days: int = 5) -> dict:
    connection = psycopg2.connect(dsn)
    connection.autocommit = True
    try:
        symbols = load_universe(connection)
        total_rows = 0
        written = 0
        quality_counts: dict[str, int] = {}
        # Chunked because a single unbounded pull over option_premium_candles
        # is exactly the query shape that was confirmed to still be running
        # after nine minutes against the shared production instance.
        cursor_day = start
        while cursor_day <= end:
            chunk_end = min(cursor_day + timedelta(days=chunk_days - 1), end)
            chain = load_chain(connection, symbols, cursor_day, chunk_end)
            if not chain.empty:
                solved = solve_frame(chain)
                total_rows += len(solved)
                for key, value in solved["quality"].value_counts().items():
                    quality_counts[key] = quality_counts.get(key, 0) + int(value)
                if write:
                    written += upsert(connection, solved)
            cursor_day = chunk_end + timedelta(days=1)
        return {"rows": total_rows, "written": written, "quality": quality_counts}
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lookback-days", type=int, default=5)
    parser.add_argument("--start", type=date.fromisoformat, default=None)
    parser.add_argument("--end", type=date.fromisoformat, default=None)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--dsn", default=os.environ.get("VANGUARD_DATABASE_URL", DEFAULT_DSN))
    args = parser.parse_args()

    end = args.end or date.today()
    start = args.start or (end - timedelta(days=args.lookback_days))
    print(f"solving implied vol for {start} .. {end}  (r={RISK_FREE_RATE:.3f}, European BSM)")

    result = run(args.dsn, start, end, args.write)
    if not result["rows"]:
        print("no priced contracts in this window")
        return 1
    print(f"contracts solved: {result['rows']:,}")
    total = sum(result["quality"].values()) or 1
    for label in ("good", "weak", "unusable"):
        count = result["quality"].get(label, 0)
        print(f"  {label:<9} {count:>8,}  ({100.0 * count / total:.1f}%)")
    print("\nOnly `good` rows feed the IV surface — see assess_quality() for the gates.")
    if args.write:
        print(f"wrote {result['written']:,} rows to option_iv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
