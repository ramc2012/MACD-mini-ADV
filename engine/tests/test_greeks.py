import math
from datetime import datetime, timedelta

from macd_trader.greeks import (IST, black_scholes, contract_gex, gamma,
                                implied_volatility, years_to_expiry)


def _expiry(days: int) -> str:
    return (datetime.now(IST) + timedelta(days=days)).date().isoformat()


def test_implied_vol_round_trips_through_the_pricer():
    spot, strike, years, rate, sigma = 100.0, 100.0, 0.25, 0.065, 0.30
    premium = black_scholes(spot, strike, years, rate, sigma, True)
    solved = implied_volatility(premium, spot, strike, years, rate, True)
    assert solved is not None and abs(solved - sigma) < 1e-3


def test_put_call_parity_holds():
    spot, strike, years, rate, sigma = 100.0, 95.0, 0.5, 0.065, 0.25
    call = black_scholes(spot, strike, years, rate, sigma, True)
    put = black_scholes(spot, strike, years, rate, sigma, False)
    assert abs((call - put) - (spot - strike * math.exp(-rate * years))) < 1e-6


def test_gamma_peaks_at_the_money_and_is_type_independent():
    years, rate, sigma = 0.25, 0.065, 0.3
    atm = gamma(100.0, 100.0, years, rate, sigma)
    otm = gamma(100.0, 130.0, years, rate, sigma)
    itm = gamma(100.0, 70.0, years, rate, sigma)
    assert atm > otm and atm > itm


def test_gex_sign_convention_calls_positive_puts_negative():
    """Dealers are assumed long calls and short puts, so call gamma adds and
    put gamma subtracts — the sign is the whole point of the number."""
    common = dict(premium=5.0, spot=100.0, strike=100.0, expiry=_expiry(20),
                  open_interest=1000, lot_size=50)
    call = contract_gex(option_type="CE", **common)
    put = contract_gex(option_type="PE", **common)
    assert call["gex"] is not None and call["gex"] > 0
    assert put["gex"] is not None and put["gex"] < 0


def test_call_and_put_gex_match_in_size_at_parity_consistent_premiums():
    """Gamma is the same for a call and a put at one strike, so the exposures
    are equal and opposite — but only when each premium implies the SAME
    volatility. Feeding both legs an identical premium implies different vols
    (put-call parity), which is why the sizes would otherwise differ."""
    spot, strike, rate, sigma = 100.0, 100.0, 0.065, 0.30
    expiry = _expiry(20)
    years = years_to_expiry(expiry)
    assert years is not None
    call_premium = black_scholes(spot, strike, years, rate, sigma, True)
    put_premium = black_scholes(spot, strike, years, rate, sigma, False)

    common = dict(spot=spot, strike=strike, expiry=expiry, open_interest=1000,
                  lot_size=50, rate=rate)
    call = contract_gex(premium=call_premium, option_type="CE", **common)
    put = contract_gex(premium=put_premium, option_type="PE", **common)
    assert call["gex"] > 0 > put["gex"]
    assert abs(abs(call["gex"]) - abs(put["gex"])) / abs(call["gex"]) < 1e-3


def test_gex_scales_with_open_interest_and_lot_size():
    base = dict(premium=5.0, spot=100.0, strike=100.0, expiry=_expiry(20),
                option_type="CE", lot_size=50)
    one = contract_gex(open_interest=1000, **base)["gex"]
    ten = contract_gex(open_interest=10_000, **base)["gex"]
    assert one and ten and abs(ten / one - 10) < 1e-6


def test_unsolvable_or_expired_contracts_return_blanks_not_errors():
    """The watchlist renders ~430 rows; one bad strike must not blank the column."""
    expired = contract_gex(premium=5.0, spot=100.0, strike=100.0, expiry=_expiry(-1),
                           option_type="CE", open_interest=100, lot_size=50)
    assert expired == {"iv": None, "gamma": None, "gex": None}

    # A premium below intrinsic value is a stale or crossed print, not a quote.
    below = contract_gex(premium=1.0, spot=150.0, strike=100.0, expiry=_expiry(20),
                         option_type="CE", open_interest=100, lot_size=50)
    assert below["gex"] is None

    missing_oi = contract_gex(premium=5.0, spot=100.0, strike=100.0, expiry=_expiry(20),
                              option_type="CE", open_interest=None, lot_size=50)
    assert missing_oi["gex"] is None


def test_years_to_expiry_uses_the_1530_ist_close():
    today = datetime.now(IST).date().isoformat()
    morning = datetime.now(IST).replace(hour=10, minute=0, second=0, microsecond=0)
    years = years_to_expiry(today, morning)
    assert years is not None and 0 < years < 1 / 365          # same-day, hours left
    assert years_to_expiry(today, morning.replace(hour=16)) is None   # after the close
