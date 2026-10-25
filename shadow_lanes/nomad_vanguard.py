"""Read-only API surface for Vanguard research and prospective paper trading.

Vanguard (M1-M10) is a research/paper system that lives OUTSIDE this backend --
its code sits in its own git worktree and runs as its own process against the
same Postgres. This router therefore projects Vanguard's TABLES and imports
none of its Python. That is deliberate:

  - the backend must not depend on a worktree path that may not exist in the
    container, and must not import research code into the API process;
  - Vanguard writes on its own cadence (`make daily-cycle`), so the API is a
    reader of whatever that last wrote, never a trigger for it. There is no
    POST here at all -- doctrine #4 forbids an execution layer, and a "run it
    now" button from a browser is exactly the execution layer it forbids.

THE DUPLICATED KNOWLEDGE, AND HOW MUCH OF IT IS LEFT. `/funnel` used to
re-express M6's entire candidate filter in SQL so the UI could show WHERE
candidates die. Two copies of one filter drift, and this one did: it could not
see the freshness legs M6 gained on 2026-08-27 because they are evaluated in
Python, not in the WHERE clause.

Vanguard's migration 006 removed the need for the copy. M6 now journals ONE
ROW PER (bar, symbol) to `candidate_evaluations` -- every input as joined, the
age of each one, and each of its six legs' own verdict -- so the funnel is a
GROUP BY over what the lane actually decided rather than a re-derivation of
what it should have decided. `/funnel` reads that journal and falls back to the
legacy re-derivation only for bars evaluated before the journal existed, which
the response labels `source: "rederived"` so nobody mistakes one for the other.

The thresholds are still mirrored (the UI renders the numbers actually applied
rather than hardcoding its own) and `backend/tests/test_vanguard_router.py`
still reads m6_select.py and asserts they match, failing loudly on a retune.
This is the same technique nav-model.test.ts uses against ViewNav.tsx.

WHY A FUNNEL AT ALL: on today's real data Vanguard emits ZERO tickets -- every
candidate dies at one leg or another, and `tickets` is nearly empty. An empty
table rendered as an empty panel is indistinguishable from a broken feed, which
is precisely the failure nav-model.ts calls out ("an em-dash indistinguishable
from a flat book"). Worse, the two have OPPOSITE meanings here: a reasoned
no-trade is the system working (doctrine #2), a stale feed producing no trade
is the system broken. The funnel is how the UI tells them apart.

WHAT `/market` AND `/symbol/{symbol}` ADD. The desk could previously show that
the lane decided nothing, but not what it decided nothing ABOUT: a trader
looking at VANGUARD could not see a single symbol's collected market
information. Those two endpoints project `candidate_evaluations` plus every
per-symbol feed the lane ingests (flow ingredients, GEX, sector RS, lead-lag,
timing, delivery, bulk/block, announcements) so the desk can render the
evidence, not just the verdict.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from .nomad_read import fetch_all as _read_all
from .nomad_research_status import challenger_status, decision_status

router = APIRouter(prefix="/api/nomad/vanguard", tags=["vanguard"])
IST = ZoneInfo("Asia/Kolkata")


@router.get("/readiness")
async def readiness() -> dict[str, Any]:
    """Decision inputs are separate from process health and held exit management."""
    return decision_status()


@router.get("/stock-challenger")
async def stock_challenger() -> dict[str, Any]:
    """Read an explicitly unpromoted equity research report; never train on GET."""
    return challenger_status()


@router.get('/stock-selection')
async def stock_selection() -> dict[str, Any]:
    """Independent stock-direction snapshot and explicit funding refusals."""
    import json
    from .nomad_research_status import RUNTIME, MAX_BYTES
    path=RUNTIME/'stock_selection/status.json'
    payload={'status':'awaiting_first_snapshot','paper_only':True,'promotion_allowed':False,'watchlist':[]}
    try:
        if path.stat().st_size<=MAX_BYTES:
            raw=json.loads(path.read_text())
            fields=('status','selection_version','source_session','published_at','observed_at','execution_basis',
                    'blockers','candidate_count','selected_count','watchlist','rejections','policy')
            payload.update({key:raw[key] for key in fields if key in raw})
    except (OSError,ValueError,TypeError):
        pass
    payload['funding_funnel']=await _fetch_all("""SELECT asset_type,horizon_label,status,
        coalesce(status_reason,'') reason,count(*) picks FROM vanguard_paper_picks
        WHERE source_session>=CURRENT_DATE-14 GROUP BY asset_type,horizon_label,status,status_reason
        ORDER BY asset_type,horizon_label,status,count(*) DESC""")
    return payload


@router.get("/paper-allocations")
async def paper_allocations(limit: int = Query(1000, ge=1, le=2000)) -> dict[str, Any]:
    """Reservations for future cohorts, separate from immutable held positions."""
    installed = await _fetch_one("SELECT to_regclass('public.vanguard_stock_allocations') AS name")
    if not installed or not installed.get("name"):
        return {"status": "not_initialized", "batches": [], "allocations": [], "paper_only": True}
    batches = await _fetch_all("""SELECT b.* FROM vanguard_stock_allocation_batches b
        WHERE b.account_id=(SELECT id FROM vanguard_paper_accounts ORDER BY activated_at DESC LIMIT 1)
        ORDER BY b.frozen_at DESC LIMIT 30""")
    allocations = await _fetch_all("""SELECT a.*,p.symbol,p.direction,p.rank,p.horizon_label,p.source_session
        FROM vanguard_stock_allocations a JOIN vanguard_paper_picks p ON p.id=a.pick_id
        WHERE a.account_id=(SELECT id FROM vanguard_paper_accounts ORDER BY activated_at DESC LIMIT 1)
        ORDER BY p.source_session DESC,p.rank,p.horizon_label LIMIT :limit""", {"limit": limit})
    return {"status": "ok", "batches": batches, "allocations": allocations,
            "paper_only": True, "new_publications_only": True,
            "note": "Ranked reservations apply to new stock publications. Existing picks, position sizes and entry costs retain their recorded terms."}

# ── Mirrored from vanguard/fusion/m6_select.py — see module docstring ────────
FLOW_MIN_ABS = 60.0
SECTOR_RS_MIN_ABS_Z = 1.0
TIMING_MIN_SCORE = 70.0
REGIME_PERMITS = ["STRONG_NEG", "NEG", "NEUTRAL"]
CONVICTION_MIN = 50.0
TOP_N_PER_BAR = 3
FLOW_MAX_AGE_SESSIONS = 3
RS_MAX_AGE_SESSIONS = 3
REGIME_MAX_AGE_BARS = 2
FLOW_MIN_INGREDIENTS = 2

# M6's six filter legs, in evaluation order, with the human sentence each one
# asserts. The desk renders these labels; the counts come from the journal.
LEGS = [
    ("flow_present", "M2 wrote a flow score for an earlier session"),
    ("flow_fresh", f"that score is <= {FLOW_MAX_AGE_SESSIONS} sessions old and built "
                   f"from >= {FLOW_MIN_INGREDIENTS} ingredients"),
    ("flow_strength", f"|flow_score| >= {FLOW_MIN_ABS}"),
    ("side_momentum", "this side's option OI/premium state is long_buildup"),
    ("sector_rs", f"|rs_z20| >= {SECTOR_RS_MIN_ABS_Z}, same direction as flow, "
                  f"<= {RS_MAX_AGE_SESSIONS} sessions old"),
    ("regime", f"GEX regime in {'/'.join(REGIME_PERMITS)}, <= {REGIME_MAX_AGE_BARS} bars old"),
    ("timing", f"timing_state = IGNITION and score >= {TIMING_MIN_SCORE}"),
]

# M7, mirrored for the risk panel. See vanguard/fusion/m7_risk.sizing_coherence
# for why these three numbers do not agree at M6's 15% stop.
RISK_PER_TRADE_PCT = 0.75
MAX_PREMIUM_PER_TRADE_PCT = 1.50
MAX_PORTFOLIO_HEAT_PCT = 2.5
MAX_CONCURRENT_POSITIONS = 3
MAX_POSITIONS_PER_SECTOR20 = 2
DAILY_LOSS_STOP_PCT = -2.0
WEEKLY_LOSS_STOP_PCT = -4.0
STOP_PCT = 0.15

# The feature tables M6 consumes, and the cadence each is written at. Used by
# /pipeline to report coverage per input rather than one blended "is it fresh".
FEATURE_TABLES = [
    ("features_flow", "ts", "M2 options informed-flow", "session"),
    ("regime", "ts", "M3 GEX regime", "bar"),
    ("sector_rs", "ts", "M4 sector relative strength", "session"),
    ("timing", "ts", "M5 microstructure timing", "bar"),
]


async def _fetch_all(sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return await _read_all(sql, params)


async def _fetch_one(sql: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
    rows = await _fetch_all(sql, params)
    return rows[0] if rows else None


async def _watchlist_items(source_session: date) -> list[dict[str, Any]]:
    return await _fetch_all(
        """SELECT source_session, rank, symbol, option_type, direction, instrument,
                  strike, expiry, source_mark_ts, source_mark,
                  q10_return, q50_return, q90_return,
                  conservative_edge, selection_threshold,
                  COALESCE(ranking_score,conservative_edge) AS ranking_score,
                  entry_ts, entry_mark,
                  latest_ts, latest_mark, return_pct, max_return_pct, min_return_pct,
                  close_ts, close_mark, close_return_pct, status, updated_at,
                  performance_audit, exit_analysis,
                  COALESCE(ranking_score,conservative_edge) >= selection_threshold AS qualified
           FROM vanguard_watchlist_items
           WHERE source_session=:source_session ORDER BY rank""",
        {"source_session": source_session},
    )


async def _watchlist_market_benchmark(
    run: dict[str, Any], items: list[dict[str, Any]],
) -> dict[str, Any]:
    """Rank the same causal exact-contract universe after the track session.

    This is intentionally a hindsight benchmark, not another selector.  The
    eligible contracts are the CE and PE contracts that the frozen model had
    actually resolved at its source timestamp.  Re-selecting strikes after the
    move would turn cheap far-OTM contracts into a flattering but meaningless
    leaderboard.
    """
    track_session = run.get("track_session")
    if run.get("status") != "closed" or track_session is None:
        return {
            "available": False,
            "track_session": track_session,
            "ce": [], "pe": [],
            "note": "The market benchmark appears after the next session's 15:15 IST close.",
        }

    start = datetime.combine(track_session, time(9, 15), IST).astimezone(timezone.utc)
    cutoff = datetime.combine(track_session, time(14, 45), IST).astimezone(timezone.utc)
    rows = await _fetch_all(
        """WITH candidates AS MATERIALIZED (
               SELECT p.symbol,p.option_type,p.instrument,p.strike,p.expiry,
                      COALESCE(p.ranking_score,p.conservative_edge) AS model_score
               FROM vanguard_model_predictions p
               WHERE p.model_version=:model_version AND p.ts=:prediction_ts
                 AND p.instrument IS NOT NULL AND p.strike IS NOT NULL
                 AND p.expiry IS NOT NULL
                 AND EXISTS (
                     SELECT 1 FROM option_premium_candles source
                     WHERE source.underlying=p.symbol
                       AND source.option_type=p.option_type
                       AND source.strike=p.strike AND source.expiry=p.expiry
                       AND source.interval='30minute' AND source.time=p.ts
                 )
           ), dedup AS MATERIALIZED (
               SELECT DISTINCT ON (c.symbol,c.option_type,c.instrument,o.time)
                      c.*,o.time,o.close::double precision AS close
               FROM candidates c
               JOIN option_premium_candles o
                 ON o.underlying=c.symbol AND o.option_type=c.option_type
                AND o.strike=c.strike AND o.expiry=c.expiry
                AND o.interval='30minute'
               WHERE o.time BETWEEN :track_start AND :track_cutoff AND o.close>0
               ORDER BY c.symbol,c.option_type,c.instrument,o.time,
                        (o.source='upstox') DESC,o.source
           ), marks AS (
               SELECT symbol,option_type,instrument,strike,expiry,model_score,
                      (array_agg(time ORDER BY time))[1] AS entry_ts,
                      (array_agg(close ORDER BY time))[1] AS entry_mark,
                      (array_agg(time ORDER BY time DESC))[1] AS close_ts,
                      (array_agg(close ORDER BY time DESC))[1] AS close_mark
               FROM dedup
               GROUP BY symbol,option_type,instrument,strike,expiry,model_score
               HAVING min(time)=:track_start AND max(time)=:track_cutoff
           ), ranked AS (
               SELECT *,close_mark/entry_mark-1 AS return_pct,
                      row_number() OVER (
                          PARTITION BY option_type
                          ORDER BY close_mark/entry_mark DESC,symbol
                      ) AS side_rank,
                      count(*) OVER (PARTITION BY option_type) AS eligible
               FROM marks
           )
           SELECT side_rank,eligible,symbol,option_type,instrument,strike,expiry,
                  model_score,entry_ts,entry_mark,close_ts,close_mark,return_pct
           FROM ranked WHERE side_rank<=10 ORDER BY option_type,side_rank""",
        {
            "model_version": run["model_version"],
            "prediction_ts": run["prediction_ts"],
            "track_start": start,
            "track_cutoff": cutoff,
        },
    )
    selected = {row["instrument"] for row in items}
    for row in rows:
        row["model_selected"] = row["instrument"] in selected
    by_side = {
        side: [row for row in rows if row["option_type"] == side]
        for side in ("CE", "PE")
    }
    coverage = {
        side.lower(): int(side_rows[0]["eligible"]) if side_rows else 0
        for side, side_rows in by_side.items()
    }
    return {
        "available": True,
        "track_session": track_session,
        "entry_time_ist": "09:15 candle close (available 09:45)",
        "exit_time_ist": "14:45 candle close (available 15:15)",
        "universe": "exact CE/PE contracts resolved by the model at the frozen source timestamp",
        "hindsight_only": True,
        "coverage": coverage,
        "ce": by_side["CE"], "pe": by_side["PE"],
    }


# NSE's session bars start at 09:15, so they sit on a :15/:45 grid. `timing`
# also carries a second, 15-minute-offset grid (:00/:30) belonging to
# instruments that trade a different session — five symbols where an NSE bar has
# roughly 210. A plain max(ts) therefore lands the entire desk on a 5-symbol
# phantom bar whenever one of those is newest, which is most evenings. Every
# "latest bar" in this router is grid-qualified for that reason.
_ON_NSE_GRID = """
    EXTRACT(minute FROM {col} AT TIME ZONE 'Asia/Kolkata') IN (15, 45)
    AND ({col} AT TIME ZONE 'Asia/Kolkata')::time BETWEEN TIME '09:15' AND TIME '15:15'
"""


async def _latest_timing_bar() -> datetime | None:
    row = await _fetch_one(
        f"SELECT max(ts) AS ts FROM timing WHERE {_ON_NSE_GRID.format(col='ts')}"
    )
    return row["ts"] if row else None


async def _funded_freshness(account_id: Any = None) -> dict[str, Any]:
    """Evaluate open risk now, independently of the last persisted equity row."""
    scope = "WHERE account_id=:account" if account_id is not None else ""
    row = await _fetch_one(f"""SELECT
        count(*) FILTER (WHERE exit_ts IS NULL) AS open_positions,
        count(*) FILTER (WHERE exit_ts IS NULL AND
            (latest_ts IS NULL OR latest_ts < now()-interval '40 minutes')) AS stale_positions,
        count(*) FILTER (WHERE exit_ts IS NULL AND exit_due_at < now()) AS overdue_positions,
        min(latest_ts) FILTER (WHERE exit_ts IS NULL) AS oldest_mark_at,
        now() AS evaluated_at
        FROM vanguard_paper_positions {scope}""",
        {"account": account_id} if account_id is not None else None)
    return row or {}


def _equity_with_freshness(snapshot: dict[str, Any] | None,
                           freshness: dict[str, Any]) -> dict[str, Any] | None:
    if not snapshot:
        return snapshot
    projected = dict(snapshot)
    projected["snapshot_stale_positions"] = snapshot.get("stale_positions")
    projected["stale_positions"] = freshness.get("stale_positions", 0)
    projected["overdue_positions"] = freshness.get("overdue_positions", 0)
    stamp = snapshot.get("ts")
    if isinstance(stamp, str):
        stamp = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    if isinstance(stamp, datetime):
        stamp = stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp
        age = max(0.0, (datetime.now(timezone.utc)-stamp).total_seconds())
        projected["snapshot_age_seconds"] = round(age, 1)
        projected["snapshot_stale"] = age > 40*60
    projected["freshness"] = freshness
    return projected


@router.get("/summary")
async def summary() -> dict[str, Any]:
    """Headline state of the whole lane: what it last evaluated, what it
    selected, what the book is doing, and how much simulated capital exists."""
    tickets = await _fetch_one(
        """SELECT count(*) AS total,
                  count(*) FILTER (WHERE emitted) AS emitted,
                  count(*) FILTER (WHERE NOT emitted) AS gated,
                  max(ts) AS last_ticket_ts
           FROM tickets"""
    )
    book = await _fetch_one(
        """SELECT count(*) FILTER (WHERE NOT o.closed) AS open_positions,
                  count(*) FILTER (WHERE o.closed) AS closed_positions,
                  count(*) FILTER (WHERE o.closed AND o.r_multiple > 0) AS winners,
                  avg(o.r_multiple) FILTER (WHERE o.closed) AS avg_r,
                  sum(o.pnl_rupees) FILTER (WHERE o.closed) AS realized_pnl
           FROM outcomes o"""
    )
    capital = await _fetch_one(
        """SELECT dt, starting_equity, ending_equity, realized_pnl
           FROM paper_capital_daily ORDER BY dt DESC LIMIT 1"""
    )
    latest_bar = await _latest_timing_bar()
    swing = await _fetch_one(
        """SELECT source_session,prediction_ts,item_count,status,decision_at,
                  actionable_count,actionable_note,ranking_policy
           FROM vanguard_swing_watchlist_runs ORDER BY source_session DESC LIMIT 1"""
    )

    book = book or {}
    legacy_book_summary = dict(book)
    funded = await _fetch_one("""SELECT count(*) FILTER(WHERE exit_ts IS NULL) open_positions,
       count(*) FILTER(WHERE exit_ts IS NOT NULL) closed_positions,
       count(*) FILTER(WHERE pnl_rupees>0) winners,COALESCE(sum(pnl_rupees),0) realized_pnl
       FROM vanguard_paper_positions""")
    funded_equity = _equity_with_freshness(
        await _fetch_one("SELECT * FROM vanguard_paper_equity ORDER BY ts DESC LIMIT 1"),
        await _funded_freshness())
    for key in ("open_positions","closed_positions","winners","realized_pnl"):
        book[key] = (float(book.get(key) or 0) + float((funded or {}).get(key) or 0) if key=="realized_pnl"
                     else int(book.get(key) or 0) + int((funded or {}).get(key) or 0))
    if funded_equity:
        book["unrealized_pnl"] = funded_equity["unrealized_pnl"]
        book["stale_positions"] = funded_equity["stale_positions"]
        book["overdue_positions"] = funded_equity["overdue_positions"]
        book["valuation_stale"] = bool(funded_equity.get("snapshot_stale") or book["stale_positions"])
    closed = (book or {}).get("closed_positions") or 0
    winners = (book or {}).get("winners") or 0
    return {
        "latest_timing_bar": latest_bar,
        "activity": {"swing": swing, "legacy_ticket_book": True,
                     "note": "Research observations and actionable selections are separate; legacy ticket P&L does not measure the current swing rankers."},
        "tickets": tickets or {},
        "legacy_book": legacy_book_summary,
        "paper_account": funded_equity,
        "book": {
            **(book or {}),
            "hit_rate": (winners / closed) if closed else None,
            # Carried INTO `book` (not left only under `capital`) so the shared
            # landing-card reader in nav-model.normalizeBook can pick it up from
            # a single path; its alias list already recognises `total_equity`.
            # Unrealized P&L is deliberately NOT here: nothing marks open paper
            # positions to market, and inventing a 0 would read as a flat book
            # rather than an unmeasured one. The desk declares it absent.
            "total_equity": (float((capital or {}).get("ending_equity") or 0) + float((funded_equity or {}).get("equity") or 0)) if capital or funded_equity else None,
        },
        "capital": capital,
        "thresholds": {
            "flow_min_abs": FLOW_MIN_ABS,
            "sector_rs_min_abs_z": SECTOR_RS_MIN_ABS_Z,
            "timing_min_score": TIMING_MIN_SCORE,
            "regime_permits": REGIME_PERMITS,
            "conviction_min": CONVICTION_MIN,
            "top_n_per_bar": TOP_N_PER_BAR,
        },
    }


@router.get("/model")
async def model_status() -> dict[str, Any]:
    """Versioned nonlinear selector and its latest paper/shadow predictions.

    This endpoint is read-only.  In particular, a shadow model remains visible
    without being able to emit a paper ticket, so negative holdout evidence is
    not hidden behind an apparently empty selection panel.
    """
    model = await _fetch_one(
        """SELECT version, family, status, horizon_bars, cost_pct,
                  cost_provenance, training_start, training_end,
                  validation_start, validation_end, test_start, test_end,
                  n_train, n_validation, n_test, feature_names, metrics,
                  artifact_sha256, created_at
           FROM vanguard_model_versions
           ORDER BY (horizon_bars = 24) DESC,
                    (status = 'paper_active') DESC, created_at DESC LIMIT 1"""
    )
    if model is None:
        return {
            "model": None, "predictions": {},
            "note": "No nonlinear Vanguard model has been registered.",
        }
    latest = await _fetch_one(
        """SELECT max(ts) AS ts, count(*) AS evaluated,
                  count(*) FILTER (WHERE selected) AS selected,
                  count(*) FILTER (WHERE realized_return IS NOT NULL) AS resolved,
                  avg(realized_net_return) FILTER
                      (WHERE realized_net_return IS NOT NULL) AS realized_net_mean,
                  max(COALESCE(ranking_score,conservative_edge)) AS best_ranking_score,
                  max(conservative_edge) AS best_edge,
                  max(selection_threshold) AS selection_threshold
           FROM vanguard_model_predictions WHERE model_version = :version
             AND ts=(SELECT max(ts) FROM vanguard_model_predictions WHERE model_version=:version)""",
        {"version": model["version"]},
    )
    expected_timing_policy = (
        "completed_eod_direction_1_2d_v1"
        if model.get("horizon_bars") == 24 else "completed_same_bar_v1"
    )
    cumulative = await _fetch_one(
        """SELECT count(*) AS evaluated,
                  count(*) FILTER (WHERE timing_policy IS DISTINCT FROM :timing_policy) AS legacy,
                  count(*) FILTER (WHERE realized_return IS NOT NULL
                      AND timing_policy=:timing_policy) AS resolved,
                  avg(realized_net_return) FILTER
                      (WHERE timing_policy=:timing_policy) AS realized_net_mean
           FROM vanguard_model_predictions WHERE model_version=:version""",
        {"version": model["version"], "timing_policy": expected_timing_policy})
    recent = await _fetch_all(
        """SELECT ts, symbol, option_type, q10_return, q50_return, q90_return,
                  conservative_edge, selection_threshold,
                  COALESCE(ranking_score,conservative_edge) AS ranking_score,
                  selected, reason, instrument
                  , realized_return, realized_net_return, resolved_at,
                  source_mark_ts, decision_at, timing_policy
           FROM vanguard_model_predictions
           WHERE model_version = :version
             AND ts=(SELECT max(ts) FROM vanguard_model_predictions WHERE model_version=:version)
           ORDER BY ts DESC, COALESCE(ranking_score,conservative_edge) DESC LIMIT 30""",
        {"version": model["version"]},
    )
    ratio_coverage = await _fetch_one(
        """SELECT count(*) AS snapshots,
                  count(*) FILTER (WHERE straddle_to_spot IS NOT NULL) AS straddles,
                  count(*) FILTER (WHERE premium_pcr IS NOT NULL) AS premium_pcr,
                  count(*) FILTER (WHERE wing_valid) AS valid_wings,
                  min(ts) AS first_ts, max(ts) AS last_ts
           FROM option_premium_ratios"""
    )
    return {
        "model": model, "predictions": latest or {}, "cumulative": cumulative or {}, "recent": recent,
        "ratio_coverage": ratio_coverage or {},
        "paper_only": True,
        "selection_policy": {
            "m2_m5": "nonlinear features, not sequential vetoes",
            "m7": "sizing only",
            "abstention": "conservative edge must clear the versioned threshold",
            "session_cap": 3,
            "ratio_atm": "nearest common call/put strike to spot; forward series unavailable",
            "premium_pcr": "premium turnover ratio, not buyer-initiated flow",
            "wing_quality": "25-delta wings are missing unless both sides are within 0.08 delta",
            "timing": "same completed NSE candle for timing, option and ratio inputs; no stale fallback",
            "flow_rs": "previous completed session; current intraday derivations are not model inputs",
            "activation": "frozen shadow observation; research training cannot auto-promote",
            "target": model.get("metrics", {}).get("target_policy"),
            "ranking_score": model.get("metrics", {}).get("ranking_score"),
        },
    }


@router.get("/watchlist")
async def model_watchlist(
    sessions: int = Query(20, ge=1, le=100, description="Daily lists to return"),
    source_session: date | None = Query(None, description="Read an earlier frozen list"),
) -> dict[str, Any]:
    """Immutable daily model lists and their next-session mark performance.

    Watchlist rows are observations, not tickets.  Returns start at the first
    exact-contract 30-minute close in the following observed session, so an
    overnight gap is not silently presented as an executable model return.
    """
    runs = await _fetch_all(
        """SELECT r.source_session, r.model_version, r.prediction_ts,
                  r.track_session, r.item_count, r.top_n, r.selection_rule,
                  r.status, r.generated_at, r.started_at, r.closed_at,
                  m.family, m.horizon_bars, m.metrics AS model_metrics,
                  count(i.id) FILTER (WHERE i.entry_mark IS NOT NULL) AS marked,
                  count(i.id) FILTER
                      (WHERE i.status='closed' AND i.close_return_pct IS NOT NULL) AS resolved,
                  count(i.id) FILTER
                      (WHERE i.status='closed' AND i.close_return_pct > 0) AS winners,
                  count(i.id) FILTER
                      (WHERE COALESCE(i.ranking_score,i.conservative_edge)
                             >= i.selection_threshold) AS qualified,
                  avg(i.close_return_pct) FILTER
                      (WHERE i.status='closed' AND i.close_return_pct IS NOT NULL) AS avg_return_pct,
                  avg(i.close_return_pct) FILTER
                      (WHERE i.close_return_pct IS NOT NULL) AS close_avg_return_pct
           FROM vanguard_watchlist_runs r
           JOIN vanguard_model_versions m ON m.version=r.model_version
           LEFT JOIN vanguard_watchlist_items i ON i.source_session=r.source_session
           GROUP BY r.source_session, r.model_version, r.prediction_ts,
                    r.track_session, r.item_count, r.top_n, r.selection_rule,
                    r.status, r.generated_at, r.started_at, r.closed_at,
                    m.family, m.horizon_bars, m.metrics
           ORDER BY r.source_session DESC LIMIT :sessions""",
        {"sessions": sessions},
    )
    if not runs:
        return {
            "latest": None, "items": [], "current": None, "current_items": [],
            "latest_completed": None, "history": [], "paper_only": True,
            "note": "No daily model watchlist has been captured yet.",
        }
    current = runs[0]
    latest_evaluated = next((run for run in runs if run.get('status') == 'closed'), None)
    latest_completed = next(
        (run for run in runs if run.get("status") == "closed" and (run.get("resolved") or 0) > 0),
        None,
    )
    # The Frozen view defaults to the newest list with a completed outcome.
    # The newest emitted list remains independently exposed as `current`, even
    # while it is waiting for the next session.  This prevents an unresolved
    # list from hiding yesterday's completed performance.
    latest = (latest_completed or current) if source_session is None else next(
        (r for r in runs if r["source_session"] == source_session), None)
    if latest is None:
        raise HTTPException(404, "Frozen session not found in the requested history window")
    items = await _watchlist_items(latest["source_session"])
    current_items = (
        items if current["source_session"] == latest["source_session"]
        else await _watchlist_items(current["source_session"])
    )
    policy = await _fetch_one(
        "SELECT version,policy,registered_at FROM vanguard_watchlist_exit_policies ORDER BY registered_at DESC LIMIT 1")
    analysed = [r for r in items if r.get("exit_analysis")]
    exited = [r for r in analysed
              if (r["exit_analysis"].get("runner") or {}).get("net_return_pct") is not None]
    # Compare identical contracts, not a favourable subset with missing exits.
    runner_net = [float(r["exit_analysis"]["runner"]["net_return_pct"]) for r in exited]
    paired_hold = [float(r["exit_analysis"]["baseline_net_return_pct"]) for r in exited]
    stop_control = [float(r["exit_analysis"]["hard_stop_control"]["net_return_pct"])
                    for r in exited if (r["exit_analysis"].get("hard_stop_control") or {}).get("net_return_pct") is not None]
    summary = {
        "analysed": len(analysed), "runner_exited": len(exited), "total": len(items),
        "runner_net_mean": sum(runner_net) / len(runner_net) if runner_net else None,
        "paired_hold_net_mean": sum(paired_hold) / len(paired_hold) if paired_hold else None,
        "worst_runner_net": min(runner_net) if runner_net else None,
        "worst_paired_hold_net": min(paired_hold) if paired_hold else None,
        "stop_only_net_mean": sum(stop_control) / len(stop_control) if stop_control and len(stop_control) == len(exited) else None,
        "fully_paired": bool(items) and len(exited) == len(items),
        "prospective": bool(analysed) and all(r["exit_analysis"].get("validation_basis") ==
                                             "prospective_policy" for r in analysed),
    }

    # During the session the immutable EOD list does not exist yet. Surface a
    # read-only preview from the newest completed model snapshot so "no list"
    # is not confused with "no model run". The preview ranks every observable
    # contract and shows threshold qualification separately; it never writes a
    # watchlist row or creates a ticket.
    preview_head = await _fetch_one(
        """SELECT p.model_version, p.ts AS prediction_ts,
                  m.family, m.horizon_bars, m.metrics AS model_metrics,
                  (p.ts AT TIME ZONE 'Asia/Kolkata')::date AS source_session
           FROM vanguard_model_predictions p
           JOIN vanguard_model_versions m ON m.version=p.model_version
           WHERE m.horizon_bars=24 AND m.status='shadow'
             AND p.ts=(SELECT max(p2.ts) FROM vanguard_model_predictions p2
                       JOIN vanguard_model_versions m2 ON m2.version=p2.model_version
                       WHERE m2.horizon_bars=24 AND m2.status='shadow')
           ORDER BY m.created_at DESC LIMIT 1"""
    )
    preview = None
    preview_items: list[dict[str, Any]] = []
    if preview_head and (
        preview_head["source_session"] != current["source_session"]
        or preview_head["model_version"] != current["model_version"]
    ):
        preview_items = await _fetch_all(
            """WITH sides AS MATERIALIZED (
                   SELECT p.*,
                          row_number() OVER (
                              PARTITION BY p.symbol
                              ORDER BY COALESCE(p.ranking_score,p.conservative_edge) DESC,
                                       p.option_type
                          ) AS side_rank
                   FROM vanguard_model_predictions p
                   WHERE p.model_version=:version AND p.ts=:ts
                     AND p.instrument IS NOT NULL AND p.entry_mark IS NOT NULL
               ), best_sides AS MATERIALIZED (
                   SELECT * FROM sides WHERE side_rank=1
               )
               SELECT row_number() OVER
                          (ORDER BY conservative_edge DESC, symbol, option_type) AS rank,
                      symbol, option_type,
                      CASE WHEN option_type='CE' THEN 'bullish' ELSE 'bearish' END AS direction,
                      instrument, strike, expiry, source_mark_ts, entry_mark AS source_mark,
                      q10_return, q50_return, q90_return,
                      conservative_edge,
                      COALESCE(ranking_score,conservative_edge) AS ranking_score,
                      selection_threshold,
                      COALESCE(ranking_score,conservative_edge) >= selection_threshold AS qualified,
                      reason
               FROM best_sides
               ORDER BY COALESCE(ranking_score,conservative_edge) DESC,
                        symbol, option_type LIMIT 10""",
            {"version": preview_head["model_version"], "ts": preview_head["prediction_ts"]},
        )
        # Rank is presentation metadata for this non-persisted preview. Assign
        # it after the final SQL ordering so query-planner window placement
        # cannot expose the pre-deduped CE/PE row number.
        for index, row in enumerate(preview_items, start=1):
            row["rank"] = index
        preview = {
            **preview_head,
            "status": "provisional",
            "item_count": len(preview_items),
            "qualified": sum(1 for row in preview_items if row["qualified"]),
            "selection_rule": (
                "latest common completed feature/spot/option cohort; best CE/PE "
                "direction per underlying; ranked by within-symbol median margin; "
                "read-only and not frozen"
            ),
        }
    market_benchmark = await _watchlist_market_benchmark(latest, items)
    market_ranks = {
        row["instrument"]: {"side_rank": row["side_rank"], "option_type": row["option_type"]}
        for side in ("ce", "pe") for row in market_benchmark.get(side, [])
    }
    model_successes = []
    for row in sorted(
        (item for item in items
         if item.get("status") == "closed" and (item.get("close_return_pct") or 0) > 0),
        key=lambda item: float(item["close_return_pct"]), reverse=True,
    ):
        model_successes.append({
            "rank": row["rank"], "symbol": row["symbol"],
            "option_type": row["option_type"], "instrument": row["instrument"],
            "entry_ts": row["entry_ts"], "entry_mark": row["entry_mark"],
            "close_ts": row["close_ts"], "close_mark": row["close_mark"],
            "return_pct": row["close_return_pct"],
            "market_side_rank": market_ranks.get(row["instrument"], {}).get("side_rank"),
        })
    return {
        "latest": latest,
        "items": items,
        "current": current,
        "current_items": current_items,
        "latest_completed": latest_completed,
        "latest_evaluated": latest_evaluated,
        "history": runs,
        "preview": preview,
        "preview_items": preview_items,
        "paper_only": True,
        "market_benchmark": market_benchmark,
        "model_successes": model_successes,
        "performance_basis": (
            "first to latest exact-contract 30-minute close, capped at the scheduled "
            "15:15 IST exit in the next observed NSE session; mark-to-mark before costs. "
            "Best/worst exclude the entry candle. "
            "Peak is an observed opportunity, not a realizable exit."
        ),
        "membership": (
            "daily top-ranked shadow observation list; one side per underlying; "
            "qualification is displayed separately and remains mandatory for tickets"
        ),
        "provenance": (
            "Neural 1-2-session directional watchlist"
            if latest.get("horizon_bars") == 24
            else "Legacy next-session observation of a 30-minute option model"
        ) + ", NOT the MP gap_overnight (BTST) strategy",
        "horizon_note": (
            "This frozen list predicts the underlying direction over the next one and two "
            "sessions; its exact option is a next-session performance proxy."
            if latest.get("horizon_bars") == 24 else
            "This historical list came from the former next-30-minute model; carrying it "
            "into the next session was an observation target, not a BTST forecast."
        ),
        "btst_note": "BTST is the separate MP structure book: prior-close signal, next-open exit. "
                     "A list generated after the prior close cannot claim that overnight gap.",
        "exit_policy": policy, "exit_summary": summary,
    }


@router.get("/strategy-journals")
async def strategy_journals(
    source_session: date | None = Query(None, description="Earlier swing list to inspect"),
    limit: int = Query(200, ge=1, le=1000),
) -> dict[str, Any]:
    """Three isolated paper/shadow journals plus the new swing watchlist.

    The API remains read-only. Between persisted 30-minute marks, the browser
    overlays the existing quote-bus stream by exact instrument identifier.
    """
    runs = await _fetch_all(
        """SELECT r.*,
                  count(i.id) FILTER (WHERE i.entry_mark IS NOT NULL) marked,
                  count(i.id) FILTER (WHERE i.status='closed') resolved,
                  count(i.id) FILTER (WHERE i.return_pct>0) winners,
                  avg(i.return_pct) FILTER (WHERE i.entry_mark IS NOT NULL) avg_return_pct,
                  count(i.id) FILTER (WHERE i.paper_position) paper_positions,
                  count(i.id) FILTER (WHERE i.day_1_return_pct IS NOT NULL) day_1_resolved,
                  count(i.id) FILTER (WHERE i.day_2_return_pct IS NOT NULL) day_2_resolved,
                  count(i.id) FILTER (WHERE i.day_3_return_pct IS NOT NULL) day_3_resolved,
                  avg(i.day_1_return_pct) FILTER (WHERE i.day_1_return_pct IS NOT NULL) day_1_avg_return_pct,
                  avg(i.day_2_return_pct) FILTER (WHERE i.day_2_return_pct IS NOT NULL) day_2_avg_return_pct,
                  avg(i.day_3_return_pct) FILTER (WHERE i.day_3_return_pct IS NOT NULL) day_3_avg_return_pct
           FROM vanguard_swing_watchlist_runs r
           LEFT JOIN vanguard_swing_watchlist_items i USING (source_session)
           GROUP BY r.source_session,r.prediction_ts,r.direction_model_version,
                    r.contract_model_version,r.top_n,r.item_count,r.status,r.decision_at,
                    r.entry_session,r.generated_at,r.updated_at
           ORDER BY r.source_session DESC LIMIT 100"""
    )
    selected = None
    if runs:
        selected = runs[0] if source_session is None else next(
            (row for row in runs if row["source_session"] == source_session), None)
        if selected is None:
            raise HTTPException(404, "Swing session not found")
    swing_items = await _fetch_all(
        """SELECT * FROM vanguard_swing_watchlist_items
           WHERE source_session=:source_session ORDER BY rank""",
        {"source_session": selected["source_session"]},
    ) if selected else []
    # The candle archive identifies contracts with Upstox keys; the live feed
    # uses Fyers symbols. The subscription manager owns that translation and
    # refreshes it every 45 seconds, so the browser can subscribe to the exact
    # streamed contract without changing the immutable journal identity.
    if swing_items:
        def registered_app_symbol(symbol): return None  # Retained history never receives a fabricated live symbol.
        for item in swing_items:
            item["live_symbol"] = registered_app_symbol(item.get("instrument"))
    journal_rows = await _fetch_all(
        """SELECT * FROM vanguard_strategy_journal
           ORDER BY event_ts DESC,id DESC LIMIT :limit""",
        {"limit": limit},
    )
    journals = {"gap_overnight": [], "swing_1_2d": [], "oversold_mtf": []}
    for row in journal_rows:
        journals[row["strategy"]].append(row)
    # The daily output is two layers, and they must not be presented as one
    # list with a flag: the research ranking is mandatory and always complete,
    # while the actionable list is allowed to be empty and says why.
    research = {
        "CE": [row for row in swing_items if row["option_type"] == "CE"],
        "PE": [row for row in swing_items if row["option_type"] == "PE"],
    }
    for side_rows in research.values():
        side_rows.sort(key=lambda row: row.get("side_rank") or row["rank"])
    actionable = [row for row in swing_items if row.get("actionable")]
    return {
        "swing": {
            "latest": selected, "items": swing_items, "history": runs,
            "research_ranking": research,
            "actionable": {
                "items": actionable,
                "count": len(actionable),
                "note": (selected or {}).get("actionable_note"),
                "gates": "expected return, model confidence, liquidity, M7 risk",
                "empty_is_valid": True,
            },
        },
        "journals": journals,
        "paper_only": True,
        "realtime": {
            "transport": "quote_bus", "coalesce_ms": 150,
            "persisted_marks": "exact-contract completed 30-minute candles",
            "decision_snapshot": "immutable 14:15 IST bar; available after 14:45",
        },
    }


async def _journal_exists() -> bool:
    row = await _fetch_one(
        """SELECT to_regclass('public.candidate_evaluations') IS NOT NULL AS present"""
    )
    return bool(row and row["present"])


@router.get("/funnel")
async def funnel(ts: datetime | None = Query(None, description="Bar to explain; default = latest timing bar")) -> dict[str, Any]:
    """Where candidates die at one 30-minute bar.

    Read from `candidate_evaluations` -- the journal M6 writes as it decides,
    one row per symbol with each of its six legs' own verdict. `source` says
    which path produced the answer, because they are not equivalent:

      "journal"   what M6 actually decided at this bar, legs included.
      "rederived" a re-expression of the filter in SQL, for bars evaluated
                  before the journal existed. It CANNOT see the freshness legs
                  (M6 evaluates those in Python), so it will report a candidate
                  surviving further than it really would today. Labelled so the
                  difference is never invisible.
    """
    bar_ts = ts or await _latest_timing_bar()
    if bar_ts is None:
        raise HTTPException(status_code=404, detail="no timing bars exist yet")

    if await _journal_exists():
        journal = await _funnel_from_journal(bar_ts)
        if journal is not None:
            return journal
    return await _funnel_rederived(bar_ts)


async def _funnel_from_journal(bar_ts: datetime) -> dict[str, Any] | None:
    row = await _fetch_one(
        """SELECT count(*) AS entered,
                  count(*) FILTER (WHERE leg_flow_present)  AS flow_present,
                  count(*) FILTER (WHERE leg_flow_fresh)    AS flow_fresh,
                  count(*) FILTER (WHERE leg_flow_strength) AS flow_strength,
                  count(*) FILTER (WHERE leg_side_momentum) AS side_momentum,
                  count(*) FILTER (WHERE leg_sector_rs)     AS sector_rs,
                  count(*) FILTER (WHERE leg_regime)        AS regime,
                  count(*) FILTER (WHERE leg_timing)        AS timing,
                  count(*) FILTER (WHERE survived_filter)   AS survivors,
                  max(config_hash)                          AS config_hash
           FROM candidate_evaluations WHERE ts = :ts""",
        {"ts": bar_ts},
    )
    if not row or not row["entered"]:
        return None

    # Actual admission failures only: dissenting opinions on admitted rows
    # are not rejections. Keep examples for failures recorded by the journal.
    deaths = await _fetch_all(
        """SELECT first_failed_leg AS leg, count(*) AS n,
                  (array_agg(symbol ORDER BY symbol))[1:12] AS examples
           FROM candidate_evaluations
           WHERE ts = :ts AND NOT survived_filter AND first_failed_leg IS NOT NULL
           GROUP BY first_failed_leg""",
        {"ts": bar_ts},
    )
    by_leg = {d["leg"]: d for d in deaths}

    entered = int(row["entered"])
    survivors = int(row["survivors"] or 0)
    # Per-leg booleans are independent opinions under the current weighted M6.
    # A failed opinion can coexist with survived_filter=True; it is not a death.
    stages = [{"leg": "timing_bar", "stage": "evaluated symbols",
               "surviving": entered, "lost_here": 0, "gate": "M5 timing row", "examples": []}]
    for leg, gate in LEGS:
        death = by_leg.get(leg, {})
        stages.append({"leg": leg, "stage": leg.replace("_", " "),
            "surviving": int(row[leg] or 0), "passed": int(row[leg] or 0),
            "not_supporting": entered - int(row[leg] or 0),
            "lost_here": int(death.get("n") or 0),
            "gate": gate, "examples": death.get("examples") or [],
            "count_basis": "independent_leg_verdict"})
    binding = max((s for s in stages if s["lost_here"]), key=lambda s:s["lost_here"], default=None)
    return {"ts": bar_ts, "source": "journal", "config_hash": row["config_hash"],
        "mode": "independent_opinions", "stages": stages,
        "binding_constraint": binding["leg"] if binding else None,
        "binding_lost": binding["lost_here"] if binding else 0, "survivors": survivors,
        "rejected": entered - survivors,
        "note": "Per-leg counts are independent evidence, not sequential attrition. Only NOT survived_filter rows count as rejected; admission is not a sized or filled ticket."}



async def _funnel_rederived(bar_ts: datetime) -> dict[str, Any]:
    """Pre-journal fallback. See /funnel's docstring for what it cannot see."""
    row = await _fetch_one(
        """
        WITH bar AS (
            SELECT tm.symbol, tm.ts, tm.timing_state, tm.timing_score
            FROM timing tm WHERE tm.ts = :ts
        ),
        joined AS (
            SELECT b.symbol, b.ts, b.timing_state, b.timing_score,
                   st.sector20, fl.flow_score, sr.rs_z20, rg.regime
            FROM bar b
            LEFT JOIN sector_taxonomy st ON st.symbol = b.symbol
            LEFT JOIN LATERAL (
                SELECT flow_score FROM features_flow f
                WHERE f.symbol = b.symbol AND f.ts::date < :day
                ORDER BY f.ts DESC LIMIT 1
            ) fl ON true
            LEFT JOIN LATERAL (
                SELECT rs_z20 FROM sector_rs s
                WHERE s.sector20 = st.sector20 AND s.ts::date < :day
                ORDER BY s.ts DESC LIMIT 1
            ) sr ON true
            LEFT JOIN LATERAL (
                SELECT regime FROM regime r
                WHERE r.symbol = b.symbol AND r.ts <= b.ts
                ORDER BY r.ts DESC LIMIT 1
            ) rg ON true
        )
        SELECT
          count(*) AS symbols_at_bar,
          count(*) FILTER (WHERE flow_score IS NOT NULL) AS has_prior_flow,
          count(*) FILTER (WHERE flow_score IS NOT NULL
                             AND abs(flow_score) >= :flow_min) AS flow_passes,
          count(*) FILTER (WHERE flow_score IS NOT NULL
                             AND abs(flow_score) >= :flow_min
                             AND rs_z20 IS NOT NULL
                             AND abs(rs_z20) >= :rs_min
                             AND (rs_z20 > 0) = (flow_score > 0)) AS sector_confirms,
          count(*) FILTER (WHERE flow_score IS NOT NULL
                             AND abs(flow_score) >= :flow_min
                             AND rs_z20 IS NOT NULL
                             AND abs(rs_z20) >= :rs_min
                             AND (rs_z20 > 0) = (flow_score > 0)
                             AND regime = ANY(:permits)) AS regime_permits_count,
          count(*) FILTER (WHERE flow_score IS NOT NULL
                             AND abs(flow_score) >= :flow_min
                             AND rs_z20 IS NOT NULL
                             AND abs(rs_z20) >= :rs_min
                             AND (rs_z20 > 0) = (flow_score > 0)
                             AND regime = ANY(:permits)
                             AND timing_state = 'IGNITION'
                             AND timing_score >= :timing_min) AS survives_all
        FROM joined
        """,
        {
            "ts": bar_ts,
            "day": bar_ts.date(),
            "flow_min": FLOW_MIN_ABS,
            "rs_min": SECTOR_RS_MIN_ABS_Z,
            "timing_min": TIMING_MIN_SCORE,
            "permits": REGIME_PERMITS,
        },
    )
    row = row or {}
    counts = [
        ("timing_bar", "symbols with a timing bar", row.get("symbols_at_bar") or 0,
         "M5 wrote a row for this bar"),
        ("flow_present", "has prior-session flow", row.get("has_prior_flow") or 0,
         "M2 features_flow exists for an earlier session"),
        ("flow_strength", "flow conviction", row.get("flow_passes") or 0,
         f"|flow_score| >= {FLOW_MIN_ABS}"),
        ("sector_rs", "sector RS confirms", row.get("sector_confirms") or 0,
         f"|rs_z20| >= {SECTOR_RS_MIN_ABS_Z} and same direction as flow"),
        ("regime", "regime permits", row.get("regime_permits_count") or 0,
         f"M3 regime in {'/'.join(REGIME_PERMITS)}"),
        ("timing", "timing fires", row.get("survives_all") or 0,
         f"timing_state = IGNITION and score >= {TIMING_MIN_SCORE}"),
    ]
    stages = []
    for index, (leg, stage, surviving, gate) in enumerate(counts):
        previous = counts[index - 1][2] if index else surviving
        stages.append({"leg": leg, "stage": stage, "surviving": surviving,
                       "lost_here": max(0, previous - surviving), "gate": gate,
                       "examples": []})
    binding = max((s for s in stages if s["lost_here"]),
                  key=lambda s: s["lost_here"], default=None)
    return {
        "ts": bar_ts,
        "source": "rederived",
        "note": "This bar predates the evaluation journal. The freshness legs "
                "M6 applies in Python are NOT reflected here, so these counts "
                "are an upper bound on what would survive today.",
        "stages": stages,
        "binding_constraint": binding["leg"] if binding else None,
        "binding_lost": binding["lost_here"] if binding else 0,
        "survivors": row.get("survives_all") or 0,
    }


@router.get("/selection")
async def selection(
    ts: datetime | None = Query(None, description="Bar to show; default = the most recent bar that produced any ticket"),
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    """Tickets at one bar -- BOTH emitted and gated-out.

    Gated rows are the point, not noise: doctrine #5 keeps every near-miss with
    the gate that stopped it, so the UI can show a reasoned no-trade rather
    than a blank panel.
    """
    bar_ts = ts
    if bar_ts is None:
        row = await _fetch_one("SELECT max(ts) AS ts FROM tickets")
        bar_ts = row["ts"] if row else None
    if bar_ts is None:
        return {"ts": None, "tickets": [], "note": "no tickets have ever been generated"}

    tickets = await _fetch_all(
        """SELECT id, ts, symbol, instrument, direction, conviction, rank_in_session,
                  regime_at_ts, evidence, emitted, gated_reason,
                  entry_zone_low, entry_zone_high, stop, target1, target2,
                  sizing_lots, sizing_notional, sizing_risk_rupees, sizing_method,
                  strike, option_type, expiry, lot_size
           FROM tickets WHERE ts = :ts
           ORDER BY emitted DESC, conviction DESC
           LIMIT :limit""",
        {"ts": bar_ts, "limit": limit},
    )
    return {"ts": bar_ts, "tickets": tickets, "conviction_min": CONVICTION_MIN}


@router.get("/legacy-book")
async def legacy_book(limit: int = Query(200, ge=1, le=1000)) -> dict[str, Any]:
    """The paper book: open positions, closed trades, and the equity curve.

    Every row here originates from a SIMULATED fill against real historical
    bars. `fills.fill_method` is carried through verbatim so the UI can render
    the simulation basis rather than implying a broker fill.
    """
    open_positions = await _fetch_all(
        """SELECT t.id, t.symbol, t.instrument, t.direction, t.conviction,
                  t.stop, t.target1, t.target2, t.sizing_lots, t.lot_size,
                  t.sizing_risk_rupees,
                  f.fill_price, f.fill_ts, f.fill_method
           FROM outcomes o
           JOIN tickets t ON t.id = o.ticket_id
           JOIN fills f ON f.ticket_id = o.ticket_id
           WHERE NOT o.closed
           ORDER BY f.fill_ts DESC
           LIMIT :limit""",
        {"limit": limit},
    )
    closed = await _fetch_all(
        """SELECT t.id, t.symbol, t.instrument, t.direction, t.conviction,
                  t.sizing_lots, t.lot_size,
                  f.fill_price, f.fill_ts, f.fill_method,
                  o.exit_price, o.exit_ts, o.exit_reason, o.pnl_rupees,
                  o.r_multiple, o.holding_bars
           FROM outcomes o
           JOIN tickets t ON t.id = o.ticket_id
           JOIN fills f ON f.ticket_id = o.ticket_id
           WHERE o.closed
           ORDER BY o.exit_ts DESC
           LIMIT :limit""",
        {"limit": limit},
    )
    equity = await _fetch_all(
        """SELECT dt, starting_equity, ending_equity, realized_pnl
           FROM paper_capital_daily ORDER BY dt ASC LIMIT :limit""",
        {"limit": limit},
    )
    by_reason = await _fetch_all(
        """SELECT exit_reason, count(*) AS n, avg(r_multiple) AS avg_r
           FROM outcomes WHERE closed AND exit_reason IS NOT NULL
           GROUP BY exit_reason ORDER BY n DESC"""
    )
    return {
        "open_positions": open_positions,
        "closed": closed,
        "equity_curve": equity,
        "exit_reason_breakdown": by_reason,
    }


@router.get("/attribution")
async def attribution(limit: int = Query(30, ge=1, le=200)) -> dict[str, Any]:
    """M10 rollups, newest first. `latest` is broken out so the UI does not
    have to know that runs are append-only."""
    runs = await _fetch_all(
        """SELECT id, run_at, as_of_date, n_tickets_closed, hit_rate, avg_r,
                  conviction_decile_monotonic, report
           FROM attribution_runs ORDER BY run_at DESC LIMIT :limit""",
        {"limit": limit},
    )
    return {"latest": runs[0] if runs else None, "runs": runs}


@router.get("/backtests")
async def backtests(limit: int = Query(30, ge=1, le=200)) -> dict[str, Any]:
    """M8 replay results. Deliberately a DIFFERENT table from attribution:
    a backtest is not a track record and the UI must never blend them."""
    runs = await _fetch_all(
        """SELECT id, run_at, start_ts, end_ts, report
           FROM vanguard_backtest_runs ORDER BY run_at DESC LIMIT :limit""",
        {"limit": limit},
    )
    return {"latest": runs[0] if runs else None, "runs": runs}


@router.get("/pipeline")
async def pipeline() -> dict[str, Any]:
    """Feed health per M6 input, plus the overlap window where all four inputs
    actually coexist.

    The overlap is the number that explains the lane: M6 needs flow + sector RS
    (both prior-session) AND regime + timing (both same-bar) simultaneously. If
    those coverages barely intersect, the lane cannot emit no matter how the
    thresholds are tuned -- and that is a DATA problem the UI must show as such
    rather than letting it read as a quiet strategy decision.
    """
    feeds = []
    for table, ts_column, label, cadence in FEATURE_TABLES:
        stats = await _fetch_one(
            f"""SELECT count(*) AS rows,
                       min({ts_column}) AS first_ts,
                       max({ts_column}) AS last_ts,
                       count(DISTINCT {'symbol' if table != 'sector_rs' else 'sector20'}) AS entities
                FROM {table}"""  # noqa: S608 - table/column names are from a fixed local constant, never user input
        )
        feeds.append({"table": table, "label": label, "cadence": cadence, **(stats or {})})

    coverage = await _fetch_all(
        """SELECT d AS session,
                  (SELECT count(*) FROM features_flow f WHERE f.ts::date = d) AS flow_rows,
                  (SELECT count(*) FROM timing t WHERE date(t.ts AT TIME ZONE 'Asia/Kolkata') = d) AS timing_rows,
                  (SELECT count(*) FROM regime r WHERE date(r.ts AT TIME ZONE 'Asia/Kolkata') = d) AS regime_rows
           FROM (
               SELECT DISTINCT date(ts AT TIME ZONE 'Asia/Kolkata') AS d
               FROM timing
               ORDER BY 1 DESC LIMIT 30
           ) days
           ORDER BY d DESC"""
    )

    ingest = await _fetch_all(
        """SELECT collector, target_date, status, rows_written, run_at, detail
           FROM ingest_log ORDER BY run_at DESC LIMIT 40"""
    )

    sessions_all_present = sum(
        1 for row in coverage
        if (row.get("flow_rows") or 0) > 0 and (row.get("timing_rows") or 0) > 0 and (row.get("regime_rows") or 0) > 0
    )
    return {
        "feeds": feeds,
        "recent_sessions": coverage,
        "sessions_with_all_inputs": sessions_all_present,
        "sessions_examined": len(coverage),
        "ingest_log": ingest,
    }


# ── The market view: what the lane actually collected, per symbol ──────────
#
# The desk could previously show that Vanguard decided nothing, but not what it
# decided nothing ABOUT. These two endpoints are the evidence layer.

EVALUATION_SELECT_COLUMNS = """
    ce.symbol, ce.sector20, ce.direction, ce.conviction,
    ce.flow_score, ce.flow_ts, ce.flow_age_sessions, ce.flow_n_ingredients,
    ce.rs_z20, ce.rs_ts, ce.rs_age_sessions,
    ce.regime, ce.gex_percentile, ce.regime_ts, ce.regime_age_bars,
    ce.timing_state, ce.timing_score, ce.rvol, ce.va_position,
    ce.best_lag, ce.leadlag_corr,
    ce.leg_flow_present, ce.leg_flow_fresh, ce.leg_flow_strength,
    ce.leg_sector_rs, ce.leg_regime, ce.leg_timing,
    ce.first_failed_leg, ce.survived_filter, ce.component_scores,
    ce.signed_flow, ce.signed_rs, ce.signed_timing, ce.signed_regime
"""


@router.get("/market")
async def market(
    ts: datetime | None = Query(None, description="Bar to show; default = latest evaluated bar"),
    limit: int = Query(400, ge=1, le=1000),
) -> dict[str, Any]:
    """Every symbol the lane evaluated at one bar, with its collected inputs.

    This is `candidate_evaluations` projected straight out -- no filter, no
    ranking cut. A symbol that failed the very first leg appears alongside one
    that survived every leg, because "why is nothing happening" is answered by
    the failures, not by the survivors.

    Each row carries the AGE of every joined input next to the input itself.
    That pairing is the point: a flow score of +82 means one thing when it was
    computed yesterday and something else entirely when it was computed a month
    ago, and until 2026-08-27 the lane could not tell the difference.

    `session_track` gives each symbol its own conviction and timing_score
    across today's bars, so the desk can draw a sparkline per row without a
    request per symbol.
    """
    if not await _journal_exists():
        return {
            "ts": None, "symbols": [], "legs": LEGS,
            "unavailable": "candidate_evaluations does not exist yet — apply "
                           "vanguard/db/migrations/006 and run M6 for one bar.",
        }
    bar_ts = ts
    if bar_ts is None:
        row = await _fetch_one(
            f"SELECT max(ts) AS ts FROM candidate_evaluations "
            f"WHERE {_ON_NSE_GRID.format(col='ts')}"
        )
        bar_ts = row["ts"] if row else None
    if bar_ts is None:
        return {
            "ts": None, "symbols": [], "legs": LEGS,
            "unavailable": "the evaluation journal is empty — M6 has not run "
                           "since migration 006 was applied.",
        }

    # Open interest, positioning state and price performance are joined per
    # symbol from `oi_positioning` at the most recent session AT OR BEFORE this
    # bar's own date. The `<=` is deliberate: OI is an end-of-session
    # publication, so a bar from mid-session is matched against the last
    # SETTLED positioning read rather than one that did not exist yet.
    symbols = await _fetch_all(
        f"""SELECT {EVALUATION_SELECT_COLUMNS},
                   oi.dt AS oi_dt, oi.total_oi, oi.d_oi, oi.d_oi_pct, oi.oi_source,
                   oi.mwpl_pct, oi.ce_oi, oi.pe_oi, oi.oi_pcr, oi.d_oi_pcr,
                   oi.oi_state, oi.oi_state_strength,
                   oi.close, oi.d_price_pct, oi.ret_5d, oi.ret_20d, oi.ret_60d,
                   iv.atm_iv, iv.ivs, iv.skew_25d, iv.skew_reason,
                   iv.iv_percentile, iv.iv_rank, iv.d_atm_iv,
                   iv.n_strikes AS iv_n_strikes, iv.dt AS iv_dt
            FROM candidate_evaluations ce
            LEFT JOIN LATERAL (
                SELECT * FROM oi_positioning o
                WHERE o.symbol = ce.symbol
                  AND o.dt <= date(ce.ts AT TIME ZONE 'Asia/Kolkata')
                ORDER BY o.dt DESC LIMIT 1
            ) oi ON true
            LEFT JOIN LATERAL (
                SELECT * FROM iv_surface s
                WHERE s.symbol = ce.symbol
                  AND s.dt <= date(ce.ts AT TIME ZONE 'Asia/Kolkata')
                ORDER BY s.dt DESC LIMIT 1
            ) iv ON true
            WHERE ce.ts = :ts
            ORDER BY ce.survived_filter DESC, ce.conviction DESC NULLS LAST, ce.symbol
            LIMIT :limit""",  # noqa: S608 - EVALUATION_SELECT_COLUMNS is a fixed local constant
        {"ts": bar_ts, "limit": limit},
    )

    session_track = await _fetch_all(
        """SELECT symbol,
                  array_agg(conviction ORDER BY ts) AS conviction_track,
                  array_agg(timing_score ORDER BY ts) AS timing_track,
                  count(*) AS bars
           FROM candidate_evaluations
           WHERE date(ts AT TIME ZONE 'Asia/Kolkata')
                 = date(:ts AT TIME ZONE 'Asia/Kolkata')
           GROUP BY symbol""",
        {"ts": bar_ts},
    )
    tracks = {t["symbol"]: t for t in session_track}
    for row in symbols:
        track = tracks.get(row["symbol"], {})
        row["conviction_track"] = track.get("conviction_track") or []
        row["timing_track"] = track.get("timing_track") or []

    # Coverage per input, at THIS bar. A desk that shows only the winners
    # cannot distinguish "no signal" from "no data"; these three counts can.
    coverage = await _fetch_one(
        """SELECT count(*) AS evaluated,
                  count(flow_score) AS with_flow,
                  count(*) FILTER (WHERE leg_flow_fresh) AS with_fresh_flow,
                  count(rs_z20) AS with_sector_rs,
                  count(regime) AS with_regime,
                  count(timing_score) AS with_timing,
                  count(*) FILTER (WHERE timing_state = 'IGNITION') AS igniting,
                  count(*) FILTER (WHERE survived_filter) AS survivors,
                  min(flow_age_sessions) AS min_flow_age,
                  max(flow_age_sessions) AS max_flow_age
           FROM candidate_evaluations WHERE ts = :ts""",
        {"ts": bar_ts},
    )
    oi_coverage = await _fetch_one(
        """SELECT count(*) FILTER (WHERE o.total_oi IS NOT NULL) AS with_oi,
                  count(*) FILTER (WHERE o.oi_state IS NOT NULL) AS with_oi_state,
                  count(*) FILTER (WHERE o.mwpl_pct >= 95) AS at_mwpl_limit,
                  max(o.dt) AS oi_session
           FROM candidate_evaluations ce
           LEFT JOIN LATERAL (
               SELECT * FROM oi_positioning o
               WHERE o.symbol = ce.symbol
                 AND o.dt <= date(ce.ts AT TIME ZONE 'Asia/Kolkata')
               ORDER BY o.dt DESC LIMIT 1
           ) o ON true
           WHERE ce.ts = :ts""",
        {"ts": bar_ts},
    )
    iv_coverage = await _fetch_one(
        """SELECT count(*) FILTER (WHERE s.atm_iv IS NOT NULL) AS with_iv,
                  count(*) FILTER (WHERE s.ivs IS NOT NULL) AS with_ivs,
                  count(*) FILTER (WHERE s.skew_25d IS NOT NULL) AS with_skew,
                  max(s.dt) AS iv_session
           FROM candidate_evaluations ce
           LEFT JOIN LATERAL (
               SELECT * FROM iv_surface s
               WHERE s.symbol = ce.symbol
                 AND s.dt <= date(ce.ts AT TIME ZONE 'Asia/Kolkata')
               ORDER BY s.dt DESC LIMIT 1
           ) s ON true
           WHERE ce.ts = :ts""",
        {"ts": bar_ts},
    )
    coverage = {**(coverage or {}), **(oi_coverage or {}), **(iv_coverage or {})}

    by_sector = await _fetch_all(
        """SELECT coalesce(sector20, '(unclassified)') AS sector20,
                  count(*) AS n,
                  avg(conviction) AS avg_conviction,
                  count(*) FILTER (WHERE timing_state = 'IGNITION') AS igniting,
                  count(*) FILTER (WHERE survived_filter) AS survivors,
                  avg(rs_z20) AS avg_rs_z20
           FROM candidate_evaluations WHERE ts = :ts
           GROUP BY 1 ORDER BY avg_conviction DESC NULLS LAST""",
        {"ts": bar_ts},
    )

    bars = await _fetch_all(
        """SELECT DISTINCT ts FROM candidate_evaluations
           WHERE date(ts AT TIME ZONE 'Asia/Kolkata')
                 = date(:ts AT TIME ZONE 'Asia/Kolkata')
           ORDER BY ts""",
        {"ts": bar_ts},
    )

    return {
        "ts": bar_ts,
        "symbols": symbols,
        "coverage": coverage or {},
        "by_sector": by_sector,
        "session_bars": [b["ts"] for b in bars],
        "legs": LEGS,
        "thresholds": {
            "flow_min_abs": FLOW_MIN_ABS,
            "sector_rs_min_abs_z": SECTOR_RS_MIN_ABS_Z,
            "timing_min_score": TIMING_MIN_SCORE,
            "regime_permits": REGIME_PERMITS,
            "conviction_min": CONVICTION_MIN,
            "flow_max_age_sessions": FLOW_MAX_AGE_SESSIONS,
            "rs_max_age_sessions": RS_MAX_AGE_SESSIONS,
            "regime_max_age_bars": REGIME_MAX_AGE_BARS,
            "flow_min_ingredients": FLOW_MIN_INGREDIENTS,
        },
    }


@router.get("/symbol/{symbol}")
async def symbol_detail(
    symbol: str,
    bars: int = Query(60, ge=1, le=400, description="30-minute bars of intraday history"),
    sessions: int = Query(60, ge=1, le=400, description="sessions of daily history"),
) -> dict[str, Any]:
    """Everything Vanguard has collected about ONE underlying.

    Deliberately one request rather than eight: the desk renders these panels
    together, and eight round trips per symbol click against a shared
    production database is exactly the polling pattern this repo has been bitten
    by before.

    Every block is independently nullable. A symbol with no option chain since
    July genuinely has no flow history, and that block comes back empty rather
    than zero-filled -- the desk renders "not collected" for it, which is a
    different statement from "collected and flat".
    """
    symbol = symbol.upper()
    catalog = await _fetch_one(
        """SELECT c.symbol, c.lot_size, c.spot_instrument_key AS instrument_key,
                  t.sector, t.sector_group, t.sector20, t.instrument_type
           FROM fo_underlying_catalog c
           LEFT JOIN sector_taxonomy t ON t.symbol = c.symbol
           WHERE c.symbol = :symbol""",
        {"symbol": symbol},
    )
    if catalog is None:
        catalog = await _fetch_one(
            """SELECT symbol, NULL::int AS lot_size, NULL::text AS instrument_key,
                      sector, sector_group, sector20, instrument_type
               FROM sector_taxonomy WHERE symbol = :symbol""",
            {"symbol": symbol},
        )
    if catalog is None:
        raise HTTPException(status_code=404, detail=f"{symbol} is not in Vanguard's universe")

    evaluations = []
    if await _journal_exists():
        evaluations = await _fetch_all(
            f"""SELECT ce.ts, {EVALUATION_SELECT_COLUMNS}
                FROM candidate_evaluations ce
                WHERE ce.symbol = :symbol ORDER BY ce.ts DESC LIMIT :bars""",  # noqa: S608
            {"symbol": symbol, "bars": bars},
        )

    flow = await _fetch_all(
        """SELECT ts, ivs, ivs_z, skew, skew_z, os_pctile, oi_state, pcr_z,
                  flow_score, n_ingredients
           FROM features_flow WHERE symbol = :symbol
           ORDER BY ts DESC LIMIT :sessions""",
        {"symbol": symbol, "sessions": sessions},
    )
    regime = await _fetch_all(
        """SELECT ts, net_gex, regime, gamma_flip_level, gex_percentile
           FROM regime WHERE symbol = :symbol ORDER BY ts DESC LIMIT :bars""",
        {"symbol": symbol, "bars": bars},
    )
    timing = await _fetch_all(
        """SELECT ts, timing_state, timing_score, rvol, va_position
           FROM timing WHERE symbol = :symbol ORDER BY ts DESC LIMIT :bars""",
        {"symbol": symbol, "bars": bars},
    )
    sector_rs = await _fetch_all(
        """SELECT ts, sector20, rs_z5, rs_z20, rs_z60
           FROM sector_rs WHERE sector20 = :sector20
           ORDER BY ts DESC LIMIT :sessions""",
        {"sector20": catalog.get("sector20"), "sessions": sessions},
    ) if catalog.get("sector20") else []
    leadlag = await _fetch_all(
        """SELECT dt, sector20, best_lag, corr FROM leadlag
           WHERE symbol = :symbol ORDER BY dt DESC LIMIT 30""",
        {"symbol": symbol},
    )

    # Price bars, on the same :15/:45 exchange grid M5 computes its features
    # on. An unfiltered read would mix in the second, 15-minute-offset grid and
    # the chart would not line up with the timing rows drawn over it.
    price = await _fetch_all(
        """SELECT DISTINCT ON (time) time AS ts, open, high, low, close, volume
           FROM underlying_spot_candles
           WHERE underlying = :symbol AND interval = '30minute'
             AND EXTRACT(minute FROM time AT TIME ZONE 'Asia/Kolkata') IN (15, 45)
             AND (time AT TIME ZONE 'Asia/Kolkata')::time
                 BETWEEN TIME '09:15' AND TIME '15:15'
           ORDER BY time DESC, volume DESC NULLS LAST
           LIMIT :bars""",
        {"symbol": symbol, "bars": bars},
    )
    oi_history = await _fetch_all(
        """SELECT dt, total_oi, d_oi, d_oi_pct, oi_source, mwpl, mwpl_pct,
                  ce_oi, pe_oi, oi_pcr, d_oi_pcr, close, d_price_pct,
                  ret_5d, ret_20d, ret_60d, oi_state, oi_state_strength
           FROM oi_positioning WHERE symbol = :symbol
           ORDER BY dt DESC LIMIT :sessions""",
        {"symbol": symbol, "sessions": sessions},
    )
    iv_history = await _fetch_all(
        """SELECT dt, expiry, atm_iv, atm_strike, call_iv, put_iv, ivs, skew_25d,
                  skew_reason, iv_percentile, iv_rank, d_atm_iv, n_strikes, n_good,
                  delta_span
           FROM iv_surface WHERE symbol = :symbol
           ORDER BY dt DESC LIMIT :sessions""",
        {"symbol": symbol, "sessions": sessions},
    )
    iv_chain = await _fetch_all(
        """SELECT strike, option_type, iv, delta, gamma, vega, theta, premium,
                  oi, volume, log_moneyness, quality, quality_flags, iv_uncertainty
           FROM option_iv
           WHERE symbol = :symbol AND dt = (
               SELECT max(dt) FROM option_iv WHERE symbol = :symbol)
           ORDER BY option_type, strike""",
        {"symbol": symbol},
    )
    delivery = await _fetch_all(
        """SELECT dt, close, prev_close, volume, value, deliverable_qty, delivery_pct
           FROM bhavcopy_delivery WHERE symbol = :symbol
           ORDER BY dt DESC LIMIT :sessions""",
        {"symbol": symbol, "sessions": sessions},
    )
    deals = await _fetch_all(
        """SELECT dt, client_name, deal_type, kind, quantity, price
           FROM bulk_block WHERE symbol = :symbol ORDER BY dt DESC LIMIT 40""",
        {"symbol": symbol},
    )
    news = await _fetch_all(
        """SELECT dt, subject, category, attachment_url
           FROM announcements WHERE symbol = :symbol ORDER BY dt DESC LIMIT 25""",
        {"symbol": symbol},
    )
    results = await _fetch_all(
        """SELECT results_date, source FROM results_calendar
           WHERE symbol = :symbol AND results_date >= current_date - 90
           ORDER BY results_date DESC LIMIT 10""",
        {"symbol": symbol},
    )
    tickets = await _fetch_all(
        """SELECT id, ts, instrument, direction, conviction, emitted, gated_reason,
                  entry_zone_low, entry_zone_high, stop, target1, target2,
                  sizing_lots, sizing_risk_rupees, sizing_premium_rupees, sizing_method
           FROM tickets WHERE symbol = :symbol ORDER BY ts DESC LIMIT 40""",
        {"symbol": symbol},
    )
    # The F&O ban list is READ here and nowhere else in the lane. M7 does not
    # veto on it yet (see the 2026-08-27 review), so the desk showing a banned
    # name must say plainly that the risk gate is not enforcing it rather than
    # implying a control that does not exist.
    ban = await _fetch_one(
        """SELECT snapshot_date AS dt, symbol, reason FROM fo_security_ban
           WHERE symbol = :symbol ORDER BY snapshot_date DESC LIMIT 1""",
        {"symbol": symbol},
    )

    return {
        "symbol": symbol,
        "catalog": catalog,
        "evaluations": evaluations,
        "flow_history": flow,
        "regime_history": regime,
        "timing_history": timing,
        "sector_rs_history": sector_rs,
        "leadlag": leadlag,
        "oi_history": oi_history,
        "iv_history": iv_history,
        "iv_chain": iv_chain,
        "price_bars": price,
        "delivery": delivery,
        "bulk_block": deals,
        "announcements": news,
        "results_calendar": results,
        "tickets": tickets,
        "ban": ban,
        "ban_note": "fo_security_ban is displayed here but NOT enforced by M7 — "
                    "a banned name can still be sized. Reported, not implied.",
    }


@router.get("/risk")
async def risk() -> dict[str, Any]:
    """M7's configuration, the book it currently sees, and whether its three
    sizing numbers actually agree with each other.

    The coherence block mirrors `vanguard/fusion/m7_risk.sizing_coherence`. It
    is on the desk rather than buried in a module because the numbers do NOT
    agree at M6's 15% stop, and a risk panel that renders the configured limits
    without saying they cannot bind would be asserting a control that is not
    there.
    """
    premium_needed = RISK_PER_TRADE_PCT / STOP_PCT
    effective_risk = min(RISK_PER_TRADE_PCT, MAX_PREMIUM_PER_TRADE_PCT * STOP_PCT)
    stopouts_to_daily = abs(DAILY_LOSS_STOP_PCT) / effective_risk if effective_risk else None

    capital = await _fetch_one(
        """SELECT dt, starting_equity, ending_equity, realized_pnl
           FROM paper_capital_daily ORDER BY dt DESC LIMIT 1"""
    )
    open_book = await _fetch_all(
        """SELECT t.symbol, t.instrument, t.direction, t.sizing_lots,
                  t.sizing_risk_rupees, t.sizing_premium_rupees, t.sizing_risk_basis,
                  st.sector20, f.fill_price, f.fill_ts
           FROM outcomes o
           JOIN tickets t ON t.id = o.ticket_id
           JOIN fills f ON f.ticket_id = o.ticket_id
           LEFT JOIN sector_taxonomy st ON st.symbol = t.symbol
           WHERE NOT o.closed
           ORDER BY f.fill_ts DESC"""
    )
    equity = float((capital or {}).get("ending_equity") or 0) or None
    heat = None
    if equity:
        heat = 100.0 * sum(float(p["sizing_risk_rupees"] or 0) for p in open_book) / equity

    return {
        "limits": {
            "risk_per_trade_pct": RISK_PER_TRADE_PCT,
            "max_premium_per_trade_pct": MAX_PREMIUM_PER_TRADE_PCT,
            "max_portfolio_heat_pct": MAX_PORTFOLIO_HEAT_PCT,
            "max_concurrent_positions": MAX_CONCURRENT_POSITIONS,
            "max_positions_per_sector20": MAX_POSITIONS_PER_SECTOR20,
            "daily_loss_stop_pct": DAILY_LOSS_STOP_PCT,
            "weekly_loss_stop_pct": WEEKLY_LOSS_STOP_PCT,
            "stop_pct": STOP_PCT,
        },
        "coherence": {
            "coherent": premium_needed <= MAX_PREMIUM_PER_TRADE_PCT,
            "binding_cap": "premium" if premium_needed > MAX_PREMIUM_PER_TRADE_PCT else "risk_at_stop",
            "premium_needed_for_intended_risk_pct": premium_needed,
            "effective_risk_pct": effective_risk,
            "stopouts_to_daily_standdown": stopouts_to_daily,
            "daily_standdown_reachable_in_one_session":
                bool(stopouts_to_daily and stopouts_to_daily <= MAX_CONCURRENT_POSITIONS),
            "explanation":
                f"Risking {RISK_PER_TRADE_PCT}% of capital behind a stop {STOP_PCT:.0%} away "
                f"requires holding {premium_needed:.2f}% of capital in premium, which the "
                f"{MAX_PREMIUM_PER_TRADE_PCT}% premium cap forbids. The premium cap binds "
                f"first, so a stop-out actually costs {effective_risk:.3f}% and the "
                f"{DAILY_LOSS_STOP_PCT}% daily stand-down needs "
                f"{stopouts_to_daily:.1f} of them against a {MAX_CONCURRENT_POSITIONS}-position cap.",
        },
        "capital": capital,
        "open_positions": open_book,
        "portfolio_heat_pct": heat,
    }


@router.get("/cross-section")
async def cross_section(
    horizon: int | None = Query(None, description="filter to one forward horizon, in 30m bars"),
    limit: int = Query(60, ge=1, le=400),
) -> dict[str, Any]:
    """The cross-sectional IC study: does any component actually order names?

    Kept separate from `/attribution` for the same reason M8 is kept separate
    from M10 -- they answer different questions on different samples, and
    blending them would let a number measured on thousands of symbol-bars sit
    next to one measured on a handful of closed tickets as though they carried
    the same weight.
    """
    exists = await _fetch_one(
        "SELECT to_regclass('public.cross_section_ic') IS NOT NULL AS present")
    if not (exists and exists["present"]):
        return {"runs": [], "unavailable": "cross_section_ic does not exist yet — "
                                            "apply vanguard/db/migrations/006."}
    latest = await _fetch_one("SELECT max(as_of_date) AS d FROM cross_section_ic")
    as_of = (latest or {}).get("d")
    if as_of is None:
        return {"runs": [], "as_of": None,
                "unavailable": "no IC study has been run yet — `make ic`, or wait "
                               "for the EOD cycle."}
    rows = await _fetch_all(
        """SELECT component, horizon_bars, n_obs, n_sessions, mean_ic,
                  ic_se_clustered, t_stat, ci_low, ci_high, report, window_start, window_end
           FROM cross_section_ic
           WHERE as_of_date = :as_of
             AND (CAST(:horizon AS INTEGER) IS NULL OR horizon_bars = :horizon)
           ORDER BY horizon_bars, component
           LIMIT :limit""",
        {"as_of": as_of, "horizon": horizon, "limit": limit},
    )
    history = await _fetch_all(
        """SELECT as_of_date, component, horizon_bars, mean_ic, t_stat, n_sessions
           FROM cross_section_ic
           ORDER BY as_of_date DESC, component LIMIT 400"""
    )
    return {
        "as_of": as_of,
        "runs": rows,
        "history": history,
        "note": "n in every t-statistic is the number of SESSIONS, not observations. "
                "Same-session names share a market-wide shock and are not independent.",
    }


@router.get("/sentiment")
async def sentiment(limit: int = Query(60, ge=1, le=400)) -> dict[str, Any]:
    """Market-wide sentiment: participant positioning, PCR, volatility, breadth.

    MARKET-WIDE ONLY, and the response says so. NSE's participant-wise OI file
    is an aggregate by instrument class with no per-symbol dimension, so FII
    positioning can describe a regime and can never be attributed to a name.

    `sentiment_score` arrives with `sentiment_components` and is NULL whenever
    fewer than three of the five families were available -- renormalising a
    blend over one family makes that family the score, which on 2026-08-27
    produced a +100 reading from a single input.
    """
    exists = await _fetch_one(
        "SELECT to_regclass('public.market_sentiment') IS NOT NULL AS present")
    if not (exists and exists["present"]):
        return {"latest": None, "history": [],
                "unavailable": "market_sentiment does not exist yet — apply "
                               "vanguard/db/migrations/009."}
    rows = await _fetch_all(
        """SELECT * FROM market_sentiment ORDER BY dt DESC LIMIT :limit""",
        {"limit": limit},
    )
    if not rows:
        return {"latest": None, "history": [],
                "unavailable": "no sentiment sessions computed yet — "
                               "`make eod-cycle`, or run features/m_sentiment.py."}
    # The newest row that actually carries a composite. The most recent session
    # often has only the same-day feeds (participant OI and the chain) and no
    # settled price, so its score is legitimately suppressed; leading the panel
    # with it would render an empty headline every evening.
    scored = next((r for r in rows if r.get("sentiment_score") is not None), None)
    return {
        "latest": rows[0],
        "latest_scored": scored,
        "history": list(reversed(rows)),
        "scope": "market-wide",
        "note": "participant_oi is an aggregate publication by instrument class. "
                "FII/DII positioning describes the market, never an individual name.",
    }


# ─── Market Profile structure (features_mp + the MP-edge paper book) ─────────
#
# One payload for the desk's MP tab: the latest session's profile features,
# the two validated signal flags with the researched-universe boundary drawn
# explicitly, and the mp_paper_trades book those flags feed. The verdicts that
# scope what each metric may claim live on /api/mp/unified/verdicts (mp_core);
# the tab fetches them separately so the two surfaces cannot drift.

_MP_RESEARCHED = (
    "NIFTY", "BANKNIFTY", "HDFCBANK", "ICICIBANK", "SBIN", "AXISBANK",
    "KOTAKBANK", "INDUSINDBK", "BANKBARODA", "PNB", "CANBK", "UNIONBANK",
    "FEDERALBNK", "IDFCFIRSTB", "AUBANK", "RBLBANK", "YESBANK", "BANKINDIA",
)


@router.get("/mp")
async def mp_structure(
    dt: str | None = Query(None, description="Session date; default = latest"),
    limit: int = Query(300, le=1000),
) -> dict[str, Any]:
    present = await _fetch_one(
        "SELECT to_regclass('public.features_mp') IS NOT NULL AS present"
    )
    if not (present and present["present"]):
        return {"available": False,
                "note": "features_mp does not exist yet — the MP structure "
                        "step has not run (migration 011 + cycle daemon EOD)."}

    target = dt or (await _fetch_one(
        "SELECT max(dt)::text AS d FROM features_mp"))["d"]
    if target is None:
        return {"available": False, "note": "features_mp holds no sessions yet."}

    # asyncpg types the parameter from the column, so it must be a real date --
    # a CAST-from-string raises "'str' has no attribute 'toordinal'".
    from datetime import date as _date

    target_d = _date.fromisoformat(str(target))
    rows = await _fetch_all(
        """SELECT * FROM features_mp WHERE dt = :d
           ORDER BY (sig_strong_close OR sig_oversold_mtf) DESC,
                    exp_range_pct DESC NULLS LAST
           LIMIT :lim""",
        {"d": target_d, "lim": limit})
    sessions = await _fetch_all(
        "SELECT DISTINCT dt::text AS d FROM features_mp ORDER BY 1 DESC LIMIT 15")

    day_types: dict[str, int] = {}
    for r in rows:
        key = r.get("day_type") or "unknown"
        day_types[key] = day_types.get(key, 0) + 1
    researched = set(_MP_RESEARCHED)
    for r in rows:
        r["researched"] = r["underlying"] in researched
        r["dt"] = str(r["dt"])
        r.pop("computed_at", None)

    trades = await _fetch_all(
        """SELECT id, strategy, underlying, signal_dt::text AS signal_dt,
                  entry_px, entry_src, notional, cost_bp,
                  exit_ts::text AS exit_ts, exit_px, exit_reason,
                  gross_ret_pct, net_ret_pct, status
           FROM mp_paper_trades ORDER BY signal_dt DESC, id DESC LIMIT 200""")
    trade_summary = await _fetch_all(
        """SELECT strategy, status, count(*) AS n,
                  round(avg(net_ret_pct), 3) AS avg_net_pct,
                  round(sum(net_ret_pct * notional / 100.0), 0) AS pnl_rs,
                  round(avg((net_ret_pct > 0)::int) * 100, 0) AS win_pct
           FROM mp_paper_trades GROUP BY 1, 2 ORDER BY 1, 2""")

    return {
        "available": True,
        "as_of_dt": target,
        "sessions": [s["d"] for s in sessions],
        "summary": {
            "names": len(rows),
            "flagged_strong_close": sum(1 for r in rows if r.get("sig_strong_close")),
            "flagged_oversold_mtf": sum(1 for r in rows if r.get("sig_oversold_mtf")),
            "of_available": sum(1 for r in rows if r.get("of_available")),
            "day_types": day_types,
        },
        "features": rows,
        "signals": [r for r in rows
                    if r.get("sig_strong_close") or r.get("sig_oversold_mtf")],
        "trades": trades,
        "trade_summary": trade_summary,
        "universe_note": "Signals are TRADED only inside the researched "
                         "universe (NIFTY, BANKNIFTY, 16 banks). Flags on other "
                         "names are shown for observation, never traded — "
                         "trading them would extrapolate beyond the study.",
    }


@router.get("/oi-futures")
async def oi_futures() -> dict[str, Any]:
    """Cross-section of futures OI baselines: latest scored session per symbol.

    Reads futures_oi_baselines (vanguard ingest/futures_oi.py + features/
    m_futures_oi.py) — true stock/index FUTURES open interest, complementing
    the MWPL/option-OI oi_positioning pipeline. Intraday, the newest row per
    symbol is the live running OI scored against settled baselines; after the
    EOD pass it is the settled session.
    """
    present = await _fetch_one(
        "SELECT to_regclass('public.futures_oi_baselines') IS NOT NULL AS present")
    if not (present and present["present"]):
        return {"available": False,
                "note": "futures_oi_baselines does not exist yet — run vanguard "
                        "migration 017 and the futures OI ingest/feature steps."}

    rows = await _fetch_all(
        """SELECT DISTINCT ON (b.symbol)
                  b.symbol, b.ts::text AS ts, b.expiry::text AS expiry, b.close,
                  b.d_price_pct, b.oi, b.d_oi, b.d_oi_pct, b.d_oi_pct_z,
                  b.oi_z, b.volume_z, b.oi_pctile, b.oi_state,
                  b.activity_surge, b.is_rollover, b.lookback_sessions,
                  st.sector
           FROM futures_oi_baselines b
           LEFT JOIN sector_taxonomy st ON st.symbol = b.symbol
           WHERE b.ts >= (CURRENT_DATE - INTERVAL '7 days')
           ORDER BY b.symbol, b.ts DESC""")

    states: dict[str, int] = {}
    for r in rows:
        key = r.get("oi_state") or "flat"
        states[key] = states.get(key, 0) + 1
    freshness = await _fetch_one(
        """SELECT max(ts)::text AS latest_session,
                  max(computed_at)::text AS computed_at
           FROM futures_oi_baselines""")

    return {
        "available": True,
        "rows": rows,
        "summary": {
            "names": len(rows),
            "states": states,
            "surges": sum(1 for r in rows if r.get("activity_surge")),
            "rollovers": sum(1 for r in rows if r.get("is_rollover")),
            "baseline_ready": sum(1 for r in rows if all(r.get(k) is not None
                                  for k in ("d_oi_pct_z", "volume_z", "oi_z"))),
            "warming_up": sum(1 for r in rows if any(r.get(k) is None
                              for k in ("d_oi_pct_z", "volume_z", "oi_z"))),
        },
        "latest_session": (freshness or {}).get("latest_session"),
        "computed_at": (freshness or {}).get("computed_at"),
    }


@router.get("/oi-futures/{symbol}")
async def oi_futures_symbol(
    symbol: str,
    sessions: int = Query(120, le=500),
) -> dict[str, Any]:
    """Per-symbol futures OI baseline history for the drill-down chart."""
    rows = await _fetch_all(
        """SELECT ts::text AS ts, expiry::text AS expiry, close, d_price_pct,
                  oi, d_oi, d_oi_pct, d_oi_pct_z, oi_z, volume_z, oi_pctile,
                  oi_state, activity_surge, is_rollover
           FROM futures_oi_baselines
           WHERE symbol = :symbol
             AND ts >= (CURRENT_DATE - INTERVAL '2 years')
           ORDER BY ts DESC
           LIMIT :lim""",
        {"symbol": symbol.upper(), "lim": sessions})
    rows.reverse()
    return {"symbol": symbol.upper(), "rows": rows, "sessions": len(rows)}


def _stock_selection_quality(items: list[dict], scope: str) -> list[dict]:
    """Grade stock picks separately from option outcomes and across fixed horizons.

    CE and PE are separate cohorts. One symbol/side per source day prevents
    several contracts for a name from multiplying its apparent sample size.
    Source-day means additionally keep large lists from dominating averages.
    """
    from statistics import mean, median
    result = []
    for model in sorted({r['model_version'] for r in items}):
        for side in ('CE', 'PE'):
            cohort = [r for r in items if r['model_version'] == model and r.get('option_type') == side
                      and (scope == 'all' or r.get('qualified'))]
            if not cohort:
                continue
            unique = {}
            for r in sorted(cohort, key=lambda r: r.get('rank', 0)):
                unique.setdefault((r['source_session'], r['symbol']), r)
            group = list(unique.values())
            eligible = [r for r in group if (r.get('stock') or {}).get('causal') is True
                        and not r.get('is_replay', False)]
            for name, h in (('daily', 1), ('weekly', 5), ('monthly', 21)):
                pairs = [(r, (r['stock'].get('horizons') or {}).get(name)) for r in eligible]
                valid = [(r, p) for r, p in pairs if p and p.get('data_state') == 'observed'
                         and p.get('direction_return') is not None]
                vals = [p['direction_return'] for _, p in valid]
                excess = [p['excess_return'] for _, p in valid if p.get('excess_return') is not None]
                days, excess_days = {}, {}
                for r, p in valid:
                    days.setdefault(r['source_session'], []).append(p['direction_return'])
                    if p.get('excess_return') is not None:
                        excess_days.setdefault(r['source_session'], []).append(p['excess_return'])
                result.append({
                    'model_version': model, 'side': side, 'timeframe': name, 'holding_sessions': h,
                    'scope': scope, 'selected_n': len(group), 'eligible_n': len(eligible),
                    'excluded_n': sum((bool(r.get('stock')) and r['stock'].get('causal') is not True)
                                      or bool(r.get('is_replay')) for r in group),
                    'awaiting_refresh_n': sum(not r.get('stock') for r in group),
                    'n': len(valid), 'sessions': len(days),
                    'pending_n': sum(p is not None and p.get('data_state') == 'pending' for _, p in pairs),
                    'missing_n': sum(p is not None and p.get('data_state') == 'missing' for _, p in pairs),
                    'mean_return': mean(vals) if vals else None,
                    'median_return': median(vals) if vals else None,
                    'hit_rate': mean(v > 0 for v in vals) if vals else None,
                    'mean_session_return': mean(mean(v) for v in days.values()) if days else None,
                    'benchmark_n': len(excess), 'benchmark_sessions': len(excess_days),
                    'mean_excess_return': mean(excess) if excess else None,
                    'mean_session_excess': mean(mean(v) for v in excess_days.values()) if excess_days else None,
                    'benchmark_beat_rate': mean(v > 0 for v in excess) if excess else None,
                })
    return result


@router.get('/observation-followup')
async def observation_followup(lane: str = Query('swing', pattern='^(swing|next_session)$'), stock_scope: Literal['all','qualified'] = 'all') -> dict[str, Any]:
    """Persisted multi-session evidence. Never advances the writer from a UI read."""
    exists = await _fetch_all("SELECT to_regclass('public.vanguard_observation_followup') AS name")
    if not exists or not exists[0]['name']:
        return {'items': [], 'quality': [], 'stock_quality': [], 'status': 'not_initialized', 'paper_only': True}
    rows = await _fetch_all('''SELECT payload,updated_at FROM vanguard_observation_followup
                              WHERE lane=:lane ORDER BY source_session DESC,rank''', {'lane': lane})
    items = [r['payload'] for r in rows]
    from statistics import mean, median
    quality = []
    for model in sorted({r['model_version'] for r in items}):
        group = [r for r in items if r['model_version']==model]
        for h in (1,2,3,5,10):
            pairs = [(r, (r.get('horizons') or {}).get(str(h))) for r in group if r.get('entry_on_time') is not False]
            valid = [(r,p) for r,p in pairs if p is not None and p.get('return') is not None]
            vals = [p['return'] for _,p in valid]
            directions = [p['direction_return'] for _,p in valid if p.get('direction_return') is not None]
            days = len({r['source_session'] for r,_ in valid})
            quality.append({'model_version': model, 'horizon': h, 'n':len(valid), 'sessions':days,
                            'mean_return':mean(vals) if vals else None, 'median_return':median(vals) if vals else None,
                            'positive_fraction':mean(v>0 for v in vals) if vals else None,
                            'direction_n':len(directions),
                            'direction_hit_rate':mean(v>0 for v in directions) if directions else None,
                            'qualified_n':sum(bool(r.get('qualified')) for r,_ in valid),
                            'assessment':'descriptive_only_requires_frozen_chronological_holdout'})
    # Prospective evidence gate.  Historical model gates and a populated
    # watchlist are not permission to promote: each frozen model must first
    # accumulate enough independent source sessions and show positive central
    # tendency on BOTH horizons it was designed to predict.  This is a read-only
    # verdict; it never retrains, changes membership, writes tickets, or calls a
    # broker.
    minimum_source_sessions = 20
    promotion_gates = []
    for model in sorted({r['model_version'] for r in items}):
        reasons = []
        primary = [q for q in quality if q['model_version'] == model and q['horizon'] in (1, 2)]
        for horizon in (1, 2):
            row = next((q for q in primary if q['horizon'] == horizon), None)
            if row is None:
                reasons.append(f'session +{horizon}: no quality row')
                continue
            if row['sessions'] < minimum_source_sessions:
                reasons.append(
                    f"session +{horizon}: {row['sessions']}/{minimum_source_sessions} source sessions"
                )
            for field, label in (
                ('mean_return', 'mean premium return'),
                ('median_return', 'median premium return'),
            ):
                value = row[field]
                if value is None or value <= 0:
                    reasons.append(f'session +{horizon}: {label} is not positive')
            if row['positive_fraction'] is None or row['positive_fraction'] <= 0.5:
                reasons.append(f'session +{horizon}: premium win rate is not above 50%')
            if row['direction_hit_rate'] is None or row['direction_hit_rate'] <= 0.5:
                reasons.append(f'session +{horizon}: direction hit rate is not above 50%')
        promotion_gates.append({
            'model_version': model,
            'gate_passed': not reasons,
            'status': 'eligible_for_review' if not reasons else 'hold_shadow',
            'minimum_source_sessions': minimum_source_sessions,
            'primary_horizons': [1, 2],
            'reasons': reasons,
            'policy': 'Prospective frozen evidence only; passing permits human review, never automatic promotion.',
        })
    return {'items':items,'quality':quality,'updated_at':max((r['updated_at'] for r in rows),default=None),
            'status':'ok','paper_only':True,'units':'fraction',
            'stock_quality': _stock_selection_quality(items, stock_scope), 'stock_scope': stock_scope,
            'stock_horizon_basis': 'Same frozen stock picks at 1 / 5 / 21 NSE holding sessions, including entry day. Direction-adjusted stock returns from the next session open; separate from option expiry and paper-account P&L.',
            'promotion_gates':promotion_gates,
            'horizon_basis':'Trading sessions after the entry session; session 0 is entry day. At least the 15:15 IST observation required.',
            'validation':'No retraining or promotion. Models remain separate; repeated symbols are correlated. Horizon selection requires a frozen, untouched chronological holdout.'}

@router.get("/paper")
async def paper_account(limit: int = Query(200, ge=1, le=1000)) -> dict[str, Any]:
    """Prospective funded account. No shadow or legacy returns are credited."""
    account = await _fetch_one("SELECT * FROM vanguard_paper_accounts ORDER BY activated_at DESC LIMIT 1")
    if not account:
        return {"account": None, "paper_only": True, "picks": [], "positions": [], "performance": [], "equity_curve": [], "events": []}
    params = {"account": account["id"], "limit": limit}
    # Limit only presentation, never performance totals or current open risk.
    picks = await _fetch_all("""SELECT * FROM vanguard_paper_picks WHERE account_id=:account
                              ORDER BY source_session DESC,rank,id DESC LIMIT :limit""", params)
    positions = await _fetch_all("""SELECT p.*,s.source_key,s.source_session,s.strategy,s.horizon_label,s.horizon_sessions,
        s.model_version,s.rank,s.symbol,s.instrument,s.option_type,s.strike,s.expiry,s.lot_size,s.asset_type,s.direction,
        CASE WHEN p.exit_ts IS NULL THEN 'open' ELSE 'closed' END status,
        (p.latest_ts IS NULL OR p.latest_ts < now()-interval '40 minutes') AND p.exit_ts IS NULL AS mark_stale,
        p.exit_due_at < now() AND p.exit_ts IS NULL AS exit_overdue,
        CASE WHEN p.exit_ts IS NULL THEN
          round(((CASE WHEN s.direction='SHORT' THEN -1 ELSE 1 END)*
          (p.latest_mark*(1+(CASE WHEN s.direction='SHORT' THEN 1 ELSE -1 END)*(:slip/10000.0))-p.fill_price)*p.quantity
          -p.entry_fee-p.latest_mark*p.quantity*(1+(CASE WHEN s.direction='SHORT' THEN 1 ELSE -1 END)*(:slip/10000.0))*(:fees/10000.0))::numeric,2)
        ELSE NULL END unrealized_pnl
        FROM vanguard_paper_positions p JOIN vanguard_paper_picks s ON s.id=p.pick_id
        WHERE p.account_id=:account ORDER BY (p.exit_ts IS NULL) DESC,COALESCE(p.exit_ts,p.fill_ts) DESC
        LIMIT :limit""", {**params, "slip": account["policy"]["slippage_bps"], "fees": account["policy"]["fee_bps"]})
    equity = await _fetch_all("""SELECT * FROM (SELECT * FROM vanguard_paper_equity WHERE account_id=:account
                                 ORDER BY ts DESC LIMIT 1000) e ORDER BY ts""",params)
    latest = _equity_with_freshness(
        await _fetch_one("SELECT * FROM vanguard_paper_equity WHERE account_id=:account ORDER BY ts DESC LIMIT 1",params),
        await _funded_freshness(account["id"]))
    performance = await _fetch_all("""SELECT s.strategy,s.horizon_label,s.horizon_sessions,s.model_version,s.asset_type,s.direction,
        count(*) picks,count(p.id) entries,count(p.id) FILTER(WHERE p.id IS NOT NULL AND p.exit_ts IS NULL) open,
        count(p.id) FILTER(WHERE p.exit_ts IS NOT NULL) closed,
        count(*) FILTER(WHERE s.status='skipped') skipped,count(*) FILTER(WHERE s.status='queued') queued,
        sum(p.pnl_rupees) net_pnl,avg(p.pnl_rupees) expectancy_rupees,
        avg((p.pnl_rupees>0)::int) FILTER(WHERE p.exit_ts IS NOT NULL) hit_rate,
        sum(p.pnl_rupees) FILTER(WHERE p.pnl_rupees>0) /
          NULLIF(abs(sum(p.pnl_rupees) FILTER(WHERE p.pnl_rupees<0)),0) profit_factor,
        sum(p.entry_fee+COALESCE(p.exit_fee,0)) fees_paid
        FROM vanguard_paper_picks s LEFT JOIN vanguard_paper_positions p ON p.pick_id=s.id
        WHERE s.account_id=:account GROUP BY s.strategy,s.horizon_label,s.horizon_sessions,s.model_version,s.asset_type,s.direction
        ORDER BY s.horizon_label,s.horizon_sessions""",params)
    drawdown = await _fetch_one("""WITH path AS (SELECT equity,
        greatest(:initial,max(equity) OVER(ORDER BY ts)) peak FROM vanguard_paper_equity WHERE account_id=:account)
        SELECT max((peak-equity)/NULLIF(peak,0)) AS max_drawdown FROM path""",{**params,"initial":account["initial_capital"]})
    events = await _fetch_all("""SELECT e.*,p.symbol,p.horizon_label FROM vanguard_paper_events e
        LEFT JOIN vanguard_paper_picks p ON p.id=e.pick_id WHERE e.account_id=:account ORDER BY e.id DESC LIMIT :limit""",params)
    publishers = await _fetch_all("""SELECT horizon_label,max(published_at) latest_publication,max(source_session) latest_session,
                      count(*) publications FROM vanguard_paper_picks WHERE account_id=:account GROUP BY horizon_label""",params)
    return {"account": account, "paper_only": True, "summary": latest, "picks": picks, "positions": positions,
            "performance": performance, "max_drawdown": (drawdown or {}).get("max_drawdown"),
            "equity_curve": equity, "events": events, "publishers": publishers,
            "note": "Prospective paper experiment. New independent stock-direction lists rank 1/5/21-session horizons separately from option payoff. Older cohorts retain their frozen selections and terms. Historical shadow marks and legacy ticket P&L are excluded. Shorts are fully collateralized directional simulations without borrow or financing charges; fees are estimates, not broker statutory charges."}


async def _funded_book(limit: int) -> dict[str, Any]:
    """Compatibility projection for the shared portfolio, keeping legacy separate."""
    params={"limit":limit}
    rows=await _fetch_all("""WITH ranked AS (SELECT p.*,s.symbol,s.instrument,s.option_type,s.lot_size,s.horizon_label,s.horizon_sessions,s.asset_type,s.direction,
       round(((CASE WHEN s.direction='SHORT' THEN -1 ELSE 1 END)*
          (p.latest_mark*(1+(CASE WHEN s.direction='SHORT' THEN 1 ELSE -1 END)*(a.policy->>'slippage_bps')::float/10000)-p.fill_price)*p.quantity
          -p.entry_fee-p.latest_mark*p.quantity*(1+(CASE WHEN s.direction='SHORT' THEN 1 ELSE -1 END)*(a.policy->>'slippage_bps')::float/10000)*(a.policy->>'fee_bps')::float/10000)::numeric,2) AS unrealized_pnl,
       row_number() OVER(PARTITION BY (p.exit_ts IS NULL) ORDER BY COALESCE(p.exit_ts,p.fill_ts) DESC) row_n
       FROM vanguard_paper_positions p JOIN vanguard_paper_picks s ON s.id=p.pick_id JOIN vanguard_paper_accounts a ON a.id=p.account_id)
       SELECT * FROM ranked WHERE exit_ts IS NULL OR row_n<=:limit ORDER BY fill_ts DESC""",params)
    def adapt(p):
        return {**p,"id":f"funded:{p['id']}","sizing_lots":p["lots"],
                "target1":p["target"],"fill_method":"prospective_30m_stock_open_with_costs" if p["asset_type"]=="stock" else "prospective_3m_option_open_with_costs",
                "current_premium":p["latest_mark"],"mark_ts":p["latest_ts"],
                "position_id":f"funded:{p['id']}"}
    return {"open_positions":[adapt(p) for p in rows if p["exit_ts"] is None],
            "closed":[adapt(p) for p in rows if p["exit_ts"] is not None]}


@router.get("/book")
async def book(limit: int = Query(200, ge=1, le=1000)) -> dict[str, Any]:
    legacy = await legacy_book(limit=limit)
    funded = await _funded_book(limit=limit)
    # Prefix legacy identities too: independently allocated IDs must not collide.
    for row in legacy["open_positions"] + legacy["closed"]:
        row["id"] = f"legacy:{row['id']}"
    return {**legacy, "open_positions": funded["open_positions"] + legacy["open_positions"],
            "closed": funded["closed"] + legacy["closed"], "legacy_equity_curve": legacy["equity_curve"],
            "basis": "Prospective funded account plus separately identified legacy tickets"}
