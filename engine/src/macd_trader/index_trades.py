"""Index trades: for each index, CE, PE or no trade over the next 1-3 sessions,
with the contract, the entry and a plan -- emitted beside the option agent's
top-10 lists at 14:45, and learned the same way.

**Direction and contract together.** The top-10 lists rank contracts WITHIN a
side, which cannot say whether to buy calls or puts at all. Here a ridge
model predicts each contract's ABSOLUTE mean premium return over the next
three session closes (clipped to [-100%, +200%], so one lottery ticket does
not set the weights). For each index the best CE and the best PE among the
eligible contracts are compared: the higher is the trade if its prediction is
positive; otherwise, no trade. Eligible: the index's own options that outlive
the hold (as for every candidate), within ``MAX_MONEYNESS`` of the money.

**Little index history, so the model may pool.** There are ~4 indices x a few
weeks of labelled days -- too few to fit a direction model on alone. Pooled
configurations learn from every underlying's options (with an index flag) and
are judged ONLY on index trades; an index-only configuration and a plain
5-session trend rule compete with them. Walk-forward, as for the top-10: day
t's model saw only outcomes closed by t.

**Judged against a coin flip.** Each trade is compared with the mean of the
ATM call and ATM put of the same index and expiry -- what picking a side at
random would have earned. ``PROMOTE_MARGIN`` of mean excess is needed to
replace the champion.

**Plan.** Stop 1 ATR (14 sessions, before today) against the index, target 1.5
ATR in favour, time exit at the third session's close. The premium at each is
the Black-Scholes reprice at the entry's implied volatility one session on --
an estimate, labelled so.
"""
from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict
from datetime import UTC, date, datetime

from . import greeks, index_scorecard, market_calendar
from . import option_agent as oa
from .universe import INDEX_SPOTS

# Owner, 4 Nov 2026: index trades only on these five. The other derivative indices (NIFTYNXT50, BANKEX, FOCIT,
# NIFTYFPI) stay in the pooled training rows but are never traded.
TRADED_INDICES = ("NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY", "FINNIFTY")

MAX_MONEYNESS = 0.02
TARGET_CLIP = (-1.0, 2.0)
MIN_EDGE = 0.0
POOL_STOCK_ROWS_PER_DAY = 600
ATR_SESSIONS = 14
ATR_STOP = 1.0
ATR_TARGET = 1.5
RATE = 0.065
PROMOTE_MARGIN = 0.01

UNDERLYING_ONLY = oa.DIRECTIONAL + ["u_rv_ratio", "o_moneyness", "o_dte", "is_index"]
CONFIGS = {
    "pooled-all-l25": {"lam": 25.0, "pool": True, "features": oa.FEATURES},
    "pooled-underlying-l50": {"lam": 50.0, "pool": True, "features": UNDERLYING_ONLY},
    "index-only-l100": {"lam": 100.0, "pool": False, "features": oa.DIRECTIONAL + ["o_moneyness", "o_dte"]},
    "trend-rule": {"rule": "trend", "features": ["u_ret_5d"]},
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_index_trades (
  run_id INTEGER NOT NULL, day TEXT NOT NULL, underlying TEXT NOT NULL, side INTEGER NOT NULL,
  symbol TEXT, strike REAL, expiry TEXT, entry_price REAL, predicted REAL, conviction REAL,
  spot REAL, atr REAL, stop_spot REAL, target_spot REAL, premium_stop REAL, premium_target REAL,
  iv REAL, lot INTEGER, drivers TEXT, alternative TEXT, reason TEXT, model_version INTEGER,
  PRIMARY KEY (run_id, underlying));
CREATE TABLE IF NOT EXISTS agent_index_models (
  version INTEGER PRIMARY KEY AUTOINCREMENT, trained_at TEXT NOT NULL, config TEXT NOT NULL,
  train_from TEXT, train_to TEXT, samples INTEGER, weights TEXT, stats TEXT, metrics TEXT,
  status TEXT NOT NULL, note TEXT);
"""


def ensure_schema(history: sqlite3.Connection) -> None:
    history.executescript(SCHEMA)
    columns = {row[1] for row in history.execute("PRAGMA table_info(agent_index_trades)")}
    for column in ("series", "front_expiry"):
        if column not in columns:
            history.execute(f"ALTER TABLE agent_index_trades ADD COLUMN {column} TEXT")


def _is_index(row: dict) -> bool:
    return row["underlying"] in INDEX_SPOTS


def _target(row: dict) -> float:
    return max(TARGET_CLIP[0], min(TARGET_CLIP[1], row["y"]))


def _training_rows(rows: list[dict], pool: bool) -> list[dict]:
    """Labelled rows; pooled sets keep every index row and a fixed sample of stock rows per day."""
    labelled = [r for r in rows if r.get("y") is not None]
    if not pool:
        return [r for r in labelled if _is_index(r)]
    by_day: dict[str, list[dict]] = defaultdict(list)
    for row in labelled:
        by_day[row["day"]].append(row)
    out = []
    for day_rows in by_day.values():
        stocks = sorted((r for r in day_rows if not _is_index(r)), key=lambda r: hash((r["symbol"], r["day"])) & 0xFFFF)
        out.extend(r for r in day_rows if _is_index(r))
        out.extend(stocks[:POOL_STOCK_ROWS_PER_DAY])
    return out


def fit(rows: list[dict], config: dict) -> dict:
    if config.get("rule"):
        return {"rule": config["rule"], "features": config["features"]}
    data = _training_rows(rows, config["pool"])
    targets = [_target(r) for r in data]
    # Ridge on centred features predicts deviations from the mean; the trade /
    # no-trade call needs the level, so the mean target is the intercept.
    return {**oa.ridge(data, config["features"], config["lam"], targets),
            "intercept": sum(targets) / len(targets) if targets else 0.0}


def predict(model: dict, features: dict) -> tuple[float, list[tuple[str, float]]]:
    if model.get("rule") == "trend":
        value = features.get("u_ret_5d", 0.0) or 0.0      # signed by side: > 0 favours this side
        return value, [("u_ret_5d", value)]
    parts = []
    for name, w, m, s in zip(model["features"], model["weights"], model["mean"], model["std"]):
        z = max(-oa.WINSOR, min(oa.WINSOR, ((features.get(name, 0.0) or 0.0) - m) / s))
        parts.append((name, w * z))
    return sum(v for _n, v in parts) + model.get("intercept", 0.0), sorted(parts, key=lambda p: -abs(p[1]))


def _eligible(rows: list[dict]) -> list[dict]:
    return [r for r in rows if _is_index(r) and abs(r["features"].get("o_moneyness", 1.0)) <= MAX_MONEYNESS]


def _atm(rows: list[dict], side: int) -> dict | None:
    """Nearest expiry, then nearest strike: the contract a coin-flip trader would buy."""
    same = [r for r in rows if r["side"] == side]
    return min(same, key=lambda r: (r["expiry"], abs(r["features"].get("o_moneyness", 0.0))), default=None)


def front_expiries(symbols, day: str) -> dict[str, str]:
    """Each index's nearest listed expiry among the contracts that traded on ``day``."""
    out: dict[str, str] = {}
    for symbol in symbols:
        if not symbol.endswith(("CE", "PE")):
            continue
        parsed = oa.parse_option(symbol)
        if parsed and parsed["root"] in INDEX_SPOTS and parsed["expiry"] >= day:
            out[parsed["root"]] = min(out.get(parsed["root"], parsed["expiry"]), parsed["expiry"])
    return out


def decide(day_rows: list[dict], model: dict, front: dict[str, str] | None = None,
           features: dict[str, dict | None] | None = None) -> dict[str, dict]:
    """Per index: the chosen contract (or none) with its prediction, conviction and the other side's best.

    The contract comes from the NEAREST series that outlives the 3-session
    hold: the front series normally, the next one when the front expires
    inside the hold (candidates never include a contract that would). The
    model then picks side and strike within that one series -- it does not
    reach for a far month because a longer-dated premium happened to score.
    With the scorecard champion the side comes from ``index_scorecard`` on
    ``features`` (the index's 14:45 spot features) and the contract is that
    side's nearest-ATM strike.
    """
    by_root: dict[str, list[dict]] = defaultdict(list)
    for row in _eligible(day_rows):
        by_root[row["underlying"]].append(row)
    front = front or {}
    out = {}
    for root in TRADED_INDICES:
        rows = by_root.get(root, [])
        if not rows:
            out[root] = {"side": 0, "reason": (
                f"front series ({front[root]}) expires inside the 3-session hold and no next-series contract is streamed"
                if front.get(root) else "no streamed contract that outlives a 3-session hold")}
            continue
        nearest = min(r["expiry"] for r in rows)
        rows = [r for r in rows if r["expiry"] == nearest]
        series = "next" if front.get(root) and front[root] < nearest else "front"
        if model.get("name") == "scorecard":
            choice = index_scorecard.decide((features or {}).get(root), model)
            row = _atm(rows, choice["side"]) if choice["side"] else None
            decision = {"predicted": choice["net"], "conviction": choice["net"], "alternative": None,
                        "drivers": choice["drivers"], "benchmark": [_atm(rows, 1), _atm(rows, -1)],
                        "series": series, "front_expiry": front.get(root)}
            if choice["side"] and row is None:
                out[root] = {**decision, "side": 0, "reason": f"no {'CE' if choice['side'] > 0 else 'PE'} contract in the series"}
            elif choice["side"]:
                out[root] = {**decision, "row": row, "side": choice["side"], "reason": ""}
            else:
                out[root] = {**decision, "side": 0, "reason": choice["reason"]}
            continue
        best: dict[int, tuple[float, dict, list]] = {}
        for row in rows:
            value, parts = predict(model, row["features"])
            if model.get("rule") == "trend":
                # The rule trades the nearest ATM contract of the trend's side.
                atm = _atm(rows, row["side"])
                if atm is None or atm["symbol"] != row["symbol"]:
                    continue
            if row["side"] not in best or value > best[row["side"]][0]:
                best[row["side"]] = (value, row, parts)
        side = max(best, key=lambda s: best[s][0])
        value, row, parts = best[side]
        other = best.get(-side)
        decision = {"predicted": value, "conviction": value - other[0] if other else None,
                    "alternative": {"symbol": other[1]["symbol"], "predicted": round(other[0], 4)} if other else None,
                    "drivers": parts[:4], "row": row, "benchmark": [_atm(rows, 1), _atm(rows, -1)],
                    "series": series, "front_expiry": front.get(root)}
        if value <= MIN_EDGE:
            out[root] = {**decision, "side": 0, "reason": "underlying trend is not positive on either side" if model.get('rule')=='trend' else "expected 1-3 day premium return is not positive on either side"}
        else:
            out[root] = {**decision, "side": side, "reason": ""}
    return out


# ---------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------

def atr(hist: oa.History, spot: str, day: str) -> float | None:
    """Mean true range over the ATR_SESSIONS sessions BEFORE ``day`` (nothing from today)."""
    prior = [hist.by_day[d].get(spot) for d in hist.prior(day, ATR_SESSIONS + 1)]
    prior = [p for p in prior if p]
    ranges = [max(b["high"] - b["low"], abs(b["high"] - a["close"]), abs(b["low"] - a["close"]))
              for a, b in zip(prior, prior[1:])]
    return sum(ranges) / len(ranges) if len(ranges) >= 5 else None


def plan(row: dict, spot_price: float, atr_value: float | None, day: str) -> dict:
    side = row["side"]
    out = {"spot": spot_price, "atr": atr_value}
    if not atr_value:
        return out
    out["stop_spot"] = spot_price - side * ATR_STOP * atr_value
    out["target_spot"] = spot_price + side * ATR_TARGET * atr_value
    entry_moment = datetime.combine(date.fromisoformat(day), oa.AS_OF, oa.IST)
    years = greeks.years_to_expiry(row["expiry"], entry_moment)
    is_call = side > 0
    iv = greeks.implied_volatility(row["entry_price"], spot_price, row["strike"], years, RATE, is_call) if years else None
    if iv:
        nxt = market_calendar.next_trading_day(date.fromisoformat(day))
        later = greeks.years_to_expiry(row["expiry"], datetime.combine(nxt, oa.AS_OF, oa.IST))
        if later:
            out["iv"] = iv
            out["premium_stop"] = greeks.black_scholes(out["stop_spot"], row["strike"], later, RATE, iv, is_call)
            out["premium_target"] = greeks.black_scholes(out["target_spot"], row["strike"], later, RATE, iv, is_call)
    return out


# ---------------------------------------------------------------------------
# Walk-forward, training
# ---------------------------------------------------------------------------

def walk_forward(rows: list[dict], config: dict, tests: list[str]) -> dict:
    by_day: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_day[row["day"]].append(row)
    labelled_days = sorted({r["day"] for r in rows if r.get("y") is not None})
    trades = []
    for day in tests:
        if day not in labelled_days:
            continue
        index = labelled_days.index(day)
        train_days = set(labelled_days[:max(0, index - 3)])
        if len(train_days) < oa.MIN_TRAIN_DAYS:
            continue
        model = fit([r for d in train_days for r in by_day[d]], config)
        for root, choice in decide(by_day[day], model).items():
            if "row" not in choice:
                continue
            bench = [b["y"] for b in choice["benchmark"] if b and b.get("y") is not None]
            baseline = sum(bench) / len(bench) if len(bench) == 2 else None
            chosen = choice["row"] if choice["side"] else None
            trades.append({"day": day, "underlying": root, "side": choice["side"],
                           "symbol": chosen["symbol"] if chosen else None,
                           "y": chosen.get("y") if chosen else None, "baseline": baseline,
                           "predicted": choice["predicted"]})
    return {"config": config, "log": trades, **summarise(trades)}


def summarise(trades: list[dict]) -> dict:
    taken = [t for t in trades if t["side"] and t.get("y") is not None]
    excess = [t["y"] - t["baseline"] for t in taken if t.get("baseline") is not None]
    def mean(values):
        return round(sum(values) / len(values), 5) if values else None
    t_stat = None
    if len(excess) > 2:
        m = sum(excess) / len(excess)
        sd = math.sqrt(sum((e - m) ** 2 for e in excess) / (len(excess) - 1))
        t_stat = round(m / (sd / math.sqrt(len(excess))), 2) if sd else None
    return {"opportunities": len(trades), "trades": len(taken), "no_trade": sum(1 for t in trades if not t["side"]),
            "mean_return": mean([t["y"] for t in taken]), "win_rate": mean([1.0 if t["y"] > 0 else 0.0 for t in taken]),
            "mean_excess": mean(excess), "excess_t": t_stat,
            "calls": sum(1 for t in taken if t["side"] > 0), "puts": sum(1 for t in taken if t["side"] < 0)}


def train(history: sqlite3.Connection, note: str = "") -> dict:
    """The scorecard once the multi-year index history is seeded; until then, the ridge configurations.

    The scorecard's training is its condition selection on ``agent_index_days`` (see
    ``index_scorecard``), redone nightly after the day's session is appended and labelled.
    """
    ensure_schema(history)
    index_scorecard.ensure_schema(history)
    if history.execute("SELECT COUNT(*) FROM agent_index_days").fetchone()[0]:
        return _train_scorecard(history, note)
    return _train_ridge(history, note)


def _train_scorecard(history: sqlite3.Connection, note: str = "") -> dict:
    updated = index_scorecard.update_history(history)
    model = index_scorecard.select(history)
    # This year's labelled days under the selection, in the table shape the UI reads. The selection
    # used only earlier years, so this is out of sample (R: the stop distance is one unit).
    year = str(datetime.now(UTC).year)
    taken = []
    opportunities = 0
    for features, ce_r, pe_r in history.execute(
            "SELECT features, ce_r, pe_r FROM agent_index_days WHERE day >= ? AND ce_r IS NOT NULL AND features IS NOT NULL",
            (f"{year}-01-01",)):
        opportunities += 1
        side = index_scorecard.decide(json.loads(features), model)["side"]
        if side:
            taken.append((side, ce_r if side > 0 else pe_r))
    summary = {"opportunities": opportunities, "trades": len(taken), "no_trade": opportunities - len(taken),
               "mean_return": None, "mean_excess": None, "excess_t": None,
               "win_rate": round(sum(1 for _s, r in taken if r > 0) / len(taken), 4) if taken else None,
               "mean_r": round(sum(r for _s, r in taken) / len(taken), 4) if taken else None,
               "calls": sum(1 for s, _r in taken if s > 0), "puts": sum(1 for s, _r in taken if s < 0)}
    selected_note = f"CE: {', '.join(model['ce']) or 'none'} | PE: {', '.join(model['pe']) or 'none'}"
    span = history.execute("SELECT MIN(day), MAX(day) FROM agent_index_days WHERE ce_r IS NOT NULL").fetchone()
    stamp = datetime.now(UTC).isoformat()
    previous = history.execute(
        "SELECT config FROM agent_index_models WHERE status = 'champion' ORDER BY version DESC LIMIT 1").fetchone()
    previous_name = json.loads(previous[0])["name"] if previous else None
    with history:
        history.execute("UPDATE agent_index_models SET status = 'retired' WHERE status = 'champion'")
        cursor = history.execute(
            """INSERT INTO agent_index_models (trained_at, config, train_from, train_to, samples, weights, stats, metrics, status, note)
               VALUES (?,?,?,?,?,?,?,?,'champion',?)""",
            (stamp, json.dumps({"name": "scorecard", "ce": model["ce"], "pe": model["pe"], "min_net": model["min_net"]}),
             span[0], span[1], model["rows"], json.dumps(model),
             json.dumps({"history_update": updated, "years": model["years"], "lifts": model["lifts"]}),
             json.dumps({"scorecard": summary}),
             note or (("scorecard selected: " if previous_name != "scorecard" else "scorecard reselected: ") + selected_note)))
        version = cursor.lastrowid
    return {"status": "promoted" if previous_name != "scorecard" else "kept", "champion": "scorecard", "best": "scorecard",
            "version": version, "selected": {"ce": model["ce"], "pe": model["pe"]}, "years": model["years"],
            "history_update": updated, "this_year": summary}


def _train_ridge(history: sqlite3.Connection, note: str = "") -> dict:
    """Judge configurations walk-forward; preserve every refit's weights under its own version."""
    rows = oa.load_candidates(history)
    labelled_days = sorted({r["day"] for r in rows if r.get("y") is not None})
    if len(labelled_days) < oa.MIN_TRAIN_DAYS + 4:
        return {"status": "insufficient", "labelled_days": len(labelled_days)}
    tests = labelled_days[-oa.VALIDATION_DAYS:] if len(labelled_days) >= oa.MIN_TRAIN_DAYS + 4 + oa.VALIDATION_DAYS \
        else labelled_days[oa.MIN_TRAIN_DAYS + 3:]
    results = {name: walk_forward(rows, config, tests) for name, config in CONFIGS.items()}
    score_of = lambda name: results[name]["mean_excess"] if results[name]["mean_excess"] is not None else -1.0
    best = max(results, key=score_of)
    champion = history.execute(
        "SELECT version, config FROM agent_index_models WHERE status = 'champion' ORDER BY version DESC LIMIT 1").fetchone()
    champion_name = json.loads(champion[1])["name"] if champion else None
    if champion_name not in CONFIGS:
        champion_name = None
    promote = champion_name is None or (best != champion_name and score_of(best) >= score_of(champion_name) + PROMOTE_MARGIN)
    chosen = best if promote else champion_name
    model = fit(rows, CONFIGS[chosen])
    metrics = {name: {k: v for k, v in r.items() if k not in ("log", "config")} for name, r in results.items()}
    stamp = datetime.now(UTC).isoformat()
    stats = json.dumps({"validation_days": tests, "trades": results[chosen]["log"]})
    with history:
        history.execute("UPDATE agent_index_models SET status = 'retired' WHERE status = 'champion'")
        cursor = history.execute(
            """INSERT INTO agent_index_models (trained_at, config, train_from, train_to, samples, weights, stats, metrics, status, note)
               VALUES (?,?,?,?,?,?,?,?,'champion',?)""",
            (stamp, json.dumps({"name": chosen, **CONFIGS[chosen]}), labelled_days[0], labelled_days[-1], len(rows),
             json.dumps(model), stats, json.dumps(metrics),
             note or (f"promoted {chosen}" if promote else f"refitted {chosen}"
                      + (f"; best {best} did not clear {PROMOTE_MARGIN:.0%}" if best != chosen else ""))))
        version = cursor.lastrowid
    return {"status": "promoted" if promote else "kept", "champion": chosen, "best": best, "version": version,
            "validation_days": len(tests), "validation": metrics}


def active_model(history: sqlite3.Connection) -> tuple[int | None, dict | None, str | None]:
    ensure_schema(history)
    row = history.execute(
        "SELECT version, weights, config FROM agent_index_models WHERE status = 'champion' ORDER BY version DESC LIMIT 1").fetchone()
    return (row[0], json.loads(row[1]), json.loads(row[2])["name"]) if row else (None, None, None)


def baseline_model(history: sqlite3.Connection) -> tuple[int, dict, str]:
    """Existing trend rule as an untrained paper control, never a champion."""
    ensure_schema(history)
    history.execute('BEGIN IMMEDIATE')
    with history:
        row=history.execute("SELECT version,weights,config FROM agent_index_models WHERE status='baseline' ORDER BY version DESC LIMIT 1").fetchone()
        if row:return row[0],json.loads(row[1]),json.loads(row[2])['name']
        name='trend-rule';model=fit([],CONFIGS[name])
        cursor=history.execute('''INSERT INTO agent_index_models(trained_at,config,train_from,train_to,samples,weights,stats,metrics,status,note)
            VALUES(?,?,NULL,NULL,0,?,?,?,'baseline',?)''',
            (datetime.now(UTC).isoformat(),json.dumps({'name':name,**CONFIGS[name]}),json.dumps(model),'{}','{}',
             'Untrained cold-start paper baseline; not calibrated return or evidence of edge.'))
        return cursor.lastrowid,model,name


# ---------------------------------------------------------------------------
# Emission and views
# ---------------------------------------------------------------------------

def emit(history: sqlite3.Connection, run_id: int, day: str, rows: list[dict], hist: oa.History,
         lots: dict[str, int] | None = None, model: dict | None = None, version: int | None = None,
         front: dict[str, str] | None = None) -> list[dict]:
    """Decide and store each index's trade for ``run_id``."""
    ensure_schema(history)
    if model is None:
        version, model, _name = active_model(history)
        if model is None:
            version, model, _name = baseline_model(history)
    stored = []
    if front is None:
        front = front_expiries((hist.by_day.get(day) or {}).keys(), day)
    features = None
    if model.get("name") == "scorecard":
        index_scorecard.update_history(history, day)     # yesterday's session must be in the daily history
        features = {root: index_scorecard.features_for(history, root, day) for root in TRADED_INDICES}
    for root, choice in decide(rows, model, front, features).items():
        spot = INDEX_SPOTS[root]
        spot_price = (hist.by_day.get(day, {}).get(spot) or {}).get("p1445")
        record = {"underlying": root, "side": choice["side"], "reason": choice["reason"], "predicted": choice.get("predicted"),
                  "conviction": choice.get("conviction"), "alternative": choice.get("alternative"),
                  "drivers": [[n, round(v, 4)] for n, v in choice.get("drivers", [])],
                  "series": choice.get("series"), "front_expiry": choice.get("front_expiry") or front.get(root)}
        row = choice.get("row") if choice["side"] else None
        if row is not None and spot_price:
            record.update(plan(row, spot_price, atr(hist, spot, day), day))
            record.update({"symbol": row["symbol"], "strike": row["strike"], "expiry": row["expiry"],
                           "entry_price": row["entry_price"], "lot": (lots or {}).get(row["symbol"])})
        elif spot_price:
            record.update({"spot": spot_price, "atr": atr(hist, spot, day)})
        history.execute(
            """INSERT OR REPLACE INTO agent_index_trades (run_id, day, underlying, side, symbol, strike, expiry, entry_price,
                 predicted, conviction, spot, atr, stop_spot, target_spot, premium_stop, premium_target, iv, lot, drivers,
                 alternative, reason, model_version, series, front_expiry)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (run_id, day, root, record["side"], record.get("symbol"), record.get("strike"), record.get("expiry"),
             record.get("entry_price"), record.get("predicted"), record.get("conviction"), record.get("spot"), record.get("atr"),
             record.get("stop_spot"), record.get("target_spot"), record.get("premium_stop"), record.get("premium_target"),
             record.get("iv"), record.get("lot"), json.dumps(record["drivers"]), json.dumps(record["alternative"]),
             record["reason"], version, record.get("series"), record.get("front_expiry")))
        stored.append(record)
    history.commit()
    return stored


_COLUMNS = ("underlying", "side", "symbol", "strike", "expiry", "entry_price", "predicted", "conviction", "spot", "atr",
            "stop_spot", "target_spot", "premium_stop", "premium_target", "iv", "lot", "drivers", "alternative", "reason",
            "model_version", "series", "front_expiry")


def _outcome(history: sqlite3.Connection, day: str, trade: dict) -> dict:
    """Premium outcomes from the candidate labels, and whether the index reached the stop or target first."""
    out = {"r1": None, "r2": None, "r3": None, "y": None, "max_ret": None, "min_ret": None, "spot_path": None}
    if not trade.get("symbol"):
        return out
    labels = history.execute("SELECT r1, r2, r3, y, max_ret, min_ret FROM agent_candidates WHERE day = ? AND symbol = ?",
                             (day, trade["symbol"])).fetchone()
    if labels:
        out.update(dict(zip(("r1", "r2", "r3", "y", "max_ret", "min_ret"), labels)))
    if trade.get("stop_spot") is None:
        return out
    spot = INDEX_SPOTS[trade["underlying"]]
    side, cut = trade["side"], oa._as_of_epoch(day)
    days = [day] + [market_calendar.next_trading_day(date.fromisoformat(day)).isoformat()]
    while len(days) < 4:
        days.append(market_calendar.next_trading_day(date.fromisoformat(days[-1])).isoformat())
    path = "open"
    for index, d in enumerate(days):
        start, end = oa.session_bounds(None, d)
        bar = history.execute(
            """SELECT MAX(high), MIN(low), COUNT(*) FROM historical_candles WHERE symbol = ? AND timeframe_seconds = 60
               AND timestamp >= ? AND timestamp < ?""", (spot, max(start, cut) if index == 0 else start, end)).fetchone()
        if not bar or not bar[2]:
            break
        high, low = bar[0], bar[1]
        hit_stop = (low <= trade["stop_spot"]) if side > 0 else (high >= trade["stop_spot"])
        hit_target = (high >= trade["target_spot"]) if side > 0 else (low <= trade["target_spot"])
        # Minute order within a session is not read here: both in one session counts as the stop (conservative).
        if hit_stop:
            path = f"stop D+{index}"
            break
        if hit_target:
            path = f"target D+{index}"
            break
        path = "time exit" if index == 3 else "open"
    out["spot_path"] = path
    return out


def view(history: sqlite3.Connection, run_id: int | None) -> list[dict]:
    ensure_schema(history)
    if run_id is None:
        return []
    rows = history.execute(f"SELECT day, {', '.join(_COLUMNS)} FROM agent_index_trades WHERE run_id = ? ORDER BY underlying",
                           (run_id,)).fetchall()
    out = []
    for raw in rows:
        day, trade = raw[0], dict(zip(_COLUMNS, raw[1:]))
        model = history.execute("SELECT config FROM agent_index_models WHERE version = ?",
                                (trade.get("model_version"),)).fetchone()
        config=json.loads(model[0]) if model else {}
        trade["prediction_kind"] = "scorecard" if config.get("name") == "scorecard" else "rule_score" if config.get('rule')=='trend' else "return"
        trade["drivers"] = json.loads(trade["drivers"] or "[]")
        trade["alternative"] = json.loads(trade["alternative"] or "null")
        if trade.get("symbol"):
            last, last_ts = oa._latest_price(history, trade["symbol"])
            trade["last_price"] = last
            trade["since_entry_pct"] = round((last / trade["entry_price"] - 1) * 100, 2) if last and trade["entry_price"] else None
            spot_now, _ = oa._latest_price(history, INDEX_SPOTS[trade["underlying"]])
            trade["spot_now"] = spot_now
        trade.update(_outcome(history, day, trade))
        out.append(trade)
    return out


def performance(history: sqlite3.Connection, since: str) -> dict:
    """Every run's index trades (latest run per day) against the coin-flip baseline."""
    ensure_schema(history)
    runs = history.execute(
        """SELECT r.run_id, r.day, r.source FROM agent_runs r
           JOIN (SELECT day, MAX(run_id) AS run_id FROM agent_runs WHERE day >= ? GROUP BY day) last ON last.run_id = r.run_id
           ORDER BY r.day""", (since,)).fetchall()
    trades = []
    for run_id, day, source in runs:
        for trade in view(history, run_id):
            baseline = None
            if trade.get("symbol"):
                baseline = _coin_flip(history, day, trade["underlying"], trade["expiry"])
            trades.append({"day": day, "source": source, "baseline": baseline, **{k: trade.get(k) for k in (
                "underlying", "side", "symbol", "entry_price", "predicted", "r1", "r2", "r3", "y", "max_ret", "min_ret",
                "spot_path", "reason")}})
    summary = summarise([t for t in trades if t.get("y") is not None or not t["side"]])
    paths = defaultdict(int)
    for t in trades:
        if t.get("spot_path"):
            paths[t["spot_path"].split(" ")[0]] += 1
    return {"trades": trades, "summary": summary, "paths": dict(paths)}


def _coin_flip(history: sqlite3.Connection, day: str, root: str, expiry: str) -> float | None:
    rows = history.execute(
        """SELECT side, y, json_extract(features, '$.o_moneyness') FROM agent_candidates
           WHERE day = ? AND underlying = ? AND expiry = ? AND y IS NOT NULL""", (day, root, expiry)).fetchall()
    best = {}
    for side, y, moneyness in rows:
        if side not in best or abs(moneyness or 0) < best[side][0]:
            best[side] = (abs(moneyness or 0), y)
    return (best[1][1] + best[-1][1]) / 2 if len(best) == 2 else None


def models_view(history: sqlite3.Connection, limit: int = 20) -> list[dict]:
    ensure_schema(history)
    out = []
    for version, trained, config, first, last, samples, weights, metrics, status, note in history.execute(
            """SELECT version, trained_at, config, train_from, train_to, samples, weights, metrics, status, note
               FROM agent_index_models ORDER BY version DESC LIMIT ?""", (limit,)):
        w = json.loads(weights or "{}")
        out.append({"version": version, "trained_at": trained, "config": json.loads(config), "train_from": first,
                    "train_to": last, "samples": samples, "status": status, "note": note,
                    "metrics": json.loads(metrics or "{}"),
                    "weights": {"rule": w["rule"]} if w.get("rule") else
                    dict(zip(w.get("features", []), [round(x, 4) for x in w.get("weights", [])]))})
    return out
