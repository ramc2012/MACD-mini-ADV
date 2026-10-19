"""Read-only bounded snapshot. Run inside engine with stdout redirected to evidence.json.

docker compose exec -T engine python - < collect.py > evidence.json
No application imports, DB writes, backfill, strategy changes or settings changes.
"""
import collections
import datetime as dt
import hashlib
import json
import pathlib
import sqlite3
import urllib.request

UTC = dt.timezone.utc
def stamp(x):
    return dt.datetime.fromisoformat(x).timestamp()
def digest(x):
    return hashlib.sha256(json.dumps(x, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
def read_db(name):
    c = sqlite3.connect("file:/app/data/" + name + ".sqlite3?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA query_only=ON")
    c.execute("PRAGMA busy_timeout=5000")
    c.execute("BEGIN")
    return c

out = {"captured_at_utc": dt.datetime.now(UTC).isoformat(), "databases": {}, "api": {}, "paths": {}, "partial_paths": {},
       "policy_predeclaration": {"arms_pct": [50, 100], "favorable_profit_giveback_pct": 25,
           "arm_and_high_water": "completed minute closes", "trigger": "completed minute close <= entry + 0.75*(highest completed close-entry)",
           "fill": "next chronological minute open, only if <= original actual exit time", "exit_slippage_bps": 5,
           "standardized_fixed_fee_per_leg_inr": 20, "stress_additional_exit_slippage_bps": 20,
           "partial_alternatives": "separate completed single-BUY multiple-SELL cohort; prior actual partials retained, next-open closes remaining whole lots and cancels later actual sells"}}
for name in ("macd_trader", "smart_50l", "smart_1l", "blast_trader", "mp_trader"):
    c = read_db(name)
    data = {"schema": {r["name"]: r["sql"] for r in c.execute("SELECT name,sql FROM sqlite_master WHERE type='table'")}}
    for table in ("trades", "closed_positions", "mp_closed_positions"):
        data[table] = [json.loads(r[0]) for r in c.execute("SELECT payload FROM " + table)]
    data["position_excursions"] = [dict(r) for r in c.execute("SELECT * FROM position_excursions")]
    data["table_hashes"] = {k: digest(v) for k, v in data.items() if isinstance(v, list)}
    out["databases"][name] = data
    c.rollback()
    c.close()
for path in ("/api/portfolio", "/api/lanes", "/api/lanes/smart_50l/snapshot", "/api/lanes/smart_1l/snapshot", "/api/blast/snapshot", "/api/mp/snapshot", "/api/settings"):
    try:
        out["api"][path] = json.load(urllib.request.urlopen("http://127.0.0.1:8100" + path, timeout=20))
    except Exception as e:
        out["api"][path] = {"error": str(e)}
sources = ("portfolio.py", "models.py", "execution.py", "repository.py", "engine.py", "strategy.py", "smart_lane.py", "blast_engine.py", "mp_engine.py", "market_calendar.py", "sessions.py")
out["calendar_provenance"] = {"clock": "09:15 <= minute < 15:40 IST; derivatives schedule from deployed sessions.py", "holiday_in_cohort": "2026-10-02", "holiday_source": "deployed market_calendar.py", "official_timing_reference": "https://www.nseindia.com/static/market-data/market-timings", "CAS_reference": "https://www.nseindia.in/static/products-services/closing-auction-session"}
out["deployed_source_hashes"] = {}
for name in sources:
    p = pathlib.Path("/app/src/macd_trader") / name
    if p.exists():
        out["deployed_source_hashes"][name] = hashlib.sha256(p.read_bytes()).hexdigest()
# Paths only for single-entry, single-close, fully closed positions. MP has a separate table.
hist = read_db("historical")
for name, data in out["databases"].items():
    groups = collections.defaultdict(list)
    rows = data["mp_closed_positions"] if name == "mp_trader" else data["closed_positions"]
    for r in rows:
        pid = r.get("position_id", r["symbol"] + "|" + r["entry_time"])
        groups[pid].append(r)
    for pid, slices in groups.items():
        slices = sorted(slices, key=lambda r: stamp(r["exit_time"]))
        if name == "mp_trader" or slices[-1].get("partial") or slices[-1].get("remaining_quantity", 0):
            continue
        if any(str(r.get("exit_reason", "")).startswith("ADMIN_") for r in slices):
            continue
        r = slices[0]
        start, end = stamp(r["entry_time"]), stamp(slices[-1]["exit_time"])
        fills = [f for f in data["trades"] if f["symbol"] == r["symbol"] and start - .001 <= stamp(f["timestamp"]) <= end + .001]
        buys = [f for f in fills if f["side"] == "BUY"]
        sells = [f for f in fills if f["side"] == "SELL"]
        if len(buys) != 1 or sum(f["quantity"] for f in sells) != buys[0]["quantity"] or sum(x["quantity"] for x in slices) != buys[0]["quantity"]:
            continue
        if len(sells) != len(slices) or any(abs(x["entry_price"] - buys[0]["price"]) > .000001 for x in slices):
            continue
        candles = [list(x) for x in hist.execute(
            "SELECT timestamp,open,high,low,close,volume FROM historical_candles WHERE symbol=? AND timeframe_seconds=60 AND timestamp>=? AND timestamp<=? ORDER BY timestamp",
            (r["symbol"], int(start // 60 * 60), int(end // 60 * 60)))]
        target = "paths" if len(slices) == 1 else "partial_paths"
        out[target][name + "::" + pid] = {"rows": candles, "row_sha256": digest(candles), "single_entry": buys[0], "single_exit": sells[-1], "actual_sells": sorted(sells, key=lambda f: stamp(f["timestamp"]))}
hist.rollback()
hist.close()
out["api_snapshot_sha256"] = digest(out["api"])
print(json.dumps(out, separators=(",", ":")))
