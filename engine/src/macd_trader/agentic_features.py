"""Computed inputs for the agentic lane's model, so it reasons over a few precise numbers instead of raw history.

  session_shape   today's hourly closes, opening gap, first-hour range, yesterday's high/low/close, distance from
                  the futures VWAP (index spot has no volume)
  daily_atr       14-session average true range from stored minute bars (points and % of price)
  positioning     the latest option-chain snapshot: put/call OI ratio, the largest CE and PE OI strikes, today's
                  change in OI (NIFTY and BANKNIFTY are the chains the desk records)
  contract_risk   per contract: implied volatility, delta, time decay in rupees per lot per day, breakeven at
                  expiry and the % move the index needs to reach it
  track_record    the lane's own recent closed positions
All reads are read-only SQLite queries bounded to a few weeks; results that only change daily are cached.
"""
from __future__ import annotations

import math
import sqlite3
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from . import market_calendar, option_greeks

IST = ZoneInfo("Asia/Kolkata")
OPEN, CLOSE = time(9, 15), time(15, 30)
FUTURES_GLOB = {"NIFTY": "NSE:NIFTY[0-9][0-9][A-Z][A-Z][A-Z]FUT", "BANKNIFTY": "NSE:BANKNIFTY[0-9][0-9][A-Z][A-Z][A-Z]FUT",
                "SENSEX": "BSE:SENSEX[0-9][0-9][A-Z][A-Z][A-Z]FUT", "MIDCPNIFTY": "NSE:MIDCPNIFTY[0-9][0-9][A-Z][A-Z][A-Z]FUT"}


def _r(value, digits=2):
    return None if value is None or not isinstance(value, (int, float)) or not math.isfinite(value) else round(value, digits)


def _bounds(day: date) -> tuple[int, int]:
    return int(datetime.combine(day, OPEN, IST).timestamp()), int(datetime.combine(day, CLOSE, IST).timestamp())


def previous_session(day: date) -> date:
    day -= timedelta(days=1)
    while not market_calendar.is_trading_day(day):
        day -= timedelta(days=1)
    return day


def _minutes(db: sqlite3.Connection, symbol: str, day: date, until: int | None = None) -> list[tuple]:
    lo, hi = _bounds(day)
    hi = min(hi, until) if until else hi
    return db.execute("""SELECT timestamp, open, high, low, close, volume FROM historical_candles
                         WHERE symbol=? AND timeframe_seconds=60 AND timestamp>=? AND timestamp<? ORDER BY timestamp""",
                      (symbol, lo, hi)).fetchall()


def session_shape(db: sqlite3.Connection, root: str, spot: str, now: datetime, price: float | None) -> dict:
    """How today unfolded for one index, up to ``now``."""
    today = now.date()
    until = int(now.timestamp()) + 1
    bars = _minutes(db, spot, today, until)
    prev = _minutes(db, spot, previous_session(today))
    out: dict = {}
    if prev:
        out["prev_high"], out["prev_low"], out["prev_close"] = (_r(max(b[2] for b in prev)), _r(min(b[3] for b in prev)),
                                                                _r(prev[-1][4]))
    if not bars:
        return out
    price = price or bars[-1][4]
    open_ = bars[0][1]
    out["open"] = _r(open_)
    if prev:
        out["gap_pct"] = _r((open_ / prev[-1][4] - 1) * 100)
    first = [b for b in bars if b[0] < bars[0][0] + 3600]
    fh_high, fh_low = max(b[2] for b in first), min(b[3] for b in first)
    out["first_hour"] = {"high": _r(fh_high), "low": _r(fh_low),
                         "now": "above" if price > fh_high else "below" if price < fh_low else "inside"}
    lo, _ = _bounds(today)
    hourly: dict[int, float] = {}
    for ts, _o, _h, _l, c, _v in bars:
        hourly[(ts - lo) // 3600] = c
    out["hourly_closes"] = [[(datetime.fromtimestamp(lo, IST) + timedelta(hours=k)).strftime("%H:%M"), _r(v)]
                            for k, v in sorted(hourly.items())]
    if prev:
        out["vs_prev_high_pct"] = _r((price / max(b[2] for b in prev) - 1) * 100)
        out["vs_prev_low_pct"] = _r((price / min(b[3] for b in prev) - 1) * 100)
    pattern = FUTURES_GLOB.get(root)
    if pattern:
        row = db.execute("""SELECT symbol FROM historical_candles WHERE symbol GLOB ? AND timeframe_seconds=60
                            AND timestamp>=? AND timestamp<? GROUP BY symbol ORDER BY SUM(volume) DESC LIMIT 1""",
                         (pattern, lo, until)).fetchone()
        if row:
            fut = _minutes(db, row[0], today, until)
            volume = sum(b[5] for b in fut)
            if volume > 0:
                vwap = sum((b[2] + b[3] + b[4]) / 3 * b[5] for b in fut) / volume
                out["vs_futures_vwap_pct"] = _r((fut[-1][4] / vwap - 1) * 100)
    return out


def daily_atr(db: sqlite3.Connection, spot: str, today: date, sessions: int = 14) -> dict:
    """Average true range over the last ``sessions`` completed sessions, from minute bars."""
    days, day = [], previous_session(today)
    while len(days) < sessions + 1:
        days.append(day)
        day = previous_session(day)
    days.reverse()
    lo, _ = _bounds(days[0])
    _, hi = _bounds(days[-1])
    rows = db.execute("""SELECT timestamp, high, low, close FROM historical_candles WHERE symbol=? AND timeframe_seconds=60
                         AND timestamp>=? AND timestamp<? ORDER BY timestamp""", (spot, lo, hi)).fetchall()
    per_day: dict[date, list] = {}
    for ts, h, l, c in rows:
        d = datetime.fromtimestamp(ts, IST).date()
        agg = per_day.setdefault(d, [h, l, c])
        agg[0], agg[1], agg[2] = max(agg[0], h), min(agg[1], l), c
    ordered = [per_day[d] for d in sorted(per_day)]
    ranges = []
    for i in range(1, len(ordered)):
        h, l, _c = ordered[i]
        pc = ordered[i - 1][2]
        ranges.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(ranges) < 5:
        return {}
    atr = sum(ranges[-sessions:]) / len(ranges[-sessions:])
    return {"atr_points": _r(atr, 1), "atr_pct": _r(atr / ordered[-1][2] * 100), "sessions": len(ranges[-sessions:])}


def positioning(db: sqlite3.Connection, root: str, now: datetime) -> dict | None:
    """Put/call OI and the biggest OI strikes from the latest chain snapshot today (nearest live expiry)."""
    lo, _ = _bounds(now.date())
    row = db.execute("""SELECT MAX(ts) FROM chain_snapshots WHERE underlying=? AND ts>=? AND ts<=? AND expiry>=?""",
                     (root, lo, int(now.timestamp()) + 1, now.date().isoformat())).fetchone()
    if not row or not row[0]:
        return None
    ts = row[0]
    expiry = db.execute("SELECT MIN(expiry) FROM chain_snapshots WHERE underlying=? AND ts=? AND expiry>=?",
                        (root, ts, now.date().isoformat())).fetchone()[0]
    legs = db.execute("""SELECT strike, option_type, oi, prev_oi FROM chain_snapshots
                         WHERE underlying=? AND ts=? AND expiry=?""", (root, ts, expiry)).fetchall()
    ce = [(k, oi or 0, (oi or 0) - (p or 0)) for k, t, oi, p in legs if t == "CE"]
    pe = [(k, oi or 0, (oi or 0) - (p or 0)) for k, t, oi, p in legs if t == "PE"]
    ce_oi, pe_oi = sum(x[1] for x in ce), sum(x[1] for x in pe)
    if not ce_oi:
        return None
    top = lambda rows: [[_r(k, 0), int(oi)] for k, oi, _d in sorted(rows, key=lambda x: -x[1])[:3]]  # noqa: E731
    return {"expiry": expiry, "snapshot_age_min": _r((now.timestamp() - ts) / 60, 0), "pcr_oi": _r(pe_oi / ce_oi),
            "top_call_oi_strikes": top(ce), "top_put_oi_strikes": top(pe),
            "today_oi_change_calls": int(sum(x[2] for x in ce)), "today_oi_change_puts": int(sum(x[2] for x in pe))}


def contract_risk(premium: float, spot: float, strike: float, expiry: str, call: bool, lot: int, now: datetime) -> dict:
    """IV, delta, time decay per lot per day, breakeven at expiry and the index move needed to reach it."""
    out: dict = {}
    if not (premium and spot and strike and expiry):
        return out
    breakeven = strike + premium if call else strike - premium
    out["breakeven"] = _r(breakeven, 1)
    out["move_needed_pct"] = _r((breakeven / spot - 1) * 100)
    try:
        years = option_greeks.years_to_expiry(expiry, now)
    except ValueError:
        return out
    if years <= 0:
        return out
    greeks = option_greeks.greeks(premium, spot, strike, years, call)
    if greeks:
        out["iv_pct"] = _r(greeks["iv"] * 100, 1)
        out["delta"] = _r(greeks["delta"])
        out["theta_rs_per_lot_day"] = _r(greeks["theta_day"] * lot, 0)
    return out


def implied_move(candidates: list[dict], spot: float | None) -> dict | None:
    """ATM straddle of the nearest expiry, as the move the option market prices to that expiry."""
    if not spot:
        return None
    nearest = min((c["expiry"] for c in candidates if c.get("expiry")), default=None)
    if not nearest:
        return None
    by_strike: dict[float, dict] = {}
    for c in candidates:
        if c.get("expiry") == nearest and c.get("premium"):
            by_strike.setdefault(float(c["strike"]), {})[c["type"]] = c["premium"]
    pairs = [(abs(k - spot), k, legs) for k, legs in by_strike.items() if "CE" in legs and "PE" in legs]
    if not pairs:
        return None
    _, strike, legs = min(pairs)
    straddle = legs["CE"] + legs["PE"]
    return {"expiry": nearest, "strike": _r(strike, 0), "straddle": _r(straddle, 1), "implied_move_pct": _r(straddle / spot * 100)}


def track_record(closed: list[dict], limit: int = 6) -> dict:
    """The lane's recent closed positions (partial exits merged), newest first, and simple totals."""
    merged: dict[str, dict] = {}
    for row in closed:
        pid = row.get("position_id") or f"{row.get('symbol')}:{row.get('entry_time')}"
        item = merged.setdefault(pid, {"contract": str(row.get("symbol", "")).split(":")[-1], "pnl_rs": 0.0,
                                       "cost": 0.0, "opened": str(row.get("entry_time") or "")[:16],
                                       "closed": "", "exit": None})
        item["pnl_rs"] += float(row.get("realized_pnl") or row.get("pnl") or 0)
        item["cost"] += float(row.get("entry_price") or 0) * float(row.get("quantity") or 0)
        when = str(row.get("exit_time") or "")[:16]
        if when >= item["closed"]:
            item["closed"], item["exit"] = when, row.get("exit_reason") or row.get("reason")
    rows = sorted(merged.values(), key=lambda r: r["closed"], reverse=True)
    for r in rows:
        r["return_pct"] = _r(r["pnl_rs"] / r["cost"] * 100, 1) if r["cost"] else None
        r["pnl_rs"] = round(r["pnl_rs"])
        r.pop("cost")
    wins = [r for r in rows if r["pnl_rs"] > 0]
    return {"closed_positions": len(rows), "wins": len(wins), "net_pnl_rs": round(sum(r["pnl_rs"] for r in rows)),
            "recent": rows[:limit]}
