"""Per-instrument whale thresholds, event features, and a nightly calibration loop.

Layer A judged every contract by one rule set: ten times the rolling median,
10% of the window's volume, a rupee floor per segment. A NIFTY future, a
thinly traded stock option and HDFCBANK are not the same market, and one rule
flooded the list with a liquid stock's routine clusters while an illiquid
contract's genuinely unusual print could never clear the floor.

This module makes each instrument its own reference and lets outcomes steer:

1. **Fingerprint** -- per symbol per session: print-size percentiles (p50,
   p90, p99, p99.9), prints, volume and rupee turnover. The baseline is the
   median over the last ``BASELINE_SESSIONS`` sessions.
2. **Parameters** -- fitted per symbol from its baseline: a large print must
   beat its OWN p99.9 cluster and a share of its OWN daily turnover; a child
   clip must stand out from its own p90. A per-symbol ``sensitivity`` then
   steers the rate toward ``TARGET_EVENTS_PER_DAY``. Few sessions shrink the
   fit toward the defaults. A manual override always wins.
3. **Features** -- recorded for every event at detection time (no lookahead):
   pattern, segment, size vs its own p99.9, share of window volume, share of
   daily turnover, time of day, side, and same-side repetition.
4. **Outcomes** -- each sided event is labelled once its hour has passed: did
   price move the whale's way by at least ``HIT_FRACTION`` of the symbol's own
   typical 30-minute move? (A 0.3% move is noise on one stock and a trend on
   another.)
5. **Calibration** -- nightly, per feature bin, the hit rate against the base
   rate becomes a log-odds weight, shrunk toward zero while the bin has few
   events. An event's calibrated probability is the base odds times the
   product of its bins' odds ratios. Every run is kept, with its metrics.

The loop -- fingerprint, label, calibrate, refit -- runs after the close each
night and on demand, so thresholds and scores keep learning from what
actually followed.
"""
from __future__ import annotations

import json
import math
import sqlite3
from datetime import UTC, date, datetime, timedelta
from statistics import median
from zoneinfo import ZoneInfo

from . import whale

IST = ZoneInfo("Asia/Kolkata")

BASELINE_SESSIONS = 20
# A fit from fewer sessions is shrunk toward the defaults: weight n / (n + SHRINK).
SHRINK_SESSIONS = 3
LARGE_OVER_P999 = 1.0            # a large print is at least the instrument's own p99.9 cluster
LARGE_TURNOVER_SHARE = 0.001     # ...and at least 0.1% of its median daily turnover
CHILD_OVER_P90 = 2.0             # an iceberg clip is at least twice its p90 print
CHILD_TURNOVER_SHARE = 0.0001    # ...and at least 0.01% of daily turnover
MIN_FLOOR = {"cash": (1_000_000, 100_000), "option": (200_000, 25_000), "future": (1_000_000, 100_000)}
TARGET_EVENTS_PER_DAY = (1.0, 8.0)
SENSITIVITY_BOUNDS = (0.5, 4.0)
HIT_FRACTION = 0.5
# A stock's outcome is its move net of the market's over the same window: on a
# rising day every buy "worked", and the first calibration (29 Sep 2026) gave
# the side feature the largest weight for exactly that reason.
MARKET_BENCHMARK = "NSE:NIFTY50-INDEX"
PRIOR_STRENGTH = 20.0
CALIBRATION_LOOKBACK_DAYS = 30
OVERRIDE_KEYS = {"sensitivity", "large_min_qty", "large_floor", "window_share", "large_multiple",
                 "child_min_qty", "child_floor", "child_multiple", "child_max_interval", "disabled"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS whale_fingerprints (
  symbol TEXT NOT NULL, day TEXT NOT NULL, prints INTEGER, volume REAL, notional REAL,
  p50 REAL, p90 REAL, p99 REAL, p999 REAL, max_qty REAL, avg_price REAL,
  PRIMARY KEY (symbol, day));
CREATE TABLE IF NOT EXISTS whale_params (
  symbol TEXT PRIMARY KEY, params TEXT NOT NULL, sensitivity REAL NOT NULL DEFAULT 1.0,
  baseline TEXT, override TEXT, fitted_for TEXT, fitted_at TEXT);
CREATE TABLE IF NOT EXISTS whale_event_features (
  symbol TEXT NOT NULL, day TEXT NOT NULL, ts_ms INTEGER NOT NULL, kind TEXT NOT NULL,
  side INTEGER NOT NULL, features TEXT NOT NULL, bins TEXT NOT NULL,
  ret15 REAL, ret30 REAL, ret60 REAL, typical_move REAL, hit INTEGER, labelled_at TEXT,
  PRIMARY KEY (symbol, day, ts_ms, kind));
CREATE TABLE IF NOT EXISTS whale_calibration (
  run_id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, as_of TEXT NOT NULL,
  lookback_days INTEGER, labelled INTEGER, base_rate REAL, weights TEXT, report TEXT);
"""


def ensure_schema(history: sqlite3.Connection) -> None:
    history.executescript(SCHEMA)


def _has(history: sqlite3.Connection, table: str) -> bool:
    return history.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


# ---------------------------------------------------------------------------
# 1. Fingerprints and baselines
# ---------------------------------------------------------------------------

def _quantile(ordered: list[float], q: float) -> float | None:
    if not ordered:
        return None
    return float(ordered[min(len(ordered) - 1, int(q * len(ordered)))])


def fingerprint(prints: list[tuple[int, float, int, int]]) -> dict | None:
    """One session's print-size distribution and turnover."""
    sizes = sorted(float(qty) for _ts, _price, qty, _side in prints if qty > 0)
    if len(sizes) < 20:
        return None
    volume = sum(sizes)
    notional = sum(qty * price for _ts, price, qty, _side in prints if qty > 0)
    return {"prints": len(sizes), "volume": volume, "notional": round(notional),
            "p50": _quantile(sizes, 0.5), "p90": _quantile(sizes, 0.9), "p99": _quantile(sizes, 0.99),
            "p999": _quantile(sizes, 0.999), "max_qty": sizes[-1],
            "avg_price": round(notional / volume, 4) if volume else None}


def save_fingerprint(history: sqlite3.Connection, symbol: str, day: str, fp: dict) -> None:
    history.execute(
        """INSERT OR REPLACE INTO whale_fingerprints
           (symbol, day, prints, volume, notional, p50, p90, p99, p999, max_qty, avg_price)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (symbol, day, fp["prints"], fp["volume"], fp["notional"], fp["p50"], fp["p90"],
         fp["p99"], fp["p999"], fp["max_qty"], fp["avg_price"]))


def typical_move(history: sqlite3.Connection, symbol: str, as_of: str, sessions: int = BASELINE_SESSIONS) -> float | None:
    """Median absolute 30-minute return (%) over recent sessions, from minute bars."""
    end = datetime.combine(date.fromisoformat(as_of), datetime.min.time(), IST) + timedelta(days=1)
    start = end - timedelta(days=sessions * 2 + 10)
    try:
        rows = history.execute(
            """SELECT timestamp, close FROM historical_candles WHERE symbol = ? AND timeframe_seconds = 60
               AND timestamp >= ? AND timestamp < ? ORDER BY timestamp""",
            (symbol, int(start.timestamp()), int(end.timestamp()))).fetchall()
    except sqlite3.OperationalError:
        return None
    by_day: dict[str, list[tuple[int, float]]] = {}
    for ts, close in rows:
        if close:
            by_day.setdefault(datetime.fromtimestamp(ts, IST).date().isoformat(), []).append((ts, float(close)))
    moves = []
    for points in list(by_day.values())[-sessions:]:
        marks = {}
        for ts, close in points:
            marks.setdefault((ts - points[0][0]) // 1800, close)
        closes = [marks[k] for k in sorted(marks)]
        moves.extend(abs(b / a - 1) * 100 for a, b in zip(closes, closes[1:]) if a)
    return round(median(moves), 4) if moves else None


def baseline(history: sqlite3.Connection, symbol: str, as_of: str, sessions: int = BASELINE_SESSIONS) -> dict:
    """Median fingerprint over the last sessions up to and including ``as_of``."""
    rows = history.execute(
        """SELECT prints, volume, notional, p50, p90, p99, p999, max_qty, avg_price FROM whale_fingerprints
           WHERE symbol = ? AND day <= ? ORDER BY day DESC LIMIT ?""", (symbol, as_of, sessions)).fetchall()
    keys = ("prints", "volume", "notional", "p50", "p90", "p99", "p999", "max_qty", "avg_price")
    out: dict = {"sessions": len(rows)}
    for index, key in enumerate(keys):
        values = [row[index] for row in rows if row[index] is not None]
        out[key] = round(median(values), 4) if values else None
    out["typical_move_pct"] = typical_move(history, symbol, as_of)
    return out


# ---------------------------------------------------------------------------
# 2. Parameters
# ---------------------------------------------------------------------------

def fit_params(symbol: str, day: str, base: dict, lot: int | None = None, sensitivity: float = 1.0,
               override: dict | None = None) -> dict:
    """The instrument's own thresholds from its baseline (see module docstring)."""
    params = whale.default_params(symbol, day, lot)
    kind = whale.instrument_kind(symbol)
    n = base.get("sessions") or 0
    weight = n / (n + SHRINK_SESSIONS)
    if not params["legacy"] and n and base.get("p999"):
        turnover = base.get("notional") or 0.0
        large_min, child_min = MIN_FLOOR.get(kind, (0, 0))
        fitted_large_floor = max(large_min, LARGE_TURNOVER_SHARE * turnover)
        fitted_child_floor = max(child_min, CHILD_TURNOVER_SHARE * turnover)
        params.update(
            large_min_qty=round(weight * sensitivity * LARGE_OVER_P999 * base["p999"]),
            large_floor=round(weight * sensitivity * fitted_large_floor + (1 - weight) * params["large_floor"]),
            child_min_qty=round(weight * sensitivity * CHILD_OVER_P90 * (base.get("p90") or 0)),
            child_floor=round(weight * sensitivity * fitted_child_floor + (1 - weight) * params["child_floor"]),
        )
        params["source"] = "fitted"
    params["sensitivity"] = round(sensitivity, 3)
    params["baseline_sessions"] = n
    if override:
        params.update({key: value for key, value in override.items() if key in OVERRIDE_KEYS})
        params["source"] = "override" if params["source"] == "default" else params["source"] + "+override"
    return params


def next_sensitivity(history: sqlite3.Connection, symbol: str, as_of: str, current: float) -> float:
    """Nudge toward TARGET_EVENTS_PER_DAY from the last five sessions' event rate."""
    rows = history.execute(
        """SELECT day, COUNT(*) FROM whale_events WHERE symbol = ? AND layer = 'A' AND day <= ?
           GROUP BY day ORDER BY day DESC LIMIT 5""", (symbol, as_of)).fetchall()
    if not rows:
        return current
    rate = sum(count for _day, count in rows) / len(rows)
    low, high = TARGET_EVENTS_PER_DAY
    if rate > high:
        current *= 1.25
    elif rate < low:
        current *= 0.9
    return round(min(SENSITIVITY_BOUNDS[1], max(SENSITIVITY_BOUNDS[0], current)), 3)


def load_params(history: sqlite3.Connection) -> dict[str, dict]:
    """symbol -> tuned params for detection (empty before the first fit)."""
    if not _has(history, "whale_params"):
        return {}
    out = {}
    for symbol, params in history.execute("SELECT symbol, params FROM whale_params"):
        try:
            out[symbol] = json.loads(params)
        except ValueError:
            continue
    return out


def refit(history: sqlite3.Connection, day: str, lots: dict[str, int] | None = None,
          symbols: list[str] | None = None) -> int:
    """Refit every fingerprinted symbol's params for the next session."""
    ensure_schema(history)
    names = symbols or [row[0] for row in history.execute("SELECT DISTINCT symbol FROM whale_fingerprints")]
    current = {row[0]: (row[1], row[2]) for row in history.execute(
        "SELECT symbol, sensitivity, override FROM whale_params")}
    stamp = datetime.now(UTC).isoformat()
    with history:
        for symbol in names:
            sensitivity, override_text = current.get(symbol, (1.0, None))
            override = json.loads(override_text) if override_text else None
            sensitivity = float((override or {}).get("sensitivity") or next_sensitivity(history, symbol, day, sensitivity or 1.0))
            base = baseline(history, symbol, day)
            params = fit_params(symbol, day, base, (lots or {}).get(symbol), sensitivity, override)
            history.execute(
                """INSERT INTO whale_params (symbol, params, sensitivity, baseline, override, fitted_for, fitted_at)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(symbol) DO UPDATE SET params=excluded.params, sensitivity=excluded.sensitivity,
                     baseline=excluded.baseline, fitted_for=excluded.fitted_for, fitted_at=excluded.fitted_at""",
                (symbol, json.dumps(params), sensitivity, json.dumps(base), override_text, day, stamp))
    return len(names)


def set_override(history: sqlite3.Connection, symbol: str, override: dict | None, day: str,
                 lots: dict[str, int] | None = None) -> dict:
    """Pin some of a symbol's thresholds (None clears); refits it at once."""
    ensure_schema(history)
    clean = {key: value for key, value in (override or {}).items() if key in OVERRIDE_KEYS} or None
    with history:
        history.execute(
            """INSERT INTO whale_params (symbol, params, sensitivity, override) VALUES (?, '{}', 1.0, ?)
               ON CONFLICT(symbol) DO UPDATE SET override=excluded.override""",
            (symbol, json.dumps(clean) if clean else None))
    refit(history, day, lots, [symbol])
    return symbol_view(history, symbol, day)


# ---------------------------------------------------------------------------
# 3. Features
# ---------------------------------------------------------------------------

def _bin(value: float | None, edges: list[float], labels: list[str]) -> str:
    if value is None:
        return "unknown"
    for edge, label in zip(edges, labels):
        if value < edge:
            return label
    return labels[-1]


def features(event: whale.WhaleEvent, base: dict, prior_same_side: int) -> tuple[dict, dict]:
    """(raw features, their bins) for one event, from what was known at the time."""
    f = dict(event.features or {})
    p999 = base.get("p999")
    turnover = base.get("notional")
    notional = f.get("notional") or event.quantity * event.price
    raw = {
        "size_vs_p999": round(event.quantity / p999, 3) if p999 else None,
        "window_share": f.get("window_share"),
        "turnover_share_pct": round(100 * notional / turnover, 4) if turnover else None,
        "notional": notional, "median": f.get("median"), "prior_same_side": prior_same_side,
        "minute": datetime.fromtimestamp(event.ts_ms / 1000, IST).strftime("%H:%M"),
    }
    minute = datetime.fromtimestamp(event.ts_ms / 1000, IST)
    clock = minute.hour * 60 + minute.minute
    bins = {
        "kind": event.kind,
        "segment": whale.instrument_kind(event.symbol),
        "side": "buy" if event.side > 0 else "sell" if event.side < 0 else "unknown",
        "size_vs_p999": _bin(raw["size_vs_p999"], [1, 2, 5], ["<1x", "1-2x", "2-5x", "5x+"]),
        "window_share": _bin(raw["window_share"], [0.05, 0.15, 0.40], ["<5%", "5-15%", "15-40%", "40%+"]),
        "turnover_share": _bin(raw["turnover_share_pct"], [0.05, 0.2, 1.0], ["<0.05%", "0.05-0.2%", "0.2-1%", "1%+"]),
        "time": "open" if clock < 600 else "close" if clock >= 870 else "mid",
        "repeat": "0" if prior_same_side == 0 else "1-2" if prior_same_side <= 2 else "3+",
    }
    return raw, bins


def save_features(history: sqlite3.Connection, day: str, events: list[whale.WhaleEvent]) -> int:
    """Record each event's features (existing rows, and their labels, are kept)."""
    if not events:
        return 0
    ensure_schema(history)
    bases: dict[str, dict] = {}
    recent: dict[tuple[str, int], list[int]] = {}
    rows = []
    for event in sorted(events, key=lambda e: e.ts_ms):
        if event.symbol not in bases:
            bases[event.symbol] = baseline(history, event.symbol, day)
        stamps = recent.setdefault((event.symbol, event.side), [])
        prior = sum(1 for ts in stamps if event.ts_ms - ts <= 30 * 60 * 1000)
        stamps.append(event.ts_ms)
        raw, bins = features(event, bases[event.symbol], prior if event.side else 0)
        rows.append((event.symbol, day, event.ts_ms, event.kind, event.side, json.dumps(raw), json.dumps(bins)))
    with history:
        history.executemany(
            """INSERT OR IGNORE INTO whale_event_features (symbol, day, ts_ms, kind, side, features, bins)
               VALUES (?,?,?,?,?,?,?)""", rows)
    return len(rows)


# ---------------------------------------------------------------------------
# 4. Outcomes
# ---------------------------------------------------------------------------

def label_outcomes(history: sqlite3.Connection, since: str) -> int:
    """Label every unlabelled sided event since ``since`` whose next hour has traded."""
    ensure_schema(history)
    pending = history.execute(
        """SELECT symbol, day, ts_ms, kind, side FROM whale_event_features
           WHERE hit IS NULL AND side != 0 AND day >= ?""", (since,)).fetchall()
    moves: dict[tuple[str, str], float | None] = {}
    labelled = 0
    stamp = datetime.now(UTC).isoformat()
    updates = []
    def path(symbol: str, start: int) -> list[tuple]:
        return history.execute(
            """SELECT timestamp, close FROM historical_candles WHERE symbol = ? AND timeframe_seconds = 60
               AND timestamp >= ? AND timestamp <= ? ORDER BY timestamp""",
            (symbol, start, start + 3 * 3600)).fetchall()

    def returns(bars: list[tuple], start: int) -> dict[int, float | None]:
        entry = float(bars[0][1])
        out = {}
        for minutes in (15, 30, 60):
            close = next((c for ts, c in bars if ts >= start + minutes * 60 and c), None)
            out[minutes] = (float(close) / entry - 1) * 100 if close else None
        return out

    for symbol, day, ts_ms, kind, side in pending:
        start = ts_ms // 1000 // 60 * 60
        bars = path(symbol, start)
        if not bars or bars[-1][0] < start + 3600 or not bars[0][1]:
            continue                         # the hour after the event has not traded yet
        raw = returns(bars, start)
        market = {15: 0.0, 30: 0.0, 60: 0.0}
        if whale.instrument_kind(symbol) == "cash":
            bench = path(MARKET_BENCHMARK, start)
            if bench and bench[0][1]:
                market = {k: (v or 0.0) for k, v in returns(bench, start).items()}
        forward = {m: round(side * (raw[m] - market[m]), 4) if raw[m] is not None else None for m in raw}
        key = (symbol, day)
        if key not in moves:
            moves[key] = typical_move(history, symbol, day)
        typical = moves[key]
        hit = None
        if forward[30] is not None and typical:
            hit = 1 if forward[30] >= HIT_FRACTION * typical else 0
        updates.append((forward[15], forward[30], forward[60], typical, hit, stamp, symbol, day, ts_ms, kind))
        labelled += 1
    with history:
        history.executemany(
            """UPDATE whale_event_features SET ret15=?, ret30=?, ret60=?, typical_move=?, hit=?, labelled_at=?
               WHERE symbol=? AND day=? AND ts_ms=? AND kind=?""", updates)
    return labelled


# ---------------------------------------------------------------------------
# 5. Calibration and scoring
# ---------------------------------------------------------------------------

def fit_weights(labelled: list[tuple[dict, int, float | None]]) -> tuple[dict, float | None, dict]:
    """(weights, base_rate, report) from (bins, hit, ret30) outcomes.

    Per feature bin, the log-odds of a hit against the base rate, with a Beta
    prior of PRIOR_STRENGTH pseudo-events at the base rate -- a bin seen twice
    moves its weight a little, a bin seen hundreds of times says what it saw.
    Shared by the nightly calibration and the historical edge study.
    """
    report: dict = {"labelled": len(labelled), "features": {}}
    if not labelled:
        return {}, None, report
    base_rate = sum(hit for _b, hit, _r in labelled) / len(labelled)
    base_rate = min(0.95, max(0.05, base_rate))
    alpha, beta = PRIOR_STRENGTH * base_rate, PRIOR_STRENGTH * (1 - base_rate)
    base_log_odds = math.log(base_rate / (1 - base_rate))
    features = sorted({feature for bins, _h, _r in labelled for feature in bins})
    weights: dict = {}
    for feature in features:
        groups: dict[str, list] = {}
        for bins, hit, ret in labelled:
            groups.setdefault(bins.get(feature, "unknown"), []).append((hit, ret))
        table = {}
        for value, outcomes in groups.items():
            n = len(outcomes)
            hits = sum(h for h, _r in outcomes)
            rets = [r for _h, r in outcomes if r is not None]
            table[value] = {"n": n, "hits": hits, "hit_rate": round(hits / n, 3),
                            "weight": round(math.log((hits + alpha) / (n - hits + beta)) - base_log_odds, 4),
                            "avg_ret30_pct": round(sum(rets) / len(rets), 3) if rets else None}
        weights[feature] = {value: row["weight"] for value, row in table.items()}
        report["features"][feature] = table
    scored = sorted(((probability(bins, weights, base_rate), hit) for bins, hit, _r in labelled),
                    key=lambda pair: pair[0], reverse=True)
    top = scored[: max(1, len(scored) // 5)]
    report["top_quintile_hit_rate"] = round(sum(h for _p, h in top) / len(top), 3)
    report["mean_brier"] = round(sum((p - h) ** 2 for p, h in scored) / len(scored), 4)
    return weights, base_rate, report


def calibrate(history: sqlite3.Connection, as_of: str, lookback_days: int = CALIBRATION_LOOKBACK_DAYS) -> dict:
    """Feature-bin log-odds weights from labelled outcomes; stored as a new run."""
    ensure_schema(history)
    since = (date.fromisoformat(as_of) - timedelta(days=lookback_days)).isoformat()
    rows = history.execute(
        """SELECT bins, hit, ret30 FROM whale_event_features WHERE hit IS NOT NULL AND day >= ? AND day <= ?""",
        (since, as_of)).fetchall()
    labelled = [(json.loads(bins), hit, ret) for bins, hit, ret in rows]
    weights, base_rate, report = fit_weights(labelled)
    report["base_rate"] = round(base_rate, 4) if base_rate is not None else None
    with history:
        cursor = history.execute(
            """INSERT INTO whale_calibration (created_at, as_of, lookback_days, labelled, base_rate, weights, report)
               VALUES (?,?,?,?,?,?,?)""",
            (datetime.now(UTC).isoformat(), as_of, lookback_days, len(labelled), base_rate,
             json.dumps(weights), json.dumps(report)))
    report["run_id"] = cursor.lastrowid
    return report


def latest_calibration(history: sqlite3.Connection) -> tuple[dict, float | None, int | None]:
    """(weights, base_rate, run_id) of the newest run with labelled events."""
    if not _has(history, "whale_calibration"):
        return {}, None, None
    row = history.execute(
        """SELECT weights, base_rate, run_id FROM whale_calibration WHERE labelled > 0
           ORDER BY run_id DESC LIMIT 1""").fetchone()
    if not row:
        return {}, None, None
    return json.loads(row[0] or "{}"), row[1], row[2]


def probability(bins: dict, weights: dict, base_rate: float | None) -> float | None:
    """Calibrated P(move the whale's way) for one event's feature bins."""
    if base_rate is None or not weights:
        return None
    log_odds = math.log(base_rate / (1 - base_rate))
    for feature, value in bins.items():
        log_odds += weights.get(feature, {}).get(value, 0.0)
    return round(1 / (1 + math.exp(-log_odds)), 4)


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------

def run(ticks: sqlite3.Connection, history: sqlite3.Connection, day: str, *,
        lots: dict[str, int] | None = None, whale_symbols: list[str] | None = None) -> dict:
    """Scan the day with tuned params, fingerprint it, label, calibrate, refit.

    Replaces the day's Layer A events with the full-day pass. Shared by the
    nightly job and the on-demand route.
    """
    ensure_schema(history)
    whale.ensure_schema(history)
    params = load_params(history)
    gaps = whale.feed_gaps(ticks, day)
    universe = sorted({*whale.traded_symbols(ticks, day), *(whale_symbols or [])})
    fingerprints = 0

    def on_prints(symbol: str, prints: list) -> None:
        nonlocal fingerprints
        fp = fingerprint(prints)
        if fp:
            save_fingerprint(history, symbol, day, fp)
            fingerprints += 1

    events = whale.scan(ticks, day, universe, lots=lots, gaps=gaps,
                        params={s: p for s, p in params.items() if not p.get("disabled")}, on_prints=on_prints)
    events = [e for e in events if not params.get(e.symbol, {}).get("disabled")]
    history.commit()
    with history:
        history.execute("DELETE FROM whale_events WHERE day = ? AND layer = 'A'", (day,))
    saved = whale.save_events(history, day, events)
    entries = whale.record_entries(history, day)
    featured = save_features(history, day, events)
    since = (date.fromisoformat(day) - timedelta(days=7)).isoformat()
    labelled = label_outcomes(history, since)
    calibration = calibrate(history, day)
    refitted = refit(history, day, lots)
    return {"day": day, "universe": len(universe), "fingerprints": fingerprints, "events": saved,
            "names": len({e.symbol for e in events}), "entries_added": entries, "featured": featured,
            "labelled": labelled, "calibration_run": calibration.get("run_id"),
            "base_rate": calibration.get("base_rate"), "refitted": refitted, "feed_gaps": len(gaps)}


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

def symbol_view(history: sqlite3.Connection, symbol: str, day: str) -> dict:
    """One instrument: fingerprints, baseline, params, override and its recent events."""
    if not _has(history, "whale_params"):
        return {"symbol": symbol, "params": whale.default_params(symbol, day), "fingerprints": [], "events": []}
    row = history.execute(
        "SELECT params, sensitivity, baseline, override, fitted_for, fitted_at FROM whale_params WHERE symbol = ?",
        (symbol,)).fetchone()
    prints = history.execute(
        """SELECT day, prints, volume, notional, p50, p90, p99, p999, max_qty FROM whale_fingerprints
           WHERE symbol = ? ORDER BY day DESC LIMIT 20""", (symbol,)).fetchall()
    events = history.execute(
        """SELECT day, ts_ms, kind, side, features, bins, ret15, ret30, ret60, typical_move, hit
           FROM whale_event_features WHERE symbol = ? ORDER BY ts_ms DESC LIMIT 100""", (symbol,)).fetchall()
    weights, base_rate, _run = latest_calibration(history)
    return {
        "symbol": symbol, **whale.describe(symbol),
        "params": json.loads(row[0]) if row and row[0] and row[0] != "{}" else whale.default_params(symbol, day),
        "sensitivity": row[1] if row else 1.0, "baseline": json.loads(row[2]) if row and row[2] else None,
        "override": json.loads(row[3]) if row and row[3] else None,
        "fitted_for": row[4] if row else None, "fitted_at": row[5] if row else None,
        "fingerprints": [dict(zip(("day", "prints", "volume", "notional", "p50", "p90", "p99", "p999", "max_qty"), r))
                         for r in prints],
        "events": [{"day": d, "at": datetime.fromtimestamp(ts / 1000, IST).strftime("%H:%M:%S"), "kind": k,
                    "side": s, "features": json.loads(f), "bins": json.loads(b),
                    "probability": probability(json.loads(b), weights, base_rate),
                    "ret15": r15, "ret30": r30, "ret60": r60, "typical_move": tm, "hit": hit}
                   for d, ts, k, s, f, b, r15, r30, r60, tm, hit in events],
    }


def overview(history: sqlite3.Connection, runs: int = 20) -> dict:
    """The latest calibration, the run history and the parameter population."""
    if not _has(history, "whale_calibration"):
        return {"runs": [], "latest": None, "params": {"symbols": 0}, "constants": constants()}
    history_rows = history.execute(
        """SELECT run_id, created_at, as_of, lookback_days, labelled, base_rate, report FROM whale_calibration
           ORDER BY run_id DESC LIMIT ?""", (runs,)).fetchall()
    latest = None
    run_list = []
    for run_id, created, as_of, lookback, labelled, base_rate, report in history_rows:
        parsed = json.loads(report or "{}")
        run_list.append({"run_id": run_id, "created_at": created, "as_of": as_of, "lookback_days": lookback,
                         "labelled": labelled, "base_rate": base_rate,
                         "top_quintile_hit_rate": parsed.get("top_quintile_hit_rate"),
                         "mean_brier": parsed.get("mean_brier")})
        if latest is None:
            latest = {"run_id": run_id, "as_of": as_of, **parsed}
    counts = history.execute(
        """SELECT COUNT(*), SUM(override IS NOT NULL), AVG(sensitivity), SUM(sensitivity > 1.001),
                  SUM(sensitivity < 0.999) FROM whale_params""").fetchone() if _has(history, "whale_params") else (0,) * 5
    pending = history.execute("SELECT COUNT(*) FROM whale_event_features WHERE hit IS NULL AND side != 0").fetchone()[0] \
        if _has(history, "whale_event_features") else 0
    return {"latest": latest, "runs": run_list,
            "params": {"symbols": counts[0] or 0, "overrides": counts[1] or 0,
                       "avg_sensitivity": round(counts[2], 3) if counts[2] else None,
                       "tightened": counts[3] or 0, "loosened": counts[4] or 0},
            "pending_labels": pending, "constants": constants()}


def params_table(history: sqlite3.Connection, limit: int = 300, query: str | None = None) -> list[dict]:
    if not _has(history, "whale_params"):
        return []
    sql = "SELECT symbol, params, sensitivity, baseline, override, fitted_for FROM whale_params"
    args: tuple = ()
    if query:
        sql += " WHERE symbol LIKE ?"
        args = (f"%{query.upper()}%",)
    out = []
    for symbol, params, sensitivity, base, override, fitted_for in history.execute(sql + " ORDER BY symbol LIMIT ?", (*args, limit)):
        p = json.loads(params) if params and params != "{}" else {}
        b = json.loads(base) if base else {}
        out.append({"symbol": symbol, "kind": whale.instrument_kind(symbol), "sensitivity": sensitivity,
                    "source": p.get("source"), "large_min_qty": p.get("large_min_qty"), "large_floor": p.get("large_floor"),
                    "child_min_qty": p.get("child_min_qty"), "child_floor": p.get("child_floor"),
                    "window_share": p.get("window_share"), "legacy": p.get("legacy"), "disabled": p.get("disabled"),
                    "baseline_sessions": b.get("sessions"), "p50": b.get("p50"), "p99": b.get("p99"), "p999": b.get("p999"),
                    "turnover": b.get("notional"), "typical_move_pct": b.get("typical_move_pct"),
                    "override": json.loads(override) if override else None, "fitted_for": fitted_for})
    return out


def constants() -> dict:
    return {"baseline_sessions": BASELINE_SESSIONS, "shrink_sessions": SHRINK_SESSIONS,
            "large_over_p999": LARGE_OVER_P999, "large_turnover_share": LARGE_TURNOVER_SHARE,
            "child_over_p90": CHILD_OVER_P90, "child_turnover_share": CHILD_TURNOVER_SHARE,
            "min_floor": MIN_FLOOR, "target_events_per_day": TARGET_EVENTS_PER_DAY,
            "sensitivity_bounds": SENSITIVITY_BOUNDS, "hit_fraction_of_typical_30m_move": HIT_FRACTION,
            "prior_strength": PRIOR_STRENGTH, "calibration_lookback_days": CALIBRATION_LOOKBACK_DAYS,
            "override_keys": sorted(OVERRIDE_KEYS)}
