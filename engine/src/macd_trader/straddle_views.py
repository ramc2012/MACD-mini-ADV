"""ATM straddle and ATM open interest series from the stored option chains (Market breadth -> ATM straddle & OI).

Source: chain_snapshots (oi_capture.py) -- index chains every minute, every F&O stock chain (+-4 strikes) once per
15-minute sweep, front series only (stocks add the next series in a series' last week). Nothing is downloaded here.

Each snapshot's ATM strike is the stored strike nearest the chain's spot (or a fixed strike when one is chosen). The
straddle is that strike's CE + PE last price; its volume is the two contracts' traded quantity since their previous
snapshot that day (a contract's first snapshot of the day counts its whole cumulative volume only when it is the
day's first snapshot; otherwise its volume is unknown and carries no weight). VWAPs of the straddle restart each
session, each IST week (Monday) and each calendar month, and never start before the series' first stored snapshot.
"""
from __future__ import annotations

import sqlite3
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
INDICES = ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX")
SPANS = {"day": 60, "week": 300, "expiry": 900}     # displayed resolution (seconds) per range
BAND = 0.04                                           # strikes within 4% of spot are read
_cache: dict[tuple, tuple[float, object]] = {}


def _cached(key: tuple, ttl: float, build):
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < ttl:
        return hit[1]
    value = build()
    _cache[key] = (time.monotonic(), value)
    if len(_cache) > 256:
        for stale in sorted(_cache, key=lambda k: _cache[k][0])[:64]:
            _cache.pop(stale, None)
    return value


def _read(path: str) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)


def _day(ts: int) -> date:
    return datetime.fromtimestamp(ts, IST).date()


def _start(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=IST).timestamp())


def instruments(path: str) -> dict:
    def build():
        connection = _read(path)
        try:
            rows = connection.execute(
                "SELECT underlying, MIN(day), MAX(day), COUNT(*) FROM chain_days GROUP BY underlying").fetchall()
        except sqlite3.OperationalError:
            rows = []
        finally:
            connection.close()
        order = {name: i for i, name in enumerate(INDICES)}
        items = [dict(underlying=u, kind="index" if u in order else "stock", first_day=a, last_day=b, days=n)
                 for u, a, b, n in rows]
        items.sort(key=lambda r: (order.get(r["underlying"], len(order)), r["underlying"]))
        return {"instruments": items}
    return _cached(("instruments", path), 300, build)


def expiries(path: str, underlying: str) -> list[dict]:
    def build():
        connection = _read(path)
        try:
            rows = connection.execute(
                """SELECT expiry, MIN(ts), MAX(ts), COUNT(DISTINCT ts) FROM chain_snapshots
                   WHERE underlying = ? GROUP BY expiry ORDER BY expiry""", (underlying,)).fetchall()
        except sqlite3.OperationalError:
            rows = []
        finally:
            connection.close()
        return [dict(expiry=e, first_ts=a, last_ts=b, snapshots=n) for e, a, b, n in rows]
    return _cached(("expiries", path, underlying), 60, build)


def _front(series: list[dict], today: date) -> dict | None:
    live = [s for s in series if s["expiry"] >= today.isoformat()]
    return live[0] if live else (series[-1] if series else None)


def series(path: str, underlying: str, expiry: str | None = None, span: str = "day", day: str | None = None,
           strike: float | None = None, today: date | None = None) -> dict:
    span = span if span in SPANS else "day"
    listed = expiries(path, underlying)
    chosen = next((s for s in listed if s["expiry"] == expiry), None) if expiry else _front(listed, today or _day(int(time.time())))
    base = dict(underlying=underlying, span=span, expiries=listed, strike_mode="fixed" if strike else "atm", points=[])
    if not chosen:
        return {**base, "expiry": expiry, "note": "No stored option chain for this instrument and expiry."}
    first, last = chosen["first_ts"], chosen["last_ts"]
    view_day = date.fromisoformat(day) if day else _day(last)
    view_day = min(max(view_day, _day(first)), _day(last))
    end = _start(view_day + timedelta(days=1))
    week_start = _start(view_day - timedelta(days=view_day.weekday()))
    month_start = _start(view_day.replace(day=1))
    range_start = {"day": _start(view_day), "week": week_start, "expiry": first}[span]
    load_start = max(first, min(range_start, week_start, month_start))
    key = ("series", path, underlying, chosen["expiry"], span, view_day.isoformat(), strike, last)
    return _cached(key, 30, lambda: {**base, **_build(path, underlying, chosen, span, view_day, strike,
                                                        load_start, range_start, end)})


def _build(path, underlying, chosen, span, view_day, strike, load_start, range_start, end) -> dict:
    connection = _read(path)
    try:
        rows = connection.execute(
            """SELECT ts, strike, option_type, symbol, ltp, volume, oi, spot FROM chain_snapshots
               WHERE underlying = ? AND ts >= ? AND ts < ? AND expiry = ? AND spot > 0
                 AND abs(strike - spot) <= spot * ? ORDER BY ts""",
            (underlying, load_start, end, chosen["expiry"], BAND)).fetchall()
        days = [r[0] for r in connection.execute(
            "SELECT day FROM chain_days WHERE underlying = ? AND day BETWEEN ? AND ? ORDER BY day",
            (underlying, _day(chosen["first_ts"]).isoformat(), _day(chosen["last_ts"]).isoformat()))]
    finally:
        connection.close()
    snapshots: list[tuple[int, float, dict]] = []
    for ts, k, side, symbol, ltp, volume, oi, spot in rows:
        if not snapshots or snapshots[-1][0] != ts:
            snapshots.append((ts, spot, {}))
        snapshots[-1][2][(k, side)] = (symbol, ltp, volume, oi)
    previous: dict[str, tuple[date, int]] = {}
    totals: dict[str, list] = {"d": [None, 0.0, 0.0], "w": [None, 0.0, 0.0], "m": [None, 0.0, 0.0]}
    anchors: dict[str, int | None] = {"d": None, "w": None, "m": None}
    seen_day: date | None = None
    step = SPANS[span]
    points: list[dict] = []
    for ts, spot, legs in snapshots:
        d = _day(ts)
        first_of_day = d != seen_day
        seen_day = d
        traded = {}
        for (k, side), (symbol, _ltp, volume, _oi) in legs.items():
            before = previous.get(symbol)
            if volume is None:
                traded[(k, side)] = None
            elif before and before[0] == d:
                traded[(k, side)] = max(0, volume - before[1])
            else:
                traded[(k, side)] = volume if first_of_day else None
            if volume is not None:
                previous[symbol] = (d, volume)
        pairs = [k for (k, side) in legs if side == "CE" and (k, "PE") in legs
                 and (legs[(k, "CE")][1] or 0) > 0 and (legs[(k, "PE")][1] or 0) > 0]
        if strike is not None:
            atm = strike if strike in pairs else None
        else:
            atm = min(pairs, key=lambda k: (abs(k - spot), k)) if pairs else None
        if atm is None:
            continue
        ce, pe = legs[(atm, "CE")], legs[(atm, "PE")]
        price = ce[1] + pe[1]
        v_ce, v_pe = traded[(atm, "CE")], traded[(atm, "PE")]
        weight = (v_ce or 0) + (v_pe or 0) if v_ce is not None and v_pe is not None else 0
        keys = {"d": d, "w": d - timedelta(days=d.weekday()), "m": (d.year, d.month)}
        vwap = {}
        for name, bucket in totals.items():
            if bucket[0] != keys[name]:
                bucket[:] = [keys[name], 0.0, 0.0]
                anchors[name] = ts
            bucket[1] += price * weight
            bucket[2] += weight
            vwap[name] = round(bucket[1] / bucket[2], 2) if bucket[2] > 0 else None
        if ts < range_start:
            continue
        point = dict(t=ts, spot=spot, strike=atm, ce=ce[1], pe=pe[1], straddle=round(price, 2),
                     ce_oi=ce[3], pe_oi=pe[3], volume=weight,
                     vwap_day=vwap["d"], vwap_week=vwap["w"], vwap_month=vwap["m"])
        if points and points[-1]["t"] // step == ts // step and _day(points[-1]["t"]) == d:
            point["volume"] += points[-1]["volume"]
            points[-1] = point                     # the bucket shows its last snapshot; VWAPs are cumulative
        else:
            points.append(point)
    gaps = sorted(b[0] - a[0] for a, b in zip(snapshots, snapshots[1:]) if _day(a[0]) == _day(b[0]))
    last_legs = snapshots[-1][2] if snapshots else {}
    return dict(
        expiry=chosen["expiry"], day=view_day.isoformat(), days=days,
        cadence_seconds=gaps[len(gaps) // 2] if gaps else None, resolution_seconds=step,
        strikes=sorted({k for (k, side) in last_legs if side == "CE" and (k, "PE") in last_legs}),
        anchors={"day": anchors["d"], "week": anchors["w"], "month": anchors["m"]},
        series_first_ts=chosen["first_ts"], series_last_ts=chosen["last_ts"], points=points,
        note=("VWAP anchors cannot start before this series' first stored snapshot "
              f"({datetime.fromtimestamp(chosen['first_ts'], IST):%Y-%m-%d %H:%M} IST)."))
