"""The MACD lane's entry gate: score every option signal, size it by its score, spend cash best-first.

Why: the September walk-forward of the old app's 1,410 entries (refit daily on closed trades
only) kept +Rs 37.1 L of +Rs 18.7 L by skipping the entries its logistic scorer ranked lowest --
the skipped ones lost Rs 18.3 L at a 31% win rate against 42% for the kept. The edge is modest
(AUC 0.57) and every input is side-neutral: the option's side, which looked far stronger, was
September's falling market and is left out. The strongest inputs: longer to expiry, a cheaper
premium relative to spot, a stronger cross, many same-side signals together, NIFTY moving the
option's way, earlier in the session.

How:
  * ``build_features`` computes the study's features from what the engine holds at the signal
    (ticks, the strategy's committed MACD, the day's range) -- the same definitions as the study.
  * ``probability`` is the exported logistic model (entry_model.MODEL).
  * ``EntryGate`` routes signals: every scored signal is queued for ``macd_entry_rank_seconds`` and
    entered best-first, so when cash is short it goes to the strongest signals rather than to
    whichever crossed first. The score SIZES the entry, it no longer skips (owner, 26 Oct 2026, after
    ATHERENERG 1400 CE scored 0.441 against a 0.452 cut and went from 76.5 to 240): the signal's
    percentile among the day's scores maps linearly to ``macd_entry_min_size_fraction``..1 of the
    normal entry notional, never below one lot. ``cut()`` (the old skip-fraction quantile) is kept
    as a reference in the journal's threshold column. Every decision is journalled (research DB,
    ``macd_entry_scores``) with its features, its size fraction and the exact model fingerprint.

The deployed model is a static export. Journalling does not train it or update its weights;
there is no automatic nightly refit in this gate.
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import math
import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import market_calendar, sqlite_journal
from .entry_model import MODEL

IST = ZoneInfo("Asia/Kolkata")
NIFTY = "NSE:NIFTY50-INDEX"
STALE_SECONDS = 120.0
MODEL_SHA256 = hashlib.sha256(json.dumps(MODEL, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
JOURNAL_SCHEMA = """CREATE TABLE IF NOT EXISTS macd_entry_scores (
  at TEXT NOT NULL, day TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT, score REAL, threshold REAL,
  decision TEXT NOT NULL, signal_id TEXT, features TEXT, model_sha256 TEXT)"""


def probability(features: dict, model: dict = MODEL) -> float:
    z = model["bias"]
    for name, w, mean, std in zip(model["features"], model["weights"], model["mean"], model["std"]):
        value = features.get(name)
        x = 0.0 if value is None or not math.isfinite(value) else (value - mean) / (std or 1.0)
        z += w * max(-model["winsor"], min(model["winsor"], x))
    return 1 / (1 + math.exp(-max(-30.0, min(30.0, z))))


def threshold(skip_fraction: float, model: dict = MODEL) -> float:
    """The score below which the lowest ``skip_fraction`` of the study's entries fell."""
    table = {int(k): v for k, v in model["percentiles"].items()}
    key = min(table, key=lambda q: abs(q - skip_fraction * 100))
    return table[key]


HOLD_DAYS = float(MODEL.get("hold_days", 2.0))
EXPIRES_WITHIN_HOLD = "EXPIRES_WITHIN_HOLD"
# The cut is the skip fraction's quantile of the day's own scores once this many are in; before that, the
# model's training percentile. A fixed cut drifts with the features: the September model's skipped 12% on
# some days and 70% on others against a designed 30%.
MIN_DAY_SCORES = 20


def score_percentile(score: float, day_scores: list[float], model: dict = MODEL) -> float:
    """Where ``score`` ranks, 0..1: among the day's scores once MIN_DAY_SCORES are in, else on the model's
    training percentiles (the same two bases the old cut used)."""
    if len(day_scores) >= MIN_DAY_SCORES:
        return sum(1 for value in day_scores if value <= score) / len(day_scores)
    table = sorted((int(k) / 100.0, v) for k, v in model["percentiles"].items())
    if score <= table[0][1]:
        return table[0][0] * (score / table[0][1] if table[0][1] > 0 else 0.0)
    for (q0, v0), (q1, v1) in zip(table, table[1:]):
        if score <= v1:
            return q0 + (q1 - q0) * ((score - v0) / (v1 - v0) if v1 > v0 else 1.0)
    return 1.0


def size_fraction(percentile: float, minimum: float) -> float:
    """Linear in rank: the weakest signal buys ``minimum`` of the normal size, the strongest all of it."""
    minimum = min(1.0, max(0.0, minimum))
    return minimum + (1.0 - minimum) * min(1.0, max(0.0, percentile))


def theta_share(days_to_expiry: float, hold_days: float) -> float:
    """Share of remaining time value lost over the hold under sqrt-time decay; 1 if it expires within the hold."""
    days = max(days_to_expiry, 1e-3)
    return 1.0 - math.sqrt(max(days - hold_days, 0.0) / days)


def quantile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = fraction * (len(ordered) - 1)
    low = int(math.floor(position))
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _ret(a, b):
    return (a / b - 1) if a and b else 0.0


def build_features(*, price: float, side: int, strike: float, expiry: str, now: datetime, is_index: bool,
                   spot: float | None, spot_prev_close: float | None, spot_open: float | None,
                   spot_close_5d: float | None, spot_vwap: float | None,
                   nifty: float | None, nifty_prev_close: float | None,
                   day_open: float | None, day_high: float | None, day_low: float | None,
                   option_prev_close: float | None, volume_today: float, prior_day_volume: float,
                   macd_history: list[float], macd_preview: float, crowd_same: int, crowd_other: int) -> dict:
    """The study's entry features (scratch features.py), from live state. Directional ones are signed by side."""
    local = now.astimezone(IST)
    opened = datetime.combine(local.date(), datetime.min.time().replace(hour=9, minute=15), IST)
    expiry_close = datetime.combine(date.fromisoformat(expiry[:10]), datetime.min.time().replace(hour=15, minute=30), IST)
    dte_frac = max(0.0, (expiry_close - local).total_seconds() / 86400)
    run, depth = 0, 0.0
    for value in reversed(macd_history):
        if value > 0:
            break
        run += 1
        depth = min(depth, value)
    hi = max(day_high or price, price)
    lo = min(day_low or price, price)
    return {
        "is_index": 1.0 if is_index else 0.0,
        "minutes_since_open": (local - opened).total_seconds() / 60,
        "dte": float((date.fromisoformat(expiry[:10]) - local.date()).days),
        # Calendar days to the 15:30 expiry, fractional, and the time value that decays over the lane's expected
        # hold. Raw dte throttled the gate as each expiry approached; matched to the hold it does not (entry_model v2).
        "dte_frac": dte_frac,
        "theta_share": theta_share(dte_frac, HOLD_DAYS),
        "log_premium": math.log(price),
        "moneyness": side * math.log(spot / strike) if spot and strike else 0.0,
        "premium_pct_spot": price / spot if spot else 0.0,
        "o_ret_open": _ret(price, day_open),
        "o_ret_prev": _ret(price, option_prev_close),
        "o_range_pos": (price - lo) / (hi - lo) if hi > lo else 0.5,
        "o_vol_ratio": math.log1p(max(0.0, volume_today) / (max(0.0, prior_day_volume) + 1)),
        "u_ret_open": side * _ret(spot, spot_open),
        "u_ret_prev": side * _ret(spot, spot_prev_close),
        "u_ret_5d": side * _ret(spot, spot_close_5d),
        "u_vwap": side * _ret(spot, spot_vwap),
        "nifty_ret_prev": side * _ret(nifty, nifty_prev_close),
        "crowd_same_side": float(crowd_same),
        "crowd_other_side": float(crowd_other),
        "macd_preview_pct": macd_preview / price,
        "macd_slope_pct": (macd_preview - (macd_history[-1] if macd_history else 0.0)) / price,
        "dip_bars": float(run),
        "dip_depth_pct": depth / price,
        "bars_seen": float(len(macd_history)),
    }


class DayTracker:
    """Each symbol's open, high and low for the current IST session, from ticks."""

    def __init__(self) -> None:
        self._day: dict[str, date] = {}
        self._stats: dict[str, list[float]] = {}

    def observe(self, symbol: str, price: float, at: datetime) -> None:
        day = at.astimezone(IST).date()
        stats = self._stats.get(symbol)
        if stats is None or self._day.get(symbol) != day:
            self._stats[symbol] = [price, price, price]
            self._day[symbol] = day
        else:
            if price > stats[1]:
                stats[1] = price
            if price < stats[2]:
                stats[2] = price

    def get(self, symbol: str) -> tuple[float | None, float | None, float | None]:
        stats = self._stats.get(symbol)
        return (stats[0], stats[1], stats[2]) if stats else (None, None, None)


@dataclass
class _Queued:
    score: float
    signal: object
    features: dict
    queued_at: float = field(default_factory=time.monotonic)


def load_reference(database_path: str, symbols: list[str], today: date) -> tuple[dict, dict]:
    """(previous session's volume per symbol, close five sessions back per symbol) from stored minute bars."""
    prior = market_calendar.previous_trading_day(today)
    back5 = today
    for _ in range(5):
        back5 = market_calendar.previous_trading_day(back5)
    def session(day):
        start = int(datetime.combine(day, datetime.min.time().replace(hour=9, minute=15), IST).timestamp())
        return start, start + 6 * 3600 + 15 * 60
    volumes, closes = {}, {}
    try:
        connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True, timeout=30)
    except sqlite3.Error:
        return volumes, closes
    try:
        wanted = set(symbols)
        start, end = session(prior)
        for symbol, volume in connection.execute(
                """SELECT symbol, SUM(volume) FROM historical_candles WHERE timeframe_seconds = 60
                   AND timestamp >= ? AND timestamp < ? GROUP BY symbol""", (start, end)):
            if symbol in wanted:
                volumes[symbol] = float(volume or 0)
        start, end = session(back5)
        for symbol, close in connection.execute(
                """SELECT symbol, close FROM historical_candles WHERE timeframe_seconds = 60
                   AND timestamp >= ? AND timestamp < ? ORDER BY timestamp""", (start, end)):
            if symbol in wanted:
                closes[symbol] = float(close)
    except sqlite3.Error:
        pass
    finally:
        connection.close()
    return volumes, closes


class EntryGate:
    def __init__(self, settings, database_path: str):
        self.settings = settings
        self.database_path = database_path
        self.days = DayTracker()
        self.prior_volume: dict[str, float] = {}
        self.close_5d: dict[str, float] = {}
        self._crowd: dict[tuple[int, str], int] = defaultdict(int)
        self._queue: list[_Queued] = []
        self._journal: list[tuple] = []
        self.counts: dict[str, int] = defaultdict(int)
        self.recent: list[dict] = []
        self._day_scores: dict[str, list[float]] = {}
        self.error: str | None = None

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.settings, "macd_entry_filter", True))

    def cut(self) -> float:
        fraction = float(getattr(self.settings, "macd_entry_skip_fraction", 0.3))
        today = self._day_scores.get(datetime.now(IST).date().isoformat(), [])
        if len(today) >= MIN_DAY_SCORES:
            return quantile(today, fraction)
        return threshold(fraction)

    async def refresh_reference(self, symbols: list[str]) -> None:
        self.prior_volume, self.close_5d = await asyncio.to_thread(
            load_reference, self.database_path, symbols, datetime.now(IST).date())

    def status(self) -> dict:
        return {"enabled": self.enabled, "skip_fraction": getattr(self.settings, "macd_entry_skip_fraction", 0.3),
                "threshold": round(self.cut(), 4), "cut_basis": "day" if len(self._day_scores.get(datetime.now(IST).date().isoformat(), [])) >= MIN_DAY_SCORES else "model",
                "hold_days": HOLD_DAYS, "queued": len(self._queue), "counts": dict(self.counts),
                "recent": self.recent[-12:], "error": self.error,
                "model": {"features": len(MODEL["features"]), **MODEL["trained_on"], "walk_forward": MODEL["walk_forward"],
                          "update_mode": "static_export", "artifact_sha256": MODEL_SHA256}}

    def admit(self, signal, features: dict) -> bool:
        """Score one BUY signal. True when it is queued for entry."""
        score = probability(features)
        day = datetime.now(IST).date().isoformat()
        scores = self._day_scores.setdefault(day, [])
        for stale in [key for key in self._day_scores if key != day]:
            del self._day_scores[stale]
        scores.append(score)
        self.counts["scored"] += 1
        if features.get("dte_frac") is not None and features["dte_frac"] < HOLD_DAYS:
            # Expires before the lane's expected return period is over: the option would be held into
            # expiry-day decay or flattened by the expiry sweep before the move it was bought for.
            self._decide(signal, features, score, EXPIRES_WITHIN_HOLD)
            return False
        signal.size_fraction = round(size_fraction(
            score_percentile(score, scores),
            float(getattr(self.settings, "macd_entry_min_size_fraction", 0.25))), 4)
        self._queue.append(_Queued(score, signal, features))
        return True

    def crowd(self, bucket: int, side: str) -> tuple[int, int]:
        """Count this signal into its 30-minute bucket; return (same side, other side) including it."""
        self._crowd[(bucket, side)] += 1
        other = "PE" if side == "CE" else "CE"
        return self._crowd[(bucket, side)], self._crowd.get((bucket, other), 0)

    def _decide(self, signal, features: dict, score: float, decision: str) -> None:
        self.counts[decision] += 1
        now = datetime.now(UTC)
        side = "CE" if signal.symbol.endswith("CE") else "PE"
        fraction = getattr(signal, "size_fraction", 1.0)
        row = {"at": now.isoformat(), "symbol": signal.symbol, "score": round(score, 4), "decision": decision,
               "size_fraction": fraction}
        self.recent.append(row)
        del self.recent[:-50]
        self._journal.append((now.isoformat(), now.astimezone(IST).date().isoformat(), signal.symbol, side,
                              round(score, 5), round(self.cut(), 5), decision, getattr(signal, "signal_id", None),
                              json.dumps({**{k: round(v, 6) for k, v in features.items()}, "size_fraction": fraction}),
                              MODEL_SHA256))

    async def run(self, get_execution, get_portfolio, validate_signal=None) -> None:
        """Drain the queue best-first every ``macd_entry_rank_seconds``; flush the journal.

        Callables, not objects: the engine rebuilds its execution manager on a reconfigure.
        """
        while True:
            await asyncio.sleep(1.0)
            try:
                await self._drain(get_execution(), get_portfolio(), validate_signal)
                if self._journal:
                    rows, self._journal = self._journal, []
                    await asyncio.to_thread(self._write, rows)
            except Exception as exc:  # noqa: BLE001 -- the gate must never take the lane down
                self.error = f"{type(exc).__name__}: {exc}"[:300]

    async def _drain(self, execution, portfolio, validate_signal=None) -> None:
        if not self._queue:
            return
        wait = float(getattr(self.settings, "macd_entry_rank_seconds", 15.0))
        if time.monotonic() - min(item.queued_at for item in self._queue) < wait:
            return
        batch, self._queue = sorted(self._queue, key=lambda item: -item.score), []
        for item in batch:
            symbol = item.signal.symbol
            if time.monotonic() - item.queued_at > STALE_SECONDS:
                self._decide(item.signal, item.features, item.score, "STALE")
                continue
            held_before = symbol in portfolio.positions
            if not held_before and validate_signal is not None:
                try:
                    rejection = validate_signal(item.signal)
                    if inspect.isawaitable(rejection):
                        rejection = await rejection
                except Exception as exc:  # noqa: BLE001 -- an unknown entry state must not submit
                    self.error = f"validation {symbol}: {type(exc).__name__}: {exc}"[:300]
                    rejection = "VALIDATION_ERROR"
                if rejection:
                    self._decide(item.signal, item.features, item.score, rejection)
                    continue
            await execution.on_signal(item.signal)
            entered = not held_before and symbol in portfolio.positions
            self._decide(item.signal, item.features, item.score,
                         "TAKEN" if entered else ("ALREADY_HELD" if held_before else "NOT_FILLED"))

    def _write(self, rows: list[tuple]) -> None:
        connection = sqlite3.connect(self.database_path, timeout=30)
        try:
            sqlite_journal.configure(connection, self.database_path)
            connection.execute(JOURNAL_SCHEMA)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(macd_entry_scores)")}
            if "model_sha256" not in columns:
                connection.execute("ALTER TABLE macd_entry_scores ADD COLUMN model_sha256 TEXT")
            with connection:
                connection.executemany(
                    """INSERT INTO macd_entry_scores
                       (at,day,symbol,side,score,threshold,decision,signal_id,features,model_sha256)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""", rows,
                )
        finally:
            connection.close()
