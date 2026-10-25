"""M6 -- Fusion & Trade Selection.

Combines M2 (flow_score), M3 (GEX regime), M4 (sector20 RS + lead-lag), M5
(timing) into one candidate list and, subject to M7's risk gate, a handful
of trade tickets. Per the spec's own framing (doctrine #2: "The system's
default answer is NO TRADE. It must justify acting, never justify
waiting.") this is expected to emit ZERO tickets on most evaluations -- see
the empirical skip-rate this module itself prints on every run.

CADENCE, and why the four inputs are joined the way they are: features_flow
and sector_rs/leadlag are computed once per SESSION (EOD); regime and timing
are computed per 30-MINUTE bar intraday. M6 evaluates at each timing bar
(the intraday trigger) and joins the most recent EOD readings available
BEFORE that bar's own day -- never that day's own not-yet-computed EOD
figures.

CANDIDATE FILTER, direction-first, evaluated as SIX NAMED LEGS. Every symbol
with a timing bar goes through them and is journaled to
`candidate_evaluations` with each leg's own verdict -- see migration 006 for
why (the previous code dropped non-survivors with a bare `continue` before a
Candidate object existed, so `tickets` could explain why four things failed
the conviction gate and nothing at all about why thousands failed the
filter, which is the only question the lane actually needs answered):

  flow_present   features_flow has ANY prior-session row for this symbol
  flow_fresh     that row is <= FLOW_MAX_AGE_SESSIONS trading sessions old
                 AND was built from >= FLOW_MIN_INGREDIENTS non-NULL
                 ingredients. NEITHER HALF OF THIS LEG EXISTED before the
                 2026-08-27 review. The LATERAL joins had no lower time bound
                 at all, so with features_flow frozen at 2026-07-28 and the
                 cycle daemon evaluating every 30 minutes, live bars were
                 joining a month-old flow score and treating it as
                 "yesterday's EOD reading". Nothing in the code objected;
                 only the accident of regime being NULL kept tickets from
                 being emitted on it. An input with no stated shelf life is
                 not a feature, it is a leak. The ingredient count is the
                 second half because ~42% of the historical candidate pool
                 was a saturated +/-100 flow score derived from a SINGLE
                 ingredient -- and the one ingredient most often surviving
                 alone (O/S) carries no direction at all.
  flow_strength  flow_score >= +CE_FLOW_MIN_ABS for the CE case, or
                 <= -PE_FLOW_MIN_ABS for the PE case                     (M2)
                 SIGNED, not absolute: the two sides are separate selectors
                 with their own bars, so a bearish score can never arm the
                 bullish case and be vetoed later by a different leg.
  sector_rs      sign(rs_z20) == direction AND |rs_z20| >= SECTOR_RS_MIN_ABS_Z,
                 with the RS row <= RS_MAX_AGE_SESSIONS sessions old     (M4)
  regime         a GEX regime bucket is a market-STRUCTURE reading, not a
                 view-direction one -- negative/neutral dealer gamma is
                 momentum-friendly regardless of which way the candidate
                 itself points (positive gamma dampens realized moves,
                 negative amplifies them). Permits STRONG_NEG/NEG/NEUTRAL,
                 and the bucket must be <= REGIME_MAX_AGE_BARS bars old.
  timing         timing_state == 'IGNITION' AND timing_score >= TIMING_MIN_SCORE
                 AND the break went THIS SIDE'S WAY -- M5 fires IGNITION on
                 "price beyond value area" and records which side in
                 va_position (> 0 above, < 0 below). Without that check a CE
                 candidate could be confirmed by a breakdown; on 2026-08-28
                 three of the four IGNITION bars were below value. An
                 unrecorded va_position fails, since the direction of the
                 break is the entire content of this leg.

Legs are evaluated in that order and SHORT-CIRCUIT: once one fails, the rest
are journaled NULL rather than FALSE, so "did not pass" is never confused
with "was never asked".

CONVICTION -- each component rescaled to [0,100] BEFORE the spec's own
0.35/0.20/0.20/0.15/0.10 weights, direction-aligned so a confirming bearish
reading scores the same as a confirming bullish one:
  flow      := |flow_score|                                    (already 0-100)
  sectorRS  := (clip(direction * rs_z20, -3, 3) + 3) / 6 * 100
  timing    := timing_score                                    (already 0-100)
  regime    := {STRONG_NEG:100, NEG:75, NEUTRAL:50, POS:25, STRONG_POS:0}[bucket]
               (direction-agnostic -- a structural reading, not a view)
  leadlag   := 50 if best_lag <= 0 else 50 + min(50, corr*100)
  conviction = 0.35*flow + 0.20*sectorRS + 0.20*timing + 0.15*regime + 0.10*leadlag

Conviction is computed for EVERY symbol whose components exist, not only for
filter survivors -- a "shadow conviction" on the full cross-section. That is
what research/cross_section_ic.py needs: correlating a component with forward
return INSIDE its own filter restricts its range to near-nothing and
guarantees an uninformative coefficient, which is what attribution_runs was
doing.

GATE: conviction >= 85 AND rank <= 3 (by conviction, within this bar's
surviving candidate set) AND M7.risk_check() allows. Every candidate that
clears the FILTER is additionally recorded in `tickets` with emitted=false
and a gated_reason when it fails the GATE or risk check.

INSTRUMENT/PRICING: Vanguard trades the underlying's current ATM option
(CE for bullish, PE for bearish) -- see the scope note in fusion/m7_risk.py.
entry is the option's own last traded premium; stop/targets are
percent-of-premium, using the 15/20/10 (stop/trail-activation/trail)
configuration this SAME research programme found materially outperforms a
naive 30% stop on real August 2026 option data (paired t=10.4) -- target1 is
the trail-activation level (+20%), target2 a further +50% extension.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import psycopg2
import psycopg2.extras

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fusion.m7_risk import load_risk_state, risk_check  # noqa: E402
from model.nonlinear_selector import (  # noqa: E402
    load_selector_model, prediction_rows,
)
from model.session_clock import available_at_sql, timely_decision  # noqa: E402

DEFAULT_DSN = ""

FLOW_MIN_ABS = 60.0
SECTOR_RS_MIN_ABS_Z = 1.0

# ── PER-SIDE THRESHOLDS (CE / PE), 2026-08-28 ─────────────────────────────
# The CE and PE cases are now selected by two explicit branches rather than
# one branch reading `abs(flow_score)`, so each side can be tuned on its own
# evidence. They DEFAULT to the shared values above, so splitting the tree
# changes nothing about what is emitted until a threshold is deliberately
# moved -- the refactor and any retune stay separately attributable.
#
# WHY THE SIDES MAY NEED DIFFERENT BARS. Measured over 109,255 journaled
# evaluations to 2026-08-28, the bullish branch has NEVER passed sector_rs:
#
#     direction   evals    strength   sector_rs   regime   timing
#     bearish     68,035     2,963        552       18        0
#     bullish     41,220       115          0        0        0
#
# Bullish clears flow_strength 0.28% of the time against bearish at 4.4% -- a
# 16x gap that is a property of the SCORE, not of the market: M2's composite
# carries a structural short bias (on 2026-08-28, oi_state was long_unwind or
# short_buildup for 200 of 324 names against 23 long_buildup, and sector
# rs_z20 averaged -0.33 with 276 readings <= -1.0 against 143 >= +1.0). A
# single symmetric bar therefore is NOT symmetric in effect; it is a short-only
# filter wearing a two-sided threshold. Raising CE's sensitivity is a
# calibration decision for the desk, and these constants are where to make it.
CE_FLOW_MIN_ABS = FLOW_MIN_ABS
PE_FLOW_MIN_ABS = FLOW_MIN_ABS
CE_RS_MIN_ABS_Z = SECTOR_RS_MIN_ABS_Z
PE_RS_MIN_ABS_Z = SECTOR_RS_MIN_ABS_Z

# The side's own contract must be BEING ACCUMULATED, not merely implied by a
# view about the underlying. `long_buildup` is open interest rising while that
# side's own OI-weighted premium rises -- money coming in and paying up.
#
# The other three states are all reasons NOT to be in the contract:
#   long_unwind     OI falling, premium falling -- holders leaving, it decays
#   short_covering  OI falling, premium rising  -- a squeeze, not accumulation;
#                   the move is buying-to-close and ends when the shorts are out
#   short_buildup   OI rising, premium falling  -- being written into, i.e. the
#                   crowd is SELLING this contract to you
MOMENTUM_STATES = {"long_buildup"}
TIMING_MIN_SCORE = 70.0
REGIME_PERMITS = {"STRONG_NEG", "NEG", "NEUTRAL"}
REGIME_SCORE = {"STRONG_NEG": 100.0, "NEG": 75.0, "NEUTRAL": 50.0, "POS": 25.0, "STRONG_POS": 0.0}
WEIGHTS = {"flow": 0.30, "side_momentum": 0.20, "sector_rs": 0.15,
           "timing": 0.20, "regime": 0.10, "leadlag": 0.05}

# How each per-side OI/premium state scores as EVIDENCE, rather than as the
# pass/fail MOMENTUM_STATES test. Being written into is the worst case, worse
# than holders simply leaving; a squeeze is real buying but ends when the
# shorts are out, so it sits above neutral and well below accumulation.
SIDE_STATE_SCORE = {
    "long_buildup": 100.0,     # OI up, this side's premium up -- money paying up
    "short_covering": 55.0,    # OI down, premium up -- a squeeze, self-limiting
    "long_unwind": 25.0,       # OI down, premium down -- holders leaving
    "short_buildup": 0.0,      # OI up, premium down -- the crowd is writing it to you
}

# At least this many axes must be present before a conviction is comparable.
MIN_COMPONENTS = 4

# NO STRICT QUALIFYING BAR (owner directive, 2026-08-28). A conviction cutoff
# selects on a score whose relationship to FORWARD return is unmeasured -- and
# the scores above 80 are observed AFTER a large move, so a high bar
# preferentially buys moves that already happened. Until
# research/cross_section_ic.py shows the score predicts forward returns, the
# ranking (TOP_N_PER_BAR) does the selecting and this stays a floor that only
# removes the clearly unevidenced.
#
# CALIBRATED, not assumed. The old value was 85.0 and had NEVER been reached:
# zero of 109,409 scored evaluations cleared it, all-time max 81.8. A threshold
# no observation has ever met is not a selective filter, it is an off switch.
# Recalibrated against the live distribution once the axes above were weighed
# rather than gated -- see the retune note in the module docstring, and re-run
# research/cross_section_ic.py before moving it again.
CONVICTION_MIN = 50.0
TOP_N_PER_BAR = 3
# Paper-only floor: when M7 declines to size a position, take one lot anyway
# and mark the row `floored:` rather than skipping. See the M7 GATE OFF note in
# build_tickets -- the point is to generate observations to study, and a lane
# that emits nothing teaches nothing.
MIN_PAPER_LOTS = 1
STOP_PCT = 0.15
TARGET1_PCT = 0.20
TARGET2_PCT = 0.50
MIN_PREMIUM = 5.0

# ── Shelf lives. See the flow_fresh leg in the module docstring. ────────────
# Sessions, not calendar days: a Friday bar joining Thursday's EOD row is one
# session old whether or not a holiday sits in between, and a Monday bar
# joining the prior Friday is also one. Calendar days would make the same
# join look 1 day old midweek and 3 days old over a weekend, and a threshold
# tuned on one would silently mean something else on the other.
FLOW_MAX_AGE_SESSIONS = 3
RS_MAX_AGE_SESSIONS = 3
# Bars, at M5's own 30-minute cadence. M3 regime is written per bar, so
# anything older than two bars means the GEX feed skipped -- and a regime
# bucket describes dealer positioning right now, not an hour ago.
REGIME_MAX_AGE_BARS = 2
# Below this, a flow_score is one ingredient's opinion wearing a composite's
# clothes. The renormalizing weighted sum in m2_flow is correct arithmetic
# and still lets a single surviving ingredient saturate the score to +/-100.
FLOW_MIN_INGREDIENTS = 2

LEG_ORDER = ("flow_present", "flow_fresh", "flow_strength", "side_momentum",
             "sector_rs", "regime", "timing")

# Every threshold and weight that changes what this module decides, hashed so
# a journaled row can be traced to the configuration that produced it. Any
# retune therefore produces visibly different rows rather than quietly
# reinterpreting old ones.
def config_hash() -> str:
    payload = {
        "flow_min_abs": FLOW_MIN_ABS,
        "sector_rs_min_abs_z": SECTOR_RS_MIN_ABS_Z,
        # The per-side bars must be hashed too, or tuning CE alone would leave
        # the journal claiming the same configuration produced different rows
        # -- which is precisely what this hash exists to prevent.
        "momentum_states": sorted(MOMENTUM_STATES),
        "ce_flow_min_abs": CE_FLOW_MIN_ABS,
        "pe_flow_min_abs": PE_FLOW_MIN_ABS,
        "ce_rs_min_abs_z": CE_RS_MIN_ABS_Z,
        "pe_rs_min_abs_z": PE_RS_MIN_ABS_Z,
        # The timing leg now also tests the BREAK DIRECTION (va_position), so
        # rows produced before that check are not comparable with rows after.
        "timing_requires_break_direction": True,
        "timing_min_score": TIMING_MIN_SCORE,
        "regime_permits": sorted(REGIME_PERMITS),
        "regime_score": REGIME_SCORE,
        "weights": WEIGHTS,
        "conviction_min": CONVICTION_MIN,
        "m7_gate": "off-sizing-only",
        "min_paper_lots": MIN_PAPER_LOTS,
        "top_n_per_bar": TOP_N_PER_BAR,
        "flow_max_age_sessions": FLOW_MAX_AGE_SESSIONS,
        "rs_max_age_sessions": RS_MAX_AGE_SESSIONS,
        "regime_max_age_bars": REGIME_MAX_AGE_BARS,
        "flow_min_ingredients": FLOW_MIN_INGREDIENTS,
        "stop_pct": STOP_PCT,
        "target1_pct": TARGET1_PCT,
        "target2_pct": TARGET2_PCT,
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


@dataclass
class Candidate:
    symbol: str
    ts: datetime
    direction: str
    flow_score: float
    rs_z20: float
    sector20: str | None
    regime: str | None
    timing_score: float
    timing_state: str
    best_lag: int | None
    corr: float | None
    conviction: float
    components: dict


@dataclass
class Evaluation:
    """One symbol at one bar: every input as joined, every leg's verdict.

    `legs` maps a leg name to True (passed), False (failed) or None (never
    asked, because an earlier leg already ended the evaluation). The
    three-state distinction is the point -- a NULL is not a fail.
    """
    symbol: str
    ts: datetime
    sector20: str | None
    inputs: dict
    legs: dict
    first_failed_leg: str | None
    survived: bool
    direction: str | None
    conviction: float | None
    components: dict | None
    signed: dict

    def as_candidate(self) -> Candidate | None:
        if not self.survived:
            return None
        return Candidate(
            symbol=self.symbol, ts=self.ts, direction=self.direction,
            flow_score=float(self.inputs["flow_score"]),
            rs_z20=float(self.inputs["rs_z20"]), sector20=self.sector20,
            regime=self.inputs["regime"], timing_score=float(self.inputs["timing_score"]),
            timing_state=self.inputs["timing_state"], best_lag=self.inputs["best_lag"],
            corr=self.inputs["leadlag_corr"], conviction=self.conviction,
            components=self.components,
        )


# ── which bar to evaluate ───────────────────────────────────────────────────
LATEST_BAR_SQL = f"""
    SELECT max(ts) FROM timing
    WHERE EXTRACT(minute FROM ts AT TIME ZONE 'Asia/Kolkata') IN (15, 45)
      AND (ts AT TIME ZONE 'Asia/Kolkata')::time BETWEEN TIME '09:15' AND TIME '15:15'
      AND EXTRACT(isodow FROM ts AT TIME ZONE 'Asia/Kolkata') <= 5
      AND {available_at_sql('ts')} <= %(now)s
"""


def latest_evaluable_bar(connection, now: datetime | None = None) -> datetime | None:
    """The newest timing bar ON NSE'S OWN GRID.

    A plain `SELECT max(ts) FROM timing` is wrong here, and was: the table also
    holds bars on a second, 15-minute-offset grid (:00/:30) belonging to
    instruments that trade a different session. Those bars carry FIVE symbols
    where an NSE bar carries roughly 210, so `max(ts)` regularly picked a
    5-symbol phantom bar and M6 evaluated a universe 40x smaller than it
    appeared to. Every skip-rate and coverage number computed off that bar was
    measuring the wrong population.

    m5_timing now filters these out at write time, but rows written before that
    remain and are not deleted -- they are a real record of what the lane did.
    Selecting on the grid rather than deleting history keeps both facts intact.
    """
    with connection.cursor() as cursor:
        cursor.execute(LATEST_BAR_SQL, {"now": now or datetime.now(timezone.utc)})
        row = cursor.fetchone()
    return row[0] if row else None


# ── session calendar ────────────────────────────────────────────────────────
# Memoised per process. The calendar is a pure function of (day, lookback) over
# settled history, and a backfill replaying 500+ bars would otherwise issue 500+
# identical DISTINCT-date scans of `timing` against the shared database. Each
# CLI invocation is its own process, so this can never serve a stale calendar
# into a later session.
_CALENDAR_CACHE: dict[tuple[date, int], list[date]] = {}


def session_calendar(connection, day: date, lookback_days: int = 120) -> list[date]:
    """Trading sessions Vanguard has actually observed, oldest first, <= day.

    Derived from `timing`'s own bars rather than an exchange holiday list: a
    hardcoded calendar is a second source of truth that rots, and what matters
    for an age-in-sessions is which sessions this lane actually saw data for.
    """
    cached = _CALENDAR_CACHE.get((day, lookback_days))
    if cached is not None:
        return cached
    with connection.cursor() as cursor:
        cursor.execute(
            """SELECT DISTINCT date(ts AT TIME ZONE 'Asia/Kolkata') AS d
               FROM timing
               WHERE ts >= %(start)s AND ts < %(end)s
               ORDER BY 1""",
            {"start": day - timedelta(days=lookback_days), "end": day + timedelta(days=1)},
        )
        calendar = [row[0] for row in cursor.fetchall()]
    _CALENDAR_CACHE[(day, lookback_days)] = calendar
    return calendar


def _age_in_sessions(calendar: list[date], row_day: date | None, bar_day: date) -> int | None:
    """How many observed sessions separate `row_day` from `bar_day`.

    Returns None when either side is not on the observed calendar -- an
    unknowable age, which the freshness leg then treats as a fail rather than
    guessing. A row_day older than the calendar window is deliberately given
    the window length as a floor, so "older than we looked" is a large number
    and not a None that could read as merely unknown.
    """
    if row_day is None or not calendar:
        return None
    if row_day < calendar[0]:
        return len(calendar)
    try:
        bar_index = calendar.index(bar_day)
    except ValueError:
        bar_index = len(calendar) - 1
    older_or_equal = [i for i, d in enumerate(calendar) if d <= row_day]
    if not older_or_equal:
        return None
    return max(0, bar_index - older_or_equal[-1])


# ── the join ────────────────────────────────────────────────────────────────
EVALUATION_SQL = """
    SELECT tm.symbol, tm.timing_score, tm.timing_state, tm.rvol, tm.va_position,
           st.sector20,
           fl.flow_score, fl.ts AS flow_ts, fl.n_ingredients AS flow_n_ingredients,
           fl.ce_state, fl.pe_state,
           sr.rs_z20, sr.ts AS rs_ts,
           rg.regime, rg.gex_percentile, rg.ts AS regime_ts,
           ll.best_lag, ll.corr AS leadlag_corr
    FROM timing tm
    LEFT JOIN sector_taxonomy st ON st.symbol = tm.symbol
    LEFT JOIN LATERAL (
        SELECT f.flow_score, f.ts, f.n_ingredients, f.ce_state, f.pe_state
        FROM features_flow f
        WHERE f.symbol = tm.symbol AND f.ts::date < %(day)s
        ORDER BY f.ts DESC LIMIT 1
    ) fl ON true
    LEFT JOIN LATERAL (
        SELECT s.rs_z20, s.ts FROM sector_rs s
        WHERE s.sector20 = st.sector20 AND s.ts::date < %(day)s
        ORDER BY s.ts DESC LIMIT 1
    ) sr ON true
    LEFT JOIN LATERAL (
        SELECT r.regime, r.gex_percentile, r.ts FROM regime r
        WHERE r.symbol = tm.symbol AND r.ts <= tm.ts
        ORDER BY r.ts DESC LIMIT 1
    ) rg ON true
    LEFT JOIN LATERAL (
        SELECT l.best_lag, l.corr FROM leadlag l
        WHERE l.symbol = tm.symbol AND l.dt < %(day)s
        ORDER BY l.dt DESC LIMIT 1
    ) ll ON true
    WHERE tm.ts = %(ts)s
"""
# NOTE the joins themselves are still "newest row strictly before this day".
# The max-age rule is applied in Python, not bolted into the WHERE clause, on
# purpose: a SQL-side cutoff would make a stale row VANISH, and the journal
# could not then distinguish "M2 never covered this symbol" from "M2 covered
# it a month ago". Both are failures; they need completely different fixes.


def _regime_age_bars(regime_ts: datetime | None, bar_ts: datetime) -> int | None:
    if regime_ts is None:
        return None
    delta = bar_ts - regime_ts
    return max(0, int(round(delta.total_seconds() / 1800.0)))


def evaluate_bar(connection, as_of_ts: datetime) -> list[Evaluation]:
    """Every symbol with a timing bar at `as_of_ts`, fully evaluated.

    Nothing is dropped. This is the function the funnel, the per-symbol desk
    view and the cross-sectional IC study all read from.
    """
    day = as_of_ts.date()
    calendar = session_calendar(connection, day)
    with connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
        cursor.execute(EVALUATION_SQL, {"ts": as_of_ts, "day": day})
        rows = cursor.fetchall()

    evaluations = []
    for row in rows:
        flow_ts = row["flow_ts"]
        rs_ts = row["rs_ts"]
        inputs = {
            "flow_score": _f(row["flow_score"]),
            "flow_ts": flow_ts,
            "ce_state": row["ce_state"], "pe_state": row["pe_state"],
            "flow_age_sessions": _age_in_sessions(calendar, flow_ts.date() if flow_ts else None, day),
            "flow_n_ingredients": row["flow_n_ingredients"],
            "rs_z20": _f(row["rs_z20"]),
            "rs_ts": rs_ts,
            "rs_age_sessions": _age_in_sessions(calendar, rs_ts.date() if rs_ts else None, day),
            "regime": row["regime"],
            "gex_percentile": _f(row["gex_percentile"]),
            "regime_ts": row["regime_ts"],
            "regime_age_bars": _regime_age_bars(row["regime_ts"], as_of_ts),
            "timing_state": row["timing_state"],
            "timing_score": _f(row["timing_score"]),
            "rvol": _f(row["rvol"]),
            "va_position": _f(row["va_position"]),
            "best_lag": row["best_lag"],
            "leadlag_corr": _f(row["leadlag_corr"]),
        }
        evaluations.append(evaluate_symbol(row["symbol"], as_of_ts, row["sector20"], inputs))
    return evaluations


def evaluate_symbol(symbol: str, ts: datetime, sector20: str | None, inputs: dict) -> Evaluation:
    """Score BOTH sides on every axis, and return the better-evidenced one.

    JUDGE THE WINNER, do not eliminate. The side is no longer picked by the
    sign of one ingredient and then confirmed by a chain of vetoes -- both the
    CE and the PE case are scored across flow, side momentum, sector RS,
    timing, regime and lead-lag, and the higher conviction wins. A name whose
    calls are being accumulated can therefore win as a CE even when the
    composite leans mildly bearish, which under the veto cascade was
    unreachable: the two conditions are anti-correlated (names with CE
    momentum averaged a flow_score of -5.8 and never exceeded +48.5 against a
    +60 bar), so requiring both was close to unsatisfiable.

    Ties go to the side with the more complete evidence, then to CE, so the
    result is deterministic rather than dict-ordering-dependent.
    """
    results = [_evaluate_side(symbol, ts, sector20, inputs, side) for side in ("CE", "PE")]
    scored = [r for r in results if r.conviction is not None]
    if not scored:
        # Nothing judgeable on either side: keep the deeper evaluation so the
        # journal still says which validity gate stopped it.
        def depth(ev: Evaluation) -> int:
            if ev.first_failed_leg is None:
                return len(LEG_ORDER)
            return LEG_ORDER.index(ev.first_failed_leg)
        return max(results, key=depth)
    return max(scored, key=lambda e: (e.conviction, len(e.components or {}),
                                      e.direction == "bullish"))



def _evaluate_side(symbol: str, ts: datetime, sector20: str | None,
                   inputs: dict, side: str) -> Evaluation:
    """Score ONE side. Validity is gated; market opinion is weighed.

    Only the first two legs can end an evaluation, and neither is a view about
    the market -- they ask whether there is anything to judge at all:

        flow_present   a features_flow row exists
        flow_fresh     it is <= FLOW_MAX_AGE_SESSIONS old AND built from
                       >= FLOW_MIN_INGREDIENTS ingredients

    The remaining legs are still evaluated and journaled -- the funnel stays
    readable, and "how often did the sector disagree" is exactly the question
    the journal exists to answer -- but they no longer SHORT-CIRCUIT and no
    longer veto. Their content reaches the decision through `_components` and
    the conviction weighting instead.

    That is the whole change of 2026-08-28: seven conjunctive vetoes drove the
    funnel to zero on every bar, because a candidate strong on four axes and
    marginal on the fifth was discarded outright while a mediocre one that
    scraped past every bar was kept. Markets do not deliver unanimous evidence.
    """
    legs: dict[str, bool | None] = {name: None for name in LEG_ORDER}
    first_failed: str | None = None

    def fail(name: str) -> None:
        nonlocal first_failed
        legs[name] = False
        if first_failed is None:
            first_failed = name

    bullish = side == "CE"
    sign = 1.0 if bullish else -1.0
    direction = "bullish" if bullish else "bearish"
    flow_min = CE_FLOW_MIN_ABS if bullish else PE_FLOW_MIN_ABS
    rs_min = CE_RS_MIN_ABS_Z if bullish else PE_RS_MIN_ABS_Z

    flow = inputs.get("flow_score")
    valid = True

    # ── validity gates: these still end the evaluation ──────────────────────
    if flow is None:
        fail("flow_present")
        valid = False
    else:
        legs["flow_present"] = True
        age = inputs.get("flow_age_sessions")
        ingredients = inputs.get("flow_n_ingredients")
        # An unknown ingredient count is a pre-006 row. Treat it as unknown,
        # not as adequate: "we did not record it" is not evidence that the
        # score was corroborated.
        fresh = (age is not None and age <= FLOW_MAX_AGE_SESSIONS)
        corroborated = (ingredients is not None and ingredients >= FLOW_MIN_INGREDIENTS)
        if not (fresh and corroborated):
            fail("flow_fresh")
            valid = False
        else:
            legs["flow_fresh"] = True

    # ── opinion legs: recorded, never fatal ─────────────────────────────────
    if valid:
        legs["flow_strength"] = flow * sign >= flow_min

        side_state = inputs.get("ce_state" if bullish else "pe_state")
        legs["side_momentum"] = side_state in MOMENTUM_STATES

        rs = inputs.get("rs_z20")
        rs_age = inputs.get("rs_age_sessions")
        legs["sector_rs"] = bool(
            rs is not None and rs_age is not None
            and rs_age <= RS_MAX_AGE_SESSIONS and rs * sign >= rs_min)

        regime_age = inputs.get("regime_age_bars")
        legs["regime"] = bool(
            inputs.get("regime") in REGIME_PERMITS
            and regime_age is not None and regime_age <= REGIME_MAX_AGE_BARS)

        timing_score = inputs.get("timing_score")
        va = inputs.get("va_position")
        legs["timing"] = bool(
            inputs.get("timing_state") == "IGNITION"
            and timing_score is not None and timing_score >= TIMING_MIN_SCORE
            and va is not None and va * sign > 0)

        # first_failed now names the strongest DISSENT, for the journal only --
        # it no longer means the candidate was discarded there.
        for name in LEG_ORDER:
            if legs[name] is False:
                first_failed = name
                break

    reported_direction = direction if flow is not None else None
    components = _components(reported_direction, sign, inputs)
    score = conviction(components)

    return Evaluation(
        symbol=symbol, ts=ts, sector20=sector20, inputs=inputs, legs=legs,
        first_failed_leg=first_failed, survived=(valid and score is not None),
        direction=reported_direction,
        conviction=score, components=components,
        signed=_signed_readings(inputs),
    )



def _components(direction: str | None, sign: float, inputs: dict) -> dict | None:
    """Every axis scored 0-100 FOR THIS SIDE, with missing axes OMITTED.

    WEIGH, DO NOT GATE (2026-08-28). The legs used to be a cascade of hard
    vetoes: a candidate strong on four axes and merely marginal on the fifth
    was rejected outright, while one mediocre everywhere that scraped past
    every bar was accepted. Markets do not deliver unanimous evidence, and the
    journal showed exactly what that costs -- with seven conjunctive legs the
    funnel reached zero on every bar, and the conviction gate behind it had
    NEVER been cleared in 109,409 evaluations (all-time max 81.8 against a
    threshold of 85). Both numbers were chosen in isolation and never checked
    against the distribution the system actually produces.

    So the market-opinion axes now SCORE instead of veto, and only validity
    -- is there data, is it fresh, is it corroborated -- remains a hard gate.

    MISSING IS OMITTED, NOT DEFAULTED. `aligned_rs = 0.0 if rs is None` scored
    an absent sector reading 50/100, identical to a genuinely neutral one, and
    under a weighted model that silently props up the least-evidenced
    candidates. Omitted keys are renormalised over by `conviction()`, the same
    contract M2's own composite uses.
    """
    if direction is None:
        return None
    out: dict[str, float] = {}

    flow = inputs.get("flow_score")
    if flow is not None:
        # SIGNED toward this side, then mapped to 0-100: a score of +80 is
        # strong evidence FOR the CE case and strong evidence AGAINST the PE
        # case. `abs(flow_score)` scored both identically, so the component
        # could not tell the two sides apart at all.
        favours = max(-100.0, min(100.0, sign * float(flow)))
        out["flow"] = (favours + 100.0) / 2.0

    # Is THIS side's own contract being accumulated (see MOMENTUM_STATES).
    side_state = inputs.get("ce_state" if sign > 0 else "pe_state")
    if side_state is not None:
        out["side_momentum"] = SIDE_STATE_SCORE.get(side_state, 50.0)

    rs = inputs.get("rs_z20")
    if rs is not None:
        aligned_rs = max(-3.0, min(3.0, sign * float(rs)))
        out["sector_rs"] = (aligned_rs + 3.0) / 6.0 * 100.0

    # Timing scores the BREAK's agreement with this side, not just its size --
    # an IGNITION that broke the other way is evidence against, not for.
    timing_score = inputs.get("timing_score")
    if timing_score is not None:
        score = float(timing_score)
        if inputs.get("timing_state") != "IGNITION":
            score *= 0.5              # a real reading, but not the trigger
        va = inputs.get("va_position")
        if va is not None and va * sign < 0:
            score = 100.0 - score     # broke against us: invert, do not drop
        out["timing"] = max(0.0, min(100.0, score))

    regime = inputs.get("regime")
    if regime is not None:
        out["regime"] = REGIME_SCORE.get(regime, 50.0)

    best_lag = inputs.get("best_lag")
    corr = inputs.get("leadlag_corr")
    if best_lag is not None and corr is not None:
        out["leadlag"] = (50.0 + min(50.0, abs(float(corr)) * 100.0)
                          if best_lag > 0 else 50.0)
    return out or None


def conviction(components: dict | None) -> float | None:
    """Weighted mean over the axes PRESENT, renormalised.

    Returns None below MIN_COMPONENTS: a conviction assembled from one axis is
    not comparable with one assembled from six, and ranking them against each
    other is how a thinly-evidenced name wins a slot it has not earned.
    """
    if not components or len(components) < MIN_COMPONENTS:
        return None
    total = sum(WEIGHTS[k] for k in components)
    if total <= 0:
        return None
    return sum(WEIGHTS[k] * v for k, v in components.items()) / total


def _signed_readings(inputs: dict) -> dict:
    """Raw SIGNED readings for the cross-sectional IC study.

    Deliberately not the direction-aligned component scores: aligning a
    magnitude to the direction the signal itself chose and then correlating
    it with a signed forward return measures the alignment, not the edge.
    `signed_timing` gives timing_score the sign of where price sits relative
    to the developing value area, which is the only directional information
    M5 carries.
    """
    timing_score = inputs.get("timing_score")
    va = inputs.get("va_position")
    signed_timing = None
    if timing_score is not None and va is not None:
        signed_timing = float(timing_score) * (1.0 if va >= 0.5 else -1.0)
    gex_pct = inputs.get("gex_percentile")
    return {
        "flow": inputs.get("flow_score"),
        "rs": inputs.get("rs_z20"),
        "timing": signed_timing,
        # A percentile in [0,1] centred so 0 is "median dealer gamma for this
        # symbol" -- the sign then means long/short gamma rather than
        # high/low, which is what the regime claim is about.
        "regime": None if gex_pct is None else (float(gex_pct) - 0.5) * 2.0,
    }


def _f(value) -> float | None:
    return None if value is None else float(value)


# ── persistence of the evaluation journal ──────────────────────────────────
EVALUATION_COLUMNS = (
    "ts", "symbol", "sector20",
    "flow_score", "flow_ts", "flow_age_sessions", "flow_n_ingredients",
    "rs_z20", "rs_ts", "rs_age_sessions",
    "regime", "gex_percentile", "regime_ts", "regime_age_bars",
    "timing_state", "timing_score", "rvol", "va_position",
    "best_lag", "leadlag_corr",
    "ce_state", "pe_state",
    "leg_flow_present", "leg_flow_fresh", "leg_flow_strength", "leg_side_momentum",
    "leg_sector_rs", "leg_regime", "leg_timing",
    "first_failed_leg", "survived_filter", "direction", "conviction",
    "component_scores", "signed_flow", "signed_rs", "signed_timing", "signed_regime",
    "config_hash",
)


def evaluation_row(evaluation: Evaluation, cfg_hash: str) -> tuple:
    """One journal row, emitted in EVALUATION_COLUMNS order BY NAME.

    Built as a dict and then ordered by the column tuple, rather than as a
    positional tuple kept in step by eye. The positional form silently
    mis-mapped `ce_state` into `best_lag` when two pairs were added in
    different orders on the two sides -- and it only surfaced because an
    integer column rejected a text value. Two columns of the same type would
    have written wrong data indefinitely with nothing to notice.
    """
    i = evaluation.inputs
    legs = evaluation.legs
    values = {
        "ts": evaluation.ts, "symbol": evaluation.symbol, "sector20": evaluation.sector20,
        "flow_score": i.get("flow_score"), "flow_ts": i.get("flow_ts"),
        "flow_age_sessions": i.get("flow_age_sessions"),
        "flow_n_ingredients": i.get("flow_n_ingredients"),
        "rs_z20": i.get("rs_z20"), "rs_ts": i.get("rs_ts"),
        "rs_age_sessions": i.get("rs_age_sessions"),
        "regime": i.get("regime"), "gex_percentile": i.get("gex_percentile"),
        "regime_ts": i.get("regime_ts"), "regime_age_bars": i.get("regime_age_bars"),
        "timing_state": i.get("timing_state"), "timing_score": i.get("timing_score"),
        "rvol": i.get("rvol"), "va_position": i.get("va_position"),
        "best_lag": i.get("best_lag"), "leadlag_corr": i.get("leadlag_corr"),
        "ce_state": i.get("ce_state"), "pe_state": i.get("pe_state"),
        "leg_flow_present": legs.get("flow_present"),
        "leg_flow_fresh": legs.get("flow_fresh"),
        "leg_flow_strength": legs.get("flow_strength"),
        "leg_side_momentum": legs.get("side_momentum"),
        "leg_sector_rs": legs.get("sector_rs"),
        "leg_regime": legs.get("regime"), "leg_timing": legs.get("timing"),
        "first_failed_leg": evaluation.first_failed_leg,
        "survived_filter": evaluation.survived, "direction": evaluation.direction,
        "conviction": evaluation.conviction,
        "component_scores": (psycopg2.extras.Json(evaluation.components)
                             if evaluation.components else None),
        "signed_flow": evaluation.signed.get("flow"),
        "signed_rs": evaluation.signed.get("rs"),
        "signed_timing": evaluation.signed.get("timing"),
        "signed_regime": evaluation.signed.get("regime"),
        "config_hash": cfg_hash,
    }
    missing = set(EVALUATION_COLUMNS) - set(values)
    if missing:
        raise RuntimeError(f"evaluation_row is missing columns: {sorted(missing)}")
    return tuple(values[column] for column in EVALUATION_COLUMNS)



def persist_evaluations(connection, evaluations: list[Evaluation]) -> int:
    if not evaluations:
        return 0
    cfg_hash = config_hash()
    rows = [evaluation_row(e, cfg_hash) for e in evaluations]
    columns = ", ".join(EVALUATION_COLUMNS)
    updates = ", ".join(
        f"{c} = EXCLUDED.{c}" for c in EVALUATION_COLUMNS if c not in ("ts", "symbol")
    )
    with connection.cursor() as cursor:
        psycopg2.extras.execute_values(
            cursor,
            f"""INSERT INTO candidate_evaluations ({columns}) VALUES %s
                ON CONFLICT (symbol, ts) DO UPDATE SET {updates},
                    computed_at = now()""",
            rows,
            page_size=500,
        )
    return len(rows)


def funnel_counts(evaluations: list[Evaluation]) -> list[dict]:
    """Survivor count after each leg, straight from the journal.

    The desk used to re-express M6's filter in SQL to draw this, which meant
    two copies of the same logic drifting apart. Now the funnel is a GROUP BY
    over what M6 actually recorded.
    """
    entered = len(evaluations)
    stages = [{"leg": "timing_bar", "surviving": entered,
               "gate": "M5 wrote a timing row for this bar"}]
    gates = {
        "flow_present": "features_flow has a prior-session row",
        "flow_fresh": f"flow <= {FLOW_MAX_AGE_SESSIONS} sessions old and built from "
                      f">= {FLOW_MIN_INGREDIENTS} ingredients",
        # Stated per side, because that is what is now tested: an absolute bar
        # would misdescribe a filter that is signed on both flow and RS.
        "flow_strength": f"flow_score >= +{CE_FLOW_MIN_ABS} (CE) "
                         f"or <= -{PE_FLOW_MIN_ABS} (PE)",
        "side_momentum": "this side's own contract is being accumulated "
                         f"({'/'.join(sorted(MOMENTUM_STATES))}), not decaying",
        "sector_rs": f"rs_z20 >= +{CE_RS_MIN_ABS_Z} (CE) or <= -{PE_RS_MIN_ABS_Z} (PE), "
                     f"<= {RS_MAX_AGE_SESSIONS} sessions old",
        "regime": f"regime in {'/'.join(sorted(REGIME_PERMITS))}, "
                  f"<= {REGIME_MAX_AGE_BARS} bars old",
        "timing": f"timing_state = IGNITION, score >= {TIMING_MIN_SCORE}, "
                  f"and the break beyond value went THIS side's way",
    }
    for leg in LEG_ORDER:
        surviving = sum(1 for e in evaluations if e.legs.get(leg) is True)
        lost = sum(1 for e in evaluations if e.legs.get(leg) is False)
        stages.append({"leg": leg, "surviving": surviving, "lost_here": lost,
                       "gate": gates[leg]})
    return stages


def load_candidates_at(connection, as_of_ts: datetime) -> list[Candidate]:
    """Filter survivors only, highest conviction first.

    Kept as the name M8's replay harness imports. It is now a projection of
    evaluate_bar() rather than its own query, so the backtest and the live
    path can never apply subtly different filters.
    """
    survivors = [e.as_candidate() for e in evaluate_bar(connection, as_of_ts)]
    return sorted([c for c in survivors if c is not None], key=lambda c: -c.conviction)


def resolve_instrument(connection, symbol: str, direction: str, as_of_ts: datetime):
    """The underlying's current front-series ATM CE/PE: symbol, premium, lot_size.

    EXPIRY IS THE PRIMARY SORT KEY, and expired/expiring series are excluded
    outright (`expiry > ts::date`). Without both, an adversarial review
    confirmed two real mis-selections on live data: for RELIANCE at
    2026-07-28 the nearest strike existed ONLY on the series expiring that
    same afternoon, resolving a near-worthless 0.80 contract that MIN_PREMIUM
    then rejected -- killing a candidate for which a perfectly tradable
    next-series contract existed; and at another spot the query returned the
    SEPTEMBER contract in preference to August purely on an arbitrary
    timestamp tie, pricing an intraday 30-minute signal off a far-month
    option. Rolling off the expiring series matches the sibling MACD mini
    project's own rollover rule.

    The spot query is pinned to interval='30minute' for the same reason the
    chain query is: underlying_spot_candles stores five intervals, so an
    unpinned "latest close" is whichever interval happened to print last --
    a nondeterministic spot, which moves the ATM strike."""
    option_type = "CE" if direction == "bullish" else "PE"
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT s.underlying, s.close AS spot
            FROM underlying_spot_candles s
            WHERE s.underlying = %(symbol)s AND s.time <= %(ts)s
              AND s.interval = '30minute'
            ORDER BY s.time DESC LIMIT 1
            """,
            {"symbol": symbol, "ts": as_of_ts},
        )
        row = cursor.fetchone()
        spot = float(row[1]) if row else None
        if spot is None:
            return None

        cursor.execute(
            """
            SELECT strike, close, expiry, open, high, low, volume, oi,
                   iv, delta, gamma, theta, vega
            FROM option_premium_candles
            WHERE underlying = %(symbol)s AND option_type = %(option_type)s
              AND interval = '30minute'
              AND time <= %(ts)s AND time >= %(ts)s - interval '2 days'
              AND close IS NOT NULL
              AND expiry > %(ts)s::date
            ORDER BY expiry ASC, time DESC, ABS(strike - %(spot)s) ASC
            LIMIT 1
            """,
            {"symbol": symbol, "option_type": option_type, "ts": as_of_ts, "spot": spot},
        )
        chain_row = cursor.fetchone()
        if chain_row is None:
            return None
        if len(chain_row) == 3:  # compatibility for narrow fake cursors in unit tests
            strike, premium, expiry = chain_row
            open_price = high = low = volume = oi = iv = delta = gamma = theta = vega = None
        else:
            (strike, premium, expiry, open_price, high, low, volume, oi,
             iv, delta, gamma, theta, vega) = chain_row

        cursor.execute("SELECT lot_size FROM fo_underlying_catalog WHERE symbol = %(symbol)s",
                       {"symbol": symbol})
        lot_row = cursor.fetchone()
        lot_size = int(lot_row[0]) if lot_row and lot_row[0] else None

    if premium is None or float(premium) < MIN_PREMIUM or lot_size is None:
        return None
    return {
        "instrument": f"{symbol}{expiry.strftime('%y%b').upper()}{int(strike)}{option_type}",
        "premium": float(premium), "strike": float(strike), "expiry": expiry,
        "option_type": option_type, "lot_size": lot_size, "spot": spot,
        "open": _f(open_price), "high": _f(high), "low": _f(low),
        "volume": _f(volume), "oi": _f(oi), "iv": _f(iv), "delta": _f(delta),
        "gamma": _f(gamma), "theta": _f(theta), "vega": _f(vega),
        "dte_days": (expiry - as_of_ts.date()).days,
    }


def resolve_instruments_at(
    connection,
    symbols: list[str],
    as_of_ts: datetime,
    *,
    min_expiry_exclusive: date | None = None,
) -> dict:
    """Resolve both ATM sides for a whole bar in one query.

    Model inference judges CE and PE independently for every symbol. Calling
    `resolve_instrument` 400+ times would turn a vectorised model into a SQL
    loop, so the production path resolves the cross-section as one snapshot.
    """
    if not symbols:
        return {}
    with connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
        cursor.execute(
            """
            WITH spot AS MATERIALIZED (
                SELECT DISTINCT ON (s.underlying) s.*
                FROM underlying_spot_candles s
                WHERE s.underlying = ANY(%(symbols)s)
                  AND s.interval='30minute' AND s.time=%(ts)s
                ORDER BY s.underlying,
                         CASE s.source WHEN 'upstox_spot' THEN 0
                                       WHEN 'upstox_sweep' THEN 1
                                       WHEN 'upstox' THEN 2
                                       WHEN 'fyers_spot' THEN 3
                                       WHEN 'fyers' THEN 4 ELSE 9 END,
                         s.synced_at DESC
            ), ranked AS (
                SELECT o.*, s.close AS spot, c.lot_size,
                       pr.straddle_to_spot, pr.normalized_straddle,
                       pr.strangle_straddle_ratio, pr.put_wing_iv_ratio,
                       pr.call_wing_iv_ratio, pr.atm_put_call_premium_ratio,
                       pr.atm_call_put_extrinsic_ratio, pr.premium_pcr,
                       pr.call_itm_atm_extrinsic_ratio,
                       pr.call_otm_atm_extrinsic_ratio,
                       pr.put_itm_atm_extrinsic_ratio,
                       pr.put_otm_atm_extrinsic_ratio,
                       pr.n_strikes AS ratio_n_strikes,
                       row_number() OVER (
                           PARTITION BY o.underlying, o.option_type
                           ORDER BY o.expiry, abs(o.strike - s.close),
                                    abs(abs(o.delta)-0.5),
                                    CASE o.source WHEN 'upstox' THEN 0 ELSE 1 END,
                                    o.source,o.instrument_key
                       ) AS rn
                FROM option_premium_candles o
                JOIN spot s ON s.underlying=o.underlying AND s.time=o.time
                JOIN fo_underlying_catalog c ON c.symbol = o.underlying
                LEFT JOIN option_premium_ratios pr
                  ON pr.ts=o.time AND pr.symbol=o.underlying AND pr.expiry=o.expiry
                WHERE o.underlying = ANY(%(symbols)s)
                  AND o.option_type IN ('CE','PE') AND o.interval = '30minute'
                  AND o.time = %(ts)s
                  AND o.expiry > %(min_expiry)s AND o.close >= %(min_premium)s
            )
            SELECT * FROM ranked WHERE rn = 1
            """,
            {
                "symbols": symbols,
                "ts": as_of_ts,
                "min_expiry": min_expiry_exclusive or as_of_ts.date(),
                "min_premium": MIN_PREMIUM,
            },
        )
        rows = cursor.fetchall()
    resolved = {}
    for row in rows:
        expiry = row["expiry"]
        strike = float(row["strike"])
        side = row["option_type"]
        resolved[(row["underlying"], side)] = {
            "instrument": f"{row['underlying']}{expiry.strftime('%y%b').upper()}{int(strike)}{side}",
            "premium": float(row["close"]), "strike": strike, "expiry": expiry,
            "source_mark_ts": row["time"],
            "option_type": side, "lot_size": int(row["lot_size"]),
            "spot": _f(row["spot"]), "open": _f(row["open"]),
            "high": _f(row["high"]), "low": _f(row["low"]),
            "volume": _f(row["volume"]), "oi": _f(row["oi"]),
            "iv": _f(row["iv"]), "delta": _f(row["delta"]),
            "gamma": _f(row["gamma"]), "theta": _f(row["theta"]),
            "vega": _f(row["vega"]), "dte_days": (expiry - as_of_ts.date()).days,
            "straddle_to_spot": _f(row.get("straddle_to_spot")),
            "normalized_straddle": _f(row.get("normalized_straddle")),
            "strangle_straddle_ratio": _f(row.get("strangle_straddle_ratio")),
            "put_wing_iv_ratio": _f(row.get("put_wing_iv_ratio")),
            "call_wing_iv_ratio": _f(row.get("call_wing_iv_ratio")),
            "atm_put_call_premium_ratio": _f(row.get("atm_put_call_premium_ratio")),
            "atm_call_put_extrinsic_ratio": _f(row.get("atm_call_put_extrinsic_ratio")),
            "premium_pcr": _f(row.get("premium_pcr")),
            "call_itm_atm_extrinsic_ratio": _f(row.get("call_itm_atm_extrinsic_ratio")),
            "call_otm_atm_extrinsic_ratio": _f(row.get("call_otm_atm_extrinsic_ratio")),
            "put_itm_atm_extrinsic_ratio": _f(row.get("put_itm_atm_extrinsic_ratio")),
            "put_otm_atm_extrinsic_ratio": _f(row.get("put_otm_atm_extrinsic_ratio")),
            "ratio_n_strikes": _f(row.get("ratio_n_strikes")),
        }
    return resolved
def persist_model_diagnostics(connection, as_of_ts: datetime,
                              evaluations: list[Evaluation]) -> dict:
    """Score the bar with the intraday model as a DIAGNOSTIC COMPARATOR.

    This writes `vanguard_model_predictions` and nothing else. It deliberately
    has no ticket, sizing or emission path: the model whose predictions land
    here spent 2026-09-02..04 routing every M6 ticket and then refusing all of
    them for being shadow, which is how a research artifact came to suppress
    the lane it was meant to measure. Its forecasts are still worth journaling
    -- resolve_model_prediction_outcomes() scores them against the next bar --
    so the comparison survives; the authority does not.
    """
    model = load_selector_model(connection)
    if model is None:
        return {"scored": 0, "model_version": None,
                "reason": "no compatible intraday artifact loaded"}
    decision_at = datetime.now(timezone.utc)
    timely = timely_decision(as_of_ts, decision_at)
    instruments = resolve_instruments_at(
        connection, sorted({evaluation.symbol for evaluation in evaluations}), as_of_ts)
    triples = []
    for evaluation in evaluations:
        for side in ("CE", "PE"):
            instrument = instruments.get((evaluation.symbol, side))
            if instrument is not None:
                triples.append((evaluation, instrument, side))
    scored = prediction_rows(model, triples)
    if not scored:
        return {"scored": 0, "model_version": model.version,
                "reason": "no ATM contract resolved for any evaluated symbol"}
    with connection.cursor() as cursor:
        psycopg2.extras.execute_values(
            cursor,
            """INSERT INTO vanguard_model_predictions
               (ts, symbol, option_type, model_version, q10_return, q50_return,
                q90_return, conservative_edge, selection_threshold, selected,
                reason, instrument, strike, expiry, entry_mark,
                source_mark_ts, decision_at, timing_policy) VALUES %s
               ON CONFLICT (ts, symbol, option_type, model_version) DO NOTHING""",
            [(
                as_of_ts, row["evaluation"].symbol, row["option_type"],
                row["model_version"], row["q10"], row["q50"], row["q90"],
                row["edge"], row["threshold"],
                # `selected` is a diagnostic verdict now, never an emission:
                # nothing downstream reads it as permission to trade.
                False,
                (f"diagnostic comparator ({model.status}); M6 journals the "
                 f"candidate universe and the pre-close swing lane owns the "
                 f"actionable list"),
                row["instrument_data"]["instrument"], row["instrument_data"]["strike"],
                row["instrument_data"]["expiry"], row["instrument_data"]["premium"],
                row["instrument_data"]["source_mark_ts"], decision_at,
                "completed_same_bar_v1" if timely else "retrospective_same_bar_v1",
            ) for row in scored],
            page_size=500,
        )
    return {"scored": len(scored), "model_version": model.version,
            "model_status": model.status, "role": "diagnostic_comparator"}


def resolve_model_prediction_outcomes(connection) -> int:
    """Resolve shadow predictions once the same contract's next bar exists."""
    with connection.cursor() as cursor:
        cursor.execute(
            f"""UPDATE vanguard_model_predictions p
               SET realized_return = o.close / p.entry_mark - 1.0,
                   realized_net_return = o.close / p.entry_mark - 1.0 - m.cost_pct,
                   resolved_at = now()
               FROM vanguard_model_versions m, option_premium_candles o
               WHERE p.model_version=m.version
                 AND p.realized_return IS NULL AND p.entry_mark > 0
                 AND p.timing_policy='completed_same_bar_v1' AND p.source_mark_ts=p.ts
                 AND p.strike IS NOT NULL AND p.expiry IS NOT NULL
                 AND o.underlying=p.symbol AND o.option_type=p.option_type
                 AND o.strike=p.strike AND o.expiry=p.expiry
                 AND o.interval='30minute' AND o.time=p.ts + interval '30 minutes'
                 AND o.close IS NOT NULL
                 AND (o.time AT TIME ZONE 'Asia/Kolkata')::date =
                     (p.ts AT TIME ZONE 'Asia/Kolkata')::date
                 AND {available_at_sql('o.time')} <= now()"""
        )
        return cursor.rowcount
# M6 journals the bar's candidate universe; it does not claim an execution
# decision. The actionable list is the pre-close swing lane's, so every row
# from this path carries this reason rather than a sizing verdict.
CANDIDATE_UNIVERSE_REASON = (
    "candidate universe; the actionable list is the pre-close swing lane"
)


def build_tickets(connection, as_of_ts: datetime, capital: float,
                  evaluations: list[Evaluation] | None = None) -> list[dict]:
    """The bar's CANDIDATE UNIVERSE, journaled. Not an execution decision.

    Doctrine (owner plan, 2026-09-04): M6 is candidate-universe construction
    and journaling -- not an independent competing selector -- and the
    30-minute neural model is a DIAGNOSTIC COMPARATOR that must not sit on
    this path.

    It used to sit on it. `build_tickets` returned `_model_tickets()` the
    moment any compatible artifact loaded, and a `shadow` artifact then
    refused every row it had just taken over ("model status shadow;
    predictions are shadow-only"): 9,030 consecutive gated rows and zero
    emissions across 2026-09-02..04, with the fusion evidence that identified
    names like KEI bearish left no surviving path of its own. A research model
    must never suppress the signal it was meant to rank. The comparator now
    writes only its own prediction journal -- see persist_model_diagnostics,
    which the caller runs alongside this.

    `evaluations` may be passed in by a caller that already ran evaluate_bar()
    -- the cycle daemon does, so a live pass evaluates the bar exactly once
    and both the journal and the tickets come from that single evaluation.
    """
    if evaluations is None:
        evaluations = evaluate_bar(connection, as_of_ts)
    candidates = sorted(
        [c for c in (e.as_candidate() for e in evaluations) if c is not None],
        key=lambda c: -c.conviction,
    )
    state = load_risk_state(connection, as_of_ts, capital)
    results = []
    for rank, candidate in enumerate(candidates, start=1):
        row = {
            "ts": as_of_ts, "symbol": candidate.symbol, "direction": candidate.direction,
            "conviction": candidate.conviction, "rank_in_session": rank,
            "regime_at_ts": candidate.regime,
            "evidence": {
                "flow_score": candidate.flow_score, "rs_z20": candidate.rs_z20,
                "timing_score": candidate.timing_score, "timing_state": candidate.timing_state,
                "best_lag": candidate.best_lag, "corr": candidate.corr,
                "component_scores": candidate.components,
                "config_hash": config_hash(),
            },
        }
        if candidate.conviction < CONVICTION_MIN:
            row.update(emitted=False, gated_reason=f"conviction {candidate.conviction:.1f} < {CONVICTION_MIN}")
            results.append(row)
            continue
        if rank > TOP_N_PER_BAR:
            row.update(emitted=False, gated_reason=f"rank {rank} > top-{TOP_N_PER_BAR}")
            results.append(row)
            continue

        instrument = resolve_instrument(connection, candidate.symbol, candidate.direction, as_of_ts)
        if instrument is None:
            row.update(emitted=False, gated_reason="no tradable ATM contract resolved")
            results.append(row)
            continue

        entry = instrument["premium"]
        stop = round(entry * (1 - STOP_PCT), 4)
        target1 = round(entry * (1 + TARGET1_PCT), 4)
        target2 = round(entry * (1 + TARGET2_PCT), 4)
        sizing = risk_check(
            state, connection, symbol=candidate.symbol, sector20=candidate.sector20,
            entry_premium=entry, stop_premium=stop, lot_size=instrument["lot_size"],
            as_of=as_of_ts,
        )
        row.update(
            instrument=instrument["instrument"],
            strike=instrument["strike"], option_type=instrument["option_type"],
            expiry=instrument["expiry"], lot_size=instrument["lot_size"],
            entry_zone_low=round(entry * 0.98, 4), entry_zone_high=round(entry * 1.02, 4),
            stop=stop, target1=target1, target2=target2,
        )
        # M7 still SIZES every candidate -- "trade available good candidates
        # along with proper sizing" (owner directive, 2026-08-28) -- and a
        # refused sizing falls back to MIN_PAPER_LOTS rather than skipping, so
        # nothing later mistakes a floor-sized row for one M7 sanctioned.
        #
        # The row is journaled, not emitted: under the 2026-09-04 plan M6
        # constructs and journals the candidate universe, and the ACTIONABLE
        # list -- 0-10 exact contracts after expected-return, confidence,
        # liquidity and M7 gates -- is the pre-close swing lane's. Carrying the
        # resolved contract and its sizing here is what makes the journaled
        # candidate reviewable rather than a bare name.
        sizing_floored = not sizing.allowed
        if sizing_floored:
            row.update(sizing_method=f"floored:{sizing.reason}")
        lots = sizing.lots if not sizing_floored else MIN_PAPER_LOTS
        row.update(
            emitted=False, gated_reason=CANDIDATE_UNIVERSE_REASON,
            sizing_lots=lots,
            sizing_notional=(sizing.notional if not sizing_floored
                             else round(entry * instrument["lot_size"] * lots, 2)),
            sizing_risk_rupees=(sizing.risk_rupees if not sizing_floored
                                else round((entry - stop) * instrument["lot_size"] * lots, 2)),
            sizing_method=(sizing.method if not sizing_floored
                           else f"floored:{sizing.reason}"),
            sizing_premium_rupees=(sizing.premium_rupees if not sizing_floored
                                   else round(entry * instrument["lot_size"] * lots, 2)),
            sizing_risk_basis=sizing.risk_basis,
        )
        results.append(row)
        # Sizing shown for the next candidate at this bar must assume the
        # earlier one was taken, or every top-N row would quote the same
        # headroom twice. This is a within-bar view refresh only; no risk
        # budget is actually consumed until the actionable list emits.
        state.open_positions.append({
            "ticket_id": -1, "symbol": candidate.symbol, "sector20": candidate.sector20,
            "risk_rupees": sizing.risk_rupees,
        })
        state.__post_init__()
    return results


def persist_tickets(connection, rows: list[dict]) -> list[int]:
    """Write the bar's journaled candidate rows.

    vanguard_model_predictions is NOT written here any more; it belongs to
    persist_model_diagnostics, which is the only place the comparator runs.
    """
    ids = []
    with connection.cursor() as cursor:
        for row in rows:
            # A bar can legitimately be evaluated twice -- a restart mid-session
            # runs a catch-up pass and then meets the scheduled one at the same
            # boundary. Return the existing row instead of a duplicate journal
            # entry; the model-keyed version of this guard covered only the
            # retired neural path, so the universe path had none at all.
            cursor.execute(
                """SELECT id FROM tickets
                   WHERE ts=%s AND symbol=%s AND direction=%s LIMIT 1""",
                (row["ts"], row["symbol"], row["direction"]),
            )
            existing = cursor.fetchone()
            if existing:
                ids.append(existing[0])
                continue
            cursor.execute(
                """INSERT INTO tickets
                   (ts, symbol, instrument, direction, entry_zone_low, entry_zone_high,
                    stop, target1, target2, conviction, rank_in_session, regime_at_ts,
                    evidence, sizing_lots, sizing_notional, sizing_risk_rupees, sizing_method,
                    sizing_premium_rupees, sizing_risk_basis,
                    emitted, gated_reason, strike, option_type, expiry, lot_size)
                   VALUES (%(ts)s, %(symbol)s, %(instrument)s, %(direction)s,
                           %(entry_zone_low)s, %(entry_zone_high)s, %(stop)s, %(target1)s, %(target2)s,
                           %(conviction)s, %(rank_in_session)s, %(regime_at_ts)s,
                           %(evidence)s, %(sizing_lots)s, %(sizing_notional)s,
                           %(sizing_risk_rupees)s, %(sizing_method)s,
                           %(sizing_premium_rupees)s, %(sizing_risk_basis)s,
                           %(emitted)s, %(gated_reason)s,
                           %(strike)s, %(option_type)s, %(expiry)s, %(lot_size)s)
                   RETURNING id""",
                {
                    "instrument": row.get("instrument"), "entry_zone_low": row.get("entry_zone_low"),
                    "entry_zone_high": row.get("entry_zone_high"), "stop": row.get("stop"),
                    "target1": row.get("target1"), "target2": row.get("target2"),
                    "sizing_lots": row.get("sizing_lots"), "sizing_notional": row.get("sizing_notional"),
                    "sizing_risk_rupees": row.get("sizing_risk_rupees"), "sizing_method": row.get("sizing_method"),
                    "sizing_premium_rupees": row.get("sizing_premium_rupees"),
                    "sizing_risk_basis": row.get("sizing_risk_basis"),
                    "evidence": psycopg2.extras.Json(row["evidence"]),
                    "strike": row.get("strike"), "option_type": row.get("option_type"),
                    "expiry": row.get("expiry"), "lot_size": row.get("lot_size"),
                    **{k: row[k] for k in ("ts", "symbol", "direction", "conviction", "rank_in_session",
                                            "regime_at_ts", "emitted", "gated_reason")},
                },
            )
            ids.append(cursor.fetchone()[0])
    return ids


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--ts", type=datetime.fromisoformat, default=None,
                        help="ISO timestamp to evaluate; default = latest timing bar")
    parser.add_argument("--capital", type=float, default=None,
                        help="default = M9's own evolving paper equity (paper_capital_daily), "
                             "not a static number -- sizing should track what the account "
                             "actually has, not always reset to a fixed figure")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--no-journal", action="store_true",
                        help="skip writing candidate_evaluations (diagnostic runs only -- "
                             "the journal is what the desk and the IC study read)")
    parser.add_argument("--dsn", default=os.environ.get("VANGUARD_DATABASE_URL", DEFAULT_DSN))
    args = parser.parse_args()

    connection = psycopg2.connect(args.dsn)
    connection.autocommit = True
    try:
        as_of_ts = args.ts
        if as_of_ts is None:
            as_of_ts = latest_evaluable_bar(connection)
        if as_of_ts is None:
            print("no timing bars on the NSE session grid -- nothing to evaluate")
            return 0
        print(f"evaluating M6 at ts={as_of_ts.isoformat()}  config={config_hash()}")

        # The lane calls itself live. Say plainly how old the bar being
        # evaluated actually is, because `underlying_spot_candles` delivers the
        # NSE equity session as an OVERNIGHT BATCH -- verified 2026-08-27: every
        # one of 26-Aug's thirteen session bars, the 09:15 one included, first
        # appeared at 27-Aug 00:00 UTC. A 30-minute "live" pass therefore cannot
        # see the current session at all, and a log line that does not say so
        # implies a freshness the data does not have.
        lag_minutes = (datetime.now(timezone.utc) - as_of_ts).total_seconds() / 60.0
        if lag_minutes > 60:
            print(f"  NOTE: this bar is {lag_minutes / 60:.1f} hours old. The NSE spot-candle "
                  f"feed arrives as an overnight batch, so an intraday pass evaluates the "
                  f"PREVIOUS session, not the current one.")

        evaluations = evaluate_bar(connection, as_of_ts)
        if not args.no_journal:
            written = persist_evaluations(connection, evaluations)
            print(f"journaled {written} per-symbol evaluations to candidate_evaluations")

        resolved = resolve_model_prediction_outcomes(connection)
        if resolved:
            print(f"resolved {resolved} prior shadow predictions against next-bar option marks")

        print("\nfilter funnel (from the journal, not a second copy of the filter):")
        for stage in funnel_counts(evaluations):
            lost = stage.get("lost_here")
            print(f"  {stage['leg']:<15} {stage['surviving']:>5}"
                  + (f"   (-{lost} died here)" if lost else "")
                  + f"   [{stage['gate']}]")

        capital = args.capital
        if capital is None:
            from paper.engine import current_capital   # local: paper.engine imports this module
            capital = current_capital(connection, as_of_ts.date())
        print(f"\ncapital: Rs{capital:,.0f}")

        rows = build_tickets(connection, as_of_ts, capital, evaluations=evaluations)

        # The comparator runs BESIDE the universe, never in front of it. Its
        # forecasts are journaled for the record; it decides nothing here.
        # It is a write, so a dry run stays a dry run.
        diagnostics = (persist_model_diagnostics(connection, as_of_ts, evaluations)
                       if args.write else {"scored": 0, "reason": "dry run (--write not given)"})
        if diagnostics.get("scored"):
            print(f"\ndiagnostic comparator {diagnostics['model_version']} "
                  f"({diagnostics.get('model_status')}): scored {diagnostics['scored']} "
                  f"CE/PE contracts into vanguard_model_predictions -- no ticket path")
        elif diagnostics.get("reason"):
            print(f"\ndiagnostic comparator idle: {diagnostics['reason']}")

        emitted = [r for r in rows if r["emitted"]]
        evaluated = len(evaluations)
        print(f"symbols evaluated: {evaluated}")
        print(f"candidates (filter passed): {len(rows)}")
        print(f"tickets emitted: {len(emitted)}  "
              f"(skip rate {100*(1-len(emitted)/max(1,evaluated)):.2f}% of the evaluated universe)")
        for row in rows:
            tag = "EMIT" if row["emitted"] else "skip"
            print(f"  [{tag}] {row['symbol']:<14} {row['direction']:<8} "
                  f"conviction={row['conviction']:5.1f} rank={row['rank_in_session']}"
                  + (f"  -- {row['gated_reason']}" if row.get("gated_reason") else
                     f"  {row.get('instrument')} lots={row.get('sizing_lots')} "
                     f"risk=Rs{row.get('sizing_risk_rupees', 0):,.0f} "
                     f"premium=Rs{row.get('sizing_premium_rupees', 0):,.0f}"))
        if args.write and rows:
            ids = persist_tickets(connection, rows)
            print(f"wrote {len(ids)} ticket rows (ids {ids[0]}..{ids[-1]})")
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
