"""ATM straddle and ATM open interest series from the stored option chains (Market breadth -> ATM straddle & OI).

Source: chain_snapshots (oi_capture.py) -- index chains every minute, every F&O stock chain (+-8 strikes since 28 Oct 2026, +-4 before) once per
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
BAND = 0.08                                           # strikes within 8% of spot are read (a 4% window dropped a
                                                      # fixed OI-band strike once a stock moved through the day)
# OI (28 Oct 2026: the rolling ATM flipped between 50-point strikes whose OI differs several-fold, so the ATM OI line
# spiked and dipped although every strike's own OI was smooth and matched the Upstox chain to 0.0% median):
OI_BAND_STRIKES = 3       # the OI line sums the day's opening ATM +-3 strikes, a set fixed for the whole session, so
                          # it moves only when OI does (a band that follows the ATM still swapped round strikes in/out)
HYSTERESIS = 0.75         # the rolling ATM changes only once spot is 3/4 of a strike step past the current one
PARTIAL_SNAPSHOT = 0.6    # a snapshot with fewer strikes than this share of the typical one is skipped
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
    """Each stored expiry with its first and last snapshot time. Sessions before today no longer change, so their scan
    (every row the underlying ever stored: 0.4 s warm, 2 s cold) is kept for hours; only today's rows are read each
    minute. It used to be the whole scan every 60 seconds, on every refresh of the straddle and options views."""
    today_start = _start(_day(int(time.time())))

    def scan(lo: int, hi: int) -> list[tuple]:
        connection = _read(path)
        try:
            return connection.execute(
                """SELECT expiry, MIN(ts), MAX(ts), COUNT(DISTINCT ts) FROM chain_snapshots
                   WHERE underlying = ? AND ts >= ? AND ts < ? GROUP BY expiry""", (underlying, lo, hi)).fetchall()
        except sqlite3.OperationalError:
            return []
        finally:
            connection.close()

    def build():
        past = _cached(("expiries-past", path, underlying, today_start), 6 * 3600, lambda: scan(0, today_start))
        merged: dict[str, list] = {e: [a, b, n] for e, a, b, n in past}
        for e, a, b, n in scan(today_start, today_start + 7 * 86400):
            if e in merged:
                merged[e] = [min(merged[e][0], a), max(merged[e][1], b), merged[e][2] + n]
            else:
                merged[e] = [a, b, n]
        return [dict(expiry=e, first_ts=a, last_ts=b, snapshots=n) for e, (a, b, n) in sorted(merged.items())]
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
        # Index chains are wide (every strike near spot each minute): read 8% around spot. A stock chain is swept as
        # ATM +-8 strikes only (+-4 before 28 Oct 2026), so read all of it (low-priced stocks have 1-rupee steps an 8% window cuts short).
        rows = connection.execute(
            """SELECT ts, strike, option_type, symbol, ltp, volume, oi, spot FROM chain_snapshots
               WHERE underlying = ? AND ts >= ? AND ts < ? AND expiry = ? AND spot > 0
                 AND abs(strike - spot) <= spot * ? ORDER BY ts""",
            (underlying, load_start, end, chosen["expiry"], BAND if underlying in INDICES else 1.0)).fetchall()
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
    sizes = sorted(len(legs) for _ts, _spot, legs in snapshots)
    typical = sizes[len(sizes) // 2] if sizes else 0
    held_atm: float | None = None
    day_band: dict[date, list[float]] = {}
    skipped = 0
    for ts, spot, legs in snapshots:
        if typical >= 10 and len(legs) < PARTIAL_SNAPSHOT * typical:
            skipped += 1
            continue
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
        priced = [k for (k, side) in legs if side == "CE" and (k, "PE") in legs
                  and (legs[(k, "CE")][1] or 0) > 0 and (legs[(k, "PE")][1] or 0) > 0]
        # A contract that has not traded today carries a stale last price (360ONE 960 CE showed 274 with spot at 970
        # on 28 Oct 2026); the straddle uses strikes whose two legs both traded today, falling back to any priced pair.
        pairs = [k for k in priced if (legs[(k, "CE")][2] or 0) > 0 and (legs[(k, "PE")][2] or 0) > 0] or priced
        listed = sorted(pairs)
        if strike is not None:
            atm = strike if strike in pairs else None
        else:
            atm = min(listed, key=lambda k: (abs(k - spot), k)) if listed else None
            if atm is not None and held_atm in pairs and held_atm != atm:
                i = listed.index(held_atm)
                gaps_near = [b - a for a, b in zip(listed[max(0, i - 1):i + 1], listed[i:i + 2]) if b > a]
                if gaps_near and abs(spot - held_atm) <= HYSTERESIS * min(gaps_near):
                    atm = held_atm
            held_atm = atm
        if atm is None:
            continue
        ce, pe = legs[(atm, "CE")], legs[(atm, "PE")]
        if d not in day_band:                     # fixed at the session's first usable snapshot
            strikes = sorted({k for (k, side) in legs if side == "CE" and (k, "PE") in legs})   # every listed strike
            opening = min(strikes, key=lambda k: (abs(k - spot), k))
            i = strikes.index(opening)
            # A +-4-strike stock sweep (before 28 Oct 2026) soon left a wider fixed band: +-2 for those small chains.
            width = OI_BAND_STRIKES if typical >= 30 else 2
            day_band[d] = strikes[i - width:i + width + 1] if i >= width and len(strikes) >= i + width + 1 else []
        band = day_band[d]
        band_oi = {}
        for side in ("CE", "PE"):
            values = [(legs.get((k, side)) or (None, None, None, None))[3] for k in band]
            # an untraded strike's OI of 0 is a real zero; only a strike missing from the snapshot voids the band
            band_oi[side] = sum(values) if band and all(v is not None and v >= 0 for v in values) else None
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
                     ce_oi=ce[3] or None, pe_oi=pe[3] or None, volume=weight,
                     ce_oi_band=band_oi["CE"], pe_oi_band=band_oi["PE"],
                     band=[band[0], band[-1]] if band else None,
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
        skipped_partial_snapshots=skipped, oi_band_strikes=OI_BAND_STRIKES,
        strikes=sorted({k for (k, side) in last_legs if side == "CE" and (k, "PE") in last_legs}),
        anchors={"day": anchors["d"], "week": anchors["w"], "month": anchors["m"]},
        series_first_ts=chosen["first_ts"], series_last_ts=chosen["last_ts"], points=points,
        note=("VWAP anchors cannot start before this series' first stored snapshot "
              f"({datetime.fromtimestamp(chosen['first_ts'], IST):%Y-%m-%d %H:%M} IST)."))
