"""Create the immutable pre-close 1-3-session swing watchlist.

The exact-contract payoff ranker owns the premium-performer ordering; the
direction ranker is a deterministic tie-break and research diagnostic. This
module has no ticket or order dependency. Normal scheduled runs only accept today's
14:15 IST decision bar; ``--allow-replay`` exists solely for explicit research
reproduction and is recorded in the run/journal payload.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import psycopg2
import psycopg2.extras

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fusion.m7_risk import load_risk_state, risk_check  # noqa: E402
from model.session_clock import available_at
from model.return_calibration import expected_net_return
from model.listwise_ranker import ListwiseMLP  # noqa: E402
from model.nonlinear_selector import feature_row  # noqa: E402
from research.train_preclose_rankers import SOURCE_OPTIONS_SQL, _instrument  # noqa: E402

DEFAULT_DSN = ""
IST = ZoneInfo("Asia/Kolkata")


def _business_day(start: date, count: int) -> date:
    from model.market_calendar import is_session
    result = start
    while count:
        result += timedelta(days=1)
        if is_session(result):
            count -= 1
    return result


def _model(connection, role: str) -> ListwiseMLP | None:
    with connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
        cursor.execute(
            """SELECT version,status,artifact FROM vanguard_rank_model_versions
               WHERE role=%s AND status IN ('paper_active','shadow') ORDER BY (status='paper_active') DESC,created_at DESC LIMIT 1""",
            (role,),
        )
        row = cursor.fetchone()
    if row is None:
        return None
    return ListwiseMLP.from_artifact(
        row["artifact"], version=row["version"], role=role, status=row["status"])


def _decision_rows(connection) -> tuple[datetime | None, list[dict]]:
    with connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
        cursor.execute(
            """SELECT max(ts) ts FROM candidate_evaluations
               WHERE (ts AT TIME ZONE 'Asia/Kolkata')::time=time '14:15'"""
        )
        head = cursor.fetchone()
        ts = head["ts"] if head else None
        if ts is None:
            return None, []
        cursor.execute(
            """SELECT ce.*,spot.close::double precision source_spot
               FROM candidate_evaluations ce
               JOIN LATERAL (
                   SELECT s.close FROM underlying_spot_candles s
                   WHERE s.underlying=ce.symbol AND s.interval='30minute' AND s.time=ce.ts
                   ORDER BY CASE s.source WHEN 'upstox_spot' THEN 0 WHEN 'upstox_sweep' THEN 1
                                          WHEN 'upstox' THEN 2 WHEN 'fyers_spot' THEN 3 ELSE 9 END,
                            s.synced_at DESC LIMIT 1
               ) spot ON true
               WHERE ce.ts=%s ORDER BY ce.symbol""",
            (ts,),
        )
        return ts, [dict(row) for row in cursor.fetchall()]


# The chain sweep for a 30-minute bar lands 60-75 minutes after that bar: at
# 14:57 IST the 13:45 bar held 10,333 contract rows and the 14:15 bar held 12.
# The decision bar is still 14:15 -- features, spot and ranking all come from
# it -- but the CONTRACTS have to come from the newest chain that actually
# exists at or before it, or the lane emits nothing on the day it is meant to.
# Using an earlier bar is strictly causal; the bar actually used is stamped on
# every row as source_mark_ts, so the <=30 min skew from the trained 14:15
# marks is recorded rather than hidden.
MIN_CHAIN_BREADTH = float(os.environ.get("VANGUARD_SWING_MIN_CHAIN_BREADTH", "0.5"))
SWING_HORIZONS = (1, 2, 3)

# ── Selection shape, measured 2026-09-15 ────────────────────────────────────
# Five sessions of realised next-session returns say three things:
#   * The list never held a top-10 mover on either side, and its picks sat at
#     the 32nd-72nd percentile of the day's own ranking.
#   * The winners were CHEAP CONVEXITY: |delta| 0.11-0.25 at Rs 10-60, on names
#     that moved 3-5%. The list bought |delta| 0.44-0.58.
#   * Direction was the binding constraint, not strike: the decision-bar layer's
#     cross-sectional direction IC was +0.048 over 5 sessions (n=1,044), while
#     conviction-vs-|move| is the one relationship that has ever measured
#     positive here (t=+8.23, 2026-08-27 study). So magnitude enters the rank.
# Every one of these is env-tunable and must be re-measured over 20+ sessions
# (research/watchlist_coverage.py) before either ranker leaves shadow.
WING_DELTA_TARGET = float(os.environ.get("VANGUARD_SWING_WING_DELTA", "0.20"))
WING_DELTA_MIN = float(os.environ.get("VANGUARD_SWING_WING_DELTA_MIN", "0.15"))
WING_DELTA_MAX = float(os.environ.get("VANGUARD_SWING_WING_DELTA_MAX", "0.25"))
# Retained for the read-only blended-policy comparator. The emitted performer
# list uses the option-payoff ranker, not a conviction/magnitude blend.
MAGNITUDE_WEIGHT = float(os.environ.get("VANGUARD_SWING_MAGNITUDE_WEIGHT", "0.30"))
# A list drawn from a sliver of the market is not a ranking of the market. The
# resolvable universe swung 358 - 7,242 contracts across those five sessions.
MIN_UNIVERSE_ROWS = int(os.environ.get("VANGUARD_SWING_MIN_UNIVERSE_ROWS", "2000"))


def resolve_chain_bar(connection, ts: datetime, symbols: list[str]) -> datetime | None:
    """Newest 30m option bar at or before `ts` with a usable cross-section."""
    with connection.cursor() as cursor:
        cursor.execute(
            """SELECT o.time,count(DISTINCT o.underlying) breadth
               FROM option_premium_candles o
               WHERE o.interval='30minute' AND o.underlying=ANY(%(symbols)s)
                 AND o.time<=%(ts)s AND o.time>%(floor)s
               GROUP BY o.time ORDER BY o.time DESC""",
            {"symbols": symbols, "ts": ts, "floor": ts - timedelta(minutes=31)},
        )
        for bar_time, breadth in cursor.fetchall():
            if (bar_time.astimezone(IST).date() == ts.astimezone(IST).date()
                    and ts - timedelta(minutes=30) <= bar_time <= ts
                    and breadth >= MIN_CHAIN_BREADTH * len(symbols)):
                return bar_time
    return None


def _lot_sizes(connection, symbols: list[str]) -> dict[str, int]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT symbol,lot_size FROM fo_underlying_catalog WHERE symbol=ANY(%s)",
            (symbols,),
        )
        return {row[0]: int(row[1]) for row in cursor.fetchall() if row[1]}


def _percentiles(values: list[float]) -> np.ndarray:
    """Empirical midranks: equal observations have equal percentiles."""
    if not values:
        return np.asarray([], dtype=np.float64)
    array = np.asarray(values, dtype=np.float64)
    if not np.all(np.isfinite(array)):
        raise ValueError("ranking percentiles require finite scores")
    if len(array) == 1:
        return np.asarray([0.5])
    order = np.argsort(array, kind="stable")
    result = np.empty(len(array), dtype=np.float64)
    start = 0
    while start < len(order):
        end = start + 1
        while end < len(order) and array[order[end]] == array[order[start]]:
            end += 1
        result[order[start:end]] = (start + end - 1) / (2.0 * (len(order) - 1))
        start = end
    return result


RANKING_POLICY = "side_horizon_unique_midrank_v1"
PERFORMER_POLICY = "option_payoff_liquid_contract_v2"


def performer_candidates(candidates: list[dict]) -> list[dict]:
    """Premium-performer challenger, using the frozen option-payoff ranker.

    Directional and conviction percentiles describe different targets and must
    not swamp the option-payoff ordering. The score remains a percentile, not
    an expected return. Confidence and payoff calibration still gate action.
    """
    return [{**row, "combined_score": row["contract_pct"],
             "ranking_policy": PERFORMER_POLICY} for row in candidates]


def score_candidates(candidates: list[dict], evaluations: list[dict]) -> list[dict]:
    """Normalize in the same side/horizon query groups used by training.

    A direction or conviction observation belongs to a name, not to each of
    its strikes. Extra contract expressions must not reweight that name in
    either empirical distribution. Contract scores belong to exact expressions.
    All inputs here are available at decision time; outcomes are never read.
    """
    by_symbol = {row["symbol"]: row for row in evaluations}
    groups: dict[tuple[str, int], list[dict]] = {}
    result = [dict(candidate) for candidate in candidates]
    for row in result:
        groups.setdefault((row["option_type"], row["horizon_sessions"]), []).append(row)
    for rows in groups.values():
        names = {row["symbol"]: row["direction_score"] for row in rows}
        name_keys = sorted(names)
        direction = dict(zip(name_keys, _percentiles([names[key] for key in name_keys])))
        conviction = dict(zip(name_keys, _percentiles([
            float((by_symbol.get(key) or {}).get("conviction") or 0.0)
            for key in name_keys
        ])))
        def expression(row):
            return (row["symbol"], str(row["expiry"]), float(row["strike"]))
        contracts = {expression(row): row["contract_score"] for row in rows}
        contract_keys = sorted(contracts)
        contract = dict(zip(contract_keys, _percentiles([
            contracts[key] for key in contract_keys
        ])))
        for row in rows:
            row["direction_pct"] = float(direction[row["symbol"]])
            row["contract_pct"] = float(contract[expression(row)])
            row["conviction_pct"] = float(conviction[row["symbol"]])
            ranker = 0.60 * row["direction_pct"] + 0.40 * row["contract_pct"]
            row["combined_score"] = float(
                (1.0 - MAGNITUDE_WEIGHT) * ranker
                + MAGNITUDE_WEIGHT * row["conviction_pct"]
            )
            row["ranking_policy"] = RANKING_POLICY
    return result


def _horizon_features(model: ListwiseMLP, horizon: int) -> list[float]:
    """Encode the horizon flags declared by the trained artifact.

    v2 rankers distinguish D+1, D+2 and D+3. Refuse a silent fallback to the
    old binary encoding: treating D+3 as D+1 would mislabel the paper result.
    """
    feature_names = getattr(model, "feature_names", ())
    if "horizon_3" in feature_names:
        return [float(horizon == 2), float(horizon == 3)]
    if horizon == 3:
        raise ValueError("ranker artifact has no horizon_3 feature")
    return [float(horizon == 2)]


def eligible_contract(row: dict) -> bool:
    """Publication requires a real contract, positive OI and exact lot metadata.

    Volume and Greeks are recorded where present; absent volume is not treated
    as zero liquidity. This filter changes prospective ranks, never old lists.
    """
    try:
        return (str(row.get("instrument_key", "")).startswith(("NSE_FO|", "BSE_FO|"))
                and int(row.get("contract_lot_size") or 0) > 0
                and np.isfinite(float(row["premium"])) and float(row["premium"]) >= 5
                and np.isfinite(float(row["option_oi"])) and float(row["option_oi"]) > 0)
    except (ValueError, TypeError, KeyError, OverflowError):
        return False


def form_candidates(connection, direction: ListwiseMLP, contract: ListwiseMLP,
                    ts: datetime, evaluations: list[dict]) -> tuple[list[dict], dict]:
    """Returns (candidates, diagnostics). The diagnostics carry the resolvable
    universe size, which decides whether a list may be published at all."""
    by_symbol = {row["symbol"]: row for row in evaluations}
    source_session = ts.astimezone(IST).date()
    chain_ts = resolve_chain_bar(connection, ts, list(by_symbol))
    if chain_ts is None:
        return [], {"universe_rows": 0, "chain_ts": None}
    with connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
        cursor.execute(
            SOURCE_OPTIONS_SQL.replace("o.source option_source,", "o.source option_source,catalog.lot_size contract_lot_size,").replace("FROM option_premium_candles o", "FROM option_premium_candles o LEFT JOIN fo_contract_catalog catalog ON catalog.instrument_key=o.instrument_key"),
            (chain_ts, list(by_symbol), _business_day(source_session, max(SWING_HORIZONS))),
        )
        option_rows = [dict(row) for row in cursor.fetchall()]
    by_side: dict[tuple[str, str], list[dict]] = {}
    eligible_rows = [row for row in option_rows if eligible_contract(row)]
    for option in eligible_rows:
        by_side.setdefault((option["underlying"], option["option_type"]), []).append(option)

    candidates: list[dict] = []
    for (symbol, side), choices in by_side.items():
        base_row = by_symbol[symbol]
        front_expiry = min(row["expiry"] for row in choices)
        front = [row for row in choices if row["expiry"] == front_expiry]
        atm = min(front, key=lambda row: (
            abs(row["strike"] - base_row["source_spot"]),
            abs(abs(row["option_delta"] or 0.5) - 0.5), row["strike"]))
        selected = [(atm, "ATM")]
        wings = [row for row in front if row["option_delta"] is not None
                 and WING_DELTA_MIN <= abs(row["option_delta"]) <= WING_DELTA_MAX]
        if wings:
            wing = min(wings, key=lambda row: (
                abs(abs(row["option_delta"]) - WING_DELTA_TARGET), row["strike"]))
            if wing["strike"] != atm["strike"]:
                selected.append((wing, "WING_25D"))
        base_features = feature_row(base_row, _instrument({**base_row, **atm}), side, ts)
        for horizon in SWING_HORIZONS:
            direction_horizon = _horizon_features(direction, horizon)
            contract_horizon = _horizon_features(contract, horizon)
            direction_score = float(direction.score(
                np.append(base_features, direction_horizon))[0])
            for option, kind in selected:
                option_features = feature_row(
                    base_row, _instrument({**base_row, **option}), side, ts)
                contract_score = float(contract.score(np.append(
                    option_features,
                    contract_horizon + [float(kind == "WING_25D")],
                ))[0])
                estimate = expected_net_return(contract.return_calibration, contract_score, horizon)
                candidates.append({
                    "symbol": symbol, "option_type": side, "horizon_sessions": horizon,
                    "instrument": option["instrument_key"], "strike": option["strike"],
                    "expiry": option["expiry"], "contract_kind": kind,
                    "source_mark": option["premium"], "source_mark_ts": chain_ts,
                    "direction_score": direction_score, "contract_score": contract_score,
                    "lot_size": option["contract_lot_size"],
                    **estimate,
                    "option_volume": option.get("option_volume"),
                    "option_oi": option.get("option_oi"),
                })
    return score_candidates(candidates, evaluations), {
        "universe_rows": len(eligible_rows), "raw_universe_rows": len(option_rows),
        "excluded_contracts": len(option_rows)-len(eligible_rows), "chain_ts": chain_ts,
        "ranking_policy": RANKING_POLICY,
    }


def select_top(candidates: list[dict], top_n: int) -> list[dict]:
    """Layer 1: the mandatory research ranking -- top `top_n` CE AND PE.

    Ranked per side, because the daily product is "top-ten CE and top-ten PE
    opportunities" (owner plan, 2026-09-04). A single mixed top-ten silently
    became one-sided whenever the market was: on 2026-09-03 it was ten PE and
    no CE at all, which is not a ranking of the CE opportunities, it is their
    absence. One best exact expression per underlying per side; `rank` stays
    global so the existing (source_session, rank) key still holds.
    """
    if top_n <= 0:
        return []
    ranked = sorted(candidates, key=lambda row: (
        -row["combined_score"], -row["direction_score"], -row["contract_score"],
        row["symbol"], row["option_type"], row["horizon_sessions"], row["strike"],
    ))
    per_side: dict[str, list[dict]] = {"CE": [], "PE": []}
    seen: dict[str, set[str]] = {"CE": set(), "PE": set()}
    for row in ranked:
        side = row["option_type"]
        if len(per_side[side]) >= top_n or row["symbol"] in seen[side]:
            continue
        seen[side].add(row["symbol"])
        per_side[side].append({**row, "side_rank": len(per_side[side]) + 1})
    selected = sorted(per_side["CE"] + per_side["PE"],
                      key=lambda row: (-row["combined_score"], row["option_type"],
                                       row["symbol"]))
    for index, row in enumerate(selected, start=1):
        row["rank"] = index
    return selected


# Layer-2 gates. Deliberately explicit and deliberately conservative: the
# actionable list is allowed to be empty, and an empty list with a stated
# reason is the correct output while the rankers are unproven.
MIN_COMBINED_SCORE = float(os.environ.get("VANGUARD_SWING_MIN_SCORE", "0.90"))
# Liquidity is judged on OI, RELATIVE to the day's own resolved universe.
#
# Not on volume: 10,217 of the 10,231 option rows at a decision bar come from
# `upstox_chain`, which carries no volume field at all, so a volume floor is an
# always-refuse gate with an empty feasible set -- the failure this lane already
# shipped once on the commodity side. Volume is still recorded per row.
#
# Not on an absolute OI number either: OI at a decision bar spans 82k..2.5m
# across names, so any constant is either inert or arbitrary. A percentile of
# the day's own cross-section stays meaningful as the universe changes.
MIN_OI_PERCENTILE = float(os.environ.get("VANGUARD_SWING_MIN_OI_PCTILE", "0.25"))
MAX_ACTIONABLE = int(os.environ.get("VANGUARD_SWING_MAX_ACTIONABLE", "10"))
SWING_STOP_PCT = 0.30


def _model_confidence_refusal(direction: ListwiseMLP, contract: ListwiseMLP) -> str | None:
    """Refuse the whole actionable layer unless BOTH rankers are promoted.

    A shadow artifact is an unproven one. It may rank -- that is layer 1 --
    but it may not make anything actionable, and it must say so once, at the
    top, rather than leaving ten rows looking merely unlucky.
    """
    unproven = [model.role for model in (direction, contract)
                if getattr(model, "status", "shadow") != "paper_active"]
    if unproven:
        return (f"{' and '.join(unproven)} ranker still shadow; the research "
                f"ranking stands, the actionable list is empty by design")
    return None


def apply_actionable_gates(connection, selected: list[dict], evaluations: list[dict],
                           ts: datetime, capital: float,
                           refusal: str | None) -> tuple[list[dict], str | None]:
    """Layer 2: expected-return, confidence, liquidity and M7 risk gates."""
    by_symbol = {row["symbol"]: row for row in evaluations}
    if refusal is not None:
        for row in selected:
            row["actionable"] = False
            row["actionable_reason"] = refusal
        return selected, refusal
    state = load_risk_state(connection, ts, capital)
    def positive_finite(value):
        try:
            return np.isfinite(float(value)) and float(value) > 0
        except (TypeError, ValueError, OverflowError):
            return False
    observed_oi = sorted(float(row["option_oi"]) for row in selected
                         if positive_finite(row.get("option_oi")))
    min_oi = (observed_oi[min(int(len(observed_oi) * MIN_OI_PERCENTILE), len(observed_oi) - 1)]
              if observed_oi else 0.0)
    taken = 0
    def ordering(row):
        try:
            value = float(row.get("combined_score"))
            return -value if np.isfinite(value) else float("inf")
        except (TypeError, ValueError, OverflowError):
            return float("inf")
    for row in sorted(selected, key=ordering):
        row["actionable"] = False
        if (not positive_finite(row.get("combined_score"))
                or not positive_finite(row.get("source_mark"))
                or not positive_finite(row.get("option_oi"))
                or not positive_finite(row.get("lot_size"))
                or float(row["lot_size"]) != int(float(row["lot_size"]))):
            row["actionable_reason"] = "invalid score, exact-contract price, OI or lot metadata"
            continue
        if float(row["combined_score"]) < MIN_COMBINED_SCORE:
            row["actionable_reason"] = (
                f"combined score {row['combined_score']:.3f} < {MIN_COMBINED_SCORE}")
            continue
        lower = row.get("expected_net_lower")
        if not positive_finite(lower):
            row["actionable_reason"] = (row.get("return_refusal") or
                "expected net return lower bound must be positive after costs")
            continue
        oi = row.get("option_oi")
        if oi is None or float(oi) < min_oi:
            row["actionable_reason"] = (
                f"OI {float(oi or 0):,.0f} below the day's "
                f"{MIN_OI_PERCENTILE:.0%} cross-sectional floor ({min_oi:,.0f})")
            continue
        if taken >= MAX_ACTIONABLE:
            row["actionable_reason"] = f"actionable cap {MAX_ACTIONABLE} reached"
            continue
        entry = float(row["source_mark"])
        sizing = risk_check(
            state, connection, symbol=row["symbol"],
            sector20=(by_symbol.get(row["symbol"]) or {}).get("sector20"),
            entry_premium=entry, stop_premium=round(entry * (1 - SWING_STOP_PCT), 4),
            lot_size=int(row["lot_size"]), as_of=ts,
        )
        row.update(sizing_lots=sizing.lots, sizing_notional=sizing.notional,
                   sizing_risk_rupees=sizing.risk_rupees, sizing_method=sizing.method)
        if not sizing.allowed:
            # M7 VETOES here, unlike M6's paper floor: this layer is the one
            # that claims to be actionable, so an unsized row is not one.
            row["actionable_reason"] = f"M7 refused sizing: {sizing.reason}"
            continue
        row["actionable"] = True
        row["actionable_reason"] = None
        taken += 1
        state.open_positions.append({
            "ticket_id": -1, "symbol": row["symbol"],
            "sector20": (by_symbol.get(row["symbol"]) or {}).get("sector20"),
            "risk_rupees": sizing.risk_rupees,
        })
        state.__post_init__()
    note = None if taken else "every candidate refused by a layer-2 gate"
    return selected, note


def create_watchlist(connection, *, top_n: int = 10, allow_replay: bool = False,
                    capital: float | None = None, now: datetime | None = None) -> dict:
    now = now or datetime.now(IST)
    direction, contract = _model(connection, "direction"), _model(connection, "contract")
    if direction is None or contract is None:
        return {"created": False, "reason": "both shadow rank models are required"}
    if not all("horizon_3" in getattr(model, "feature_names", ())
               for model in (direction, contract)):
        return {"created": False,
                "reason": "both rankers must declare horizon_3 before the 1-3d lane can emit",
                "paper_only": True}
    ts, evaluations = _decision_rows(connection)
    if ts is None:
        return {"created": False, "reason": "no 14:15 IST candidate snapshot"}
    source_session = ts.astimezone(IST).date()
    if source_session != now.astimezone(IST).date() and not allow_replay:
        return {"created": False, "reason": "latest 14:15 decision is not today's session",
                "source_session": str(source_session)}
    if not allow_replay and not available_at(ts) <= now < available_at(ts) + timedelta(minutes=30):
        return {"created": False, "reason": "decision must precede the planned 15:15 entry close",
                "source_session": str(source_session), "paper_only": True}
    with connection.cursor() as cursor:
        # Serialize emission with the live pipeline and the minute retry worker.
        cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"vanguard-swing-emit:{source_session}",))
        cursor.execute(
            "SELECT item_count,status FROM vanguard_swing_watchlist_runs WHERE source_session=%s",
            (source_session,),
        )
        existing = cursor.fetchone()
    if capital is None:
        from paper.engine import current_capital
        capital = current_capital(connection, source_session)
    if existing:
        return {"created": False, "reason": "immutable session already exists",
                "source_session": str(source_session), "item_count": existing[0], "status": existing[1]}
    candidates, universe = form_candidates(connection, direction, contract, ts, evaluations)
    if universe["universe_rows"] < MIN_UNIVERSE_ROWS:
        # Publishing a "top ten either side" drawn from a sliver of the market
        # states a ranking the data cannot support. Refuse, and say how thin.
        return {"created": False,
                "reason": (f"resolvable universe {universe['universe_rows']} contracts is below "
                           f"the {MIN_UNIVERSE_ROWS} floor; the chain sweep is too thin to rank"),
                "source_session": str(source_session),
                "universe_rows": universe["universe_rows"]}
    # Layer 1 answers the product's premium-performer objective. Models and
    # confidence/calibration gates remain unchanged; this is no promotion.
    selected = select_top(performer_candidates(candidates), top_n)
    if not selected:
        return {"created": False, "reason": "no liquid contract expressions", "source_session": str(source_session)}
    refusal = ("historical replay is observation-only" if allow_replay else
               _model_confidence_refusal(direction, contract))
    selected, note = apply_actionable_gates(
        connection, selected, evaluations, now, capital, refusal)
    actionable = [row for row in selected if row.get("actionable")]
    with connection.cursor() as cursor:
        cursor.execute(
            """INSERT INTO vanguard_swing_watchlist_runs
               (source_session,prediction_ts,direction_model_version,contract_model_version,
                top_n,item_count,status,decision_at,actionable_count,actionable_note,is_replay,ranking_policy)
               VALUES (%s,%s,%s,%s,%s,%s,'awaiting_entry',%s,%s,%s,%s,%s)""",
            (source_session, ts, direction.version, contract.version, top_n, len(selected),
             now, len(actionable), refusal or note, allow_replay, PERFORMER_POLICY),
        )
        psycopg2.extras.execute_values(
            cursor,
            """INSERT INTO vanguard_swing_watchlist_items
               (source_session,rank,side_rank,symbol,option_type,horizon_sessions,instrument,
                strike,expiry,contract_kind,direction_score,contract_score,combined_score,
                source_mark_ts,source_mark,status,lot_size,option_volume,option_oi,
                actionable,actionable_reason,sizing_lots,sizing_notional,sizing_risk_rupees,
                sizing_method,expected_net_return,expected_net_lower,return_refusal,paper_position) VALUES %s""",
            [(source_session, row["rank"], row["side_rank"], row["symbol"], row["option_type"],
              row["horizon_sessions"], row["instrument"], row["strike"], row["expiry"],
              row["contract_kind"], row["direction_score"], row["contract_score"],
              row["combined_score"], row["source_mark_ts"], row["source_mark"], "awaiting_entry",
              row.get("lot_size"), row.get("option_volume"), row.get("option_oi"),
              bool(row.get("actionable")), row.get("actionable_reason"),
              row.get("sizing_lots"), row.get("sizing_notional"),
              row.get("sizing_risk_rupees"), row.get("sizing_method"),
              row.get("expected_net_return"), row.get("expected_net_lower"), row.get("return_refusal"), True)
             for row in selected],
        )
        psycopg2.extras.execute_values(
            cursor,
            """INSERT INTO vanguard_strategy_journal
               (strategy,event_key,event_type,source_session,event_ts,symbol,option_type,instrument,
                horizon_sessions,rank,score,status,latest_mark,payload) VALUES %s
               ON CONFLICT (strategy,event_key) DO NOTHING""",
            [("swing_1_2d", f"{source_session}:{row['rank']}", "signal", source_session, now,
              row["symbol"], row["option_type"], row["instrument"], row["horizon_sessions"],
              row["rank"], row["combined_score"], "awaiting_entry", row["source_mark"],
              psycopg2.extras.Json({"contract_kind": row["contract_kind"], "strike": row["strike"],
                                    "ranking_policy": PERFORMER_POLICY,
                                    "publication_diagnostics": {**universe, "chain_ts": str(universe.get("chain_ts")), "candidate_count": len(candidates)},
                                    "expiry": str(row["expiry"]), "replay": allow_replay,
                                    "side_rank": row["side_rank"],
                                    "actionable": bool(row.get("actionable")),
                                    "actionable_reason": row.get("actionable_reason")}))
             for row in selected],
        )
    return {"created": True, "source_session": str(source_session), "item_count": len(selected),
            "ranking_policy": PERFORMER_POLICY,
            "publication_diagnostics": {**universe, "chain_ts": str(universe.get("chain_ts")), "candidate_count": len(candidates)},
            "research_ranking": {"CE": sum(1 for r in selected if r["option_type"] == "CE"),
                                 "PE": sum(1 for r in selected if r["option_type"] == "PE")},
            "actionable_count": len(actionable), "actionable_note": refusal or note,
            "direction_model": direction.version, "contract_model": contract.version,
            "paper_only": True}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", default=os.environ.get("VANGUARD_DATABASE_URL", DEFAULT_DSN))
    parser.add_argument("--top-n", type=int, default=int(os.environ.get("VANGUARD_SWING_TOP_N", "10")))
    parser.add_argument("--allow-replay", action="store_true")
    parser.add_argument("--capital", type=float, default=None)
    args = parser.parse_args()
    connection = psycopg2.connect(args.dsn)
    try:
        with connection:
            result = create_watchlist(connection, top_n=args.top_n,
                                      allow_replay=args.allow_replay, capital=args.capital)
        print(result)
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
