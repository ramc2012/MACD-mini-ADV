"""Black-Scholes implied volatility and Greeks for NSE/BSE options, and per-book exposure.

Used for measurement only (the blast journal's shadow fields and the book Greeks view); no entry or exit
reads them. European Black-Scholes on spot with r = 6.5% and no dividend: index options are European, and
for stock options this is the usual screen approximation. Research basis: research/option_recs_20261015.
"""
from __future__ import annotations

import math
from datetime import datetime, time
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
RATE = 0.065
EXPIRY_CLOSE = time(15, 30)
YEAR_SECONDS = 365.0 * 86400


def _cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def years_to_expiry(expiry_iso: str, now: datetime) -> float:
    close = datetime.combine(datetime.fromisoformat(expiry_iso[:10]).date(), EXPIRY_CLOSE, IST)
    return (close - now.astimezone(IST)).total_seconds() / YEAR_SECONDS


def price(spot: float, strike: float, years: float, vol: float, call: bool) -> float:
    root_t = math.sqrt(years)
    d1 = (math.log(spot / strike) + (RATE + vol * vol / 2) * years) / (vol * root_t)
    d2 = d1 - vol * root_t
    if call:
        return spot * _cdf(d1) - strike * math.exp(-RATE * years) * _cdf(d2)
    return strike * math.exp(-RATE * years) * _cdf(-d2) - spot * _cdf(-d1)


def implied_vol(premium: float, spot: float, strike: float, years: float, call: bool) -> float | None:
    """Bisection on [0.1%, 500%]; None when the premium is at or below intrinsic or inputs are invalid."""
    if not (premium > 0 and spot > 0 and strike > 0 and years > 0):
        return None
    intrinsic = max(0.0, spot - strike * math.exp(-RATE * years)) if call else max(0.0, strike * math.exp(-RATE * years) - spot)
    if premium <= intrinsic + 1e-9:
        return None
    low, high = 0.001, 5.0
    if price(spot, strike, years, high, call) < premium:
        return None
    for _ in range(80):
        mid = (low + high) / 2
        if price(spot, strike, years, mid, call) < premium:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def greeks(premium: float, spot: float, strike: float, years: float, call: bool) -> dict | None:
    """IV, delta, gamma, theta per calendar day and vega per one IV point, per unit of underlying."""
    vol = implied_vol(premium, spot, strike, years, call)
    if vol is None:
        return None
    root_t = math.sqrt(years)
    d1 = (math.log(spot / strike) + (RATE + vol * vol / 2) * years) / (vol * root_t)
    d2 = d1 - vol * root_t
    decay = -spot * _pdf(d1) * vol / (2 * root_t)
    carry = RATE * strike * math.exp(-RATE * years)
    theta = decay - carry * _cdf(d2) if call else decay + carry * _cdf(-d2)
    return {"iv": vol, "delta": _cdf(d1) if call else _cdf(d1) - 1.0, "gamma": _pdf(d1) / (spot * vol * root_t),
            "theta_day": theta / 365.0, "vega_point": spot * _pdf(d1) * root_t / 100.0}


def book_greeks(positions, equity: float, spot_of, now: datetime) -> dict:
    """Aggregate exposure of one book's open option positions.

    ``spot_of(symbol)`` returns (spot price, parsed option dict) or None for a symbol it cannot place.
    Delta is in rupees of underlying (delta x quantity x spot); theta in rupees per day; vega in rupees per
    IV point. Positions whose IV cannot be solved (premium at intrinsic, stale or missing spot) are counted
    in ``unpriced`` and left out of the sums.
    """
    totals = {"positions": 0, "priced": 0, "unpriced": [], "premium_value": 0.0, "net_delta": 0.0,
              "gross_delta": 0.0, "theta_day": 0.0, "vega_point": 0.0,
              "calls": {"n": 0, "delta": 0.0}, "puts": {"n": 0, "delta": 0.0}}
    by_root: dict[str, float] = {}
    for symbol, position in positions:
        if position.quantity <= 0:
            continue
        totals["positions"] += 1
        placed = spot_of(symbol)
        if placed is None:
            totals["unpriced"].append(symbol)
            continue
        spot, parsed = placed
        call = parsed["side"] > 0
        mark = position.last_price or position.average_price
        totals["premium_value"] += mark * position.quantity
        g = greeks(mark, spot, parsed["strike"], years_to_expiry(parsed["expiry"], now), call)
        if g is None:
            totals["unpriced"].append(symbol)
            continue
        totals["priced"] += 1
        delta = g["delta"] * position.quantity * spot
        totals["net_delta"] += delta
        totals["gross_delta"] += abs(delta)
        totals["theta_day"] += g["theta_day"] * position.quantity
        totals["vega_point"] += g["vega_point"] * position.quantity
        leg = totals["calls" if call else "puts"]
        leg["n"] += 1
        leg["delta"] += delta
        by_root[parsed["root"]] = by_root.get(parsed["root"], 0.0) + delta
    top = sorted(by_root.items(), key=lambda item: -abs(item[1]))[:8]
    totals.update({
        "equity": equity,
        "net_delta_pct_equity": 100 * totals["net_delta"] / equity if equity else None,
        "theta_pct_equity": 100 * totals["theta_day"] / equity if equity else None,
        # What a 1% fall in every underlying does through delta alone (no gamma, no IV change).
        "one_pct_down": -0.01 * totals["net_delta"],
        "by_underlying": [{"root": root, "net_delta": delta} for root, delta in top],
    })
    return totals
