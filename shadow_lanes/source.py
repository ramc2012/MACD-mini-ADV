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
POLICIES = {"vanguard": "macd-vanguard-atm-local-paper-v3", "cbe": "cbe-daily-cash-mp-local-v3"}


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
    from fusion.m6_select import evaluate_bar, funnel_counts
    from model import preclose_swing as swing
    day = source_session(now, "vanguard")
    bar = datetime.combine(day, time(14, 15), IST)
    # A preview of an old session is diagnostic only. The worker never freezes it outside today's window.
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
        return blocked("vanguard", day, "14:15 broker spot-bar breadth below 80% of the active catalog",
                       expected=expected, current_complete=len(spots))
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
                   "funnel": funnel_counts(evaluations), "direction_model": direction.version, "contract_model": payoff.version}
    if len(fresh) < .8 * expected:
        return blocked("vanguard", day, "Flow/sector/regime/timing inputs do not cover 80% with current-session freshness", **diagnostics)
    inputs = [{**e.inputs, "symbol": e.symbol, "ts": e.ts, "sector20": e.sector20,
               "conviction": e.conviction, "source_spot": spots[e.symbol]["close"]} for e in fresh]
    candidates, coverage = swing.form_candidates(connection, direction, payoff, bar, inputs)
    if coverage.get("universe_rows", 0) < swing.MIN_UNIVERSE_ROWS:
        return blocked("vanguard", day, "Exact-contract chain universe is too thin to rank", **diagnostics, chain=coverage)
    # The maintained paper lane trades ATM only, with one direction per stock.
    # The retained research selector may still compare wings and both sides.
    from .contracts import verify_candidates
    candidates=verify_candidates(candidates)
    selected = select_atm(swing.performer_candidates(candidates))
    by_symbol={e.symbol:e for e in fresh}
    for row in selected:
        evaluation=by_symbol[row['symbol']]
        row.update(sector20=evaluation.sector20,conviction=evaluation.conviction,
                   underlying_signal_close=float(spots[row['symbol']]['close']))
        row.update(actionable=False, actionable_reason="Fresh paper candidate; current ATM, local review and risk checks required")
    return dict(status="ok" if selected else "blocked", blockers=[] if selected else ["No eligible exact ATM contracts"],
                source_session=str(day), policy=POLICIES["vanguard"], watchlist=selected, **diagnostics, chain=coverage,
                inputs="Inherited M2 flow, M3 regime, M4 sector RS/lead-lag, M5 timing, frozen direction/payoff rankers and shared local-model context",
                model_status="Research only; no promotion or expected-profit claim")


def select_atm(candidates):
    ranked = sorted([r for r in candidates if r.get('contract_kind')=='ATM'],
                    key=lambda r:(-r['combined_score'],-r['direction_score'],-r['contract_score'],r['symbol'],r['option_type']))
    selected=[];seen=set()
    for row in ranked:
        if row['symbol'] in seen:continue
        seen.add(row['symbol']);selected.append({**row,'rank':len(selected)+1,
            'expected_net_return':None,'expected_net_lower':None,
            'return_refusal':'Same-session ATM entry differs from the inherited next-session calibration',
            'ranker_entry_basis':'Inherited rankers target next-session returns. Their scores are ordinal research context only; same-session ATM admission is not calibrated or proven.'})
        if len(selected)==10:break
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
