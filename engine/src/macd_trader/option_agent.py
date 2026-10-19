"""Option agent: at 14:45 IST, the ten CE and ten PE contracts most likely to
outperform over the next one to three sessions -- and a loop that learns from
how its picks, and everything it passed over, actually did.

**Candidates.** Every option contract with at least ``MIN_BARS`` minute bars
before 14:45, a premium of at least ``MIN_PREMIUM``, rupee turnover so far of
at least ``MIN_TURNOVER``, moneyness within ``MAX_MONEYNESS`` and an expiry
AFTER the third session ahead (a three-day hold never crosses expiry).

**Features** (everything known at 14:45, nothing later). For the underlying:
returns intraday / 1 / 5 / 20 sessions, distance from the day's VWAP, where
it sits in today's and the 20-session range, the 30-minute MACD histogram,
strength against NIFTY over 5 sessions, where it sits against the prior
session's value area, today's volume-spike balance and its realised-volatility
regime. Directional ones are signed by the option's side, so "the underlying
is strong" helps a call and hurts a put. For the contract: moneyness (ITM
positive), premium as a share of spot, days to expiry, its own intraday and
1-session return, its volume surge against its own past, and its leverage.

**Target.** The mean premium return from the 14:45 price to the close of each
of the next three sessions, expressed as a percentile WITHIN the day and the
side: the agent ranks contracts against each other, so a market-wide move it
could not have known does not drown what it can.

**Model.** Ridge regression on standardised, winsorised features. A small
grid of configurations (penalty, feature set) is re-judged every night by
walk-forward over the latest sessions -- each test day trained only on days
whose three-session outcome had closed by then -- against a plain momentum
rule. A challenger replaces the champion only if it beats it on those same
days by ``PROMOTE_MARGIN``; every version is kept with its metrics.

**Picks.** Top ``TOP_N`` per side by score, at most one contract per
underlying, so ten picks are ten views and not five strikes of one stock.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
from calendar import monthrange
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from statistics import median
from zoneinfo import ZoneInfo

from . import market_calendar
from .sessions import session_bounds
from .universe import INDEX_SPOTS, STOCK_SPOTS

IST = ZoneInfo("Asia/Kolkata")
AS_OF = time(14, 45)
HORIZONS = (1, 2, 3)
MIN_BARS = 60
MINUTE_SECONDS = 60
# Minute timestamps denote the open. A completed bar may end at the cutoff
# or at most one minute before it; an older print is not a decision/close price.
MAX_SNAPSHOT_LAG_SECONDS = 60
# An option bar exists only in minutes the contract printed, so a thin
# contract often has no 14:44 bar. Its last print within five minutes of the
# cutoff is still its price: on 9 Oct the one-minute rule left 335 of ~1,300
# contracts; five minutes, with the emission moved to 14:47, keeps ~840.
OPTION_SNAPSHOT_LAG_SECONDS = 300
MIN_PREMIUM = 2.0
MIN_TURNOVER = 500_000
MAX_MONEYNESS = 0.08
TOP_N = 10
MIN_TRAIN_DAYS = 8
VALIDATION_DAYS = 15
PROMOTE_MARGIN = 0.005
WINSOR = 4.0
BENCHMARK = "NSE:NIFTY50-INDEX"

DIRECTIONAL = ["u_ret_intraday", "u_ret_1d", "u_ret_5d", "u_ret_20d", "u_vwap_dev", "u_day_range_pos",
               "u_range20_pos", "u_macd_hist", "u_rs_5d", "u_value_pos", "u_spike_net"]
NEUTRAL = ["u_rv_ratio", "o_moneyness", "o_premium_pct", "o_dte", "o_ret_intraday", "o_ret_1d",
           "o_vol_surge", "o_leverage", "is_index"]
FEATURES = DIRECTIONAL + NEUTRAL
CONFIGS = {
    "ridge-all-l5": {"lam": 5.0, "features": FEATURES},
    "ridge-all-l25": {"lam": 25.0, "features": FEATURES},
    "ridge-underlying-l10": {"lam": 10.0, "features": DIRECTIONAL + ["u_rv_ratio", "o_moneyness", "o_dte", "is_index"]},
    "momentum-rule": {"rule": True, "features": ["u_ret_1d", "o_ret_intraday"]},
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_candidates (
  day TEXT NOT NULL, symbol TEXT NOT NULL, side INTEGER NOT NULL, underlying TEXT, spot TEXT,
  strike REAL, expiry TEXT, entry_ts INTEGER, entry_price REAL, features TEXT NOT NULL,
  source TEXT, r1 REAL, r2 REAL, r3 REAL, y REAL, y_pct REAL, max_ret REAL, min_ret REAL,
  labelled_at TEXT, PRIMARY KEY (day, symbol));
CREATE TABLE IF NOT EXISTS agent_runs (
  run_id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL, emitted_at TEXT NOT NULL,
  model_version INTEGER, candidates INTEGER, source TEXT);
CREATE TABLE IF NOT EXISTS agent_picks (
  run_id INTEGER NOT NULL, day TEXT NOT NULL, side INTEGER NOT NULL, rank INTEGER NOT NULL,
  symbol TEXT NOT NULL, underlying TEXT, strike REAL, expiry TEXT, entry_price REAL,
  score REAL, drivers TEXT, PRIMARY KEY (run_id, side, rank));
CREATE TABLE IF NOT EXISTS agent_models (
  version INTEGER PRIMARY KEY AUTOINCREMENT, trained_at TEXT NOT NULL, config TEXT NOT NULL,
  train_from TEXT, train_to TEXT, samples INTEGER, weights TEXT, stats TEXT, metrics TEXT,
  status TEXT NOT NULL, note TEXT);
"""


def ensure_schema(history: sqlite3.Connection) -> None:
    history.executescript(SCHEMA)


# ---------------------------------------------------------------------------
# Contracts
# ---------------------------------------------------------------------------

_MONTHLY = re.compile(r"^(?P<root>[A-Z0-9&\-]+?)(?P<yy>\d{2})(?P<mon>JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)"
                      r"(?P<strike>\d+(?:\.\d+)?)(?P<right>CE|PE)$")
_WEEKLY = re.compile(r"^(?P<root>[A-Z&\-]+?)(?P<yy>\d{2})(?P<m>[1-9OND])(?P<dd>\d{2})(?P<strike>\d+(?:\.\d+)?)(?P<right>CE|PE)$")
_MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


def _last_weekday(year: int, month: int, weekday: int) -> date:
    day = date(year, month, monthrange(year, month)[1])
    while day.weekday() != weekday:
        day -= timedelta(days=1)
    return day


def _trading_on_or_before(day: date) -> date:
    while not market_calendar.is_trading_day(day):
        day -= timedelta(days=1)
    return day


def parse_option(symbol: str, stored_expiry: str | None = None) -> dict | None:
    """Root, spot, strike, side (+1 CE / -1 PE) and expiry from the ticker."""
    exchange, _, body = symbol.partition(":")
    match = _MONTHLY.match(body)
    expiry = None
    if match:
        year, month = 2000 + int(match["yy"]), _MONTHS.index(match["mon"]) + 1
        # NSE monthlies expire on the last Tuesday, BSE's on the last Thursday.
        expiry = _last_weekday(year, month, 3 if exchange == "BSE" else 1)
    else:
        match = _WEEKLY.match(body)
        if not match:
            return None
        month = {"O": 10, "N": 11, "D": 12}.get(match["m"]) or int(match["m"])
        try:
            expiry = date(2000 + int(match["yy"]), month, int(match["dd"]))
        except ValueError:
            return None
    if stored_expiry:
        expiry = date.fromisoformat(stored_expiry[:10])
    root = match["root"]
    spot = INDEX_SPOTS.get(root) or STOCK_SPOTS.get(root)
    return {"root": root, "spot": spot, "strike": float(match["strike"]),
            "side": 1 if match["right"] == "CE" else -1,
            "expiry": _trading_on_or_before(expiry).isoformat()}


# ---------------------------------------------------------------------------
# Daily data
# ---------------------------------------------------------------------------

def trading_days(history: sqlite3.Connection, since: str, until: str) -> list[str]:
    rows = history.execute(
        """SELECT DISTINCT date(timestamp, 'unixepoch', '+5 hours', '+30 minutes') FROM historical_candles
           WHERE symbol = ? AND timeframe_seconds = 60 AND timestamp >= ? AND timestamp < ?""",
        (BENCHMARK, int(datetime.fromisoformat(since).replace(tzinfo=IST).timestamp()),
         int(datetime.fromisoformat(until).replace(tzinfo=IST).timestamp()) + 86_400)).fetchall()
    return sorted(r[0] for r in rows if r[0])


def _as_of_epoch(day: str) -> int:
    return int(datetime.combine(date.fromisoformat(day), AS_OF, IST).timestamp())


def summarise_day(history: sqlite3.Connection, day: str, symbols: set[str] | None = None,
                  options: bool = True) -> dict[str, dict]:
    """One session per symbol: the 14:45 snapshot, the close, and what came after.

    ``symbols`` narrows the read; ``options`` includes every option traded that day.
    """
    start, end = session_bounds(None, day)
    cut = _as_of_epoch(day)
    where = "timeframe_seconds = 60 AND timestamp >= ? AND timestamp < ?"
    params: list = [start, end]
    if symbols is not None and not options:
        marks = ",".join("?" * len(symbols))
        where += f" AND symbol IN ({marks})"
        params.extend(sorted(symbols))
    out: dict[str, dict] = {}
    session_ends: dict[str, int] = {}
    for symbol, ts, o, h, l, c, v, expiry in history.execute(
            f"""SELECT symbol, timestamp, open, high, low, close, volume, expiry FROM historical_candles
                WHERE {where} ORDER BY symbol, timestamp""", params):
        is_option = symbol.endswith(("CE", "PE"))
        if symbols is not None and symbol not in symbols and not (options and is_option):
            continue
        if not c:
            continue
        if symbol not in session_ends:
            session_ends[symbol] = session_bounds(symbol, day)[1]
        if ts >= session_ends[symbol]:
            continue
        row = out.get(symbol)
        if row is None:
            row = out[symbol] = {"open": o or c, "high": h or c, "low": l or c, "close": c, "volume": 0.0,
                                 "notional": 0.0, "bars": 0, "p1445": None, "ts1445": None, "high_before": h or c,
                                 "low_before": l or c, "volume_before": 0.0, "notional_before": 0.0,
                                 "high_after": None, "low_after": None, "closes30": {}, "minutes": [],
                                 "expiry": expiry, "close_ts": ts}
        row["close"] = c
        row["close_ts"] = ts
        row["high"] = max(row["high"], h or c)
        row["low"] = min(row["low"], l or c)
        row["volume"] += v or 0
        row["notional"] += (v or 0) * c
        if ts < cut:
            row["bars"] += 1
            row["p1445"], row["ts1445"] = c, ts
            row["high_before"] = max(row["high_before"], h or c)
            row["low_before"] = min(row["low_before"], l or c)
            row["volume_before"] += v or 0
            row["notional_before"] += (v or 0) * c
            if not is_option:
                row["minutes"].append((ts, o or c, c, v or 0))
        else:
            row["high_after"] = max(row["high_after"] or (h or c), h or c)
            row["low_after"] = min(row["low_after"] or (l or c), l or c)
        if not is_option:
            row["closes30"][(ts - start) // 1800] = c
        if expiry and not row["expiry"]:
            row["expiry"] = expiry
    return out


def _ema(values: list[float], period: int) -> list[float]:
    out, prev, k = [], None, 2 / (period + 1)
    for value in values:
        prev = value if prev is None else value * k + prev * (1 - k)
        out.append(prev)
    return out


def _macd_hist(closes: list[float]) -> float | None:
    if len(closes) < 35:
        return None
    macd = [a - b for a, b in zip(_ema(closes, 12), _ema(closes, 26))]
    signal = _ema(macd, 9)
    return macd[-1] - signal[-1]


def _ret(a: float | None, b: float | None) -> float | None:
    return (a / b - 1) if a and b else None


class History:
    """Rolling per-symbol day summaries, oldest first, for features that look back."""

    def __init__(self) -> None:
        self.days: list[str] = []
        self.by_day: dict[str, dict[str, dict]] = {}

    def add(self, day: str, summary: dict[str, dict]) -> None:
        self.days.append(day)
        self.by_day[day] = summary

    def prior(self, day: str, count: int) -> list[str]:
        index = self.days.index(day)
        return self.days[max(0, index - count):index]


def _snapshot_complete(timestamp: int | None, cutoff: int, max_lag: int = MAX_SNAPSHOT_LAG_SECONDS) -> bool:
    return timestamp is not None and 0 <= cutoff - (timestamp + MINUTE_SECONDS) <= max_lag


def _underlying_features(spot: str, day: str, hist: History, profiles: dict) -> dict | None:
    today = hist.by_day[day].get(spot)
    if not today or not today["p1445"] or not _snapshot_complete(today.get("ts1445"), _as_of_epoch(day)):
        return None
    price = today["p1445"]
    prior = [hist.by_day[d].get(spot) for d in hist.prior(day, 20)]
    prior = [p for p in prior if p]
    if len(prior) < 5:
        return None
    bench = hist.by_day[day].get(BENCHMARK)
    bench_prior = [hist.by_day[d].get(BENCHMARK) for d in hist.prior(day, 5)]
    closes30 = []
    for d in hist.prior(day, 20):
        entry = hist.by_day[d].get(spot)
        if entry:
            closes30.extend(entry["closes30"][k] for k in sorted(entry["closes30"]))
    cut = _as_of_epoch(day)
    start = session_bounds(None, day)[0]
    closes30.extend(c for k, c in sorted(today["closes30"].items()) if start + (k + 1) * 1800 <= cut)
    closes30.append(price)
    minutes = today["minutes"]
    vwap = (today["notional_before"] / today["volume_before"]) if today["volume_before"] else None
    returns = [math.log(b[2] / a[2]) for a, b in zip(minutes, minutes[1:]) if a[2] and b[2]]
    rv_today = math.sqrt(sum(r * r for r in returns) / len(returns)) if len(returns) > 20 else None
    rv_prior = []
    for entry in prior:
        mins = entry["minutes"]
        rs = [math.log(b[2] / a[2]) for a, b in zip(mins, mins[1:]) if a[2] and b[2]]
        if len(rs) > 20:
            rv_prior.append(math.sqrt(sum(r * r for r in rs) / len(rs)))
    vols = sorted(v for entry in prior[-10:] for _ts, _o, _c, v in entry["minutes"] if v)
    base_vol = vols[len(vols) // 2] if vols else 0
    spikes = sum((1 if c > o else -1 if c < o else 0) for _ts, o, c, v in minutes if base_vol and v >= 8 * base_vol)
    high20 = max([p["high"] for p in prior] + [today["high_before"]])
    low20 = min([p["low"] for p in prior] + [today["low_before"]])
    profile = profiles.get(spot)
    value_pos = 0.0
    if profile and profile.get("vah") and profile.get("val"):
        value_pos = 1.0 if price > profile["vah"] else -1.0 if price < profile["val"] else 0.0
    ret5 = _ret(price, prior[-5]["close"]) if len(prior) >= 5 else None
    bench_ret5 = _ret(bench["p1445"], bench_prior[0]["close"]) if bench and bench_prior and bench_prior[0] else None
    day_span = today["high_before"] - today["low_before"]
    hist_value = _macd_hist(closes30)
    return {
        "price": price,
        "u_ret_intraday": _ret(price, today["open"]) or 0.0,
        "u_ret_1d": _ret(price, prior[-1]["close"]) or 0.0,
        "u_ret_5d": ret5 or 0.0,
        "u_ret_20d": _ret(price, prior[0]["close"]) or 0.0,
        "u_vwap_dev": _ret(price, vwap) or 0.0,
        "u_day_range_pos": ((price - today["low_before"]) / day_span - 0.5) if day_span > 0 else 0.0,
        "u_range20_pos": ((price - low20) / (high20 - low20) - 0.5) if high20 > low20 else 0.0,
        "u_macd_hist": (hist_value / price) if hist_value is not None else 0.0,
        "u_rs_5d": (ret5 - bench_ret5) if ret5 is not None and bench_ret5 is not None else 0.0,
        "u_value_pos": value_pos,
        "u_spike_net": float(spikes),
        "u_rv_ratio": (rv_today / median(rv_prior) - 1) if rv_today and rv_prior else 0.0,
        "is_index": 1.0 if spot.endswith("-INDEX") else 0.0,
    }


def _prior_profiles(history: sqlite3.Connection, day: str) -> dict[str, dict]:
    try:
        rows = history.execute(
            """SELECT p.symbol, p.vah, p.val FROM session_profiles p
               JOIN (SELECT symbol, MAX(day) AS day FROM session_profiles WHERE day < ? GROUP BY symbol) last
               ON last.symbol = p.symbol AND last.day = p.day""", (day,)).fetchall()
    except sqlite3.OperationalError:
        return {}
    return {symbol: {"vah": vah, "val": val} for symbol, vah, val in rows}


def third_session_after(day: str, known: list[str]) -> str:
    """The third calendar trading session; missing benchmark bars cannot extend a hold."""
    return _sessions_after(day)[-1]


def _sessions_after(day: str) -> list[str]:
    sessions, cursor = [], date.fromisoformat(day)
    for _ in HORIZONS:
        cursor = market_calendar.next_trading_day(cursor)
        sessions.append(cursor.isoformat())
    return sessions


def build_candidates(history: sqlite3.Connection, day: str, hist: History, sessions_after: list[str]) -> list[dict]:
    """Every eligible contract on ``day`` with its 14:45 features."""
    summary = hist.by_day[day]
    profiles = _prior_profiles(history, day)
    horizon_end = third_session_after(day, sessions_after)
    underlying_cache: dict[str, dict | None] = {}
    rows = []
    for symbol, s in summary.items():
        if (not symbol.endswith(("CE", "PE")) or s["bars"] < MIN_BARS or not s["p1445"]
                or not _snapshot_complete(s.get("ts1445"), _as_of_epoch(day), OPTION_SNAPSHOT_LAG_SECONDS)):
            continue
        contract = parse_option(symbol, s.get("expiry"))
        if not contract or not contract["spot"]:
            continue
        # Never across expiry: the contract must outlive the third session ahead.
        if contract["expiry"] <= horizon_end:
            continue
        premium = s["p1445"]
        if premium < MIN_PREMIUM or s["notional_before"] < MIN_TURNOVER:
            continue
        spot = contract["spot"]
        if spot not in underlying_cache:
            underlying_cache[spot] = _underlying_features(spot, day, hist, profiles)
        under = underlying_cache[spot]
        if not under:
            continue
        side = contract["side"]
        moneyness = side * math.log(under["price"] / contract["strike"])
        if abs(moneyness) > MAX_MONEYNESS:
            continue
        prior_volumes = [hist.by_day[d][symbol]["volume"] for d in hist.prior(day, 5) if symbol in hist.by_day[d]]
        prev_close = next((hist.by_day[d][symbol]["close"] for d in reversed(hist.prior(day, 3))
                           if symbol in hist.by_day[d]), None)
        o_intraday = _ret(premium, s["open"]) or 0.0
        u_intraday = under["u_ret_intraday"] * side
        features = {name: under[name] * side for name in DIRECTIONAL}
        features.update({
            "u_rv_ratio": under["u_rv_ratio"], "is_index": under["is_index"],
            "o_moneyness": moneyness, "o_premium_pct": premium / under["price"],
            "o_dte": float((date.fromisoformat(contract["expiry"]) - date.fromisoformat(day)).days),
            "o_ret_intraday": o_intraday, "o_ret_1d": _ret(premium, prev_close) or 0.0,
            "o_vol_surge": math.log1p(s["volume_before"] / (median(prior_volumes) + 1)) if prior_volumes else 0.0,
            "o_leverage": max(-50.0, min(50.0, o_intraday / u_intraday)) if abs(u_intraday) > 1e-4 else 0.0,
        })
        rows.append({"day": day, "symbol": symbol, "side": side, "underlying": contract["root"], "spot": spot,
                     "strike": contract["strike"], "expiry": contract["expiry"], "entry_ts": s["ts1445"],
                     "entry_price": premium, "features": features})
    return rows


def label(rows: list[dict], sessions_after: list[str], hist_or_summaries) -> None:
    """Returns at the close of each of the next three sessions, and the best/worst in between."""
    by_day = hist_or_summaries
    for row in rows:
        symbol, entry = row["symbol"], row["entry_price"]
        today = by_day[row["day"]].get(symbol, {})
        highs = [today.get("high_after")] if today.get("high_after") else []
        lows = [today.get("low_after")] if today.get("low_after") else []
        for h, day in enumerate(_sessions_after(row["day"]), start=1):
            s = by_day.get(day, {}).get(symbol)
            end = session_bounds(symbol, day)[1]
            complete = bool(s and s.get("close") and datetime.now(UTC).timestamp() >= end
                            and _snapshot_complete(s.get("close_ts"), end))
            row[f"r{h}"] = (s["close"] / entry - 1) if complete else None
            if s:
                highs.append(s["high"])
                lows.append(s["low"])
        rets = [row.get(f"r{h}") for h in HORIZONS]
        row["y"] = sum(rets) / 3 if all(r is not None for r in rets) else None
        row["max_ret"] = (max(highs) / entry - 1) if highs else None
        row["min_ret"] = (min(lows) / entry - 1) if lows else None


def rank_within(rows: list[dict]) -> None:
    """y as a percentile within (day, side) -- the model's target."""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        if row.get("y") is not None:
            groups[(row["day"], row["side"])].append(row)
    for group in groups.values():
        ordered = sorted(group, key=lambda r: r["y"])
        for index, row in enumerate(ordered):
            row["y_pct"] = (index + 0.5) / len(ordered)


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

def _solve(matrix: list[list[float]], vector: list[float]) -> list[float]:
    n = len(vector)
    a = [row[:] + [vector[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        a[col], a[pivot] = a[pivot], a[col]
        if abs(a[col][col]) < 1e-12:
            continue
        for r in range(n):
            if r != col:
                factor = a[r][col] / a[col][col]
                for c in range(col, n + 1):
                    a[r][c] -= factor * a[col][c]
    return [a[i][n] / a[i][i] if abs(a[i][i]) > 1e-12 else 0.0 for i in range(n)]


def ridge(data: list[dict], names: list[str], lam: float, targets: list[float]) -> dict:
    """Ridge weights on standardised, winsorised features (pure Python: no numpy here)."""
    if not data:
        return {"features": names, "weights": [0.0] * len(names), "mean": [0.0] * len(names), "std": [1.0] * len(names)}
    columns = [[r["features"].get(n, 0.0) or 0.0 for r in data] for n in names]
    means = [sum(c) / len(c) for c in columns]
    stds = [math.sqrt(sum((x - m) ** 2 for x in c) / max(1, len(c) - 1)) or 1.0 for c, m in zip(columns, means)]
    x = [[max(-WINSOR, min(WINSOR, (columns[j][i] - means[j]) / stds[j])) for j in range(len(names))] for i in range(len(data))]
    k = len(names)
    xtx = [[sum(row[a] * row[b] for row in x) + (lam if a == b else 0.0) for b in range(k)] for a in range(k)]
    xty = [sum(row[a] * t for row, t in zip(x, targets)) for a in range(k)]
    return {"features": names, "weights": _solve(xtx, xty), "mean": means, "std": stds, "lam": lam}


def fit(rows: list[dict], config: dict) -> dict:
    """Ridge toward y_pct - 0.5, the within-day, within-side rank (or the rule)."""
    names = config["features"]
    if config.get("rule"):
        return {"rule": True, "features": names}
    data = [r for r in rows if r.get("y_pct") is not None]
    return ridge(data, names, config["lam"], [r["y_pct"] - 0.5 for r in data])


def score(model: dict, features: dict) -> tuple[float, list[tuple[str, float]]]:
    """(score, per-feature contributions, strongest first)."""
    if model.get("rule"):
        parts = [(n, features.get(n, 0.0) or 0.0) for n in model["features"]]
        return sum(v for _n, v in parts), sorted(parts, key=lambda p: -abs(p[1]))
    parts = []
    for name, w, m, s in zip(model["features"], model["weights"], model["mean"], model["std"]):
        z = max(-WINSOR, min(WINSOR, ((features.get(name, 0.0) or 0.0) - m) / s))
        parts.append((name, w * z))
    return sum(v for _n, v in parts), sorted(parts, key=lambda p: -abs(p[1]))


def pick(rows: list[dict], model: dict, top_n: int = TOP_N) -> dict[int, list[dict]]:
    """Top ``top_n`` stocks per side, one contract per underlying, each underlying on ONE side.

    Candidates are contracts, but the lists rank underlyings. Deduplicating
    within each side still let one underlying top both: on 30 Sep BANKNIFTY
    was the 7th call and the 1st put -- opposite bets on one index, the day
    the index lane held a put on it. Each underlying now takes the side of its
    best-scoring contract (one model scores both sides, its directional
    features signed by side, so the two scores compare). Indices are left out
    of the lists altogether: the index lane trades them (user, 9 Oct 2026),
    and on 9 Oct three of the ten put slots were NIFTY, BANKNIFTY and SENSEX.
    Training still uses both sides of everything, indices included.
    """
    scored = []
    for row in rows:
        if row["underlying"] in INDEX_SPOTS:
            continue
        value, parts = score(model, row["features"])
        scored.append({**row, "score": value, "drivers": parts[:4]})
    best: dict[str, dict] = {}
    for row in sorted(scored, key=lambda r: -r["score"]):
        underlying = row["underlying"]
        if underlying in best:
            continue
        best[underlying] = row
    return {side: sorted((r for r in best.values() if r["side"] == side), key=lambda r: -r["score"])[:top_n]
            for side in (1, -1)}


def _spearman(a: list[float], b: list[float]) -> float | None:
    if len(a) < 10:
        return None
    ra = {i: r for r, i in enumerate(sorted(range(len(a)), key=lambda i: a[i]))}
    rb = {i: r for r, i in enumerate(sorted(range(len(b)), key=lambda i: b[i]))}
    n = len(a)
    d2 = sum((ra[i] - rb[i]) ** 2 for i in range(n))
    return 1 - 6 * d2 / (n * (n * n - 1))


def walk_forward(rows: list[dict], config: dict, days: list[str] | None = None) -> dict:
    """Test each day with a model fitted only on days whose outcome had closed (index <= t-4)."""
    by_day: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if row.get("y") is not None:
            by_day[row["day"]].append(row)
    all_days = sorted(by_day)
    tests = days if days is not None else all_days[MIN_TRAIN_DAYS + 3:]
    per_day = []
    for day in tests:
        if day not in by_day:
            continue
        index = all_days.index(day)
        train_days = all_days[:max(0, index - 3)]
        if len(train_days) < MIN_TRAIN_DAYS:
            continue
        model = fit([r for d in train_days for r in by_day[d]], config)
        picks = pick(by_day[day], model)
        entry = {"day": day}
        for side, label_ in ((1, "ce"), (-1, "pe")):
            universe = [r for r in by_day[day] if r["side"] == side]
            chosen = picks[side]
            if not universe or not chosen:
                continue
            mean_all = sum(r["y"] for r in universe) / len(universe)
            mean_pick = sum(r["y"] for r in chosen) / len(chosen)
            entry[f"{label_}_excess"] = mean_pick - mean_all
            entry[f"{label_}_pick_ret"] = mean_pick
            entry[f"{label_}_hit"] = sum(1 for r in chosen if r["y_pct"] >= 0.8) / len(chosen)
            entry[f"{label_}_ic"] = _spearman([score(model, r["features"])[0] for r in universe], [r["y"] for r in universe])
        per_day.append(entry)
    return {"config": config, "days": per_day, **_aggregate(per_day)}


def _aggregate(per_day: list[dict]) -> dict:
    def stat(key):
        values = [d[key] for d in per_day if d.get(key) is not None]
        if not values:
            return None, None
        mean = sum(values) / len(values)
        sd = math.sqrt(sum((v - mean) ** 2 for v in values) / max(1, len(values) - 1))
        return round(mean, 5), (round(mean / (sd / math.sqrt(len(values))), 2) if sd else None)
    out = {"test_days": len(per_day)}
    for key in ("ce_excess", "pe_excess", "ce_pick_ret", "pe_pick_ret", "ce_hit", "pe_hit", "ce_ic", "pe_ic"):
        out[key], out[f"{key}_t"] = stat(key)
    both = [(d.get("ce_excess", 0) + d.get("pe_excess", 0)) / 2 for d in per_day if "ce_excess" in d or "pe_excess" in d]
    out["excess"] = round(sum(both) / len(both), 5) if both else None
    return out


# ---------------------------------------------------------------------------
# Persistence and the loop
# ---------------------------------------------------------------------------

def _store_candidates(history: sqlite3.Connection, rows: list[dict], source: str) -> None:
    # Backfills and emissions share the same SQLite write boundary; a freeze
    # check made before obtaining it could be invalidated by another writer.
    if not history.in_transaction:
        history.execute("BEGIN IMMEDIATE")
    with history:
        _persist_candidates(history, rows, source)


def _persist_candidates(history: sqlite3.Connection, rows: list[dict], source: str) -> None:
    """Persist under the caller's write transaction without committing it."""
    # A run freezes its whole candidate universe, including the unpicked
    # benchmark. Late backfills/re-emissions may label it, but cannot replace
    # the price or information available when that run was selected.
    frozen: dict[str, dict[str, dict]] = {}
    for day in {r["day"] for r in rows}:
        captured = _frozen_candidates(history, day)
        if captured is not None:
            frozen[day] = {r["symbol"]: r for r in captured}
    safe_rows = []
    for row in rows:
        captured = frozen.get(row["day"])
        if captured is not None:
            original = captured.get(row["symbol"])
            if original is None or row["entry_price"] != original["entry_price"]:
                continue
            row = {**row, "entry_ts": original["entry_ts"], "features": original["features"]}
        safe_rows.append(row)
    history.executemany(
            """INSERT INTO agent_candidates (day, symbol, side, underlying, spot, strike, expiry, entry_ts, entry_price,
                 features, source, r1, r2, r3, y, y_pct, max_ret, min_ret, labelled_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(day, symbol) DO UPDATE SET
                 -- Unemitted views may be rebuilt; frozen fields were restored above.
                 features=excluded.features, entry_ts=excluded.entry_ts, entry_price=excluded.entry_price,
                 -- Measured outcomes survive only when their entry denominator is unchanged.
                 r1=CASE WHEN entry_price=excluded.entry_price THEN COALESCE(excluded.r1, r1) ELSE excluded.r1 END,
                 r2=CASE WHEN entry_price=excluded.entry_price THEN COALESCE(excluded.r2, r2) ELSE excluded.r2 END,
                 r3=CASE WHEN entry_price=excluded.entry_price THEN COALESCE(excluded.r3, r3) ELSE excluded.r3 END,
                 y=CASE WHEN entry_price=excluded.entry_price THEN COALESCE(excluded.y, y) ELSE excluded.y END,
                 y_pct=CASE WHEN entry_price=excluded.entry_price THEN COALESCE(excluded.y_pct, y_pct) ELSE excluded.y_pct END,
                 max_ret=CASE WHEN entry_price=excluded.entry_price THEN COALESCE(excluded.max_ret, max_ret) ELSE excluded.max_ret END,
                 min_ret=CASE WHEN entry_price=excluded.entry_price THEN COALESCE(excluded.min_ret, min_ret) ELSE excluded.min_ret END,
                 labelled_at=CASE WHEN entry_price=excluded.entry_price THEN COALESCE(excluded.labelled_at, labelled_at)
                                 ELSE excluded.labelled_at END""",
            [(r["day"], r["symbol"], r["side"], r["underlying"], r["spot"], r["strike"], r["expiry"], r["entry_ts"],
              r["entry_price"], json.dumps(r["features"]), source, r.get("r1"), r.get("r2"), r.get("r3"), r.get("y"),
              r.get("y_pct"), r.get("max_ret"), r.get("min_ret"),
              datetime.now(UTC).isoformat() if r.get("y") is not None else None) for r in safe_rows])


def load_candidates(history: sqlite3.Connection, since: str | None = None, until: str | None = None) -> list[dict]:
    ensure_schema(history)
    return _load_candidates(history, since, until)


def _load_candidates(history: sqlite3.Connection, since: str | None = None, until: str | None = None) -> list[dict]:
    """Read without schema helpers, which would commit an active transaction."""
    sql = """SELECT day, symbol, side, underlying, spot, strike, expiry, entry_ts, entry_price, features,
                    r1, r2, r3, y, y_pct, max_ret, min_ret FROM agent_candidates"""
    conditions, params = [], []
    if since:
        conditions.append("day >= ?")
        params.append(since)
    if until:
        conditions.append("day <= ?")
        params.append(until)
    rows = history.execute(sql + (" WHERE " + " AND ".join(conditions) if conditions else "") + " ORDER BY day", params).fetchall()
    keys = ("day", "symbol", "side", "underlying", "spot", "strike", "expiry", "entry_ts", "entry_price", "features",
            "r1", "r2", "r3", "y", "y_pct", "max_ret", "min_ret")
    out = []
    for row in rows:
        item = dict(zip(keys, row))
        item["features"] = json.loads(item["features"])
        out.append(item)
    return out


def _frozen_candidates(history: sqlite3.Connection, day: str) -> list[dict] | None:
    if history.execute("SELECT 1 FROM agent_runs WHERE day = ? LIMIT 1", (day,)).fetchone() is None:
        return None
    return _load_candidates(history, day, day)


def _capture_emission(history: sqlite3.Connection, day: str, source: str, version: int | None,
                      rows: list[dict]) -> tuple[int | None, list[dict], bool]:
    """Atomically freeze the canonical candidates and create their run.

    Two connections may build different local snapshots. Only the first
    writer captures the day; every subsequent run must score its stored
    candidate prices and features, rather than its discarded local snapshot.
    """
    history.execute("BEGIN IMMEDIATE")
    with history:
        if source == "scheduled":
            existing = history.execute(
                "SELECT run_id FROM agent_runs WHERE day = ? AND source = 'scheduled'", (day,)).fetchone()
            if existing:
                return existing[0], [], False
        canonical = _frozen_candidates(history, day)
        if canonical is None:
            if not rows:
                return None, [], False
            # Bootstrap rows are still mutable until their first run. Remove
            # symbols excluded by the actual decision before freezing the
            # universe; subsequent runs must reload exactly this membership.
            selected = {r["symbol"] for r in rows}
            previous = _load_candidates(history, day, day)
            history.executemany("DELETE FROM agent_candidates WHERE day = ? AND symbol = ?",
                                [(day, r["symbol"]) for r in previous if r["symbol"] not in selected])
            _persist_candidates(history, rows, source)
            canonical = _load_candidates(history, day, day)
            if any(r.get("y") is not None for r in canonical):
                rank_within(canonical)
                _persist_candidates(history, canonical, source)
        cursor = history.execute(
            "INSERT INTO agent_runs (day, emitted_at, model_version, candidates, source) VALUES (?,?,?,?,?)",
            (day, datetime.now(UTC).isoformat(), version, len(canonical), source))
        return cursor.lastrowid, canonical, True


def build_history(history: sqlite3.Connection, since: str, until: str, lookback_days: int = 35) -> tuple[History, list[str]]:
    """Day summaries from ``since`` minus a lookback, through ``until``."""
    first = (date.fromisoformat(since) - timedelta(days=lookback_days)).isoformat()
    days = trading_days(history, first, until)
    hist = History()
    spots = set(INDEX_SPOTS.values()) | set(STOCK_SPOTS.values()) | {BENCHMARK}
    # Options are read from a week before `since`: a contract's volume surge and
    # prior close look back five sessions.
    options_from = (date.fromisoformat(since) - timedelta(days=8)).isoformat()
    for day in days:
        hist.add(day, summarise_day(history, day, spots, options=day >= options_from))
    return hist, days


def bootstrap(history: sqlite3.Connection, since: str, until: str) -> dict:
    """Rebuild candidates and labels for past sessions from the minute bars."""
    ensure_schema(history)
    # Three sessions past `until` are read too, so its last days can be labelled.
    horizon = (date.fromisoformat(until) + timedelta(days=10)).isoformat()
    hist, days = build_history(history, since, horizon)
    total = labelled = 0
    for day in days:
        if day < since or day > until:
            continue
        after = _sessions_after(day)
        rows = _frozen_candidates(history, day)
        if rows is None:
            rows = build_candidates(history, day, hist, after)
        summaries = dict(hist.by_day)
        for future in after:
            if future not in summaries:
                summaries[future] = summarise_day(history, future, {r["symbol"] for r in rows}, options=False)
        label(rows, after, summaries)
        rank_within(rows)
        _store_candidates(history, rows, "history")
        total += len(rows)
        labelled += sum(1 for r in rows if r.get("y") is not None)
    return {"since": since, "until": until, "days": len([d for d in days if since <= d <= until]),
            "candidates": total, "labelled": labelled}


def relabel(history: sqlite3.Connection, today: str | None = None, lookback_days: int = 14) -> int:
    """Update each completed horizon; train only once all three have closed.

    Only the last ``lookback_days``: a contract whose bars never arrived stays
    unlabelled rather than being re-read every night forever.
    """
    ensure_schema(history)
    today = today or datetime.now(IST).date().isoformat()
    since = (date.fromisoformat(today) - timedelta(days=lookback_days)).isoformat()
    pending = sorted(r[0] for r in history.execute(
        "SELECT DISTINCT day FROM agent_candidates WHERE y IS NULL AND day >= ?", (since,)))
    if not pending:
        return 0
    days = trading_days(history, pending[0], today)
    updated = 0
    for day in pending:
        if day not in days:
            continue
        after = _sessions_after(day)
        if after[0] > today:
            continue
        rows = load_candidates(history, day, day)
        symbols = {r["symbol"] for r in rows}
        summaries = {d: summarise_day(history, d, symbols, options=False) for d in [day, *after] if d <= today}
        label(rows, after, summaries)
        rank_within(rows)
        _store_candidates(history, rows, "live")
        updated += sum(1 for r in rows if r.get("y") is not None)
    return updated


def train(history: sqlite3.Connection, note: str = "") -> dict:
    """Judge every configuration walk-forward on the same recent days.

    The best becomes a new version. It replaces the champion only when it
    beats the champion's configuration on those days by ``PROMOTE_MARGIN``;
    otherwise it is kept as a challenger and the champion is refitted on the
    new labels. Every refit gets a new version: past runs still refer to the
    exact weights and training window used to select them.
    """
    ensure_schema(history)
    rows = [r for r in load_candidates(history) if r.get("y") is not None]
    days = sorted({r["day"] for r in rows})
    if len(days) < MIN_TRAIN_DAYS + 4:
        return {"status": "insufficient", "labelled_days": len(days), "needed": MIN_TRAIN_DAYS + 4}
    tests = days[-VALIDATION_DAYS:] if len(days) >= MIN_TRAIN_DAYS + 4 + VALIDATION_DAYS else days[MIN_TRAIN_DAYS + 3:]
    results = {name: walk_forward(rows, config, tests) for name, config in CONFIGS.items()}
    best = max(results, key=lambda name: results[name]["excess"] if results[name]["excess"] is not None else -1)
    champion = history.execute(
        "SELECT version, config FROM agent_models WHERE status = 'champion' ORDER BY version DESC LIMIT 1").fetchone()
    champion_name = json.loads(champion[1])["name"] if champion else None
    if champion_name not in CONFIGS:
        champion_name = None
    champion_excess = results[champion_name]["excess"] if champion_name else None
    promote = champion_name is None or (
        best != champion_name and (results[best]["excess"] or 0) >= (champion_excess or 0) + PROMOTE_MARGIN)
    chosen = best if promote else champion_name
    metrics = {name: {k: v for k, v in result.items() if k not in ("days", "config")} for name, result in results.items()}
    stamp = datetime.now(UTC).isoformat()
    with history:
        if not promote and best != chosen:
            history.execute(
                """INSERT INTO agent_models (trained_at, config, train_from, train_to, samples, weights, stats, metrics, status, note)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (stamp, json.dumps({"name": best, **CONFIGS[best]}), days[0], days[-1], len(rows),
                 json.dumps(fit(rows, CONFIGS[best])),
                 json.dumps({"validation_days": tests, "per_day": results[best]["days"]}), json.dumps(metrics),
                 "challenger", f"{best} did not beat champion {chosen} by {PROMOTE_MARGIN:.1%}"))
        history.execute("UPDATE agent_models SET status = 'retired' WHERE status = 'champion'")
        cursor = history.execute(
            """INSERT INTO agent_models (trained_at, config, train_from, train_to, samples, weights, stats, metrics, status, note)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (stamp, json.dumps({"name": chosen, **CONFIGS[chosen]}), days[0], days[-1], len(rows),
             json.dumps(fit(rows, CONFIGS[chosen])),
             json.dumps({"validation_days": tests, "per_day": results[chosen]["days"]}), json.dumps(metrics),
             "champion", note or (f"promoted {chosen}" if promote else f"refitted {chosen}")))
        version = cursor.lastrowid
    return {"status": "promoted" if promote else "kept", "champion": best if promote else champion_name, "best": best,
            "version": version, "labelled_days": len(days), "samples": len(rows), "validation_days": len(tests),
            "validation": metrics}


def active_model(history: sqlite3.Connection) -> tuple[int | None, dict | None, str | None]:
    ensure_schema(history)
    row = history.execute(
        "SELECT version, weights, config FROM agent_models WHERE status = 'champion' ORDER BY version DESC LIMIT 1").fetchone()
    if not row:
        return None, None, None
    return row[0], json.loads(row[1]), json.loads(row[2])["name"]


SPOT_SET = frozenset(INDEX_SPOTS.values()) | frozenset(STOCK_SPOTS.values()) | {BENCHMARK}
LOOKBACK_CALENDAR_DAYS = 45
OPTION_LOOKBACK_SESSIONS = 5
_prepared: dict[str, History] = {}


def prepare(history: sqlite3.Connection, day: str, refresh: bool = False) -> History:
    """The sessions BEFORE ``day``, summarised once: they do not change while ``day`` trades.

    Re-reading them was ~38 of the emission's ~43 seconds (five sessions of
    every option's minute bars). The desk prepares at 14:30, so the 14:45
    emission reads only today. Kept for one day at a time.
    """
    if not refresh and day in _prepared:
        return _prepared[day]
    since = (date.fromisoformat(day) - timedelta(days=LOOKBACK_CALENDAR_DAYS)).isoformat()
    prior = [d for d in trading_days(history, since, day) if d < day]
    recent = set(prior[-OPTION_LOOKBACK_SESSIONS:])
    hist = History()
    for d in prior:
        hist.add(d, summarise_day(history, d, SPOT_SET, options=d in recent))
    _prepared.clear()
    _prepared[day] = hist
    return hist


def emit(history: sqlite3.Connection, day: str | None = None, source: str = "scheduled",
         lots: dict[str, int] | None = None) -> dict:
    """Score today's candidates at 14:45 with the champion; store the run, its picks and every candidate."""
    ensure_schema(history)
    day = day or datetime.now(IST).date().isoformat()
    if datetime.now(IST).timestamp() < _as_of_epoch(day):
        return {"status": "cutoff_not_reached", "day": day, "as_of": AS_OF.strftime("%H:%M")}
    if source == "scheduled":
        # A desk restart after the emission must not emit a second set.
        existing = history.execute(
            "SELECT run_id FROM agent_runs WHERE day = ? AND source = 'scheduled'", (day,)).fetchone()
        if existing:
            return {"status": "already_emitted", "day": day, "run_id": existing[0]}
    version, model, name = active_model(history)
    if model is None:
        train(history, note="first model")
        version, model, name = active_model(history)
    if model is None:
        return {"status": "no_model", "day": day}
    if day not in trading_days(history, day, day):
        return {"status": "no_bars_today", "day": day}
    prior = prepare(history, day)
    hist = History()
    hist.days, hist.by_day = list(prior.days), dict(prior.by_day)
    hist.add(day, summarise_day(history, day, SPOT_SET, options=True))
    rows = _frozen_candidates(history, day)
    if rows is None:
        rows = build_candidates(history, day, hist, [])
    run_id, rows, captured = _capture_emission(history, day, source, version, rows)
    if not captured:
        if run_id is None:
            return {"status": "no_eligible_candidates", "day": day}
        return {"status": "already_emitted", "day": day, "run_id": run_id}
    # Indices belong to the index lane alone; the stock lists never carry them.
    from . import index_trades
    index_error = None
    try:
        indices = index_trades.emit(history, run_id, day, rows, hist, lots)
    except Exception as exc:  # noqa: BLE001 - the stock lists must still be stored for this run
        indices, index_error = [], f"{type(exc).__name__}: {exc}"[:300]
    picks = pick(rows, model)
    with history:
        for side, chosen in picks.items():
            for rank, row in enumerate(chosen, start=1):
                history.execute(
                    """INSERT INTO agent_picks (run_id, day, side, rank, symbol, underlying, strike, expiry, entry_price, score, drivers)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (run_id, day, side, rank, row["symbol"], row["underlying"], row["strike"], row["expiry"],
                     row["entry_price"], round(row["score"], 5), json.dumps([[n, round(v, 4)] for n, v in row["drivers"]])))
    return {"status": "emitted", "run_id": run_id, "day": day, "model_version": version, "model": name,
            "candidates": len(rows), "ce": [p["symbol"] for p in picks[1]], "pe": [p["symbol"] for p in picks[-1]],
            "index": {t["underlying"]: t.get("symbol") or "no trade" for t in indices}, "index_error": index_error}


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

def _latest_price(history: sqlite3.Connection, symbol: str) -> tuple[float | None, int | None]:
    row = history.execute(
        """SELECT close, timestamp FROM historical_candles WHERE symbol = ? AND timeframe_seconds = 60
           ORDER BY timestamp DESC LIMIT 1""", (symbol,)).fetchone()
    return (row[0], row[1]) if row else (None, None)


def picks_view(history: sqlite3.Connection, day: str | None = None, lots: dict[str, int] | None = None,
               run_id: int | None = None) -> dict:
    """One emission -- ``run_id``, else ``day``'s latest, else the newest -- with a volatility-based size on every pick."""
    from . import agent_equity, index_trades
    ensure_schema(history)
    per_root = agent_equity.lot_by_root(lots or {})
    columns = "SELECT run_id, day, emitted_at, model_version, candidates, source FROM agent_runs"
    if run_id is not None:
        run = history.execute(f"{columns} WHERE run_id = ?", (run_id,)).fetchone()
    elif day:
        run = history.execute(f"{columns} WHERE day = ? ORDER BY run_id DESC LIMIT 1", (day,)).fetchone()
    else:
        run = history.execute(f"{columns} ORDER BY run_id DESC LIMIT 1").fetchone()
    if not run:
        return {"run": None, "ce": [], "pe": [], "index": []}
    run_id, run_day, emitted, version, candidates, source = run
    out = {"run": {"run_id": run_id, "day": run_day, "emitted_at": emitted, "model_version": version,
                   "candidates": candidates, "source": source}, "ce": [], "pe": []}
    for side, rank, symbol, underlying, strike, expiry, entry, sc, drivers in history.execute(
            """SELECT side, rank, symbol, underlying, strike, expiry, entry_price, score, drivers FROM agent_picks
               WHERE run_id = ? ORDER BY side DESC, rank""", (run_id,)):
        last, last_ts = _latest_price(history, symbol)
        labels = history.execute(
            "SELECT r1, r2, r3, y, max_ret, min_ret, features FROM agent_candidates WHERE day = ? AND symbol = ?",
            (run_day, symbol)).fetchone()
        item = {"rank": rank, "symbol": symbol, "underlying": underlying, "strike": strike, "expiry": expiry,
                "entry_price": entry, "score": sc, "drivers": json.loads(drivers or "[]"),
                "last_price": last, "last_at": datetime.fromtimestamp(last_ts, IST).strftime("%d %b %H:%M") if last_ts else None,
                "since_entry_pct": round((last / entry - 1) * 100, 2) if last and entry else None,
                "r1": labels[0] if labels else None, "r2": labels[1] if labels else None, "r3": labels[2] if labels else None,
                "max_ret": labels[4] if labels else None, "min_ret": labels[5] if labels else None}
        lot = (lots or {}).get(symbol) or per_root.get(underlying)
        item["size"] = agent_equity.size_pick(
            {"entry_price": entry, "features": json.loads(labels[6] or "{}") if labels else {}, "strike": strike,
             "expiry": expiry, "side": side, "day": run_day}, lot)
        out["ce" if side > 0 else "pe"].append(item)
    out["index"] = index_trades.view(history, run_id)
    for trade in out["index"]:
        if trade.get("symbol"):
            lot = trade.get("lot") or per_root.get(trade["underlying"])
            vol = agent_equity.daily_premium_vol(trade["entry_price"], trade.get("spot"), trade["strike"], trade["expiry"],
                                                 trade["side"], run_day)
            n = agent_equity.index_lots(trade["entry_price"], trade.get("premium_stop"), lot or 0, vol)
            trade["size"] = {"lots": n, "lot_size": lot, "capital": round(n * (lot or 0) * trade["entry_price"]),
                             "risk_at_stop": round(n * (lot or 0) * (trade["entry_price"] - trade["premium_stop"]))
                             if trade.get("premium_stop") is not None else None}
    return out


def performance(history: sqlite3.Connection, days: int = 30) -> dict:
    """Every scheduled/manual run's picks with outcomes, against their day's full candidate set."""
    ensure_schema(history)
    since = (datetime.now(IST).date() - timedelta(days=days)).isoformat()
    runs = history.execute(
        """SELECT r.run_id, r.day, r.model_version, r.source FROM agent_runs r
           JOIN (SELECT day, MAX(run_id) AS run_id FROM agent_runs WHERE day >= ? GROUP BY day) last ON last.run_id = r.run_id
           ORDER BY r.day""", (since,)).fetchall()
    per_run = []
    for run_id, day, version, source in runs:
        entry = {"day": day, "run_id": run_id, "model_version": version, "source": source}
        for side, key in ((1, "ce"), (-1, "pe")):
            picks = history.execute(
                """SELECT c.r1, c.r2, c.r3, c.y, c.y_pct, c.max_ret, c.min_ret FROM agent_picks p
                   JOIN agent_candidates c ON c.day = p.day AND c.symbol = p.symbol WHERE p.run_id = ? AND p.side = ?""",
                (run_id, side)).fetchall()
            universe = history.execute("SELECT AVG(y) FROM agent_candidates WHERE day = ? AND side = ? AND y IS NOT NULL",
                                       (day, side)).fetchone()[0]
            done = [p for p in picks if p[3] is not None]
            mean = lambda idx: (sum(p[idx] for p in picks if p[idx] is not None) / max(1, sum(1 for p in picks if p[idx] is not None))) \
                if any(p[idx] is not None for p in picks) else None
            entry[key] = {"picks": len(picks), "labelled": len(done), "r1": mean(0), "r2": mean(1), "r3": mean(2),
                          "y": mean(3), "max_ret": mean(5), "min_ret": mean(6),
                          "universe_y": universe, "excess": (mean(3) - universe) if done and universe is not None else None,
                          "top_quintile": (sum(1 for p in done if p[4] >= 0.8) / len(done)) if done else None}
        per_run.append(entry)
    def agg(key, field):
        values = [r[key][field] for r in per_run if r[key].get(field) is not None]
        return round(sum(values) / len(values), 5) if values else None
    summary = {side: {field: agg(side, field) for field in ("r1", "r2", "r3", "y", "excess", "top_quintile", "max_ret", "min_ret")}
               for side in ("ce", "pe")}
    basket, level = [], 1.0
    for r in per_run:
        rets = [r[k]["y"] for k in ("ce", "pe") if r[k].get("y") is not None]
        if rets:
            level *= 1 + sum(rets) / len(rets)
            basket.append({"day": r["day"], "level": round(level, 4)})
    from . import index_trades
    return {"since": since, "runs": per_run, "summary": summary, "basket": basket,
            "index": index_trades.performance(history, since)}


def models_view(history: sqlite3.Connection, limit: int = 30) -> dict:
    ensure_schema(history)
    rows = history.execute(
        """SELECT version, trained_at, config, train_from, train_to, samples, weights, metrics, status, note
           FROM agent_models ORDER BY version DESC LIMIT ?""", (limit,)).fetchall()
    models = []
    for version, trained, config, first, last, samples, weights, metrics, status, note in rows:
        w = json.loads(weights or "{}")
        models.append({"version": version, "trained_at": trained, "config": json.loads(config), "train_from": first,
                       "train_to": last, "samples": samples, "status": status, "note": note,
                       "metrics": json.loads(metrics or "{}"),
                       "weights": dict(zip(w.get("features", []), [round(x, 4) for x in w.get("weights", [])])) if not w.get("rule") else {"rule": w.get("features")}})
    from . import index_trades
    return {"models": models, "configs": list(CONFIGS), "index_models": index_trades.models_view(history),
            "index_configs": list(index_trades.CONFIGS), "constants": {
        "as_of": AS_OF.strftime("%H:%M"), "top_n": TOP_N, "horizons": HORIZONS, "min_premium": MIN_PREMIUM,
        "min_turnover": MIN_TURNOVER, "max_moneyness": MAX_MONEYNESS, "max_per_underlying": 1, "one_side_per_underlying": True,
        "validation_days": VALIDATION_DAYS, "promote_margin": PROMOTE_MARGIN}}


def pending_symbols(history: sqlite3.Connection, today: str, lookback_days: int = 12) -> tuple[str | None, list[str]]:
    """Unlabelled candidates still trading: (earliest day, symbols) whose forward bars to complete."""
    ensure_schema(history)
    since = (date.fromisoformat(today) - timedelta(days=lookback_days)).isoformat()
    rows = history.execute(
        "SELECT day, symbol FROM agent_candidates WHERE y IS NULL AND day >= ? AND expiry >= ?", (since, today)).fetchall()
    if not rows:
        return None, []
    return min(day for day, _s in rows), sorted({symbol for _d, symbol in rows})


def learn(history: sqlite3.Connection) -> dict:
    """The nightly half of the loop: label what has closed, then re-judge the models."""
    from . import index_trades
    return {"labelled": relabel(history), "train": train(history), "index": index_trades.train(history)}


def watch_symbols(database_path: str, today: str | None = None, lookback_days: int = 7) -> set[str]:
    """Contracts the agent has recommended recently and that have not expired: the engine subscribes
    them (analysis-only) so their prices stay live and their minute bars keep landing for 1-3 days,
    even after the strike drifts out of the day's ATM ladder."""
    today = today or datetime.now(IST).date().isoformat()
    since = (date.fromisoformat(today) - timedelta(days=lookback_days)).isoformat()
    try:
        connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True, timeout=10)
    except sqlite3.Error:
        return set()
    out: set[str] = set()
    try:
        for table in ("agent_picks", "agent_index_trades"):
            try:
                rows = connection.execute(f"SELECT DISTINCT symbol FROM {table} WHERE day >= ? AND symbol IS NOT NULL", (since,))
            except sqlite3.OperationalError:
                continue
            for (symbol,) in rows:
                parsed = parse_option(symbol)
                if parsed and parsed["expiry"] >= today:
                    out.add(symbol)
    finally:
        connection.close()
    return out



def hold_window(day: str) -> tuple[str, int]:
    """(the session the 1-3 day hold ends on, sessions elapsed since ``day`` up to today, capped at 3)."""
    ends = third_session_after(day, [])
    today = datetime.now(IST).date()
    cursor, elapsed = date.fromisoformat(day), 0
    while cursor < today and elapsed < 3:
        cursor = market_calendar.next_trading_day(cursor)
        if cursor <= today:
            elapsed += 1
    return ends, elapsed


def runs_view(history: sqlite3.Connection, days: int = 30) -> list[dict]:
    """Every emission of the last ``days``: when, which model, how far into its hold, and how it has done."""
    ensure_schema(history)
    since = (datetime.now(IST).date() - timedelta(days=days)).isoformat()
    today = datetime.now(IST).date().isoformat()
    out = []
    for run_id, day, emitted, version, candidates, source in history.execute(
            """SELECT run_id, day, emitted_at, model_version, candidates, source FROM agent_runs
               WHERE day >= ? ORDER BY day DESC, run_id DESC""", (since,)):
        ends, elapsed = hold_window(day)
        row = {"run_id": run_id, "day": day, "emitted_at": emitted, "model_version": version, "candidates": candidates,
               "source": source, "hold_until": ends, "sessions_elapsed": elapsed, "open": ends >= today}
        for side, key in ((1, "ce"), (-1, "pe")):
            got = history.execute(
                """SELECT COUNT(*), AVG(c.y), COUNT(c.y), AVG(c.r1) FROM agent_picks p
                   LEFT JOIN agent_candidates c ON c.day = p.day AND c.symbol = p.symbol
                   WHERE p.run_id = ? AND p.side = ?""", (run_id, side)).fetchone()
            row[key] = {"picks": got[0], "mean_1_3d": got[1], "labelled": got[2], "mean_d1": got[3]}
        out.append(row)
    return out


def active_view(history: sqlite3.Connection, lots: dict[str, int] | None = None) -> dict:
    """Every pick still inside its 1-3 session hold, across emissions (the latest run of each day)."""
    ensure_schema(history)
    today = datetime.now(IST).date().isoformat()
    since = (datetime.now(IST).date() - timedelta(days=10)).isoformat()
    latest = history.execute(
        """SELECT MAX(run_id), day FROM agent_runs WHERE day >= ? GROUP BY day ORDER BY day DESC""", (since,)).fetchall()
    lists = []
    for run_id, day in latest:
        ends, elapsed = hold_window(day)
        if ends < today:
            continue
        view = picks_view(history, lots=lots, run_id=run_id)
        lists.append({"run": view["run"], "hold_until": ends, "sessions_elapsed": elapsed,
                      "ce": view["ce"], "pe": view["pe"], "index": [t for t in view["index"] if t.get("symbol")]})
    return {"today": today, "lists": lists}


# ---------------------------------------------------------------------------
# Agent vs the actual top performers
# ---------------------------------------------------------------------------

HORIZONS_VIEW = {"y": "mean of D+1..D+3 closes", "r1": "D+1 close", "r2": "D+2 close", "r3": "D+3 close",
                 "live": "latest price"}


def _outcomes(history: sqlite3.Connection, day: str, side: int, horizon: str) -> list[dict]:
    """Every candidate of ``day``/``side`` with its return on ``horizon`` ("live": entry -> latest stored price).

    When a stored horizon is not labelled yet (a list still in its hold), the latest price stands in and the
    row says so -- "so far", never passed off as final.
    """
    rows = history.execute(
        """SELECT symbol, underlying, strike, expiry, entry_price, r1, r2, r3, y FROM agent_candidates
           WHERE day = ? AND side = ?""", (day, side)).fetchall()
    out = []
    for symbol, underlying, strike, expiry, entry, r1, r2, r3, y in rows:
        stored = {"r1": r1, "r2": r2, "r3": r3, "y": y}.get(horizon)
        final = stored is not None
        value = stored
        if value is None and entry:
            last, _ = _latest_price(history, symbol)
            value = (last / entry - 1) if last else None
        if value is not None:
            out.append({"symbol": symbol, "underlying": underlying, "strike": strike, "expiry": expiry,
                        "entry_price": entry, "ret": value, "final": final})
    out.sort(key=lambda r: -r["ret"])
    for rank, row in enumerate(out, start=1):
        row["rank"] = rank
        row["percentile"] = 1 - (rank - 0.5) / len(out)
    return out


def compare_view(history: sqlite3.Connection, run_id: int | None = None, horizon: str = "y", top: int = TOP_N) -> dict:
    """One list against the actual top ``top`` performers among the same day's candidates, per side."""
    ensure_schema(history)
    columns = "SELECT run_id, day, source FROM agent_runs"
    run = (history.execute(f"{columns} WHERE run_id = ?", (run_id,)).fetchone() if run_id is not None
           else history.execute(f"{columns} ORDER BY run_id DESC LIMIT 1").fetchone())
    if not run:
        return {"run": None}
    run_id, day, source = run
    out = {"run": {"run_id": run_id, "day": day, "source": source}, "horizon": horizon,
           "horizon_label": HORIZONS_VIEW.get(horizon, horizon), "top": top}
    for side, key in ((1, "ce"), (-1, "pe")):
        universe = _outcomes(history, day, side, horizon)
        by_symbol = {r["symbol"]: r for r in universe}
        picked = [s for (s,) in history.execute(
            "SELECT symbol FROM agent_picks WHERE run_id = ? AND side = ? ORDER BY rank", (run_id, side))]
        picks = [by_symbol[s] | {"picked": True} for s in picked if s in by_symbol]
        best = [r | {"picked": r["symbol"] in picked} for r in universe[:top]]
        out[key] = {"universe": len(universe), "picks": picks, "actual_top": best,
                    **_compare_metrics(universe, picks, best)}
    # Index trades: the chosen contract against the best call and put of that index on the day.
    from . import index_trades
    index_trades.ensure_schema(history)
    both = {side: _outcomes(history, day, side, horizon) for side in (1, -1)}
    out["index"] = []
    for underlying, side, symbol in history.execute(
            "SELECT underlying, side, symbol FROM agent_index_trades WHERE run_id = ?", (run_id,)):
        pool = {s: [r for r in both[s] if r["underlying"] == underlying] for s in (1, -1)}
        chosen = next((r for r in pool.get(side, []) if r["symbol"] == symbol), None) if side else None
        within = pool.get(side, []) if side else []
        out["index"].append({
            "underlying": underlying, "side": side, "symbol": symbol,
            "ret": chosen["ret"] if chosen else None,
            "rank": (within.index(chosen) + 1) if chosen else None, "of": len(within),
            "best_ce": pool[1][0] if pool[1] else None, "best_pe": pool[-1][0] if pool[-1] else None,
        })
    return out


def _compare_metrics(universe: list[dict], picks: list[dict], best: list[dict]) -> dict:
    if not universe or not picks:
        return {"overlap": 0, "picks_mean": None, "top_mean": None, "universe_mean": None, "universe_median": None,
                "capture": None, "picks_percentile": None, "top_quintile_hits": 0, "final": False}
    mean = lambda rows: sum(r["ret"] for r in rows) / len(rows)
    picks_mean, top_mean, universe_mean = mean(picks), mean(best), mean(universe)
    spread = top_mean - universe_mean
    return {
        "overlap": sum(1 for r in picks if r["rank"] <= len(best)),
        "picks_mean": picks_mean, "top_mean": top_mean, "universe_mean": universe_mean,
        "universe_median": universe[len(universe) // 2]["ret"],
        "capture": (picks_mean - universe_mean) / spread if spread > 1e-9 else None,
        "picks_percentile": sum(r["percentile"] for r in picks) / len(picks),
        "top_quintile_hits": sum(1 for r in picks if r["percentile"] >= 0.8),
        "final": all(r["final"] for r in picks + best),
    }


def compare_summary(history: sqlite3.Connection, days: int = 60, horizon: str = "y") -> dict:
    """Every list's comparison in one table, and the averages over the lists whose outcome is final."""
    ensure_schema(history)
    since = (datetime.now(IST).date() - timedelta(days=days)).isoformat()
    latest = history.execute(
        "SELECT MAX(run_id), day FROM agent_runs WHERE day >= ? GROUP BY day ORDER BY day", (since,)).fetchall()
    rows, finals = [], defaultdict(list)
    for run_id, day in latest:
        view = compare_view(history, run_id, horizon)
        row = {"run_id": run_id, "day": day, "source": view["run"]["source"]}
        for key in ("ce", "pe"):
            metrics = {k: v for k, v in view[key].items() if k not in ("picks", "actual_top")}
            row[key] = metrics
            if metrics.get("final") and metrics.get("picks_mean") is not None:
                finals[key].append(metrics)
        rows.append(row)
    def avg(items, field):
        values = [m[field] for m in items if m.get(field) is not None]
        return sum(values) / len(values) if values else None
    summary = {key: {"lists": len(items), "overlap": avg(items, "overlap"), "picks_percentile": avg(items, "picks_percentile"),
                     "capture": avg(items, "capture"), "picks_mean": avg(items, "picks_mean"), "top_mean": avg(items, "top_mean"),
                     "universe_mean": avg(items, "universe_mean")}
               for key, items in finals.items()}
    return {"horizon": horizon, "horizon_label": HORIZONS_VIEW.get(horizon, horizon), "rows": rows, "summary": summary}
