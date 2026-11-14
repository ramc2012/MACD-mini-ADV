"""Options & flow desk (Auction desk -> Options & flow, and the MI console's Options tab).

Eight tools computed from data the app already stores; nothing is downloaded here.

Chain tools (chain_snapshots in historical.sqlite3: index chains about once a minute, +-8% of spot, front series; F&O stock
chains every 15-minute sweep):
  3 NTM VolX       mean implied volatility of the 5 strikes nearest spot (ATM +-2), CE and PE, against India VIX
  4 Gamma          per-strike dealer gamma exposure (calls +, puts -), the zero-gamma level, call / put walls, net GEX
                   through the day, and the expected-move band (open +- 1 sigma from the opening NTM IV)
  5 Spectrum       cumulative call and put volume through the session, and the day's highest-volume strikes as zones
  8 Options table  per-strike LTP, bid/ask, IV, OI, OI change, volume and Greeks at the chosen moment
  9 Writers' P&L   for OI added today: added quantity x (average premium while it was added - premium now). Positive
                   means whoever sold the added contracts is ahead. It assumes the added OI was written (sold to open);
                   OI cannot say which side opened it.
 10 IV range       each stored session's NTM IV near the close (41 sessions from 3 Sep 2026 on NIFTY), its high / low /
                   percentile, and projected moves spot x IV x sqrt(days / 365)

Futures tools (tick_minute_flow in ticks.sqlite3: per-minute volume, buy/sell volume and delta from the live feed; OI from
oi_minutes, captured for the NIFTY, BANKNIFTY and SENSEX current-month futures since 26 Oct 2026):
  6 MFLOW          futures volume split by what OI did: OI rising = new business, OI falling = old business leaving; with
                   price it gives long build-up, short build-up, short covering and long unwinding. OI updates less
                   often than price, so the volume traded since the last OI change is assigned to the next change.
  7 Smart candles  candles with the initial-balance box (first hour), colour strength = delta / volume, and boxes around
                   balance clusters (runs of overlapping bars).

Limits: buy/sell volume is classified from quotes (not exchange tick-by-tick), and IV is solved from one mid or last
price per contract (European Black-Scholes on spot, r = 6.5%, calendar days), so an illiquid strike's IV can be stale.
"""
from __future__ import annotations

import math
import re
import sqlite3
import statistics
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import option_greeks as og
from .straddle_views import INDICES, _cached, _day, _front, _read, _start, expiries

IST = ZoneInfo("Asia/Kolkata")
BAND = 0.08                 # index chain strikes within 8% of spot are read
NTM_STRIKES = 5             # ATM +-2
PARTIAL_SNAPSHOT = 0.6      # a snapshot with fewer rows than this share of the day's median is skipped
GEX_EVERY_SECONDS = 300     # net GEX through the day is solved every 5 minutes (each point solves every contract)
SESSION_OPEN_MIN = 9 * 60 + 15
SESSION_MINUTES = 375       # 09:15-15:30
IB_MINUTES = 60
WALL_SPAN = 0.05            # zero-gamma search: spot +-5%
EXCHANGE = {"SENSEX": "BSE", "BANKEX": "BSE", "FOCIT": "BSE"}


# ------------------------------------------------------------------------------------------------ helpers

def _premium(ltp, bid, ask) -> float | None:
    """Mid of a sane two-sided quote, else the last price."""
    if bid and ask and 0 < bid <= ask:
        mid = (bid + ask) / 2
        if (ask - bid) / mid <= 0.2:
            return mid
    return ltp if ltp and ltp > 0 else None


def _years(expiry: str, ts: int) -> float:
    return og.years_to_expiry(expiry, datetime.fromtimestamp(ts, IST))


def _iv(prem, spot, strike, years, call) -> float | None:
    if not prem or years <= 0:
        return None
    return og.implied_vol(prem, spot, strike, years, call)


def _gamma(spot: float, strike: float, years: float, vol: float) -> float:
    if spot <= 0 or strike <= 0 or years <= 0 or vol <= 0:
        return 0.0
    root = math.sqrt(years)
    d1 = (math.log(spot / strike) + (og.RATE + vol * vol / 2) * years) / (vol * root)
    return og._pdf(d1) / (spot * vol * root)


def _step(strikes) -> float:
    ordered = sorted(set(strikes))
    gaps = [round(b - a, 4) for a, b in zip(ordered, ordered[1:]) if b > a]
    return statistics.mode(gaps) if gaps else 0.0


def _ist(ts: int) -> str:
    return datetime.fromtimestamp(ts, IST).strftime("%Y-%m-%d %H:%M:%S IST")


def _minute_of_day(ts: int) -> int:
    moment = datetime.fromtimestamp(ts, IST)
    return moment.hour * 60 + moment.minute


def _snapshots(rows) -> list[tuple[int, list]]:
    """Group rows by snapshot time and drop partial snapshots."""
    groups: dict[int, list] = {}
    for row in rows:
        groups.setdefault(row[0], []).append(row)
    if not groups:
        return []
    typical = statistics.median(len(v) for v in groups.values())
    return [(ts, groups[ts]) for ts in sorted(groups) if len(groups[ts]) >= PARTIAL_SNAPSHOT * typical]


def _per_minute(snaps):
    """The last snapshot in each minute (index chains are captured about once a minute; this keeps one point each)."""
    last: dict[int, tuple] = {}
    for ts, rows in snaps:
        last[ts // 60] = (ts, rows)
    return [last[k] for k in sorted(last)]


def _ntm(rows, spot):
    strikes = sorted({r[1] for r in rows}, key=lambda k: (abs(k - spot), k))[:NTM_STRIKES]
    return set(strikes)


def _ntm_iv(rows, spot, expiry, ts) -> dict:
    years = _years(expiry, ts)
    near = _ntm(rows, spot)
    ce, pe = [], []
    for r in rows:
        if r[1] not in near:
            continue
        vol = _iv(_premium(r[3], r[8], r[9]), spot, r[1], years, r[2] == "CE")
        if vol:
            (ce if r[2] == "CE" else pe).append(vol * 100)
    both = ce + pe
    return {"ntm_iv": round(sum(both) / len(both), 2) if both else None,
            "ce_iv": round(sum(ce) / len(ce), 2) if ce else None,
            "pe_iv": round(sum(pe) / len(pe), 2) if pe else None}


# ------------------------------------------------------------------------------------------------ chain view

ROW_SQL = """SELECT ts, strike, option_type, ltp, volume, oi, prev_oi, spot, bid, ask, fp, vix FROM chain_snapshots
             WHERE underlying = ? AND expiry = ? AND ts >= ? AND ts < ? AND spot > 0 AND abs(strike - spot) <= spot * ?
             ORDER BY ts"""


def chain_view(path: str, underlying: str, expiry: str | None = None, day: str | None = None,
               at: int | None = None, today: date | None = None) -> dict:
    """Every chain tool for one underlying, expiry and session. ``at`` (epoch seconds) picks the moment the table,
    gamma and writers describe; default the session's last snapshot."""
    underlying = underlying.upper()
    listed = expiries(path, underlying)
    chosen = next((s for s in listed if s["expiry"] == expiry), None) if expiry else _front(
        listed, today or _day(int(time.time())))
    base = {"underlying": underlying, "expiries": [s["expiry"] for s in listed]}
    if not chosen:
        return {**base, "expiry": expiry, "note": "No stored option chain for this instrument and expiry."}
    first, last = chosen["first_ts"], chosen["last_ts"]
    view_day = date.fromisoformat(day) if day else _day(last)
    view_day = min(max(view_day, _day(first)), _day(last))
    key = ("chain_view", path, underlying, chosen["expiry"], view_day.isoformat(), at, last)
    ttl = 30 if view_day >= _day(int(time.time())) else 3600
    return _cached(key, ttl, lambda: {**base, **_chain_build(path, underlying, chosen["expiry"], view_day, at)})


def _chain_build(path: str, underlying: str, expiry: str, view_day: date, at: int | None) -> dict:
    connection = _read(path)
    try:
        rows = connection.execute(ROW_SQL, (underlying, expiry, _start(view_day), _start(view_day + timedelta(days=1)),
                                            BAND if underlying in INDICES else 1.0)).fetchall()
        days = [r[0] for r in connection.execute(
            "SELECT day FROM chain_days WHERE underlying = ? ORDER BY day", (underlying,))]
    finally:
        connection.close()
    # the collector keeps sweeping to 15:40; after the 15:30 close the quotes go stale while time to expiry keeps
    # shrinking, so the IV tail is noise. The session's curves and its default moment end at 15:30.
    snaps = [(ts, r) for ts, r in _snapshots(rows) if _minute_of_day(ts) <= SESSION_OPEN_MIN + SESSION_MINUTES]
    out = {"expiry": expiry, "day": view_day.isoformat(), "days": days}
    if not snaps:
        return {**out, "note": f"No usable chain snapshots for {underlying} {expiry} on {view_day.isoformat()}."}
    minutes = _per_minute(snaps)
    moment_ts = max((ts for ts, _ in snaps if at is None or ts <= at), default=snaps[-1][0])
    moment_rows = next(r for ts, r in snaps if ts == moment_ts)
    spot = moment_rows[0][7]
    step = _step(r[1] for r in moment_rows)
    out.update(
        as_of=moment_ts, as_of_ist=_ist(moment_ts), spot=spot, futures=moment_rows[0][10], vix=moment_rows[0][11],
        step=step, atm=min({r[1] for r in moment_rows}, key=lambda k: (abs(k - spot), k)),
        snapshots=len(snaps), first_ts=snaps[0][0], last_ts=snaps[-1][0],
        days_to_expiry=round(max(0.0, _years(expiry, moment_ts)) * 365, 2),
    )
    volx = _volx(minutes, expiry)
    out["volx"] = volx
    out["expected_move"] = _expected_move(volx, expiry, moment_ts, spot)
    out["table"] = _table(moment_rows, expiry, moment_ts, snaps)
    out["gamma"] = _gamma_view(moment_rows, expiry, moment_ts, minutes, step)
    out["spectrum"] = _spectrum(snaps)
    out["writers"] = _writers(snaps, moment_ts)
    now_iv = next((p["ntm_iv"] for p in reversed(volx) if p["ts"] <= moment_ts and p["ntm_iv"]), None)
    out["iv_range"] = iv_range(path, underlying, view_day, current=now_iv, spot=spot,
                               days_to_expiry=out["days_to_expiry"])
    return out


def _volx(minutes, expiry) -> list[dict]:
    points = []
    for ts, rows in minutes:
        spot = rows[0][7]
        points.append({"ts": ts, "spot": spot, "vix": rows[0][11], **_ntm_iv(rows, spot, expiry, ts)})
    return points


def _expected_move(volx, expiry, moment_ts, spot) -> dict:
    """Open +- 1 sigma through the session from the first NTM IV, and 1-sigma moves from the chosen moment."""
    opening = next((p for p in volx if p["ntm_iv"]), None)
    band = []
    if opening:
        s0, iv0 = opening["spot"], opening["ntm_iv"] / 100
        day_sigma = s0 * iv0 * math.sqrt(1 / 365)
        for p in volx:
            elapsed = min(SESSION_MINUTES, max(0, _minute_of_day(p["ts"]) - SESSION_OPEN_MIN))
            width = day_sigma * math.sqrt(elapsed / SESSION_MINUTES)
            band.append({"ts": p["ts"], "spot": p["spot"], "upper": round(s0 + width, 2), "lower": round(s0 - width, 2)})
    current = next((p for p in reversed(volx) if p["ntm_iv"] and p["ts"] <= moment_ts), None)
    moves = {}
    if current:
        iv = current["ntm_iv"] / 100
        to_expiry = max(0.0, _years(expiry, moment_ts)) * 365
        for label, days in (("1 day", 1.0), ("1 week", 7.0), ("to expiry", to_expiry)):
            if days > 0:
                moves[label] = {"days": round(days, 2), "points": round(spot * iv * math.sqrt(days / 365), 2),
                                "pct": round(iv * math.sqrt(days / 365) * 100, 3)}
    return {"open": opening and {"ts": opening["ts"], "spot": opening["spot"], "ntm_iv": opening["ntm_iv"]},
            "band": band, "moves": moves}


def _table(rows, expiry, ts, snaps) -> list[dict]:
    years = _years(expiry, ts)
    spot = rows[0][7]
    opening = {}
    for r in snaps[0][1]:
        opening[(r[1], r[2])] = r[5]
    grid: dict[float, dict] = {}
    for r in rows:
        strike, kind = r[1], r[2]
        prem = _premium(r[3], r[8], r[9])
        g = og.greeks(prem, spot, strike, years, kind == "CE") if prem and years > 0 else None
        grid.setdefault(strike, {"strike": strike})[kind] = {
            "ltp": r[3], "bid": r[8], "ask": r[9], "volume": r[4], "oi": r[5],
            "oi_change": (r[5] - r[6]) if r[5] is not None and r[6] is not None else None,
            "oi_change_today": (r[5] - opening[(strike, kind)]) if r[5] is not None and opening.get((strike, kind)) is not None else None,
            "iv": round(g["iv"] * 100, 2) if g else None,
            "delta": round(g["delta"], 4) if g else None,
            "gamma": g["gamma"] if g else None,
            "theta": round(g["theta_day"], 3) if g else None,
            "vega": round(g["vega_point"], 3) if g else None,
        }
    return [grid[k] for k in sorted(grid)]


def _gex_rows(rows, expiry, ts):
    """(strike, kind, oi, iv) for every solvable contract at one snapshot."""
    years = _years(expiry, ts)
    spot = rows[0][7]
    solved = []
    for r in rows:
        if r[5] is None or r[5] <= 0:
            continue
        vol = _iv(_premium(r[3], r[8], r[9]), spot, r[1], years, r[2] == "CE")
        if vol:
            solved.append((r[1], r[2], r[5], vol))
    return solved, years, spot


def _net_gex(solved, years, at_spot) -> float:
    total = 0.0
    for strike, kind, oi, vol in solved:
        exposure = _gamma(at_spot, strike, years, vol) * oi * at_spot * at_spot * 0.01
        total += exposure if kind == "CE" else -exposure
    return total


def _gamma_view(rows, expiry, ts, minutes, step) -> dict:
    """GEX in rupees of dealer gamma per 1% move, OI in shares (the chain stores quantities, not lots). Convention:
    dealers long calls / short puts (SqueezeMetrics), so call gamma counts +, put gamma -. A model output."""
    solved, years, spot = _gex_rows(rows, expiry, ts)
    per: dict[float, dict] = {}
    for strike, kind, oi, vol in solved:
        exposure = _gamma(spot, strike, years, vol) * oi * spot * spot * 0.01
        cell = per.setdefault(strike, {"strike": strike, "call": 0.0, "put": 0.0})
        if kind == "CE":
            cell["call"] += exposure
        else:
            cell["put"] -= exposure
    strikes = [dict(c, net=c["call"] + c["put"]) for _, c in sorted(per.items())]
    net = sum(c["net"] for c in strikes)
    zero = None
    if solved and years > 0:
        grid_step = step or spot * 0.0025
        lo, hi = spot * (1 - WALL_SPAN), spot * (1 + WALL_SPAN)
        points = []
        level = lo
        while level <= hi:
            points.append((level, _net_gex(solved, years, level)))
            level += grid_step / 2
        crossings = [(a, b) for a, b in zip(points, points[1:]) if (a[1] <= 0 < b[1]) or (a[1] >= 0 > b[1])]
        if crossings:
            (x0, y0), (x1, y1) = min(crossings, key=lambda c: abs((c[0][0] + c[1][0]) / 2 - spot))
            zero = round(x0 + (x1 - x0) * (0 - y0) / (y1 - y0), 2) if y1 != y0 else round(x0, 2)
    call_wall = max(strikes, key=lambda c: c["call"])["strike"] if strikes else None
    put_wall = min(strikes, key=lambda c: c["put"])["strike"] if strikes else None
    timeline = []
    last_point = None
    for p_ts, p_rows in minutes:
        if last_point is not None and p_ts - last_point < GEX_EVERY_SECONDS and p_ts != minutes[-1][0]:
            continue
        last_point = p_ts
        s_solved, s_years, s_spot = _gex_rows(p_rows, expiry, p_ts)
        timeline.append({"ts": p_ts, "spot": s_spot, "net": round(_net_gex(s_solved, s_years, s_spot))})
    return {"spot": spot, "net": round(net), "zero_gamma": zero, "call_wall": call_wall, "put_wall": put_wall,
            "strikes": [{k: (round(v) if k != "strike" else v) for k, v in c.items()} for c in strikes],
            "timeline": timeline, "unit": "rupees per 1% move (OI in shares)"}


def _spectrum(snaps) -> dict:
    """Cumulative CE / PE volume: each contract adds what it traded since its previous snapshot. A contract first seen
    after the day's first snapshot (a strike that moved into the band) adds nothing for that first sighting."""
    seen: dict[tuple, int] = {}
    day_volume: dict[tuple, int] = {}
    first_ts = snaps[0][0]
    ce = pe = 0
    points = []
    for ts, rows in snaps:
        for r in rows:
            key = (r[1], r[2])
            volume = r[4] or 0
            before = seen.get(key)
            if before is None:
                added = volume if ts == first_ts else 0
            else:
                added = max(0, volume - before)
            seen[key] = volume
            day_volume[key] = max(day_volume.get(key, 0), volume)
            if r[2] == "CE":
                ce += added
            else:
                pe += added
        points.append({"ts": ts, "spot": rows[0][7], "ce": ce, "pe": pe})
    points = [p for i, p in enumerate(points) if i == len(points) - 1 or p["ts"] // 60 != points[i + 1]["ts"] // 60]
    zones = []
    for kind in ("CE", "PE"):
        top = sorted(((k[0], v) for k, v in day_volume.items() if k[1] == kind), key=lambda x: -x[1])[:5]
        zones += [{"strike": s, "type": kind, "volume": v} for s, v in top]
    return {"points": points, "zones": zones, "pcr": round(pe / ce, 3) if ce else None, "ce": ce, "pe": pe}


def _writers(snaps, moment_ts) -> dict:
    """Writers' unrealised P&L on OI added today, per strike at ``moment_ts`` and in total through the day."""
    state: dict[tuple, dict] = {}
    timeline = []
    per = {}
    for ts, rows in snaps:
        if ts > moment_ts:
            break
        for r in rows:
            key = (r[1], r[2])
            oi = r[5]
            prem = _premium(r[3], r[8], r[9])
            if oi is None or prem is None:
                continue
            s = state.get(key)
            if s is None:
                opening = r[6] if r[6] is not None else oi
                s = state[key] = {"open": opening, "last": opening, "added": 0, "notional": 0.0, "prem": prem}
            change = oi - s["last"]
            if change > 0:
                s["added"] += change
                s["notional"] += change * prem
            s["last"] = oi
            s["prem"] = prem
        total = {"CE": 0.0, "PE": 0.0}
        for key, s in state.items():
            net = s["last"] - s["open"]
            if net > 0 and s["added"]:
                total[key[1]] += net * (s["notional"] / s["added"] - s["prem"])
        if not timeline or ts // 60 != timeline[-1]["ts"] // 60:
            timeline.append({"ts": ts, "ce": round(total["CE"]), "pe": round(total["PE"])})
        else:
            timeline[-1] = {"ts": ts, "ce": round(total["CE"]), "pe": round(total["PE"])}
    for key, s in state.items():
        net = s["last"] - s["open"]
        cell = per.setdefault(key[0], {"strike": key[0]})
        avg = s["notional"] / s["added"] if s["added"] else None
        cell[key[1]] = {"net_added": net, "avg_premium": round(avg, 2) if avg else None, "premium": round(s["prem"], 2),
                        "pnl": round(net * (avg - s["prem"])) if net > 0 and avg else 0}
    return {"strikes": [per[k] for k in sorted(per)], "timeline": timeline,
            "ce_total": timeline[-1]["ce"] if timeline else 0, "pe_total": timeline[-1]["pe"] if timeline else 0,
            "basis": "OI added today x (average premium while it was added - premium now); assumes the added OI was written"}


# ------------------------------------------------------------------------------------------------ IV range

def iv_range(path: str, underlying: str, view_day: date, current: float | None = None, spot: float | None = None,
             days_to_expiry: float | None = None) -> dict:
    """NTM IV near each stored session's close (the last snapshot by 15:25 IST, else the day's last) on the nearest expiry at least one
    calendar day away (an expiry-day IV is not comparable), up to ``view_day``."""
    key = ("iv_range", path, underlying, view_day.isoformat())
    series = _cached(key, 600 if view_day >= _day(int(time.time())) else 86400,
                     lambda: _iv_series(path, underlying, view_day))
    values = [p["iv"] for p in series if p["iv"]]
    out = {"series": series, "sessions": len(values)}
    if not values:
        return out
    now = current if current else values[-1]
    window20 = values[-20:]
    out.update(current=now, high=max(values), low=min(values), mean=round(sum(values) / len(values), 2),
               high_20=max(window20), low_20=min(window20),
               percentile=round(100 * sum(v <= now for v in values) / len(values), 1),
               rank=round(100 * (now - min(values)) / (max(values) - min(values)), 1) if max(values) > min(values) else None)
    if spot:
        out["projected"] = {label: {"days": round(d, 2), "points": round(spot * now / 100 * math.sqrt(d / 365), 2)}
                            for label, d in (("1 day", 1.0), ("1 week", 7.0), ("to expiry", days_to_expiry or 0))
                            if d and d > 0}
    return out


def _iv_series(path: str, underlying: str, view_day: date) -> list[dict]:
    connection = _read(path)
    try:
        days = [r[0] for r in connection.execute(
            "SELECT day FROM chain_days WHERE underlying = ? AND day <= ? ORDER BY day",
            (underlying, view_day.isoformat()))]
        series = []
        for d in days:
            day = date.fromisoformat(d)
            close = int(datetime(day.year, day.month, day.day, 15, 25, tzinfo=IST).timestamp())
            # older sessions keep only their last snapshot per underlying (whale.py pruning), which can fall after 15:25
            ts = connection.execute(
                "SELECT MAX(ts) FROM chain_snapshots WHERE underlying = ? AND ts BETWEEN ? AND ? AND expiry > ?",
                (underlying, _start(day), close, d)).fetchone()[0] or connection.execute(
                "SELECT MAX(ts) FROM chain_snapshots WHERE underlying = ? AND ts BETWEEN ? AND ? AND expiry > ?",
                (underlying, _start(day), _start(day + timedelta(days=1)) - 1, d)).fetchone()[0]
            if not ts:
                continue
            rows = connection.execute(
                """SELECT ts, strike, option_type, ltp, volume, oi, prev_oi, spot, bid, ask, fp, vix, expiry
                   FROM chain_snapshots WHERE underlying = ? AND ts = ? AND spot > 0 AND expiry > ?
                   AND abs(strike - spot) <= spot * 0.03""", (underlying, ts, d)).fetchall()
            if not rows:
                continue
            expiry = min(r[12] for r in rows)
            chosen = [r for r in rows if r[12] == expiry]
            spot = chosen[0][7]
            iv = _ntm_iv(chosen, spot, expiry, ts)["ntm_iv"]
            series.append({"day": d, "ts": ts, "iv": iv, "spot": spot, "vix": chosen[0][11], "expiry": expiry,
                           "days_to_expiry": round(max(0.0, _years(expiry, ts)) * 365, 2)})
        return series
    finally:
        connection.close()


# ------------------------------------------------------------------------------------------------ futures flow

def _futures_symbol(ticks: sqlite3.Connection, underlying: str, start: int, end: int) -> str | None:
    exchange = EXCHANGE.get(underlying, "NSE")
    pattern = re.compile(rf"{exchange}:{re.escape(underlying)}\d{{2}}[A-Z]{{3}}FUT")
    best, best_n = None, 0
    for symbol_id, symbol in ticks.execute("SELECT id, symbol FROM tick_symbols WHERE symbol LIKE ?",
                                           (f"{exchange}:{underlying}%FUT",)):
        if not pattern.fullmatch(symbol):
            continue
        n = ticks.execute("SELECT COUNT(*) FROM tick_minute_flow WHERE symbol_id = ? AND minute_ts >= ? AND minute_ts < ?",
                          (symbol_id, start, end)).fetchone()[0]
        if n > best_n:
            best, best_n = symbol, n
    return best


def futures_flow(tick_path: str, research_path: str, underlying: str, day: str | None = None, tf: int = 5,
                 today: date | None = None) -> dict:
    """MFLOW and smart candles for one underlying's current futures contract on one session."""
    underlying = underlying.upper()
    tf = tf if tf in (1, 3, 5, 15, 30) else 5
    view_day = date.fromisoformat(day) if day else (today or _day(int(time.time())))
    key = ("futures_flow", tick_path, underlying, view_day.isoformat(), tf)
    ttl = 30 if view_day >= _day(int(time.time())) else 3600
    return _cached(key, ttl, lambda: _flow_build(tick_path, research_path, underlying, view_day, tf))


def _flow_build(tick_path, research_path, underlying, view_day, tf) -> dict:
    start, end = _start(view_day), _start(view_day + timedelta(days=1))
    ticks = _read(tick_path)
    try:
        symbol = _futures_symbol(ticks, underlying, start, end)
        if symbol is None:
            # the session asked for may have no flow (a holiday, before the open): fall back to the last session with any
            last = None
            for probe in range(1, 8):
                d = view_day - timedelta(days=probe)
                s = _futures_symbol(ticks, underlying, _start(d), _start(d + timedelta(days=1)))
                if s:
                    view_day, start, end, symbol, last = d, _start(d), _start(d + timedelta(days=1)), s, d
                    break
            if symbol is None:
                return {"underlying": underlying, "day": view_day.isoformat(),
                        "note": f"No futures minute flow stored for {underlying}."}
        rows = ticks.execute(
            """SELECT f.minute_ts, f.open, f.high, f.low, f.close, f.volume, f.buy_volume, f.sell_volume, f.delta
               FROM tick_minute_flow f JOIN tick_symbols s ON s.id = f.symbol_id
               WHERE s.symbol = ? AND f.minute_ts >= ? AND f.minute_ts < ? ORDER BY f.minute_ts""",
            (symbol, start, end)).fetchall()
    finally:
        ticks.close()
    research = _read(research_path)
    try:
        oi_rows = research.execute("SELECT ts, oi FROM oi_minutes WHERE symbol = ? AND ts >= ? AND ts < ? ORDER BY ts",
                                   (symbol, start - 86400 * 4, end)).fetchall()
    except sqlite3.OperationalError:
        oi_rows = []
    finally:
        research.close()
    minutes = [r for r in rows if SESSION_OPEN_MIN <= _minute_of_day(r[0]) < SESSION_OPEN_MIN + SESSION_MINUTES]
    out = {"underlying": underlying, "symbol": symbol, "day": view_day.isoformat(), "tf": tf}
    if not minutes:
        return {**out, "note": f"No session minutes for {symbol} on {view_day.isoformat()}."}
    out["mflow"] = mflow(minutes, oi_rows, start, tf)
    out["candles"] = smart_candles(minutes, tf)
    return out


def mflow(minutes, oi_rows, day_start: int, tf: int) -> dict:
    """Assign futures volume to the sign of the next OI change. ``minutes``: (ts, o, h, l, c, volume, buy, sell, delta);
    ``oi_rows``: (ts, oi) readings, which may start before the session (the previous reading anchors the first change)."""
    if not oi_rows:
        return {"available": False, "note": "OI is not captured for this futures contract (oi_minutes holds NIFTY, "
                                            "BANKNIFTY and SENSEX front-month futures from 26 Oct 2026)."}
    readings = sorted(oi_rows)
    before = [oi for ts, oi in readings if ts < day_start]
    last_oi = before[-1] if before else None
    index = 0
    pending_volume = 0
    pending_from_price = minutes[0][1]
    classes = {"long_buildup": 0, "short_buildup": 0, "short_covering": 0, "long_unwinding": 0}
    buckets: dict[int, dict] = {}
    cum_new = cum_exit = 0
    opening_oi = None
    for ts, o, h, l, c, volume, buy, sell, delta in minutes:
        minute_end = ts + 60
        oi = None
        while index < len(readings) and readings[index][0] < minute_end:
            if readings[index][0] >= day_start or oi is None:
                oi = readings[index][1]
            index += 1
        if oi is not None and opening_oi is None:
            opening_oi = last_oi if last_oi is not None else oi
        bucket_ts = day_start + ((ts - day_start) // (tf * 60)) * tf * 60
        b = buckets.setdefault(bucket_ts, {"ts": bucket_ts, "volume": 0, "new": 0, "exit": 0, "oi": None, "close": c,
                                           **{k: 0 for k in classes}})
        b["volume"] += volume or 0
        b["close"] = c
        pending_volume += volume or 0
        if oi is not None and last_oi is not None and oi != last_oi:
            up_oi = oi > last_oi
            up_price = c >= pending_from_price
            kind = ("long_buildup" if up_price else "short_buildup") if up_oi else (
                "short_covering" if up_price else "long_unwinding")
            classes[kind] += pending_volume
            b[kind] += pending_volume
            if up_oi:
                b["new"] += pending_volume
                cum_new += pending_volume
            else:
                b["exit"] += pending_volume
                cum_exit += pending_volume
            pending_volume = 0
            pending_from_price = c
        if oi is not None:
            last_oi = oi
            b["oi"] = oi
        b["cum_new"], b["cum_exit"] = cum_new, cum_exit
    series = [buckets[k] for k in sorted(buckets)]
    total = sum(classes.values())
    return {"available": True, "series": series, "classes": classes, "unassigned": pending_volume,
            "opening_oi": opening_oi, "oi": last_oi, "oi_change": (last_oi - opening_oi) if last_oi and opening_oi else None,
            "new": cum_new, "exit": cum_exit,
            "shares": {k: round(v / total, 3) for k, v in classes.items()} if total else {}}


def smart_candles(minutes, tf: int) -> dict:
    """tf-minute candles with delta strength, the first-hour initial balance and balance-cluster boxes."""
    if not minutes:
        return {"bars": [], "ib": None, "clusters": []}
    day_start = minutes[0][0] - (_minute_of_day(minutes[0][0]) - SESSION_OPEN_MIN) * 60 - minutes[0][0] % 60
    bars: dict[int, dict] = {}
    for ts, o, h, l, c, volume, buy, sell, delta in minutes:
        bucket = day_start + ((ts - day_start) // (tf * 60)) * tf * 60
        b = bars.get(bucket)
        if b is None:
            bars[bucket] = {"ts": bucket, "open": o, "high": h, "low": l, "close": c, "volume": volume or 0,
                            "delta": delta or 0}
        else:
            b["high"], b["low"], b["close"] = max(b["high"], h), min(b["low"], l), c
            b["volume"] += volume or 0
            b["delta"] += delta or 0
    ordered = [bars[k] for k in sorted(bars)]
    for b in ordered:
        b["strength"] = round(b["delta"] / b["volume"], 3) if b["volume"] else 0.0
    ib_rows = [m for m in minutes if _minute_of_day(m[0]) < SESSION_OPEN_MIN + IB_MINUTES]
    ib = {"high": max(m[2] for m in ib_rows), "low": min(m[3] for m in ib_rows),
          "start": ib_rows[0][0], "end": ib_rows[-1][0] + 60} if ib_rows else None
    return {"bars": ordered, "ib": ib, "clusters": balance_clusters(ordered)}


def balance_clusters(bars, min_bars: int = 3, overlap: float = 0.6, max_height: float = 2.5) -> list[dict]:
    """Runs of bars that each overlap the run's range by at least ``overlap`` of their own range, while the run stays
    within ``max_height`` x the median bar range: rotation (balance) rather than a directional move."""
    if len(bars) < min_bars:
        return []
    ranges = [b["high"] - b["low"] for b in bars if b["high"] > b["low"]]
    if not ranges:
        return []
    limit = max_height * statistics.median(ranges)
    clusters = []
    i = 0
    while i < len(bars):
        lo, hi = bars[i]["low"], bars[i]["high"]
        j = i + 1
        while j < len(bars):
            b = bars[j]
            span = b["high"] - b["low"]
            shared = max(0.0, min(hi, b["high"]) - max(lo, b["low"]))
            new_lo, new_hi = min(lo, b["low"]), max(hi, b["high"])
            if (span <= 0 or shared / span >= overlap) and new_hi - new_lo <= limit:
                lo, hi = new_lo, new_hi
                j += 1
            else:
                break
        if j - i >= min_bars:
            clusters.append({"start": bars[i]["ts"], "end": bars[j - 1]["ts"], "high": hi, "low": lo, "bars": j - i})
            i = j
        else:
            i += 1
    return clusters
