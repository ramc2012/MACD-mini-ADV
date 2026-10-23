"""Evidence-labelled Market Profile annotations and per-period baselines, for the auction chart and the agentic lane.

Every note carries the base rate it was measured with (research/mp_advanced_20261024: 5,250 index sessions
2021-26, both halves agreeing), so the reader weighs it instead of trusting the shape:
  poor high / poor low   the top / bottom TPO row touched by 2+ periods -> revisited next session 67% / 57.5%
                         (a high / low with a tail: 49% / 42%)
  tails (excess)         2+ single-print rows at an extreme -> the extreme holds 3 sessions 33-53% by length
                         (no tail: 21% high, 29% low)
  single-print zones     internal runs of single prints -> filled next session 42%, within 5 sessions 66%
  80% rule state         opened outside the prior session's value and two 30-minute periods closed back inside ->
                         the far side of value the same day 46% (all outside opens 25%, any re-entry 40%)
  naked POCs             shown as levels only: hit no more often than any price at the same distance
Per period (A = 09:15 ... M = 15:15): volume so far vs the period's usual share of a session, range vs its usual
range in ATR, and how one-sided the order flow is vs the period's 90th percentile (period_baselines_data.py).
TPO rows are 0.025% of the session open, 30-minute periods -- the definitions the research measured.
Reads the research store read-only (minute bars); pure functions otherwise.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, time
from zoneinfo import ZoneInfo

from . import agentic_features as feat
from .period_baselines_data import BASELINES

IST = ZoneInfo("Asia/Kolkata")
LETTERS = "ABCDEFGHIJKLM"
ROW = 0.00025                       # TPO row height as a fraction of the session open
TAIL_HOLD = {"high": ((8, 0.455), (4, 0.377), (2, 0.327)), "low": ((8, 0.534), (4, 0.44), (2, 0.401))}
NO_TAIL_HOLD = {"high": 0.213, "low": 0.292}
FUTURES_ROOTS = {"NIFTY": "NSE:NIFTY", "BANKNIFTY": "NSE:BANKNIFTY", "SENSEX": "BSE:SENSEX"}


def period_of(hm: int) -> str | None:
    """The 30-minute period letter of a minute of the day (555 = 09:15)."""
    if hm < 555 or hm >= 930:
        return None
    return LETTERS[min((hm - 555) // 30, len(LETTERS) - 1)]


def _bars(db: sqlite3.Connection, symbol: str, day, until: int | None = None) -> list[tuple]:
    rows = feat._minutes(db, symbol, day, until)
    out = []
    for ts, o, h, l, c, v in rows:
        local = datetime.fromtimestamp(ts, IST)
        out.append((local.hour * 60 + local.minute, float(o), float(h), float(l), float(c), float(v or 0)))
    return out


def tpo(bars: list[tuple]) -> dict | None:
    """TPO profile of minute bars [(hm, o, h, l, c, v)]: POC, 70% value area, poor extremes, tails, single prints."""
    if len(bars) < 30:
        return None
    step = bars[0][1] * ROW
    lo = min(b[3] for b in bars)
    rows: dict[int, set] = {}
    for hm, o, h, l, c, v in bars:
        p = (hm - 555) // 30
        for k in range(int((l - lo) // step), int((h - lo) // step) + 1):
            rows.setdefault(k, set()).add(p)
    keys = sorted(rows)
    counts = [len(rows[k]) for k in keys]
    poc_i = max(range(len(keys)), key=lambda i: (counts[i], -abs(i - len(keys) / 2)))
    i0 = i1 = poc_i
    total, acc = sum(counts), counts[poc_i]
    while acc < 0.7 * total and (i0 > 0 or i1 < len(keys) - 1):
        up = counts[i1 + 1] if i1 < len(keys) - 1 else -1
        dn = counts[i0 - 1] if i0 > 0 else -1
        if up >= dn:
            i1 += 1
            acc += up
        else:
            i0 -= 1
            acc += dn

    def price(i: int) -> float:
        return lo + (keys[i] + 0.5) * step

    tail_h = 0
    for c in reversed(counts):
        if c != 1:
            break
        tail_h += 1
    tail_l = 0
    for c in counts:
        if c != 1:
            break
        tail_l += 1
    periods = {(hm - 555) // 30 for hm, *_ in bars}
    if len(periods) < 2:                    # one period: every row is a single print, nothing to read
        tail_h = tail_l = 0
    zones, run = [], []
    for i in range(tail_l, len(keys) - tail_h):
        if counts[i] == 1:
            if run and keys[i] != keys[run[-1]] + 1:
                zones.append((round(price(run[0]) - step / 2, 2), round(price(run[-1]) + step / 2, 2)))
                run = []
            run.append(i)
        elif run:
            zones.append((round(price(run[0]) - step / 2, 2), round(price(run[-1]) + step / 2, 2)))
            run = []
    return {"high": max(b[2] for b in bars), "low": min(b[3] for b in bars), "poc": round(price(poc_i), 2),
            "vah": round(price(i1) + step / 2, 2), "val": round(price(i0) - step / 2, 2),
            "poor_high": counts[-1] >= 2 and len(periods) >= 2, "poor_low": counts[0] >= 2 and len(periods) >= 2,
            "tail_high_rows": tail_h if tail_h >= 2 else 0, "tail_low_rows": tail_l if tail_l >= 2 else 0,
            "single_print_zones": zones}


def period_closes(bars: list[tuple]) -> list[tuple[str, float, bool]]:
    """(letter, close, complete) per 30-minute period so far, in order."""
    last: dict[str, float] = {}
    for hm, o, h, l, c, v in bars:
        letter = period_of(hm)
        if letter:
            last[letter] = c
    last_hm = bars[-1][0] if bars else 0
    out = []
    for letter in sorted(last, key=LETTERS.index):
        end = 555 + 30 * LETTERS.index(letter) + (15 if letter == "M" else 30) - 1
        out.append((letter, last[letter], last_hm >= end))
    return out


def eighty_rule(bars: list[tuple], prior: dict | None) -> dict:
    """Where today stands in the 80%-rule sequence against the prior session's value area."""
    if not bars or not prior:
        return {"state": "unknown"}
    vah, val, opn = prior["vah"], prior["val"], bars[0][1]
    if val <= opn <= vah:
        return {"state": "opened inside prior value", "vah": vah, "val": val}
    above = opn > vah
    far = val if above else vah
    closes = [(k, c) for k, c, done in period_closes(bars) if done]
    inside_run, entered_at = 0, None
    for k, c in closes:
        inside_run = inside_run + 1 if val <= c <= vah else 0
        if inside_run >= 2 and entered_at is None:
            entered_at = k
    reached = (min(b[3] for b in bars) <= far) if above else (max(b[2] for b in bars) >= far)
    side = "above" if above else "below"
    if entered_at and reached:
        state = f"opened {side} prior value, two periods closed back inside by {entered_at}, far side {far:g} reached"
    elif entered_at:
        state = f"opened {side} prior value, two periods closed back inside by {entered_at}: far side {far:g} same day 46% (base 25%)"
    elif reached:
        state = f"opened {side} prior value and traded through it to the far side {far:g} without the two-period re-entry"
    elif any(val <= c <= vah for _, c in closes):
        state = f"opened {side} prior value, back inside for one period: far side {far:g} 40% once two periods close inside"
    else:
        state = f"opened {side} prior value and has not re-entered: far side {far:g} reached 25% of such days"
    return {"state": state, "vah": vah, "val": val, "far_side": far, "rule_active": bool(entered_at and not reached)}


def _kind(symbol: str) -> tuple[str, str | None]:
    if symbol.endswith("FUT"):
        for root, prefix in FUTURES_ROOTS.items():
            if symbol.startswith(prefix) and (root != "NIFTY" or not symbol.startswith(("NSE:NIFTYBANK", "NSE:NIFTYMID"))):
                return "futures", root
        return "futures", None
    if symbol.endswith("-EQ"):
        return "stock", symbol.split(":", 1)[-1].removesuffix("-EQ")
    if symbol.endswith("-INDEX"):
        return "index", None
    return "other", None


def _day_volume_baseline(db: sqlite3.Connection, symbol: str, day) -> float | None:
    """Median session volume over the prior 20 sessions of this symbol's own minute bars."""
    sessions, d = [], day
    for _ in range(20):
        d = feat.previous_session(d)
        sessions.append(d)
    lo, _ = feat._bounds(sessions[-1])
    _, hi = feat._bounds(sessions[0])
    rows = db.execute("""SELECT timestamp / 86400 AS d, SUM(volume) FROM historical_candles WHERE symbol=? AND timeframe_seconds=60
                         AND timestamp>=? AND timestamp<? GROUP BY d""", (symbol, lo, hi)).fetchall()
    vols = sorted(v for _, v in rows if v)
    return float(vols[len(vols) // 2]) if len(vols) >= 8 else None


def period_reading(bars: list[tuple], flow_minutes: list | None, symbol: str, atr: float | None,
                   day_volume: float | None, now: datetime) -> dict:
    """The current period against its baseline: volume pace, range, and how one-sided the flow is."""
    hm = now.hour * 60 + now.minute
    letter = period_of(hm)
    if not letter:
        return {}
    start = 555 + 30 * LETTERS.index(letter)
    length = 15 if letter == "M" else 30
    elapsed = max(1, min(length, hm - start + 1))
    cur = [b for b in bars if start <= b[0] < start + length]
    kind, key = _kind(symbol)
    out = {"period": letter, "starts": f"{start // 60:02d}:{start % 60:02d}", "minutes_elapsed": elapsed}
    rng = BASELINES["index_range"].get(letter, {})
    if cur and atr:
        r = (max(b[2] for b in cur) - min(b[3] for b in cur)) / atr
        out.update(range_atr=round(r, 3), range_usual=rng.get("range_med"), range_p90=rng.get("range_p90"),
                   range_abnormal=bool(rng.get("range_p90")) and r >= rng["range_p90"])
    out.update(day_high_share=rng.get("day_high"), day_low_share=rng.get("day_low"))
    vol = sum(b[5] for b in cur)
    if kind == "futures" and key in BASELINES["futures"]:
        base = BASELINES["futures"][key].get(letter) or {}
        share_med, share_p90 = base.get("vol_share_med"), base.get("vol_share_p90")
        day_volume = day_volume or BASELINES["futures"][key].get("median_day_volume")
        p90_delta = base.get("abs_delta_share_p90")
    elif kind == "stock":
        share_med = (BASELINES["stocks"].get(key) or BASELINES["stocks_all"]).get(letter)
        share_p90, p90_delta = None, None
    else:
        share_med = share_p90 = p90_delta = None
    if vol > 0 and share_med and day_volume:
        expected = share_med * day_volume * elapsed / length
        out["rvol"] = round(vol / expected, 2) if expected else None
        if share_p90:
            out["rvol_p90"] = round(share_p90 / share_med, 2)
            out["volume_abnormal"] = out["rvol"] is not None and out["rvol"] >= out["rvol_p90"]
        else:
            out["volume_abnormal"] = out["rvol"] is not None and out["rvol"] >= 2.0
    if flow_minutes:
        tsd = now.date()
        lo_ts = datetime.combine(tsd, time(start // 60, start % 60), IST).timestamp()
        rows = [m for m in flow_minutes if lo_ts <= m[0] < lo_ts + length * 60]
        v = sum(m[2] for m in rows)
        if v > 0:
            share = (sum(m[3] for m in rows) - sum(m[4] for m in rows)) / v
            out.update(delta_share=round(share, 3), delta_p90=p90_delta,
                       delta_abnormal=bool(p90_delta) and abs(share) >= p90_delta)
    return out


def annotate(db_path: str, symbol: str, now: datetime | None = None, flow_minutes: list | None = None,
             cache: dict | None = None) -> dict:
    """All annotations for one symbol now. ``cache`` (a dict the caller keeps) holds the daily parts."""
    now = (now or datetime.now(IST)).astimezone(IST)
    cache = cache if cache is not None else {}
    key = (symbol, now.date().isoformat())
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10) as db:
            if key not in cache:
                prev_day = feat.previous_session(now.date())
                daily = feat.daily_ohlc(db, symbol, now.date())
                trs = [max(daily[i][2] - daily[i][3], abs(daily[i][2] - daily[i - 1][4]), abs(daily[i][3] - daily[i - 1][4]))
                       for i in range(1, len(daily))]
                cache[key] = {"prior": tpo(_bars(db, symbol, prev_day)),
                              "atr": sum(trs[-14:]) / len(trs[-14:]) if len(trs) >= 5 else None,
                              "day_volume": _day_volume_baseline(db, symbol, now.date())}
            daily = cache[key]
            bars = _bars(db, symbol, now.date(), int(now.timestamp()) + 1)
    except sqlite3.Error as exc:
        return {"symbol": symbol, "error": str(exc)[:160]}
    today = tpo(bars)
    prior = daily["prior"]
    out = {"symbol": symbol, "at": now.strftime("%H:%M"), "atr": round(daily["atr"], 2) if daily["atr"] else None,
           "today": today, "prior_value": {k: prior[k] for k in ("vah", "val", "poc", "high", "low")} if prior else None,
           "eighty_rule": eighty_rule(bars, prior),
           "period": period_reading(bars, flow_minutes, symbol, daily["atr"], daily["day_volume"], now)}
    notes = []
    p = out["period"]
    if p:
        bits = [f"period {p['period']} ({p['starts']})"]
        if p.get("rvol") is not None:
            bits.append(f"volume {p['rvol']:.1f}x usual for this half hour" + (" -- ABNORMAL" if p.get("volume_abnormal") else ""))
        if p.get("range_atr") is not None:
            bits.append(f"range {p['range_atr']:.2f} ATR vs usual {p['range_usual']:.2f} (p90 {p['range_p90']:.2f})"
                        + (" -- ABNORMAL" if p.get("range_abnormal") else ""))
        if p.get("delta_share") is not None:
            bits.append(f"flow {p['delta_share']:+.0%} one-sided" + (f" vs p90 {p['delta_p90']:.0%} -- ABNORMAL" if p.get("delta_abnormal") else ""))
        if p.get("day_high_share") is not None:
            bits.append(f"the day's high/low is made in this period on {p['day_high_share']:.0%}/{p['day_low_share']:.0%} of days")
        notes.append("; ".join(bits))
    if today:
        if today["poor_high"]:
            notes.append(f"poor high at {today['high']:g} so far (2+ periods at the top): revisited next session 67%, within 3 sessions 80% (with a tail 49%)")
        if today["poor_low"]:
            notes.append(f"poor low at {today['low']:g} so far: revisited next session 57% (with a tail 42%)")
        for side in ("high", "low"):
            n = today[f"tail_{side}_rows"]
            if n:
                odds = next(o for rows, o in TAIL_HOLD[side] if n >= rows)
                notes.append(f"{'selling' if side == 'high' else 'buying'} tail of {n} rows at the {side}: the {side} held 3 sessions "
                             f"{odds:.0%} of the time (no tail {NO_TAIL_HOLD[side]:.0%})")
        if today["single_print_zones"]:
            z = ", ".join(f"{a:g}-{b:g}" for a, b in today["single_print_zones"][:4])
            notes.append(f"single-print zones {z}: filled next session 42%, within 5 sessions 66%")
    if out["eighty_rule"].get("state") not in (None, "unknown"):
        notes.append(f"80% rule: {out['eighty_rule']['state']}")
    out["notes"] = notes
    return out
