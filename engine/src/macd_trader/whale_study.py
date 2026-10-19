"""Historical edge study: minute-level whale activity and what followed it.

Print-level Layer A needs raw ticks, and those exist for a handful of recent
sessions only. Durable 1-minute bars go back months, so this study asks the
same question one resolution coarser: when an instrument trades an extreme
minute -- extreme against ITS OWN baseline -- in one direction, does price
keep going that way?

Baselines (per instrument, trailing ``BASELINE_SESSIONS``, no lookahead):
the median volume of each 15-minute slot of the day (volume is U-shaped, so
09:15 is judged against 09:15), the p99.5 minute volume, median daily
turnover, median 1-minute range and the typical 30-minute move.

Event: minute volume >= SPIKE_MULTIPLE x its slot median and >= the p99.5,
rupee turnover over a segment floor, and a directional bar (side = sign of
close - open). One event per instrument per COOLDOWN_MINUTES.

Outcome: the side-adjusted return 15/30/60 minutes on (stocks net of NIFTY),
a hit when the 30-minute one is >= HIT_FRACTION of the typical move -- the
same label the live calibration uses.

Edge: feature weights are fitted on the earliest ``train_fraction`` of the
sessions only and applied to the later ones. The report says how the top
quintile of the held-out events did against their own base rate, with a
binomial z-score, per feature bin and per segment, and stores the baselines
it ended with for the live detector to read.
"""
from __future__ import annotations

import json
import math
import sqlite3
from bisect import insort
from collections import defaultdict, deque
from datetime import UTC, date, datetime
from statistics import median
from zoneinfo import ZoneInfo

from . import whale
from .sessions import in_session
from .whale_tuning import HIT_FRACTION, MARKET_BENCHMARK, fit_weights, probability

IST = ZoneInfo("Asia/Kolkata")
BASELINE_SESSIONS = 10
MIN_BASELINE_SESSIONS = 5
SPIKE_MULTIPLE = 8.0
SPIKE_QUANTILE = 0.995
COOLDOWN_MINUTES = 15
# Every Nth ordinary directional minute, labelled the same way: without it a
# spike's hit rate has nothing to be compared with.
CONTROL_EVERY = 45
NOTIONAL_FLOOR = {"cash": 2_500_000, "option": 500_000, "future": 2_500_000}

SCHEMA = """
CREATE TABLE IF NOT EXISTS whale_studies (
  study_id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, since TEXT, until TEXT,
  sessions INTEGER, symbols INTEGER, events INTEGER, train_until TEXT, report TEXT, weights TEXT, base_rate REAL);
CREATE TABLE IF NOT EXISTS whale_minute_baselines (
  symbol TEXT NOT NULL, as_of TEXT NOT NULL, sessions INTEGER, slot_median TEXT, p995 REAL,
  turnover REAL, range_median REAL, typical_move_pct REAL, PRIMARY KEY (symbol, as_of));
"""


def _slot(moment: datetime) -> int:
    return ((moment.hour * 60 + moment.minute) - (9 * 60 + 15)) // 15


def _bin(value, edges, labels):
    if value is None:
        return "unknown"
    for edge, label in zip(edges, labels):
        if value < edge:
            return label
    return labels[-1]


def _sessions(history: sqlite3.Connection, symbol: str, since: str, until: str) -> dict[str, list[tuple]]:
    """day -> [(ts, open, high, low, close, volume)] within the instrument's own session."""
    start = int(datetime.fromisoformat(since).replace(tzinfo=IST).timestamp())
    end = int(datetime.fromisoformat(until).replace(tzinfo=IST).timestamp()) + 86_400
    out: dict[str, list[tuple]] = defaultdict(list)
    for ts, o, h, l, c, v in history.execute(
            """SELECT timestamp, open, high, low, close, volume FROM historical_candles
               WHERE symbol = ? AND timeframe_seconds = 60 AND timestamp >= ? AND timestamp < ? ORDER BY timestamp""",
            (symbol, start, end)):
        moment = datetime.fromtimestamp(ts, UTC)
        if c and in_session(symbol, moment):
            out[moment.astimezone(IST).date().isoformat()].append((ts, o, h, l, c, v or 0))
    return out


def _thirty_minute_moves(bars: list[tuple]) -> list[float]:
    if not bars:
        return []
    marks: dict[int, float] = {}
    for ts, _o, _h, _l, close, _v in bars:
        marks.setdefault((ts - bars[0][0]) // 1800, close)
    closes = [marks[k] for k in sorted(marks)]
    return [abs(b / a - 1) * 100 for a, b in zip(closes, closes[1:]) if a]


def _baseline(window: deque) -> dict | None:
    if len(window) < MIN_BASELINE_SESSIONS:
        return None
    slots: dict[int, list[float]] = defaultdict(list)
    volumes: list[float] = []
    ranges: list[float] = []
    moves: list[float] = []
    turnovers: list[float] = []
    for bars in window:
        turnover = 0.0
        for ts, _o, high, low, close, volume in bars:
            slots[_slot(datetime.fromtimestamp(ts, IST))].append(volume)
            volumes.append(volume)
            if close and high is not None and low is not None:
                ranges.append((high - low) / close * 100)
            turnover += volume * close
        turnovers.append(turnover)
        moves.extend(_thirty_minute_moves(bars))
    volumes.sort()
    return {"sessions": len(window), "slot_median": {k: median(v) for k, v in slots.items()},
            "p995": volumes[min(len(volumes) - 1, int(SPIKE_QUANTILE * len(volumes)))],
            "turnover": median(turnovers), "range_median": median(ranges) if ranges else None,
            "typical_move_pct": median(moves) if moves else None}


def _forward(bars: list[tuple], index: int) -> dict[int, float | None]:
    ts0, close0 = bars[index][0], bars[index][4]
    out: dict[int, float | None] = {}
    for minutes in (15, 30, 60):
        close = next((c for ts, _o, _h, _l, c, _v in bars[index:] if ts >= ts0 + minutes * 60), None)
        out[minutes] = (close / close0 - 1) * 100 if close and close0 else None
    return out


def _benchmark_forward(bench: dict[int, float], ts0: int) -> dict[int, float]:
    base = bench.get(ts0)
    out = {}
    for minutes in (15, 30, 60):
        later = bench.get(ts0 + minutes * 60)
        out[minutes] = (later / base - 1) * 100 if base and later else 0.0
    return out


def events_for(history: sqlite3.Connection, symbol: str, since: str, until: str,
               bench: dict[int, float], controls: list | None = None) -> tuple[list[dict], dict | None]:
    """Every spike event of one instrument, labelled; and its final baseline.

    controls collects ordinary directional minutes (every CONTROL_EVERY-th,
    not spikes) labelled identically -- the comparison a spike's hit rate needs.
    """
    kind = whale.instrument_kind(symbol)
    if kind == "index":
        return [], None
    sessions = _sessions(history, symbol, since, until)
    window: deque = deque(maxlen=BASELINE_SESSIONS)
    events: list[dict] = []
    for day in sorted(sessions):
        bars = sessions[day]
        base = _baseline(window)
        window.append(bars)
        if base is None or not base["typical_move_pct"]:
            continue
        cooldown_until = 0
        same_side: dict[int, int] = defaultdict(int)
        for index, (ts, o, high, low, close, volume) in enumerate(bars):
            if ts < cooldown_until or not volume or not close:
                continue
            slot_median = base["slot_median"].get(_slot(datetime.fromtimestamp(ts, IST)))
            if not slot_median or volume < SPIKE_MULTIPLE * slot_median or volume < base["p995"]:
                if controls is not None and index % CONTROL_EVERY == 0 and close != o:
                    side = 1 if close > o else -1
                    forward = _forward(bars, index)
                    if forward[30] is not None:
                        market = _benchmark_forward(bench, ts) if kind == "cash" else {30: 0.0}
                        ret30 = side * (forward[30] - market[30])
                        controls.append({"segment": kind, "day": day, "ret30": ret30, "ret60": None,
                                         "hit": 1 if ret30 >= HIT_FRACTION * base["typical_move_pct"] else 0})
                continue
            notional = volume * close
            if notional < NOTIONAL_FLOOR.get(kind, 0):
                continue
            side = 1 if close > o else -1 if close < o else 0
            if side == 0:
                continue
            cooldown_until = ts + COOLDOWN_MINUTES * 60
            forward = _forward(bars, index)
            if forward[30] is None:
                continue
            market = _benchmark_forward(bench, ts) if kind == "cash" else {15: 0.0, 30: 0.0, 60: 0.0}
            ret = {m: side * (forward[m] - market[m]) if forward[m] is not None else None for m in forward}
            span = (high - low) if high is not None and low is not None else 0
            clv = side * (((close - low) - (high - close)) / span) if span else 0.0
            moment = datetime.fromtimestamp(ts, IST)
            clock = moment.hour * 60 + moment.minute
            prior = same_side[side]
            same_side[side] += 1
            bins = {
                "segment": kind,
                "side": "buy" if side > 0 else "sell",
                "volume_multiple": _bin(volume / slot_median, [15, 30, 60], ["8-15x", "15-30x", "30-60x", "60x+"]),
                "turnover_share": _bin(100 * notional / base["turnover"] if base["turnover"] else None,
                                       [0.5, 1.5, 4], ["<0.5%", "0.5-1.5%", "1.5-4%", "4%+"]),
                "close_location": _bin(clv, [0.0, 0.5], ["against", "middle", "with"]),
                "range_vs_typical": _bin((span / close * 100) / base["range_median"] if base["range_median"] else None,
                                         [2, 5], ["<2x", "2-5x", "5x+"]),
                "time": "open" if clock < 600 else "close" if clock >= 870 else "mid",
                "repeat": "0" if prior == 0 else "1-2" if prior <= 2 else "3+",
            }
            events.append({"symbol": symbol, "day": day, "ts": ts, "side": side, "bins": bins,
                           "ret15": ret[15], "ret30": ret[30], "ret60": ret[60],
                           "typical_move": base["typical_move_pct"],
                           "hit": 1 if ret[30] >= HIT_FRACTION * base["typical_move_pct"] else 0})
    return events, _baseline(window)


def _summary(rows: list[dict], base_rate: float | None = None) -> dict:
    if not rows:
        return {"n": 0}
    hits = sum(r["hit"] for r in rows)
    rate = hits / len(rows)
    out = {"n": len(rows), "hit_rate": round(rate, 4),
           "avg_ret30_pct": round(sum(r["ret30"] for r in rows) / len(rows), 4),
           "avg_ret60_pct": round(sum(r["ret60"] for r in rows if r["ret60"] is not None)
                                  / max(1, sum(1 for r in rows if r["ret60"] is not None)), 4)}
    if base_rate:
        out["lift"] = round(rate / base_rate, 3)
        out["z"] = (round((rate - base_rate) / math.sqrt(base_rate * (1 - base_rate) / len(rows)), 2)
                    if 0 < base_rate < 1 else None)
    return out


def run_study(history: sqlite3.Connection, since: str, until: str, *, train_fraction: float = 0.6,
              symbols: list[str] | None = None, min_sessions: int = 12) -> dict:
    """Walk-forward edge study over [since, until]; stored with its baselines."""
    history.executescript(SCHEMA)
    start = int(datetime.fromisoformat(since).replace(tzinfo=IST).timestamp())
    end = int(datetime.fromisoformat(until).replace(tzinfo=IST).timestamp()) + 86_400
    if symbols is None:
        symbols = [row[0] for row in history.execute(
            """SELECT symbol FROM historical_candles WHERE timeframe_seconds = 60 AND timestamp >= ? AND timestamp < ?
               AND symbol NOT LIKE '%-INDEX' GROUP BY symbol
               HAVING COUNT(DISTINCT date(timestamp, 'unixepoch', '+5 hours', '+30 minutes')) >= ?""",
            (start, end, min_sessions))]
    bench = {ts: close for ts, close in history.execute(
        """SELECT timestamp, close FROM historical_candles WHERE symbol = ? AND timeframe_seconds = 60
           AND timestamp >= ? AND timestamp < ?""", (MARKET_BENCHMARK, start, end)) if close}
    events: list[dict] = []
    controls: list[dict] = []
    baselines = []
    for symbol in symbols:
        found, base = events_for(history, symbol, since, until, bench, controls)
        events.extend(found)
        if base:
            baselines.append((symbol, until, base["sessions"], json.dumps(base["slot_median"]), base["p995"],
                              base["turnover"], base["range_median"], base["typical_move_pct"]))
    with history:
        history.executemany("INSERT OR REPLACE INTO whale_minute_baselines VALUES (?,?,?,?,?,?,?,?)", baselines)
    days = sorted({e["day"] for e in events})
    report: dict = {"since": since, "until": until, "symbols": len(symbols), "baselines": len(baselines),
                    "events": len(events), "sessions": len(days),
                    "method": {"baseline_sessions": BASELINE_SESSIONS, "spike_multiple": SPIKE_MULTIPLE,
                               "spike_quantile": SPIKE_QUANTILE, "cooldown_minutes": COOLDOWN_MINUTES,
                               "notional_floor": NOTIONAL_FLOOR, "hit": f"side-adjusted 30m return >= {HIT_FRACTION} x the "
                               "instrument's typical 30m move (stocks net of NIFTY)", "train_fraction": train_fraction}}
    if len(days) < 4 or not events:
        report["verdict"] = "not enough sessions with a baseline to study"
        weights, base_rate, train_until = {}, None, None
    else:
        cut = days[max(1, int(len(days) * train_fraction)) - 1]
        train = [e for e in events if e["day"] <= cut]
        test = [e for e in events if e["day"] > cut]
        train_until = cut
        weights, base_rate, fitted = fit_weights([(e["bins"], e["hit"], e["ret30"]) for e in train])
        for e in test:
            e["p"] = probability(e["bins"], weights, base_rate)
        test_base = sum(e["hit"] for e in test) / len(test) if test else None
        ranked = sorted(test, key=lambda e: e["p"] or 0, reverse=True)
        fifth = max(1, len(ranked) // 5)
        quintiles = [_summary(ranked[i * fifth:(i + 1) * fifth] if i < 4 else ranked[4 * fifth:], test_base)
                     for i in range(5)] if len(ranked) >= 10 else []
        report.update({
            "train": {"from": days[0], "to": cut, **_summary(train)},
            "test": {"from": next((d for d in days if d > cut), None), "to": days[-1], **_summary(test)},
            "test_quintiles": quintiles,
            "features_train": fitted["features"],
            "features_test": {feature: {value: _summary([e for e in test if e["bins"].get(feature) == value], test_base)
                                        for value in sorted({e["bins"].get(feature) for e in test})}
                              for feature in (fitted["features"] or {})},
            "by_segment_test": {kind: _summary([e for e in test if e["bins"]["segment"] == kind], test_base)
                                for kind in ("cash", "future", "option")},
            "follow_vs_fade_test": {"follow_avg_ret30_pct": _summary(test).get("avg_ret30_pct")},
            # The same test on ordinary directional minutes of the same instruments
            # and sessions: what a spike's base rate should be compared with.
            "control_test": _summary([c for c in controls if c["day"] > cut]),
            "control_by_segment_test": {kind: _summary([c for c in controls if c["day"] > cut and c["segment"] == kind])
                                         for kind in ("cash", "future", "option")},
            "spike_vs_control_test": _summary(test, (sum(c["hit"] for c in controls if c["day"] > cut)
                                                      / max(1, sum(1 for c in controls if c["day"] > cut))) or None),
        })
        top = quintiles[0] if quintiles else {}
        report["verdict"] = (
            f"held-out top quintile hit {top.get('hit_rate', 0) * 100:.1f}% vs {test_base * 100:.1f}% base "
            f"(lift {top.get('lift')}, z {top.get('z')}) on {len(test)} events over {len({e['day'] for e in test})} sessions"
            if top else "held-out sample too small to rank")
    with history:
        cursor = history.execute(
            """INSERT INTO whale_studies (created_at, since, until, sessions, symbols, events, train_until, report, weights, base_rate)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (datetime.now(UTC).isoformat(), since, until, len(days), len(symbols), len(events), train_until,
             json.dumps(report), json.dumps(weights), base_rate))
    report["study_id"] = cursor.lastrowid
    return report


def latest(history: sqlite3.Connection, limit: int = 10) -> dict:
    if history.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='whale_studies'").fetchone() is None:
        return {"latest": None, "studies": []}
    rows = history.execute(
        """SELECT study_id, created_at, since, until, sessions, symbols, events, train_until, report
           FROM whale_studies ORDER BY study_id DESC LIMIT ?""", (limit,)).fetchall()
    studies = [{"study_id": r[0], "created_at": r[1], "since": r[2], "until": r[3], "sessions": r[4],
                "symbols": r[5], "events": r[6], "train_until": r[7],
                "verdict": json.loads(r[8] or "{}").get("verdict")} for r in rows]
    return {"latest": ({"study_id": rows[0][0], **json.loads(rows[0][8] or "{}")} if rows else None),
            "studies": studies}
