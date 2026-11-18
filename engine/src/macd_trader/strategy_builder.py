"""Strategy builder data (owner, 18 Nov 2026): the option chain a payoff / what-if is built on.

Read from the chains the engine already stores (chain_snapshots in historical.sqlite3; index chains about once a
minute, F&O stock chains every 15-minute sweep), so building a strategy costs no broker call. Each strike carries the
call and put last price, bid/ask, OI, volume and Black-Scholes IV (option_greeks, on the quote mid when it is sane).
The payoff, Greeks and what-if maths run in the browser (frontend/src/payoffMath.ts) on these numbers.

Also here: the physical-settlement margin schedule for stock options (NSE Clearing: delivery margin is levied on
in-the-money long options and on futures and short options from four sessions before expiry, 10% / 25% / 45% / 70% of
the delivery margin on E-4 / E-3 / E-2 / E-1, blocked by brokers at the start of the day; many brokers ask for it or
square off earlier). The builder shows which day each stage starts for the chosen expiry.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from . import market_calendar
from .option_greeks import implied_vol
from .universe import INDEX_SPOTS

IST = ZoneInfo("Asia/Kolkata")
EXPIRY_CLOSE = time(15, 30)
DELIVERY_STAGES = ((4, 10), (3, 25), (2, 45), (1, 70))


def _mid(ltp, bid, ask) -> float | None:
    if bid and ask and 0 < bid <= ask and (ask - bid) / ((ask + bid) / 2) <= 0.25:
        return (bid + ask) / 2
    return float(ltp) if ltp and ltp > 0 else None


def _years(expiry: str, ts: int) -> float:
    close = datetime.combine(date.fromisoformat(expiry[:10]), EXPIRY_CLOSE, IST)
    return max((close.timestamp() - ts) / (365.0 * 86400), 0.5 / 365)


def stored_spots(research_path: str):
    """``spot_price(symbol)`` from storage, for when the engine holds no live print (after a restart out of hours):
    the spot stamped on the underlying's latest stored chain, else its latest daily close. Memoised per call site."""
    memo: dict[str, float | None] = {}

    def lookup(symbol: str) -> float | None:
        if symbol in memo:
            return memo[symbol]
        name = symbol.split(":", 1)[-1].rsplit("-", 1)[0]
        value = None
        try:
            with sqlite3.connect(f"file:{research_path}?mode=ro", uri=True, timeout=5) as db:
                row = db.execute("SELECT MAX(ts) FROM chain_snapshots WHERE underlying = ?", (name,)).fetchone()
                if row and row[0]:
                    spot = db.execute("SELECT spot FROM chain_snapshots WHERE underlying = ? AND ts = ? AND spot > 0 LIMIT 1",
                                      (name, row[0])).fetchone()
                    value = float(spot[0]) if spot else None
                if value is None:
                    close = db.execute("SELECT close FROM daily_closes WHERE symbol = ? ORDER BY day DESC LIMIT 1",
                                       (symbol,)).fetchone()
                    value = float(close[0]) if close and close[0] else None
        except sqlite3.Error:
            value = None
        memo[symbol] = value
        return value

    return lookup


def underlyings(research_path: str) -> list[dict]:
    """Underlyings with a stored chain, newest session first per name."""
    with sqlite3.connect(f"file:{research_path}?mode=ro", uri=True, timeout=10) as db:
        rows = db.execute("SELECT underlying, MAX(day) FROM chain_days GROUP BY underlying").fetchall()
    out = [{"underlying": u, "day": d, "kind": "index" if u in INDEX_SPOTS else "stock"} for u, d in rows]
    out.sort(key=lambda r: (r["kind"] != "index", r["underlying"]))
    return out


def delivery_schedule(expiry: str) -> list[dict]:
    """E-5..E-1 sessions before ``expiry`` and the share of delivery margin each asks for (stock options)."""
    back, cursor = {}, date.fromisoformat(expiry[:10])
    for n in range(1, 6):
        cursor = market_calendar.previous_trading_day(cursor)
        back[n] = cursor
    out = [{"stage": f"E-{n}", "day": back[n].isoformat(), "delivery_margin_pct": pct} for n, pct in DELIVERY_STAGES]
    out.append({"stage": "E-5", "day": back[5].isoformat(), "delivery_margin_pct": 0,
                "note": "last session before any delivery margin"})
    return sorted(out, key=lambda r: r["day"])


def chain(research_path: str, underlying: str, expiry: str | None = None, live_spot: float | None = None) -> dict:
    """The latest stored snapshot of one underlying's chain for one expiry (default: nearest unexpired)."""
    with sqlite3.connect(f"file:{research_path}?mode=ro", uri=True, timeout=10) as db:
        row = db.execute("SELECT MAX(ts) FROM chain_snapshots WHERE underlying = ?", (underlying,)).fetchone()
        if not row or row[0] is None:
            raise KeyError(underlying)
        ts = int(row[0])
        rows = db.execute("""SELECT expiry, strike, option_type, symbol, ltp, bid, ask, oi, volume, spot, fp
                             FROM chain_snapshots WHERE underlying = ? AND ts = ? ORDER BY strike""",
                          (underlying, ts)).fetchall()
    taken = datetime.fromtimestamp(ts, IST)
    expiries = sorted({r[0][:10] for r in rows if r[0][:10] >= taken.date().isoformat()})
    if not expiries:
        raise KeyError(underlying)
    chosen = expiry if expiry in expiries else expiries[0]
    spots = sorted(r[9] for r in rows if r[9])
    stored_spot = spots[len(spots) // 2] if spots else None
    spot = live_spot or stored_spot
    futures = next((r[10] for r in rows if r[10]), None)
    years = _years(chosen, ts)
    strikes: dict[float, dict] = {}
    for e, strike, kind, symbol, ltp, bid, ask, oi, volume, row_spot, _fp in rows:
        if e[:10] != chosen:
            continue
        price = _mid(ltp, bid, ask)
        iv = implied_vol(price, stored_spot or row_spot, strike, years, kind == "CE") if price and (stored_spot or row_spot) else None
        strikes.setdefault(float(strike), {"strike": float(strike)})[kind.lower()] = {
            "symbol": symbol, "ltp": ltp, "bid": bid, "ask": ask, "oi": oi, "volume": volume,
            "iv": round(iv * 100, 2) if iv else None}
    return {"underlying": underlying, "kind": "index" if underlying in INDEX_SPOTS else "stock",
            "as_of": taken.isoformat(timespec="minutes"), "spot": round(spot, 2) if spot else None,
            "stored_spot": stored_spot, "futures": futures, "expiry": chosen, "expiries": expiries,
            "days_to_expiry": (date.fromisoformat(chosen) - datetime.now(IST).date()).days,
            "rows": [strikes[k] for k in sorted(strikes)],
            "delivery": delivery_schedule(chosen) if underlying not in INDEX_SPOTS else []}
