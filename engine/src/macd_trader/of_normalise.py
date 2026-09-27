"""Per-symbol normalisation of order-flow readings.

A delta of +1,200 means one thing on NIFTY futures and something entirely
different on a Rs 2 option that trades 300 lots a minute. Raw order-flow
numbers are therefore not comparable across the desk's universe, and any
surface that ranks or colours symbols by raw delta is ranking them by size,
not by behaviour. This module converts a raw reading into a *symbol-relative*
one using the per-symbol baselines the research pass persisted to
``of_symbol_baseline`` (view ``of_symbol_baseline_latest``) in the tick store.

Three doctrines govern everything here.

**1. The aggressor is inferred, never reported.** NSE publishes price,
quantity, time and the best bid/ask. It does not publish who crossed the
spread. So volume, trade count and RVOL are *measured*; delta, normalised
delta and the composite score are *inferred* and inherit the classifier's
error. That split is published structurally as ``basis`` so a consumer cannot
render an inference with the authority of a measurement.

**2. No baseline, no number.** A symbol with no baseline row, too small a
sample behind it, or a reading the classifier could barely assign sides to
yields ``None`` — never a zero, never a default. A default would read as
"average", which is a claim about the symbol that nobody measured.

**3. The composite score describes, it does not predict.** The same research
pass that produced these baselines measured the forward information content of
exactly these ingredients and found it *negative* and economically worthless:
per-symbol rank IC of delta/volume against the next bar's tradeable return was
-0.0437 (t -17.1). ``flow_score`` says "this symbol's inferred flow is unusually
one-sided **for this symbol**" and nothing more. A high positive score is not a
buy signal; if anything the measured sign points the other way.
"""
from __future__ import annotations

import math
import sqlite3
import time
from dataclasses import dataclass
from datetime import date, datetime
from threading import RLock
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

# --- table / schema ---------------------------------------------------------
BASELINE_VIEW = "of_symbol_baseline_latest"
BASELINE_TABLE = "of_symbol_baseline"
# Columns this module reads. A narrower list than the 29 the research pass
# writes: naming them explicitly means a future column addition cannot silently
# shift a positional read.
BASELINE_COLUMNS = (
    "symbol", "as_of", "instrument", "sessions", "bars",
    "volume_median", "volume_p25", "volume_p75",
    "abs_delta_median", "nd_median", "nd_p25", "nd_p75",
    "trades_median", "delta_mean", "delta_sd", "ret_sd",
    "spread_bps_median", "price_median",
    "quote_share", "classified_share", "estimator",
)
# The classifier the baselines were built from. Anything else is a warning,
# not a fatal: a volume median survives a classifier change untouched, a delta
# scale does not.
CURRENT_ESTIMATOR = "quote_rule_zero_tick"

# --- hard floors: below these the answer is None, not a number --------------
# A baseline needs this many active minute bars behind it before it describes
# a symbol rather than a handful of minutes. 11 of the 1,049 rows written by
# the first research pass sit under it (thin far-OTM options).
MIN_BASELINE_BARS = 30
# A baseline older than this is describing a different market regime.
MAX_BASELINE_AGE_DAYS = 30
# A reading needs this many prints before its buy/sell split means anything.
MIN_READING_TRADES = 5
# ...and this much of the window actually covered by the print deque.
MIN_COVERED_SECONDS = 20.0
# A reading older than this is from a dead tape; publishing a "current" flow
# number for it would be a lie of tense.
MAX_READING_AGE_SECONDS = 3600.0
# Below this share of the window's volume classified, the directional outputs
# are withheld. RVOL survives — it rests on volume, which is published.
MIN_READING_CLASSIFIED_SHARE = 0.50

# --- grade thresholds -------------------------------------------------------
GOOD_BASELINE_BARS = 200
POOR_BASELINE_BARS = 90
GOOD_CLASSIFIED_SHARE = 0.90
POOR_CLASSIFIED_SHARE = 0.70
GOOD_QUOTE_SHARE = 0.70
POOR_QUOTE_SHARE = 0.30
STALE_BASELINE_DAYS = 5
POOR_BASELINE_DAYS = 15
GOOD_READING_TRADES = 15
STALE_READING_SECONDS = 300.0

# --- scoring ----------------------------------------------------------------
DEFAULT_WINDOW_SECONDS = 60.0
# IQR -> sigma for a normal distribution. Used so an IQR-scaled score and an
# sd-scaled z are on the same axis before they are averaged.
IQR_TO_SIGMA = 1.349
# A z of this size maps to tanh(1) = 0.76, i.e. a score of ~76.
SCORE_SCALE = 2.0
FLOW_SCORE_RANGE = (-100.0, 100.0)
# Ingredient weights. Equal by construction: the research found no basis for
# preferring one over the other, and inventing a weighting would be inventing
# a result.
INGREDIENT_WEIGHTS = {"nd_score": 1.0, "delta_z": 1.0}
# RVOL at or above this attenuates nothing. Below it the score is scaled down
# pro rata: a symbol trading at a third of its usual volume cannot have a
# conviction reading. RVOL can only ever attenuate the score, never inflate it.
FULL_PARTICIPATION_RVOL = 1.0
# Floors that stop a degenerate baseline dispersion from exploding a score.
MIN_ND_SIGMA = 0.05
MIN_DELTA_SD = 1.0

# What rests on published exchange data, and what rests on an inference.
BASIS = {
    "measured": ["volume_per_minute", "trades_per_minute", "rvol", "trade_rvol"],
    "inferred": ["delta_per_minute", "nd", "nd_score", "delta_z", "flow_score"],
}


@dataclass(frozen=True)
class Baseline:
    """One symbol's normalisation baseline, as persisted by the research pass."""

    symbol: str
    as_of: str
    instrument: str | None
    sessions: int | None
    bars: int | None
    volume_median: float | None
    volume_p25: float | None
    volume_p75: float | None
    abs_delta_median: float | None
    nd_median: float | None
    nd_p25: float | None
    nd_p75: float | None
    trades_median: float | None
    delta_mean: float | None
    delta_sd: float | None
    ret_sd: float | None
    spread_bps_median: float | None
    price_median: float | None
    quote_share: float | None
    classified_share: float | None
    estimator: str | None

    @property
    def nd_sigma(self) -> float | None:
        """Robust sigma of per-minute delta/volume, from the IQR."""
        if self.nd_p25 is None or self.nd_p75 is None:
            return None
        iqr = self.nd_p75 - self.nd_p25
        if iqr <= 0:
            return None
        return max(iqr / IQR_TO_SIGMA, MIN_ND_SIGMA)

    def age_days(self, today: date | None = None) -> int | None:
        if today is None:
            today = datetime.now(IST).date()
        try:
            as_of = date.fromisoformat(self.as_of)
        except (TypeError, ValueError):
            return None
        return (today - as_of).days


@dataclass(frozen=True)
class Reading:
    """A raw order-flow reading over one trailing window, in tape units.

    Built from classified prints, so it conserves the tape the same way
    ``FlowState`` does: ``buy_volume + sell_volume + unclassified_volume ==
    volume``.
    """

    symbol: str
    end_ts: float
    window_seconds: float
    covered_seconds: float
    partial: bool
    volume: float
    buy_volume: float
    sell_volume: float
    unclassified_volume: float
    trades: int
    quote_trades: int

    @property
    def delta(self) -> float:
        return self.buy_volume - self.sell_volume

    @property
    def per_minute(self) -> float:
        """Factor converting a window total into a per-minute rate."""
        return 60.0 / self.covered_seconds if self.covered_seconds > 0 else 0.0

    @property
    def volume_per_minute(self) -> float:
        return self.volume * self.per_minute

    @property
    def delta_per_minute(self) -> float:
        return self.delta * self.per_minute

    @property
    def trades_per_minute(self) -> float:
        return self.trades * self.per_minute

    @property
    def nd(self) -> float | None:
        """Delta as a share of window volume, -1..+1. None when unmeasured."""
        return (self.delta / self.volume) if self.volume > 0 else None

    @property
    def classified_share(self) -> float | None:
        if self.volume <= 0:
            return None
        return (self.volume - self.unclassified_volume) / self.volume

    @property
    def quote_share(self) -> float | None:
        return (self.quote_trades / self.trades) if self.trades else None


def reading_from_prints(
    symbol: str,
    prints,
    window_seconds: float = DEFAULT_WINDOW_SECONDS,
    end_ts: float | None = None,
) -> Reading | None:
    """Fold a bounded print deque into one trailing-window reading.

    ``prints`` is any iterable of objects carrying ``timestamp``, ``size``,
    ``side`` and ``method`` — ``orderflow.Print`` satisfies it, and so does a
    test stub. This module deliberately does not import ``orderflow``: the
    dependency runs the other way.

    The window is anchored to the LAST PRINT, not to the wall clock, so the
    reading is a real measurement of a real minute of tape. Its age against the
    wall clock is reported separately by :func:`normalise` and the caller can
    reject it.

    Returns None when the deque cannot cover enough of the window to support a
    rate. That is the honest answer: a rate extrapolated from four seconds of
    tape is a fabrication.
    """
    rows = [row for row in prints if row is not None]
    if not rows:
        return None
    if end_ts is None:
        end_ts = rows[-1].timestamp
    window_start = end_ts - window_seconds
    oldest = rows[0].timestamp
    window = [row for row in rows if window_start <= row.timestamp <= end_ts]
    if not window:
        return None
    if oldest > window_start:
        # The deque is bounded (and a session has a start), so it may not reach
        # back a full window. Measure what it does reach and say so.
        partial = True
        covered = min(end_ts - oldest, window_seconds)
    else:
        partial = False
        covered = window_seconds
    if covered < MIN_COVERED_SECONDS:
        return None

    volume = buy = sell = unclassified = 0.0
    quote_trades = 0
    for row in window:
        size = float(row.size)
        volume += size
        if row.side > 0:
            buy += size
        elif row.side < 0:
            sell += size
        else:
            unclassified += size
        if getattr(row, "method", None) == "quote":
            quote_trades += 1
    return Reading(
        symbol=symbol,
        end_ts=float(end_ts),
        window_seconds=float(window_seconds),
        covered_seconds=float(covered),
        partial=partial,
        volume=volume,
        buy_volume=buy,
        sell_volume=sell,
        unclassified_volume=unclassified,
        trades=len(window),
        quote_trades=quote_trades,
    )


class BaselineStore:
    """Read-only, cached access to ``of_symbol_baseline_latest``.

    The whole view is about a thousand rows, so it is loaded in one pass and
    kept for ``ttl_seconds``; the daily research refresh is then picked up
    without restarting the desk. Every failure mode — missing file, missing
    table, locked database, unreadable row — resolves to "no baselines", which
    resolves to ``None`` at the surface. A normalisation layer must never be
    able to take down the live flow snapshot.
    """

    def __init__(self, db_path: str | None = None, ttl_seconds: float = 600.0):
        self._db_path = db_path
        self._ttl = ttl_seconds
        self._lock = RLock()
        self._rows: dict[str, Baseline] = {}
        self._loaded_at: float | None = None
        self._error: str | None = None

    @property
    def db_path(self) -> str:
        if self._db_path is None:
            from .config import settings
            self._db_path = settings.tick_database_path
        return self._db_path

    @property
    def error(self) -> str | None:
        return self._error

    def invalidate(self) -> None:
        with self._lock:
            self._loaded_at = None

    def _fetch(self) -> dict[str, Baseline]:
        columns = ", ".join(BASELINE_COLUMNS)
        # mode=ro (not immutable=1): the tick-store writer is live in the same
        # container, and immutable would pin us to a stale page cache.
        uri = f"file:{self.db_path}?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=2.0)
        try:
            try:
                cursor = connection.execute(f"SELECT {columns} FROM {BASELINE_VIEW}")
            except sqlite3.Error:
                # The view is additive too; fall back to the table's newest
                # dated row per symbol rather than failing outright.
                cursor = connection.execute(
                    f"SELECT {columns} FROM {BASELINE_TABLE} b WHERE b.as_of = "
                    f"(SELECT MAX(as_of) FROM {BASELINE_TABLE} x WHERE x.symbol = b.symbol)"
                )
            rows: dict[str, Baseline] = {}
            for row in cursor:
                baseline = Baseline(*row)
                if baseline.symbol:
                    rows[baseline.symbol] = baseline
            return rows
        finally:
            connection.close()

    def _ensure(self) -> None:
        now = time.time()
        with self._lock:
            if self._loaded_at is not None and now - self._loaded_at < self._ttl:
                return
            try:
                self._rows = self._fetch()
                self._error = None
            except Exception as exc:  # missing db, missing table, locked, torn
                self._rows = {}
                self._error = f"{type(exc).__name__}: {exc}"
            self._loaded_at = now

    def get(self, symbol: str) -> Baseline | None:
        self._ensure()
        with self._lock:
            return self._rows.get(symbol)

    def __len__(self) -> int:
        self._ensure()
        with self._lock:
            return len(self._rows)


# One store per process. Lazily loaded, so importing this module costs nothing
# and a desk with no tick database simply never gets a baseline.
_default_store: BaselineStore | None = None


def default_store() -> BaselineStore:
    global _default_store
    if _default_store is None:
        _default_store = BaselineStore()
    return _default_store


def _grade(reasons: list[tuple[str, str]]) -> str:
    severities = {severity for severity, _ in reasons}
    if "low" in severities:
        return "low"
    if "medium" in severities:
        return "medium"
    return "high"


def normalise(
    reading: Reading,
    baseline: Baseline | None,
    now: float | None = None,
    today: date | None = None,
) -> dict | None:
    """Turn one raw reading into a symbol-relative one. Pure — no I/O.

    Returns None when nothing survives the floors. Individual outputs are None
    when their own ingredient is missing; the caller can render what is present
    and omit the rest.
    """
    if baseline is None:
        return None
    if baseline.bars is None or baseline.bars < MIN_BASELINE_BARS:
        return None
    age_days = baseline.age_days(today)
    if age_days is not None and age_days > MAX_BASELINE_AGE_DAYS:
        return None
    if reading.trades < MIN_READING_TRADES or reading.volume <= 0:
        return None
    if now is None:
        now = time.time()
    age_seconds = max(0.0, now - reading.end_ts)
    if age_seconds > MAX_READING_AGE_SECONDS:
        return None

    classified_share = reading.classified_share
    quote_share = reading.quote_share
    directional_ok = (
        classified_share is not None and classified_share >= MIN_READING_CLASSIFIED_SHARE
    )

    # --- measured: volume, trade count -------------------------------------
    rvol = None
    if baseline.volume_median:
        rvol = reading.volume_per_minute / baseline.volume_median
    trade_rvol = None
    if baseline.trades_median:
        trade_rvol = reading.trades_per_minute / baseline.trades_median

    # --- inferred: everything with an aggressor tag in it ------------------
    # The raw reading stays raw; it is published beside its own
    # classified_share. What gets withheld on poor classification is the
    # NORMALISED output, which is the thing a reader would otherwise compare
    # across symbols as if it were like-for-like.
    nd = reading.nd if directional_ok else None
    nd_score = None
    nd_sigma = baseline.nd_sigma
    if nd is not None and nd_sigma is not None and baseline.nd_median is not None:
        nd_score = (nd - baseline.nd_median) / nd_sigma
    # NAME COLLISION, deliberate but worth stating: research/of_validation.py
    # also publishes a `delta_z`, and research/README_of.md quotes an IC for it
    # (-0.0319, t -12.4). That one is z against a ROLLING PRIOR-BAR mean/sd;
    # this one is z against the symbol's STORED DAILY baseline. Both are
    # per-minute (of_validation buckets at 60s, and delta_per_minute matches),
    # so they are on the same scale -- but they are different estimators, and
    # the README's coefficient does not transfer to this field. Do not read one
    # as evidence about the other.
    delta_z = None
    if directional_ok and baseline.delta_sd is not None and baseline.delta_mean is not None:
        sd = max(baseline.delta_sd, MIN_DELTA_SD)
        delta_z = (reading.delta_per_minute - baseline.delta_mean) / sd

    ingredients: list[dict] = []
    weighted = 0.0
    weight_total = 0.0
    for name, value in (("nd_score", nd_score), ("delta_z", delta_z)):
        if value is None:
            continue
        weight = INGREDIENT_WEIGHTS[name]
        weighted += weight * value
        weight_total += weight
        ingredients.append({
            "name": name,
            "value": round(value, 4),
            "weight": weight,
            "basis": "inferred",
        })

    flow_score = None
    participation = None
    combined_z = None
    if weight_total > 0:
        combined_z = weighted / weight_total
        # RVOL only ever attenuates. When it is unmeasurable the score is left
        # UNATTENUATED -- but `participation` stays None, because publishing the
        # 1.0 used internally would be indistinguishable from a symbol measured
        # to be trading at exactly its usual size. The attenuation actually
        # applied is a separate, internal number; the published field answers
        # "what did you measure", not "what did you multiply by".
        if rvol is not None:
            participation = min(1.0, max(0.0, rvol / FULL_PARTICIPATION_RVOL))
        attenuation = 1.0 if participation is None else participation
        flow_score = FLOW_SCORE_RANGE[1] * math.tanh(combined_z / SCORE_SCALE) * attenuation
        # An ADDITIVE decomposition of combined_z, not a restatement of the
        # score. The previous `contribution` was flow_score * (weight/total),
        # which with equal weights is just flow_score/n for every ingredient --
        # it varied with neither the ingredient's own z nor its sign, so a
        # strongly negative nd_score and a flat delta_z reported identically.
        # These sum to combined_z exactly (asserted in the tests).
        for item in ingredients:
            item["z_contribution"] = round(item["weight"] * item["value"] / weight_total, 4)

    if rvol is None and trade_rvol is None and flow_score is None:
        # Nothing measurable and nothing inferable: say nothing.
        return None

    # --- confidence ---------------------------------------------------------
    reasons: list[tuple[str, str]] = []
    if baseline.bars < POOR_BASELINE_BARS:
        reasons.append(("low", "baseline_sample_small"))
    elif baseline.bars < GOOD_BASELINE_BARS:
        reasons.append(("medium", "baseline_sample_modest"))
    if baseline.estimator != CURRENT_ESTIMATOR:
        # A volume median is untouched by a classifier change; a delta scale is
        # not. Worth a downgrade and a name, not a blackout.
        reasons.append(("medium", "baseline_estimator_legacy"))
    if age_days is not None:
        if age_days > POOR_BASELINE_DAYS:
            reasons.append(("low", "baseline_stale"))
        elif age_days > STALE_BASELINE_DAYS:
            reasons.append(("medium", "baseline_stale"))
    if baseline.classified_share is not None and baseline.classified_share < GOOD_CLASSIFIED_SHARE:
        reasons.append(("medium", "baseline_classification_partial"))
    if baseline.quote_share is not None and baseline.quote_share < GOOD_QUOTE_SHARE:
        reasons.append(("medium", "baseline_quote_coverage_partial"))
    if classified_share is None or classified_share < POOR_CLASSIFIED_SHARE:
        reasons.append(("low", "reading_classification_poor"))
    elif classified_share < GOOD_CLASSIFIED_SHARE:
        reasons.append(("medium", "reading_classification_partial"))
    if quote_share is None or quote_share < POOR_QUOTE_SHARE:
        reasons.append(("medium", "reading_quote_coverage_poor"))
    if reading.trades < GOOD_READING_TRADES:
        reasons.append(("medium", "reading_few_prints"))
    if reading.partial:
        reasons.append(("medium", "window_partial"))
    if age_seconds > STALE_READING_SECONDS:
        reasons.append(("medium", "reading_stale"))

    ordered: list[str] = []
    for _, name in reasons:
        if name not in ordered:
            ordered.append(name)

    return {
        "symbol": reading.symbol,
        "window_seconds": reading.window_seconds,
        "window_end": int(reading.end_ts),
        "window_covered_seconds": round(reading.covered_seconds, 1),
        "window_partial": reading.partial,
        "reading_age_seconds": round(age_seconds, 1),
        "stale": age_seconds > STALE_READING_SECONDS,
        "reading": {
            "volume": reading.volume,
            "volume_per_minute": round(reading.volume_per_minute, 1),
            "buy_volume": reading.buy_volume,
            "sell_volume": reading.sell_volume,
            "unclassified_volume": reading.unclassified_volume,
            "delta": reading.delta,
            "delta_per_minute": round(reading.delta_per_minute, 1),
            "trades": reading.trades,
            "trades_per_minute": round(reading.trades_per_minute, 1),
            "nd": round(reading.nd, 4) if reading.nd is not None else None,
        },
        "rvol": round(rvol, 3) if rvol is not None else None,
        "trade_rvol": round(trade_rvol, 3) if trade_rvol is not None else None,
        "nd_score": round(nd_score, 3) if nd_score is not None else None,
        "delta_z": round(delta_z, 3) if delta_z is not None else None,
        "combined_z": round(combined_z, 3) if combined_z is not None else None,
        "participation": round(participation, 3) if participation is not None else None,
        "flow_score": round(flow_score, 1) if flow_score is not None else None,
        "flow_score_range": list(FLOW_SCORE_RANGE),
        "ingredients": ingredients,
        "basis": {"measured": list(BASIS["measured"]), "inferred": list(BASIS["inferred"])},
        "baseline": {
            "as_of": baseline.as_of,
            "age_days": age_days,
            "instrument": baseline.instrument,
            "estimator": baseline.estimator,
            "estimator_current": baseline.estimator == CURRENT_ESTIMATOR,
            "volume_median": baseline.volume_median,
            "trades_median": baseline.trades_median,
            "nd_median": round(baseline.nd_median, 4) if baseline.nd_median is not None else None,
            "nd_sigma": round(nd_sigma, 4) if nd_sigma is not None else None,
            "delta_mean": round(baseline.delta_mean, 1) if baseline.delta_mean is not None else None,
            "delta_sd": round(baseline.delta_sd, 1) if baseline.delta_sd is not None else None,
            "ret_sd": baseline.ret_sd,
            "spread_bps_median": baseline.spread_bps_median,
        },
        "confidence": {
            # How much history the comparison rests on.
            "baseline_bars": baseline.bars,
            "baseline_sessions": baseline.sessions,
            "baseline_classified_share": baseline.classified_share,
            "baseline_quote_share": baseline.quote_share,
            # ...and how well the classifier did on THIS reading.
            "reading_classified_share": round(classified_share, 3) if classified_share is not None else None,
            "reading_quote_share": round(quote_share, 3) if quote_share is not None else None,
            "reading_trades": reading.trades,
            "directional_withheld": not directional_ok,
            "grade": _grade(reasons),
            "grade_reasons": ordered,
        },
    }


def normalised_flow(
    symbol: str,
    prints,
    window_seconds: float = DEFAULT_WINDOW_SECONDS,
    store: BaselineStore | None = None,
    now: float | None = None,
    today: date | None = None,
) -> dict | None:
    """Baseline lookup + reading + normalisation, for one symbol. None-safe."""
    reading = reading_from_prints(symbol, prints, window_seconds=window_seconds)
    if reading is None:
        return None
    baselines = store if store is not None else default_store()
    try:
        baseline = baselines.get(symbol)
    except Exception:
        return None
    return normalise(reading, baseline, now=now, today=today)
