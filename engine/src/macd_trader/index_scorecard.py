"""Index side scorecard: which indices to trade at 14:45, CE or PE, from conditions that held up for years.

Why a scorecard and not a fitted model. On 5,063 index-days (NIFTY / BANKNIFTY / SENSEX from 2021,
MIDCPNIFTY from 2022; research/index_model_20261012) no regression or boosted-tree side model beat
"always CE" walk-forward: each one learned the recent drift (the 31-day ridge bought puts, five-year
fits bought calls). A handful of short-horizon conditions did lift the win rate of the lane's own plan
in every year: the last hour's direction, the hourly RSI above 70 for calls, the gap, today's move in
ATRs. Puts against an oversold daily or an overbought hourly chart were the worst trade there was.

**Training** is the selection: a condition counts for a side only if, in EVERY complete year of
history (from ``FIRST_YEAR``), the side won more often with the condition than without. It is rerun
nightly on the stored history, so a condition that stops working drops out. Selected the strict way
-- each year using only earlier years -- the scorecard beat the trend logic in 2024, 2025 and 2026,
and always-CE in 2025 and 2026 (not in the 2024 bull year).

**Decision.** net = (selected CE conditions true) - (selected PE conditions true). CE when net >=
``MIN_NET``; PE when net <= -``MIN_NET`` and no veto; otherwise no trade.

**History.** ``agent_index_days`` holds one row per index per session: the day's OHLC, the 14:45
features and, three sessions later, the lane's plan outcome for both sides (stop 1 ATR, target 1.5
ATR, third-session close). Seeded from Fyers 1-min history (2021 ->); extended nightly from the
stored minute bars with the same code that computes the live features.
"""
from __future__ import annotations

import json
import math
import sqlite3
from datetime import UTC, date, datetime, timedelta

from zoneinfo import ZoneInfo

from .universe import INDEX_SPOTS

IST = ZoneInfo("Asia/Kolkata")

AS_OF_SECONDS = 14 * 3600 + 45 * 60          # 14:45 IST, seconds after midnight
SESSION_OPEN_SECONDS = 9 * 3600 + 15 * 60
IST_OFFSET = 19800
FIRST_YEAR = 2022
MIN_YEAR_ROWS = 400
MIN_CONDITION_ROWS = 20
MIN_NET = 2
ATR_SESSIONS = 14
STOP_ATR, TARGET_ATR, HOLD_SESSIONS = 1.0, 1.5, 3
# Conditions are SELECTED on a symmetric 1.0 / 1.0 ATR outcome, a cleaner read of "was the side right",
# and TRADED on the lane's 1.5 / 1.0 plan. Selected this way the scorecard won 50% / 54% of trades
# in 2025 / 2026 out of sample (+0.13R / +0.18R); selected on the 1.5 / 1.0 outcome itself, 48% / 48%.
SELECT_TARGET_ATR, SELECT_STOP_ATR = 1.0, 1.0
HOURLY_SESSIONS = 25

SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_index_days (
  underlying TEXT NOT NULL, day TEXT NOT NULL,
  open REAL, high REAL, low REAL, close REAL,
  features TEXT, ce_path TEXT, ce_r REAL, pe_path TEXT, pe_r REAL,
  ce_sel REAL, pe_sel REAL,
  source TEXT, updated_at TEXT,
  PRIMARY KEY (underlying, day));
"""

# name -> (favours CE, favours PE), each a predicate on the feature dict
CONDITIONS = {
    "last_hour": (lambda f: f["last_hour"] > 0, lambda f: f["last_hour"] < 0),
    "trend_h": (lambda f: f["trend_h"] == 1, lambda f: f["trend_h"] == -1),
    "trend_d": (lambda f: f["trend_d"] == 1, lambda f: f["trend_d"] == -1),
    "trend_w": (lambda f: f["trend_w"] == 1, lambda f: f["trend_w"] == -1),
    "move_follow": (lambda f: f["move_atr"] > 0.5, lambda f: f["move_atr"] < -0.5),
    "move_fade": (lambda f: f["move_atr"] < -0.5, lambda f: f["move_atr"] > 0.5),
    "rsi_h_follow": (lambda f: f["rsi_h"] > 70, lambda f: f["rsi_h"] < 30),
    "rsi_h_fade": (lambda f: f["rsi_h"] < 30, lambda f: f["rsi_h"] > 70),
    "rsi_d_follow": (lambda f: f["rsi_d"] > 70, lambda f: f["rsi_d"] < 30),
    "rsi_d_fade": (lambda f: f["rsi_d"] < 30, lambda f: f["rsi_d"] > 70),
    "ret5_follow": (lambda f: f["u_ret_5d"] > 0, lambda f: f["u_ret_5d"] < 0),
    "ret20_follow": (lambda f: f["u_ret_20d"] > 0, lambda f: f["u_ret_20d"] < 0),
    "gap_follow": (lambda f: f["gap"] > 0.003, lambda f: f["gap"] < -0.003),
    "range20_high": (lambda f: f["u_range20_pos"] > 0.3, lambda f: f["u_range20_pos"] < -0.3),
}


def put_vetoed(f: dict) -> str | None:
    """Puts against an oversold daily or an overbought hourly chart: 29% / 34% win, worst in the study."""
    if f.get("rsi_d", 50) < 30:
        return f"daily RSI {f['rsi_d']:.0f} oversold"
    if f.get("rsi_h", 50) > 70:
        return f"hourly RSI {f['rsi_h']:.0f} overbought"
    return None


def ensure_schema(history: sqlite3.Connection) -> None:
    history.executescript(SCHEMA)


# ---------------------------------------------------------------------------
# Indicators (pure Python; same definitions as the research build)
# ---------------------------------------------------------------------------

def ema_series(values: list[float], span: int) -> list[float]:
    alpha, out = 2 / (span + 1), []
    for v in values:
        out.append(v if not out else alpha * v + (1 - alpha) * out[-1])
    return out


def rsi(values: list[float], n: int = 14) -> float:
    """Wilder RSI over ``values``, seeded with the mean of the first ``n`` changes."""
    if len(values) < n + 2:
        return 50.0
    d = [b - a for a, b in zip(values, values[1:])]
    au = sum(max(x, 0) for x in d[:n]) / n
    ad = sum(max(-x, 0) for x in d[:n]) / n
    for x in d[n:]:
        au = (au * (n - 1) + max(x, 0)) / n
        ad = (ad * (n - 1) + max(-x, 0)) / n
    return 100.0 if ad == 0 else 100 - 100 / (1 + au / ad)


def trend(values: list[float]) -> int:
    """+1 close > EMA20 > EMA50, -1 the mirror, else 0 (needs 55 values)."""
    if len(values) < 55:
        return 0
    e20, e50 = ema_series(values, 20)[-1], ema_series(values, 50)[-1]
    return 1 if values[-1] > e20 > e50 else -1 if values[-1] < e20 < e50 else 0


def weekly_closes(days: list[str], closes: list[float]) -> list[float]:
    """Last close of each Monday-Friday week, in order (the current week ends at its latest value)."""
    out, last_week = [], None
    for d, c in zip(days, closes):
        when = date.fromisoformat(d)
        week = when - timedelta(days=when.weekday())
        if week != last_week:
            out.append(c)
            last_week = week
        else:
            out[-1] = c
    return out


# ---------------------------------------------------------------------------
# Features and labels from minute bars
# ---------------------------------------------------------------------------

def _clock(ts: int) -> int:
    return (ts + IST_OFFSET) % 86400


def compute_features(day: str, today: list[tuple], daily: list[tuple], hourly_prior: list[tuple]) -> dict | None:
    """Features at 14:45 from today's minute bars (ts, o, h, l, c), the prior sessions' daily OHLC
    (day, o, h, l, c; oldest first) and the prior sessions' minute bars for hourly bars."""
    before = [b for b in today if _clock(b[0]) < AS_OF_SECONDS]
    if len(before) < 300 or _clock(before[-1][0]) < AS_OF_SECONDS - 120 or len(daily) < 60:
        return None
    p, o = before[-1][4], before[0][1]
    p1345 = next((b[4] for b in reversed(before) if _clock(b[0]) < AS_OF_SECONDS - 3600), p)
    closes = [d[4] for d in daily]
    highs, lows = [d[2] for d in daily], [d[3] for d in daily]
    prev = closes[-1]
    trs = [max(h - l, abs(h - pc), abs(l - pc)) for h, l, pc in zip(highs[1:], lows[1:], closes[:-1])]
    atr = sum(trs[-ATR_SESSIONS:]) / min(ATR_SESSIONS, len(trs))
    hist_c = closes + [p]
    hi_before, lo_before = max(b[2] for b in before), min(b[3] for b in before)
    h20, l20 = max(max(highs[-20:]), hi_before), min(min(lows[-20:]), lo_before)
    hourly: dict[tuple, float] = {}
    for b in list(hourly_prior) + before:
        key = ((b[0] + IST_OFFSET) // 86400, (_clock(b[0]) - SESSION_OPEN_SECONDS) // 3600)
        hourly[key] = b[4]
    hourly_closes = [hourly[k] for k in sorted(hourly)]
    days = [d[0] for d in daily] + [day]
    return {
        "spot": p, "atr": atr, "u_ret_1d": p / prev - 1, "u_ret_5d": p / closes[-5] - 1, "u_ret_20d": p / closes[-20] - 1,
        "u_range20_pos": (p - l20) / (h20 - l20) - 0.5 if h20 > l20 else 0.0,
        "gap": o / prev - 1, "last_hour": p / p1345 - 1, "move_atr": (p - prev) / atr if atr else 0.0,
        "rsi_d": rsi(hist_c[-120:]), "rsi_h": rsi(hourly_closes[-120:]),
        "trend_d": trend(hist_c), "trend_w": trend(weekly_closes(days, hist_c)), "trend_h": trend(hourly_closes),
    }


def plan_outcomes(features: dict, path: list[tuple], third_close: float,
                  target_atr: float = TARGET_ATR, stop_atr: float = STOP_ATR) -> dict:
    """A stop/target plan for both sides over ``path`` (minute bars from 14:45 to the third session's close),
    in R: the stop distance is one unit."""
    p, atr = features["spot"], features["atr"]
    out = {}
    for side, tag in ((1, "ce"), (-1, "pe")):
        stop, target = p - side * stop_atr * atr, p + side * target_atr * atr
        result = None
        for _ts, _o, h, l, _c in path:
            hit_stop = l <= stop if side > 0 else h >= stop
            hit_target = h >= target if side > 0 else l <= target
            if hit_stop:                     # both inside one minute counts as the stop
                result = ("stop", -1.0)
                break
            if hit_target:
                result = ("target", target_atr / stop_atr)
                break
        if result is None:
            result = ("time", side * (third_close / p - 1) / (stop_atr * atr / p))
        out[f"{tag}_path"], out[f"{tag}_r"] = result
    return out


# ---------------------------------------------------------------------------
# Stored history
# ---------------------------------------------------------------------------

def _session_minutes(history: sqlite3.Connection, symbol: str, day: str) -> list[tuple]:
    start = int(datetime.fromisoformat(f"{day}T09:15:00+05:30").timestamp())
    end = int(datetime.fromisoformat(f"{day}T15:30:00+05:30").timestamp())
    return history.execute(
        """SELECT timestamp, open, high, low, close FROM historical_candles
           WHERE symbol = ? AND timeframe_seconds = 60 AND timestamp >= ? AND timestamp < ? ORDER BY timestamp""",
        (symbol, start, end)).fetchall()


def _minute_days(history: sqlite3.Connection, symbol: str, since: str) -> list[str]:
    start = int(datetime.fromisoformat(f"{since}T00:00:00+05:30").timestamp())
    rows = history.execute(
        """SELECT DISTINCT date(timestamp, 'unixepoch', '+5 hours', '+30 minutes') FROM historical_candles
           WHERE symbol = ? AND timeframe_seconds = 60 AND timestamp >= ?""", (symbol, start)).fetchall()
    return sorted(r[0] for r in rows if r[0])


def daily_rows(history: sqlite3.Connection, root: str, before_day: str) -> list[tuple]:
    return history.execute(
        "SELECT day, open, high, low, close FROM agent_index_days WHERE underlying = ? AND day < ? ORDER BY day",
        (root, before_day)).fetchall()


def features_for(history: sqlite3.Connection, root: str, day: str) -> dict | None:
    """Today's 14:45 features for one index, from the stored daily history and minute bars."""
    ensure_schema(history)
    spot = INDEX_SPOTS[root]
    daily = daily_rows(history, root, day)
    prior_days = [d[0] for d in daily][-HOURLY_SESSIONS:]
    hourly_prior = [b for d in prior_days for b in _session_minutes(history, spot, d)]
    return compute_features(day, _session_minutes(history, spot, day), daily, hourly_prior)


def update_history(history: sqlite3.Connection, today: str | None = None) -> dict:
    """Append every completed session the minute store has, then label rows whose third session closed."""
    ensure_schema(history)
    now = datetime.now(IST)
    today = today or now.date().isoformat()
    # A session is complete after its close: the nightly run appends today too, the 14:47 emission does not.
    last_complete = today if (today == now.date().isoformat() and (now.hour, now.minute) >= (15, 35)) else None
    added = labelled = 0
    stamp = datetime.now(UTC).isoformat()
    for root, spot in INDEX_SPOTS.items():
        last = history.execute("SELECT MAX(day) FROM agent_index_days WHERE underlying = ?", (root,)).fetchone()[0]
        if last is None:
            continue                         # never seeded: nothing to extend
        for day in _minute_days(history, spot, last):
            if day <= last or (day >= today and day != last_complete):
                continue
            bars = _session_minutes(history, spot, day)
            if len(bars) < 300:
                continue
            feats = features_for(history, root, day)
            with history:
                history.execute(
                    """INSERT OR REPLACE INTO agent_index_days (underlying, day, open, high, low, close, features, source, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (root, day, bars[0][1], max(b[2] for b in bars), min(b[3] for b in bars), bars[-1][4],
                     json.dumps(feats) if feats else None, "minute-store", stamp))
            added += 1
        pending = history.execute(
            "SELECT day, features FROM agent_index_days WHERE underlying = ? AND ce_path IS NULL AND features IS NOT NULL ORDER BY day",
            (root,)).fetchall()
        days = [r[0] for r in history.execute("SELECT day FROM agent_index_days WHERE underlying = ? ORDER BY day", (root,))]
        for day, feats in pending:
            i = days.index(day)
            if i + HOLD_SESSIONS >= len(days):
                continue
            later = days[i + 1:i + 1 + HOLD_SESSIONS]
            path = [b for b in _session_minutes(history, spot, day) if _clock(b[0]) >= AS_OF_SECONDS]
            for d in later:
                path += _session_minutes(history, spot, d)
            if not path:
                continue
            third = history.execute("SELECT close FROM agent_index_days WHERE underlying = ? AND day = ?",
                                    (root, later[-1])).fetchone()[0]
            f = json.loads(feats)
            out = plan_outcomes(f, path, third)
            sel = plan_outcomes(f, path, third, SELECT_TARGET_ATR, SELECT_STOP_ATR)
            with history:
                history.execute("UPDATE agent_index_days SET ce_path=?, ce_r=?, pe_path=?, pe_r=?, ce_sel=?, pe_sel=?, updated_at=? WHERE underlying=? AND day=?",
                                (out["ce_path"], out["ce_r"], out["pe_path"], out["pe_r"], sel["ce_r"], sel["pe_r"], stamp, root, day))
            labelled += 1
    return {"added": added, "labelled": labelled}


# ---------------------------------------------------------------------------
# Selection ("training") and the decision
# ---------------------------------------------------------------------------

def select(history: sqlite3.Connection, current_year: int | None = None) -> dict:
    """Conditions whose win-rate lift for a side was positive in every complete year before ``current_year``."""
    ensure_schema(history)
    current_year = current_year or datetime.now(IST).year
    rows = [(int(d[:4]), json.loads(f), cr, pr) for d, f, cr, pr in history.execute(
        "SELECT day, features, ce_sel, pe_sel FROM agent_index_days WHERE features IS NOT NULL AND ce_sel IS NOT NULL")]
    years = sorted({y for y, *_ in rows if FIRST_YEAR <= y < current_year})
    years = [y for y in years if sum(1 for r in rows if r[0] == y) >= MIN_YEAR_ROWS]
    selected: dict[str, list[str]] = {"ce": [], "pe": []}
    lifts: dict[str, dict] = {}
    for name, (fav_ce, fav_pe) in CONDITIONS.items():
        for tag, fav, idx in (("ce", fav_ce, 2), ("pe", fav_pe, 3)):
            per_year = []
            for y in years:
                year_rows = [r for r in rows if r[0] == y]
                base = sum(1 for r in year_rows if r[idx] > 0) / len(year_rows)
                sub = [r for r in year_rows if fav(r[1])]
                per_year.append(round(sum(1 for r in sub if r[idx] > 0) / len(sub) - base, 4)
                                if len(sub) >= MIN_CONDITION_ROWS else None)
            lifts[f"{tag}:{name}"] = per_year
            if years and len(years) >= 2 and all(v is not None and v > 0 for v in per_year):
                selected[tag].append(name)
    return {"name": "scorecard", "ce": selected["ce"], "pe": selected["pe"], "min_net": MIN_NET,
            "years": years, "lifts": lifts, "rows": len(rows)}


def decide(features: dict | None, model: dict) -> dict:
    """Side (+1 CE, -1 PE, 0 none), the net score and the conditions that fired."""
    if not features:
        return {"side": 0, "net": None, "reason": "no 14:45 features (minute bars or daily history missing)", "drivers": []}
    bull = [n for n in model.get("ce", []) if CONDITIONS[n][0](features)]
    bear = [n for n in model.get("pe", []) if CONDITIONS[n][1](features)]
    net = len(bull) - len(bear)
    drivers = [[f"CE:{n}", 1] for n in bull] + [[f"PE:{n}", -1] for n in bear]
    need = model.get("min_net", MIN_NET)
    if net >= need:
        return {"side": 1, "net": net, "reason": "", "drivers": drivers}
    if net <= -need:
        veto = put_vetoed(features)
        if veto:
            return {"side": 0, "net": net, "reason": f"put vetoed: {veto}", "drivers": drivers}
        return {"side": -1, "net": net, "reason": "", "drivers": drivers}
    return {"side": 0, "net": net, "reason": f"scorecard net {net:+d} (needs {need:+d} either way)", "drivers": drivers}
