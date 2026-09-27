"""Black-Scholes delta, gamma and gamma exposure (GEX) for the option watchlist.

The Fyers websocket carries no greeks — its full-mode symbol payload has 23
fields and none of them is delta or gamma — so gamma is computed rather than
received. The chain is: observed premium -> implied volatility (bisection) ->
gamma -> GEX.

GEX convention (SqueezeMetrics): dealers are assumed long calls and short
puts, so call gamma contributes positively and put gamma negatively. The
figure is scaled to rupees of dealer gamma per 1% move in the underlying:

    GEX = gamma x OI x lot_size x spot^2 x 0.01,  signed by option type

Positive net GEX implies dealers dampen moves (they sell rallies, buy dips);
negative implies they amplify them. This is a MODEL output, not an exchange
figure — its accuracy depends on the implied volatility solved from a single
last-traded premium, which on an illiquid strike can be stale.
"""
from __future__ import annotations

import math
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
EXPIRY_TIME = time(15, 30)
TRADING_DAYS = 365.0
MIN_SIGMA, MAX_SIGMA = 0.01, 5.0
SOLVER_STEPS = 60


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def expiry_date(expiry: str) -> date | None:
    """The expiry as a date, or None when the string is not one."""
    try:
        return datetime.fromisoformat(expiry).date()
    except (TypeError, ValueError):
        return None


def years_to_expiry(expiry: str, now: datetime | None = None) -> float | None:
    """Year fraction to the 15:30 IST expiry, or None if unusable/expired."""
    day = expiry_date(expiry)
    if day is None:
        return None
    moment = (now or datetime.now(IST)).astimezone(IST)
    seconds = (datetime.combine(day, EXPIRY_TIME, IST) - moment).total_seconds()
    if seconds <= 0:
        return None
    return seconds / (TRADING_DAYS * 24 * 3600)


def black_scholes(spot: float, strike: float, years: float, rate: float,
                  sigma: float, is_call: bool) -> float:
    if spot <= 0 or strike <= 0 or years <= 0 or sigma <= 0:
        return 0.0
    d1 = (math.log(spot / strike) + (rate + 0.5 * sigma * sigma) * years) / (sigma * math.sqrt(years))
    d2 = d1 - sigma * math.sqrt(years)
    discount = math.exp(-rate * years)
    if is_call:
        return spot * _norm_cdf(d1) - strike * discount * _norm_cdf(d2)
    return strike * discount * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def implied_volatility(premium: float, spot: float, strike: float, years: float,
                       rate: float, is_call: bool) -> float | None:
    """Bisection solve. Bisection, not Newton: vega collapses on deep OTM
    strikes and a Newton step there diverges, which is exactly where an
    option watchlist spends much of its time."""
    if premium <= 0 or spot <= 0 or strike <= 0 or years <= 0:
        return None
    intrinsic = max(0.0, (spot - strike) if is_call else (strike - spot))
    if premium < intrinsic:
        # Below intrinsic value: a stale or crossed print, not a solvable quote.
        return None
    low, high = MIN_SIGMA, MAX_SIGMA
    if black_scholes(spot, strike, years, rate, high, is_call) < premium:
        return None                      # premium beyond the model's range
    for _ in range(SOLVER_STEPS):
        mid = 0.5 * (low + high)
        if black_scholes(spot, strike, years, rate, mid, is_call) < premium:
            low = mid
        else:
            high = mid
    return 0.5 * (low + high)


def gamma(spot: float, strike: float, years: float, rate: float, sigma: float) -> float:
    """Gamma is identical for calls and puts at the same strike."""
    if spot <= 0 or strike <= 0 or years <= 0 or sigma <= 0:
        return 0.0
    d1 = (math.log(spot / strike) + (rate + 0.5 * sigma * sigma) * years) / (sigma * math.sqrt(years))
    return _norm_pdf(d1) / (spot * sigma * math.sqrt(years))


def delta(spot: float, strike: float, years: float, rate: float, sigma: float,
          is_call: bool) -> float:
    """Signed Black-Scholes delta: 0..1 for a call, -1..0 for a put."""
    if spot <= 0 or strike <= 0 or years <= 0 or sigma <= 0:
        return 0.0
    d1 = (math.log(spot / strike) + (rate + 0.5 * sigma * sigma) * years) / (sigma * math.sqrt(years))
    return _norm_cdf(d1) if is_call else _norm_cdf(d1) - 1.0


def _intrinsic_delta(underlying: float, strike: float, is_call: bool) -> tuple[float, None, str]:
    in_the_money = underlying > strike if is_call else underlying < strike
    return ((1.0 if is_call else -1.0) if in_the_money else 0.0), None, "intrinsic"


def contract_delta(*, premium: float, underlying: float, strike: float, expiry: str,
                   option_type: str, rate: float = 0.065,
                   now: datetime | None = None) -> tuple[float, float | None, str]:
    """(delta, implied vol, source) for one traded premium.

    The whale tracker weights every OI change by delta, so a strike whose
    print the solver refuses must still get a number rather than drop out of
    the ranking. Below-intrinsic and out-of-range premiums are stale prints on
    illiquid strikes, almost always deep ITM or far OTM, where the delta is
    known without a model: 1 (or -1) inside the money, 0 outside. ``source``
    says which path produced the figure so a reader can see how much of a
    ranking rests on the fallback. Pass the futures price as ``underlying``
    with ``rate`` 0 when the chain carries one; forward pricing then absorbs
    the basis that a spot-plus-rate model only approximates.
    """
    is_call = str(option_type).upper() == "CE"
    if underlying <= 0 or strike <= 0:
        return 0.0, None, "none"
    years = years_to_expiry(expiry, now)
    if years is None:
        # No time left is not the same as no usable expiry. Between 15:30 and
        # the 15:40 F&O close on its own expiry day a contract still trades,
        # and those ten minutes are the unwind the collector was widened for;
        # its delta there is the intrinsic one, not zero for every strike.
        day = expiry_date(expiry)
        today = (now or datetime.now(IST)).astimezone(IST).date()
        if day is None or day < today:
            return 0.0, None, "none"
        return _intrinsic_delta(underlying, strike, is_call)
    sigma = None
    if premium and premium > 0:
        sigma = implied_volatility(premium, underlying, strike, years, rate, is_call)
    if sigma is None:
        return _intrinsic_delta(underlying, strike, is_call)
    return delta(underlying, strike, years, rate, sigma, is_call), sigma, "model"


def contract_gex(*, premium: float, spot: float, strike: float, expiry: str,
                 option_type: str, open_interest: int, lot_size: int,
                 rate: float = 0.065, now: datetime | None = None) -> dict:
    """Per-contract implied vol, gamma and signed GEX.

    Returns zeros/None rather than raising: the watchlist renders ~430 rows and
    a single unsolvable strike must not blank the column.
    """
    blank = {"iv": None, "gamma": None, "gex": None}
    if not (premium and spot and strike and lot_size) or open_interest is None:
        return blank
    years = years_to_expiry(expiry, now)
    if years is None:
        return blank
    is_call = str(option_type).upper() == "CE"
    sigma = implied_volatility(premium, spot, strike, years, rate, is_call)
    if sigma is None:
        return blank
    g = gamma(spot, strike, years, rate, sigma)
    exposure = g * max(0, int(open_interest)) * int(lot_size) * spot * spot * 0.01
    return {
        "iv": round(sigma * 100, 2),                     # percent
        "gamma": g,
        "gex": exposure if is_call else -exposure,
    }
