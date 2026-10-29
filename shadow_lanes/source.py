"""Read existing market history/models; never resume or mutate a Nomad book."""
from __future__ import annotations

import os
import sys
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg2
from psycopg2.extras import RealDictCursor

from .vendor.daily_stock_selection import DAILY_SQL, VERSION, rank_daily

VG_ROOT = Path(os.environ.get("VANGUARD_SOURCE", Path(__file__).resolve().parents[1] / "vanguard"))
sys.path.insert(0, str(VG_ROOT))
from model.market_calendar import is_session, previous_session

IST = ZoneInfo("Asia/Kolkata")
POLICIES = {"vanguard": "macd-vanguard-atm-local-paper-v3", "cbe": "cbe-daily-trend-v1-20261029"}


def source_session(now, lane):
    local = now.astimezone(IST)
    cutoff = time(14, 45) if lane == "vanguard" else time(15, 30)
    return local.date() if is_session(local.date()) and local.time() >= cutoff else previous_session(local.date())


def publication_window(now, lane):
    local = now.astimezone(IST)
    start, end = (time(14, 45), time(15, 15)) if lane == "vanguard" else (time(15, 31), time(20, 0))
    return is_session(local.date()) and start <= local.time() < end


def next_session(day, count=1):
    while count:
        day += timedelta(days=1)
        if is_session(day):
            count -= 1
    return day


def connect(readonly=True):
    # Credentials come from the platform's existing container environment, never a source default.
    db = psycopg2.connect(host=os.environ.get("MI_PG_HOST", "nomadcurie_db"),
                          user=os.environ["MI_PG_USER"], password=os.environ["MI_PG_PASSWORD"],
                          dbname=os.environ.get("LANE_SOURCE_DB", "nomadcurie"), connect_timeout=5)
    db.set_session(readonly=readonly)
    with db.cursor() as cur:
        cur.execute("SET statement_timeout='30s'")
    return db


def blocked(lane, day, *reasons, **details):
    return dict(status="blocked", source_session=str(day), policy=POLICIES[lane], blockers=list(reasons), watchlist=[], **details)


def cbe(connection, now):
    from .daily_market import scan
    return scan(connection, now, source_session(now, 'cbe'))


def legacy_cbe_diagnostic(connection, now):
    day = source_session(now, "cbe")
    with connection.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(DAILY_SQL.replace(":start", "%(start)s").replace(":as_of", "%(as_of)s"),
                    {"start": now - timedelta(days=260), "as_of": now})
        rows = cur.fetchall()
        cur.execute("SELECT symbol,sector20 FROM sector_taxonomy")
        sectors = {r["symbol"]: r["sector20"] for r in cur.fetchall()}
        cur.execute("SELECT count(*) FROM fo_underlying_catalog WHERE kind='STOCK' AND fno_active IS TRUE")
        expected = cur.fetchone()["count"]
    ranked = rank_daily(rows, day, (1,))
    if ranked["status"] != "ok":
        return {**ranked, "policy": POLICIES["cbe"]}
    if expected < 10 or ranked.get("current_complete", 0) < .8 * expected:
        return blocked("cbe", day, "Completed daily stock breadth below 80% of the active catalog", expected=expected,
                       current_complete=ranked.get("current_complete", 0))
    longs = [{**r, "symbol": r["instrument"], "sector": sectors.get(r["instrument"], "UNKNOWN"),
              "source_mark": r["latest_close"], "source_mark_ts": str(day) + "T15:15:00+05:30",
              "actionable": False} for r in ranked["results"] if r.get("eligible") and r.get("direction") == "LONG"][:10]
    return {**ranked, "watchlist": longs, "policy": POLICIES["cbe"], "expected": expected,
            "blockers": [] if longs else ["No long candidate passes the inherited daily policy"],
            "status": "ok" if longs else "blocked", "long_only": True,
            "inputs": "12 completed aligned 30-minute broker bars per session; original 5/20 momentum and EMA20/50 policy"}


def vanguard(connection, now):
    day = source_session(now, "vanguard")
    # A preview of an old session is diagnostic only. The worker never freezes it outside today's window.
    ranked = rank_bar(connection, datetime.combine(day, time(14, 15), IST), now)
    if ranked.get("status") == "blocked":
        return ranked
    selected = ranked.pop("selected")
    for key in ("rows", "status", "source_session"):
        ranked.pop(key, None)
    for row in selected:
        row.update(actionable=False, actionable_reason="Fresh paper candidate; current ATM, local review and risk checks required")
    return dict(status="ok" if selected else "blocked", blockers=[] if selected else ["No eligible exact ATM contracts"],
                source_session=str(day), policy=POLICIES["vanguard"], watchlist=selected, **ranked,
                inputs="Inherited M2 flow, M3 regime, M4 sector RS/lead-lag, M5 timing, frozen direction/payoff rankers and shared local-model context",
                model_status="Research only; no promotion or expected-profit claim")


# Decision filter (owner, 28 Oct 2026: "filtered best trade candidates; filtering logical and not too restrictive").
# Hard filters only where a pick could not be traded as intended; the evidence legs vote, they do not veto one by one
# (the old M6 chain of flow >= 60 AND sector RS AND regime AND timing ignition left 0 of 213 stocks most days).
MAX_ATM_DISTANCE = 0.03      # an "ATM" leg further than 3% from spot is not at the money
MIN_TRADED_LOTS = 2          # lots traded in the session by the decision bar
EVIDENCE_FLOOR = -3          # dropped only when nearly every leg argues for the other side (was -2; on October data
                             # every momentum veto tried cost captured top performers, research/vanguard_decision_20261028)
# Ranking (research/vanguard_decision_20261028, found on October 2026 and checked on September out of sample):
# among the tradable near-ATM legs, the one furthest out of the money -- more leverage per rupee, the reason "cheap"
# options win; implied-vol level itself did not separate winners -- caught 16.7% (Oct) / 12.9% (Sep) of each day's
# top-10 performers over 1-3 sessions against random 6.7% / 9.2%, with paper results of +19.6% / +31.8% a pick
# (random +6.5% / +5.5%). Cheapness breaks ties; the payoff ranker and the votes inform, they do not lead.
OWN_SIDE_OI = {"long_buildup": 1, "short_covering": 1, "long_unwind": -1, "short_buildup": -1}


def evidence(inputs, side):
    """Votes (+1 for, -1 against, 0 neutral) of the five inherited legs for buying this side's option."""
    s = 1 if side == "CE" else -1
    flow, rs = inputs.get("flow_score"), inputs.get("rs_z20")
    votes = {
        "flow": 0 if flow is None else (1 if flow * s >= 20 else -1 if flow * s <= -20 else 0),
        "sector_rs": 0 if rs is None else (1 if rs * s >= 0.5 else -1 if rs * s <= -0.5 else 0),
        "own_side_oi": OWN_SIDE_OI.get(inputs.get("ce_state" if side == "CE" else "pe_state"), 0),
        # GEX regime is not directional: negative gamma lets moves extend (good for a bought option), positive pins.
        "regime": {"STRONG_NEG": 1, "NEG": 1, "POS": -1, "STRONG_POS": -1}.get(inputs.get("regime"), 0),
        "timing": 0,
    }
    if inputs.get("timing_state") == "IGNITION" and (inputs.get("timing_score") or 0) >= 60 and inputs.get("va_position") is not None:
        votes["timing"] = 1 if (inputs["va_position"] - 0.5) * s > 0 else -1
    return votes, sum(votes.values())


def screen(row, inputs, spot):
    votes, net = evidence(inputs, row["option_type"])
    distance = abs(float(row["strike"]) / spot - 1) if spot else None
    lots = (row.get("option_volume") or 0) / max(1, int(row.get("lot_size") or 1))
    reasons = []
    if distance is None or distance > MAX_ATM_DISTANCE:
        reasons.append(f"strike {row['strike']:g} is {(distance or 0) * 100:.1f}% from spot: not at the money")
    if lots < MIN_TRADED_LOTS:
        reasons.append(f"only {lots:.1f} lots traded by the decision bar")
    if net <= EVIDENCE_FLOOR:
        against = [k for k, v in votes.items() if v < 0]
        reasons.append(f"evidence against this side (net {net:+d}: {', '.join(against)})")
    premium = float(row.get("source_mark") or 0)
    side = 1 if row["option_type"] == "CE" else -1
    row.update(evidence=votes, evidence_net=net, atm_distance=distance, traded_lots=round(lots, 1),
               premium_pct=premium / spot if spot and premium > 0 else None,
               otm=side * (float(row["strike"]) / spot - 1) if spot else None,     # > 0: out of the money
               filter_reasons=reasons, filtered=bool(reasons))
    return row


def rank_bar(connection, bar, now):
    """Every exact ATM candidate at ``bar``, screened and ranked, with the stage counts that explain the list."""
    from fusion.m6_select import evaluate_bar, funnel_counts
    from model import preclose_swing as swing
    day = bar.astimezone(IST).date()
    with connection.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT symbol FROM fo_underlying_catalog WHERE kind='STOCK' AND fno_active IS TRUE ORDER BY symbol")
        universe = [r["symbol"] for r in cur.fetchall()]
        cur.execute("""SELECT DISTINCT ON (underlying) underlying,close,time,synced_at
          FROM underlying_spot_candles WHERE interval='30minute' AND time=%s AND underlying=ANY(%s)
          AND synced_at<=%s AND source IN ('upstox_spot','upstox_sweep','upstox') AND close>0
          ORDER BY underlying,CASE source WHEN 'upstox_spot' THEN 0 WHEN 'upstox_sweep' THEN 1 ELSE 2 END,synced_at DESC""",
                    (bar, universe, now))
        spots = {r["underlying"]: r for r in cur.fetchall()}
    expected = len(universe)
    if expected < 10 or len(spots) < .8 * expected:
        return blocked("vanguard", day, f"Broker spot-bar breadth below 80% of the active catalog at {bar:%H:%M}",
                       expected=expected, current_complete=len(spots), bar=bar.isoformat())
    direction, payoff = swing._model(connection, "direction"), swing._model(connection, "contract")
    if direction is None or payoff is None:
        return blocked("vanguard", day, "Both inherited direction and option-payoff model artifacts are required")
    if not all("horizon_3" in getattr(model, "feature_names", ()) for model in (direction, payoff)):
        return blocked("vanguard", day, "Both frozen artifacts must explicitly support the three-session horizon")
    evaluations = evaluate_bar(connection, bar)
    fresh = [e for e in evaluations if e.symbol in spots and e.inputs.get("flow_age_sessions") is not None
             and e.inputs["flow_age_sessions"] <= 3 and e.inputs.get("rs_age_sessions") is not None
             and e.inputs["rs_age_sessions"] <= 3 and e.inputs.get("regime_age_bars") is not None
             and e.inputs["regime_age_bars"] <= 1 and (e.inputs.get("flow_n_ingredients") or 0) >= 2]
    diagnostics = {"expected": expected, "current_complete": len(spots), "fresh_feature_symbols": len(fresh),
                   "funnel": funnel_counts(evaluations), "direction_model": direction.version, "contract_model": payoff.version,
                   "bar": bar.isoformat()}
    if len(fresh) < .8 * expected:
        return blocked("vanguard", day, "Flow/sector/regime/timing inputs do not cover 80% with current-session freshness", **diagnostics)
    inputs = [{**e.inputs, "symbol": e.symbol, "ts": e.ts, "sector20": e.sector20,
               "conviction": e.conviction, "source_spot": spots[e.symbol]["close"]} for e in fresh]
    candidates, coverage = swing.form_candidates(connection, direction, payoff, bar, inputs)
    if coverage.get("universe_rows", 0) < swing.MIN_UNIVERSE_ROWS:
        return blocked("vanguard", day, "Exact-contract chain universe is too thin to rank", **diagnostics, chain=coverage)
    # The maintained paper lane trades ATM only, with one direction per stock.
    from .contracts import verify_candidates
    by_symbol = {e.symbol: e for e in fresh}
    best = {}
    for row in swing.performer_candidates(verify_candidates(candidates)):
        if row.get("contract_kind") != "ATM":
            continue
        key = (row["symbol"], row["option_type"])
        if key not in best or (row["combined_score"], row["direction_score"]) > (best[key]["combined_score"], best[key]["direction_score"]):
            best[key] = row
    rows = []
    for row in best.values():
        evaluation = by_symbol[row["symbol"]]
        spot = float(spots[row["symbol"]]["close"])
        row.update(sector20=evaluation.sector20, conviction=evaluation.conviction, underlying_signal_close=spot)
        rows.append(screen(row, evaluation.inputs, spot))
    selected = select_atm([r for r in rows if not r["filtered"]])
    chosen = {(r["symbol"], r["option_type"]) for r in selected}
    for row in rows:
        row["selected"] = (row["symbol"], row["option_type"]) in chosen
    rows.sort(key=lambda r: (r["filtered"], rank_key(r), r["symbol"]))
    sides = {side: [r for r in rows if r["option_type"] == side] for side in ("CE", "PE")}
    stages = [
        {"stage": "F&O stocks in the active catalog", "surviving": expected},
        {"stage": "spot bar at the decision bar", "surviving": len(spots)},
        {"stage": "flow, sector, regime and timing inputs fresh", "surviving": len(fresh)},
        {"stage": "exact ATM contract on the chain (stocks: CE / PE)", "surviving": len({r["symbol"] for r in rows}),
         "ce": len(sides["CE"]), "pe": len(sides["PE"])},
        {"stage": "really at the money (within %d%% of spot)" % round(MAX_ATM_DISTANCE * 100),
         "ce": sum(1 for r in sides["CE"] if r["atm_distance"] is not None and r["atm_distance"] <= MAX_ATM_DISTANCE),
         "pe": sum(1 for r in sides["PE"] if r["atm_distance"] is not None and r["atm_distance"] <= MAX_ATM_DISTANCE)},
        {"stage": "traded at least %d lots" % MIN_TRADED_LOTS,
         "ce": sum(1 for r in sides["CE"] if r["traded_lots"] >= MIN_TRADED_LOTS),
         "pe": sum(1 for r in sides["PE"] if r["traded_lots"] >= MIN_TRADED_LOTS)},
        {"stage": "evidence not clearly against (net > %d)" % EVIDENCE_FLOOR,
         "ce": sum(1 for r in sides["CE"] if r["evidence_net"] > EVIDENCE_FLOOR),
         "pe": sum(1 for r in sides["PE"] if r["evidence_net"] > EVIDENCE_FLOOR)},
        {"stage": "pass every filter", "ce": sum(1 for r in sides["CE"] if not r["filtered"]),
         "pe": sum(1 for r in sides["PE"] if not r["filtered"])},
        {"stage": f"selected: {PER_SIDE} per side, furthest out of the money first",
         "ce": sum(1 for r in selected if r["option_type"] == "CE"), "pe": sum(1 for r in selected if r["option_type"] == "PE")},
    ]
    return dict(status="ok", source_session=str(day), **diagnostics, chain=coverage, stages=stages,
                filters={"max_atm_distance": MAX_ATM_DISTANCE, "min_traded_lots": MIN_TRADED_LOTS, "evidence_floor": EVIDENCE_FLOOR},
                rows=rows, selected=selected)


def latest_bar(connection, now):
    """The newest 30-minute spot bar that has closed by ``now`` (bars are stamped with their start)."""
    with connection.cursor() as cur:
        cur.execute("SELECT max(time) FROM underlying_spot_candles WHERE interval='30minute' AND time <= %s",
                    (now - timedelta(minutes=30),))
        row = cur.fetchone()
    return row[0].astimezone(IST) if row and row[0] else None


def ranker_models(connection):
    """The direction and option-payoff rankers the lane uses, with their training windows and stored metrics."""
    with connection.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""SELECT DISTINCT ON (role) version,role,family,status,training_start,training_end,validation_start,
                         validation_end,test_start,test_end,n_train,n_validation,n_test,metrics,created_at,
                         cardinality(feature_names) features
                       FROM vanguard_rank_model_versions WHERE status IN ('paper_active','shadow')
                       ORDER BY role,(status='paper_active') DESC,created_at DESC""")
        return [dict(r) for r in cur.fetchall()]


def decision_view(connection, now, bar=None):
    """Decision flow for the latest closed bar: every exact ATM candidate screened, ranked and explained."""
    bar = bar or latest_bar(connection, now)
    if bar is None:
        return blocked("vanguard", now.date(), "No closed 30-minute spot bar stored yet")
    ranked = rank_bar(connection, bar, now)
    ranked["models"] = ranker_models(connection)
    ranked["as_of"] = now.isoformat()
    ranked["decision_bar"] = bar.isoformat()
    ranked["list_bar"] = "14:15 IST (the 14:47 list); this view re-runs the same ranking on the latest closed bar"
    ranked.pop("selected", None)
    return ranked


PER_SIDE = 10      # owner, 28 Oct 2026: 10 CE and 10 PE every day, whatever is already held
HOLD_SESSIONS = 3  # each pick is held up to 3 sessions or until its stop, and measured at D1/D2/D3


def rank_key(r):
    """Furthest out of the money first, then the cheapest premium share of spot, then the payoff ranker and evidence."""
    otm, share = r.get('otm'), r.get('premium_pct')
    return (-otm if otm is not None else 9.0, share if share is not None else 9.0, -r['combined_score'],
            -r.get('evidence_net', 0), -r['direction_score'])


def select_atm(candidates, per_side=PER_SIDE):
    ranked = sorted([r for r in candidates if r.get('contract_kind')=='ATM'],
                    key=lambda r:(*rank_key(r),-r['contract_score'],r['symbol'],r['option_type']))
    selected=[];seen=set();count={'CE':0,'PE':0}
    for row in ranked:
        side=row.get('option_type')
        if row['symbol'] in seen or side not in count or count[side]>=per_side:continue
        seen.add(row['symbol']);count[side]+=1
        selected.append({**row,'rank':len(selected)+1,'side_rank':count[side],
            'model_horizon_sessions':row.get('horizon_sessions'),'horizon_sessions':HOLD_SESSIONS,
            'expected_net_return':None,'expected_net_lower':None,
            'return_refusal':'Same-session ATM entry differs from the inherited next-session calibration',
            'ranker_entry_basis':'Inherited rankers target next-session returns. Their scores are ordinal research context only; same-session ATM admission is not calibrated or proven.'})
        if all(n>=per_side for n in count.values()):break
    return selected


def observe_outcomes(connection, publication, now):
    """Exact instrument, completed bar, real observation time. No fills or substituted contracts."""
    day = datetime.fromisoformat(publication["source_session"]).date()
    for row in publication.get("watchlist", []):
        instrument = row.get("instrument") or row.get("symbol")
        baseline = float(row.get("source_mark") or 0)
        if not instrument or baseline <= 0:
            continue
        horizons = [("D1", 1, time(14, 15)), ("D2", 2, time(14, 15)), ("D3", 3, time(14, 15)), ("BTST", 1, time(9, 15))] if publication["lane"] == "vanguard" else [("D1", 1, time(14, 45))]
        for label, count, clock in horizons:
            target = datetime.combine(next_session(day, count), clock, IST)
            if target + timedelta(minutes=30) > now:
                continue
            with connection.cursor() as cur:
                if publication["lane"] == "vanguard":
                    cur.execute("""SELECT close,synced_at FROM option_premium_candles WHERE instrument_key=%s
                      AND interval='30minute' AND time=%s AND synced_at<=%s AND close>0 ORDER BY synced_at DESC LIMIT 1""", (instrument, target, now))
                else:
                    cur.execute("""SELECT close,synced_at FROM underlying_spot_candles WHERE underlying=%s
                      AND interval='30minute' AND time=%s AND synced_at<=%s AND close>0
                      AND source IN ('upstox_spot','upstox_sweep','upstox') ORDER BY synced_at DESC LIMIT 1""", (instrument, target, now))
                mark = cur.fetchone()
            if mark:
                yield instrument, label, dict(baseline_price=baseline, observed_price=float(mark[0]),
                    observed_return=float(mark[0]) / baseline - 1, source_bar=target.isoformat(),
                    source_available_at=mark[1].isoformat(), method="Observed quote-to-quote return before costs; no fill")
