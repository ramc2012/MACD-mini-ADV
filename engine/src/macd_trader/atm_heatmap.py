"""ATM option heat map: how each underlying's at-the-money call and put premiums have moved in the CURRENT period.

Rows are the contract selector's tradable ATM contracts (one CE and one PE per underlying, nearest eligible expiry).
Each column is period-to-date, from the period's first trade to now:
  1H  the current hour, aligned to the 09:15 open like the app's hourly bars (09:15, 10:15, ... 15:15)
  4H  the current four-hour block (09:15-13:15, 13:15-close)
  1D  today, from the 09:15 open
  1W  this week, from its first session's open
  1M  this month, from its first session's open
Outside the session "now" is the last close, so on a weekend the periods are Friday's last hour, Friday, this week
and this month. The one-day view also compares to the previous regular session close for the same
instrument. Prefer the same-session quote's previous close where available, otherwise use the prior
session's last stored close. The period reference price is the contract's first stored minute OPEN at or after the period start. When
that first trade came well after the start (an illiquid contract, or one that entered the band mid-period) the
change is still measured from it and the leg's ``partial`` map records when it began. The current price is the
latest tick, else the last stored close. The underlying's own move is reported alongside.
"""
from __future__ import annotations

import math
import sqlite3
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from . import market_calendar
from .sectors import sector_of

IST = ZoneInfo("Asia/Kolkata")
OPEN, CLOSE = time(9, 15), time(15, 30)
PERIODS = ("1H", "4H", "1D", "1W", "1M")
PREVIOUS_CLOSE = "PREV_CLOSE"
BLOCK_MINUTES = {"1H": 60, "4H": 240}
# A first trade later than this after the period start marks the change as partial.
PARTIAL_AFTER_SECONDS = {"1H": 15 * 60, "4H": 30 * 60, "1D": 60 * 60, "1W": 60 * 60, "1M": 60 * 60}
INDEX_ORDER = ("NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY")
# Display names of the context-only sector and broad indices (engine.context_indices) and India VIX.
SPOT_LABELS = {
    "NSE:NIFTYIT-INDEX": "NIFTY IT", "NSE:NIFTYAUTO-INDEX": "NIFTY AUTO", "NSE:NIFTYPHARMA-INDEX": "NIFTY PHARMA",
    "NSE:NIFTYFMCG-INDEX": "NIFTY FMCG", "NSE:NIFTYMETAL-INDEX": "NIFTY METAL", "NSE:NIFTYENERGY-INDEX": "NIFTY ENERGY",
    "NSE:NIFTYREALTY-INDEX": "NIFTY REALTY", "NSE:FINNIFTY-INDEX": "FIN NIFTY", "NSE:NIFTYNXT50-INDEX": "NIFTY NEXT 50",
    "NSE:NIFTYPSUBANK-INDEX": "NIFTY PSU BANK", "NSE:NIFTYPVTBANK-INDEX": "NIFTY PVT BANK", "NSE:NIFTYMEDIA-INDEX": "NIFTY MEDIA",
    "NSE:NIFTYINFRA-INDEX": "NIFTY INFRA", "NSE:INDIAVIX-INDEX": "INDIA VIX",
}


def spot_label(symbol: str) -> str:
    return SPOT_LABELS.get(symbol) or symbol.split(":", 1)[-1].replace("-INDEX", "")


def session_anchor(now: datetime) -> datetime:
    """The latest moment that was inside a session: now if trading, else the last close."""
    local = now.astimezone(IST)
    day = local.date()
    if market_calendar.is_trading_day(day) and local.time() >= OPEN:
        return min(local, datetime.combine(day, CLOSE, IST))
    day -= timedelta(days=1)
    while not market_calendar.is_trading_day(day):
        day -= timedelta(days=1)
    return datetime.combine(day, CLOSE, IST)


def _first_session_on_or_after(day: date) -> date:
    while not market_calendar.is_trading_day(day):
        day += timedelta(days=1)
    return day


def period_start(anchor: datetime, period: str) -> datetime:
    """When the current ``period`` began, for an ``anchor`` inside (or at the close of) a session."""
    opened = datetime.combine(anchor.date(), OPEN, IST)
    if period in BLOCK_MINUTES:
        size = BLOCK_MINUTES[period]
        elapsed = int((anchor - opened).total_seconds() // 60)
        # At the close the last block is the one that just ended, not an empty one starting at 15:30.
        block = min(elapsed, int((datetime.combine(anchor.date(), CLOSE, IST) - opened).total_seconds() // 60) - 1) // size
        return opened + timedelta(minutes=block * size)
    if period == "1D":
        return opened
    if period == "1W":
        first = _first_session_on_or_after(anchor.date() - timedelta(days=anchor.weekday()))
    elif period == "1M":
        first = _first_session_on_or_after(anchor.date().replace(day=1))
    else:
        raise ValueError(period)
    return datetime.combine(first, OPEN, IST)


def _first_open(db: sqlite3.Connection, symbol: str, start: int, end: int) -> tuple[float, int] | None:
    row = db.execute("""SELECT open, timestamp FROM historical_candles WHERE symbol = ? AND timeframe_seconds = 60
                        AND timestamp >= ? AND timestamp <= ? ORDER BY timestamp LIMIT 1""", (symbol, start, end)).fetchone()
    return (float(row[0]), int(row[1])) if row and row[0] else None


def _price(value: float | None) -> float | None:
    return float(value) if value is not None and math.isfinite(value) and value > 0 else None


def _last_close(db: sqlite3.Connection, symbol: str, at: int, start: int = 0) -> float | None:
    row = db.execute("""SELECT close FROM historical_candles WHERE symbol = ? AND timeframe_seconds = 60
                        AND timestamp BETWEEN ? AND ? AND close > 0 ORDER BY timestamp DESC LIMIT 1""",
                     (symbol, start, at)).fetchone()
    return _price(row[0]) if row else None


def _symbols(db: sqlite3.Connection) -> list[str]:
    """Every stored symbol, by skip-scanning the primary key (a DISTINCT over the table takes ~40 s)."""
    out, last = [], ""
    while row := db.execute("SELECT symbol FROM historical_candles WHERE symbol > ? ORDER BY symbol LIMIT 1", (last,)).fetchone():
        last = row[0]
        out.append(last)
    return out


def historical_contracts(database_path: str, day: date) -> list[dict]:
    """The ATM CE/PE each underlying had at ``day``'s close, rebuilt from stored bars.

    For every root: the nearest expiry after ``day`` among options that traded that day, then the strike nearest
    the spot's close. Only contracts that traded on ``day`` qualify, so the pick is one the book could have held.
    """
    from .option_agent import parse_option
    opened = int(datetime.combine(day, OPEN, IST).timestamp())
    closed = int(datetime.combine(day, CLOSE, IST).timestamp())
    with sqlite3.connect(f"file:{database_path}?mode=ro", uri=True, timeout=30) as db:
        candidates: dict[str, list[tuple[str, dict]]] = {}
        for symbol in _symbols(db):
            if not symbol.endswith(("CE", "PE")):
                continue
            parsed = parse_option(symbol)
            if parsed and parsed["spot"] and parsed["expiry"] > day.isoformat():
                candidates.setdefault(parsed["root"], []).append((symbol, parsed))
        traded = lambda symbol: db.execute(
            """SELECT 1 FROM historical_candles WHERE symbol = ? AND timeframe_seconds = 60
               AND timestamp >= ? AND timestamp <= ? LIMIT 1""", (symbol, opened, closed)).fetchone() is not None
        contracts = []
        for root, options in candidates.items():
            spot = _last_close(db, options[0][1]["spot"], closed + 59)
            if not spot:
                continue
            for expiry in sorted({parsed["expiry"] for _, parsed in options}):
                legs = {(parsed["strike"], parsed["side"]): (symbol, parsed) for symbol, parsed in options if parsed["expiry"] == expiry}
                strikes = sorted({strike for strike, _ in legs}, key=lambda strike: abs(strike - spot))[:6]
                # The nearest strike whose call and put both traded that day; else the nearest traded of each side.
                both = next((strike for strike in strikes if all((strike, side) in legs and traded(legs[(strike, side)][0])
                                                                 for side in (1, -1))), None)
                chosen = {}
                for right, side in (("CE", 1), ("PE", -1)):
                    pick = legs[(both, side)] if both is not None else next(
                        (legs[(strike, side)] for strike in strikes if (strike, side) in legs and traded(legs[(strike, side)][0])), None)
                    if pick:
                        chosen[right] = pick
                if len(chosen) == 2:
                    for right, (symbol, parsed) in chosen.items():
                        contracts.append({"underlying": root, "spot_symbol": parsed["spot"], "option_type": right,
                                          "symbol": symbol, "strike": parsed["strike"], "expiry": expiry})
                    break
    return contracts


def build(database_path: str, contracts: list[dict], live_prices: dict[str, float], now: datetime | None = None,
          context_spots: list[str] | None = None, *, previous_closes: dict[str, float] | None = None) -> dict:
    """``contracts``: dicts with underlying, spot_symbol, option_type, symbol, strike, expiry (ATM, tradable).
    ``context_spots``: index symbols shown in the Spot view only (sectors, broad indices, VIX); kind "sector"."""
    anchor = session_anchor(now or datetime.now(IST))
    starts = {name: period_start(anchor, name) for name in PERIODS}
    anchor_ts = int(anchor.timestamp()) + 59
    session_start_ts = int(starts["1D"].timestamp())
    prior_day = market_calendar.previous_trading_day(anchor.date())
    prior_open = int(datetime.combine(prior_day, OPEN, IST).timestamp())
    prior_close = datetime.combine(prior_day, CLOSE, IST)
    prior_close_ts = int(prior_close.timestamp())
    previous_closes = previous_closes or {}
    by_underlying: dict[str, dict] = {}
    for contract in contracts:
        row = by_underlying.setdefault(contract["underlying"], {
            "kind": "index" if contract["underlying"] in INDEX_ORDER else "stock",
            "underlying": contract["underlying"], "spot_symbol": contract["spot_symbol"],
            "sector": sector_of(contract["spot_symbol"]),
            "strike": contract["strike"], "expiry": str(contract["expiry"])[:10]})
        row[contract["option_type"].lower()] = {"symbol": contract["symbol"], "strike": contract["strike"]}
    for symbol in context_spots or []:
        by_underlying.setdefault(spot_label(symbol), {"underlying": spot_label(symbol), "spot_symbol": symbol, "kind": "sector",
                                                      "strike": None, "expiry": ""})
    with sqlite3.connect(f"file:{database_path}?mode=ro", uri=True, timeout=30) as db:
        for row in by_underlying.values():
            legs = [("spot", row["spot_symbol"])] + [(leg, row[leg]["symbol"]) for leg in ("ce", "pe") if leg in row]
            for leg, symbol in legs:
                current = _price(live_prices.get(symbol)) or _last_close(db, symbol, anchor_ts, session_start_ts)
                previous = _price(previous_closes.get(symbol)) or _last_close(db, symbol, prior_close_ts, prior_open)
                changes = {PREVIOUS_CLOSE: round((current / previous - 1) * 100, 2)
                           if current is not None and previous is not None else None}
                comparison = {PREVIOUS_CLOSE: previous, "1D": None}
                partial = {}
                for name, start in starts.items():
                    first = _first_open(db, symbol, int(start.timestamp()), anchor_ts)
                    base = _price(first[0]) if first else None
                    if name == "1D":
                        comparison[name] = base
                    if base is None or current is None:
                        changes[name] = None
                        continue
                    changes[name] = round((current / base - 1) * 100, 2)
                    max_delay = 0 if name == "1D" else PARTIAL_AFTER_SECONDS[name]
                    if first[1] - int(start.timestamp()) > max_delay:
                        partial[name] = datetime.fromtimestamp(first[1], IST).isoformat(timespec="minutes")
                if leg == "spot":
                    row["spot"], row["spot_change"], row["spot_partial"] = current, changes, partial
                    row["spot_comparison"] = comparison
                else:
                    row[leg].update({"price": current, "change": changes, "partial": partial, "comparison": comparison})
    by_underlying = {key: row for key, row in by_underlying.items() if row["kind"] != "sector" or row.get("spot")}
    rows = sorted(by_underlying.values(), key=lambda r: (
        INDEX_ORDER.index(r["underlying"]) if r["underlying"] in INDEX_ORDER else len(INDEX_ORDER), r["underlying"]))
    return {"as_of": anchor.isoformat(), "periods": list(PERIODS), "basis": "period to date",
            "references": {**{name: start.isoformat() for name, start in starts.items()},
                           PREVIOUS_CLOSE: prior_close.isoformat()}, "rows": rows}
