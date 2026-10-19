"""Read-only bounded feature inventory and sampled as-of context receipt.

Run on host with stdlib Python. This sends only frozen lifecycle metadata and
SQL text to the existing engine container; it performs no application imports,
broker/network calls, database writes, or settings changes.
"""
import bisect
import collections
import datetime as dt
import hashlib
import json
import pathlib
import subprocess
from zoneinfo import ZoneInfo

HERE = pathlib.Path(__file__).resolve().parent
SOURCE = HERE.parent / "data_analysis" / "evidence.json"
e = json.loads(SOURCE.read_text())
paths = {**e["paths"], **e.get("partial_paths", {})}
cycles = []
for key, p in paths.items():
    buy = p["single_entry"]
    sells = p["actual_sells"]
    symbol = buy["symbol"]
    root = ("BANKNIFTY" if ":BANKNIFTY" in symbol else
            "NIFTY" if ":NIFTY" in symbol else
            "SENSEX" if ":SENSEX" in symbol else "stock")
    cycles.append({"key": key, "symbol": symbol, "root": root,
                   "start": dt.datetime.fromisoformat(buy["timestamp"]).timestamp(),
                   "end": max(dt.datetime.fromisoformat(f["timestamp"]).timestamp() for f in sells)})
payload = {"start": min(c["start"] for c in cycles) - 300,
           "end": max(c["end"] for c in cycles),
           "direct_symbols": sorted({c["symbol"] for c in cycles if c["root"] in ("NIFTY", "BANKNIFTY")}),
           "all_symbols": sorted({c["symbol"] for c in cycles})}
CODE = r'''
import collections,datetime as dt,json,sqlite3,sys,time
payload=json.load(sys.stdin)
out={"captured_at_utc":dt.datetime.now(dt.timezone.utc).isoformat(),"queries":{},"schemas":{},"parameters":payload}
c=sqlite3.connect("file:/app/data/historical.sqlite3?mode=ro",uri=True)
c.row_factory=sqlite3.Row;c.execute("PRAGMA query_only=ON");c.execute("PRAGMA busy_timeout=3000")
deadline=time.monotonic()+35;c.set_progress_handler(lambda:int(time.monotonic()>deadline),10000)
def run(name,sql,args=()):
    try:out["queries"][name]={"sql":sql,"parameters":args,"rows":[dict(r) for r in c.execute(sql,args)]}
    except Exception as exc:out["queries"][name]={"sql":sql,"parameters":args,"error":str(exc)}
for table in ("historical_candles","historical_indicators","chain_snapshots","whale_strike_windows","whale_windows","whale_flow_minutes","dispersion_history"):
    out["schemas"][table]=c.execute("SELECT sql FROM sqlite_master WHERE name=?",(table,)).fetchone()[0]
run("chain_days","SELECT underlying,COUNT(*) days,MIN(day) first_day,MAX(day) last_day FROM chain_days GROUP BY underlying")
run("dispersion","SELECT source,timeframe_seconds,COUNT(*) rows,MIN(timestamp) first_ts,MAX(timestamp) last_ts,MIN(ce_eligible) min_ce,MIN(pe_eligible) min_pe,MAX(total) max_total FROM dispersion_history GROUP BY source,timeframe_seconds")
run("window_sources","SELECT underlying,source,COUNT(*) rows,MIN(ts) first_ts,MAX(ts) last_ts FROM whale_windows GROUP BY underlying,source")
run("flow_sources","SELECT symbol,source,COUNT(*) rows,MIN(minute_ts) first_ts,MAX(minute_ts) last_ts,SUM(oi IS NOT NULL) oi_rows FROM whale_flow_minutes GROUP BY symbol,source")
for root in ("NIFTY","BANKNIFTY"):
    args=(root,payload["start"],payload["end"])
    run("chain_summary:"+root,"SELECT COUNT(*) rows,COUNT(DISTINCT ts) snapshots,COUNT(DISTINCT symbol) symbols,MIN(ts) first_ts,MAX(ts) last_ts,SUM(oi>0) oi_positive,SUM(bid>0 AND ask>=bid) valid_bidask,SUM(prev_oi IS NOT NULL) prev_oi_rows,SUM(vix IS NOT NULL) vix_rows FROM chain_snapshots WHERE underlying=? AND ts BETWEEN ? AND ?",args)
    run("strike_quality:"+root,"SELECT delta_source,flow_source,COUNT(*) rows,SUM(iv IS NOT NULL) iv_rows,SUM(delta IS NOT NULL) delta_rows,MIN(ts) first_ts,MAX(ts) last_ts FROM whale_strike_windows WHERE underlying=? AND ts BETWEEN ? AND ? GROUP BY delta_source,flow_source",args)
    run("window_quality:"+root,"SELECT source,COUNT(*) rows,SUM(pcr_oi IS NOT NULL) pcr_rows,SUM(divergence) divergent_rows,SUM(composite IS NOT NULL) composite_rows,SUM(fut_oi IS NOT NULL) fut_oi_rows,MIN(history_days) min_history_days,MAX(history_days) max_history_days FROM whale_windows WHERE underlying=? AND ts BETWEEN ? AND ? GROUP BY source",args)
    run("windows:"+root,"SELECT underlying,ts,day,slot,window_seconds,then_ts,spot,fut,expiry,pcr_oi,pcr_oi_then,pcr_jump,pcr_vol_window,net_delta_units,net_dn,unusual_count,fut_symbol,fut_delta_units,fut_ofi,fut_dn,fut_oi,fut_d_oi,fut_sign,a_net,divergence_ratio,divergence,score_a,score_b,score_c,score_d,z_a,z_b,z_c,z_d,composite,composite_decayed,history_days,source FROM whale_windows WHERE underlying=? AND ts BETWEEN ? AND ? ORDER BY ts",args)
    symbols=payload["direct_symbols"]
    sql="SELECT * FROM whale_strike_windows WHERE underlying=? AND ts BETWEEN ? AND ? AND symbol IN ("+",".join("?" for _ in symbols)+") ORDER BY ts,strike,option_type"
    run("direct_strikes:"+root,sql,(*args,*symbols))
    sql="SELECT * FROM chain_snapshots WHERE underlying=? AND ts BETWEEN ? AND ? AND symbol IN ("+",".join("?" for _ in symbols)+") ORDER BY ts,symbol"
    run("direct_chain:"+root,sql,(*args,*symbols))
out["candle_symbols"]=[]
for symbol in payload["all_symbols"]:
    row=c.execute("SELECT COUNT(*) rows,MIN(timestamp) first_ts,MAX(timestamp) last_ts,SUM(volume=0) zero_volume FROM historical_candles WHERE symbol=? AND timeframe_seconds=60",(symbol,)).fetchone()
    out["candle_symbols"].append({"symbol":symbol,**dict(row)})
c.close()
c=sqlite3.connect("file:/app/data/ticks.sqlite3?mode=ro",uri=True);c.row_factory=sqlite3.Row;c.execute("PRAGMA query_only=ON");c.execute("PRAGMA busy_timeout=3000")
out["ticks"]={"raw_span":dict(c.execute("SELECT MIN(ts_ms) first_ts_ms,MAX(ts_ms) last_ts_ms FROM ticks").fetchone()),"tail_50000_quality":dict(c.execute("SELECT COUNT(*) rows,SUM(oi IS NOT NULL) oi_rows,SUM(bid>0 AND ask>=bid) valid_bidask,SUM(bid_qty>0) bid_size_rows,SUM(tbq IS NOT NULL AND tsq IS NOT NULL) pending_qty_rows FROM (SELECT * FROM ticks ORDER BY ts_ms DESC LIMIT 50000)").fetchone()),"condensed_days":[dict(r) for r in c.execute("SELECT * FROM tick_condensed_days ORDER BY day DESC LIMIT 30")]}
c.close();print(json.dumps(out,separators=(",",":")))
'''
proc = subprocess.run(["docker", "exec", "-i", "macd-mini-parallel-engine-1", "python", "-c", CODE],
                      input=json.dumps(payload), capture_output=True, text=True, check=True, timeout=55)
out = json.loads(proc.stdout)
out["frozen_source_sha256"] = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
out["cohort_root_counts"] = dict(collections.Counter(c["root"] for c in cycles))
IST = ZoneInfo("Asia/Kolkata")
lookup = {}
for root in ("NIFTY", "BANKNIFTY"):
    rows = out["queries"]["windows:" + root]["rows"]
    lookup[root] = ([r["ts"] for r in rows], rows)


def join(root, decision):
    times, rows = lookup[root]
    i = bisect.bisect_right(times, decision) - 1
    if i < 0:
        return None
    row = rows[i]
    same_day = dt.datetime.fromtimestamp(row["ts"], IST).date() == dt.datetime.fromtimestamp(decision, IST).date()
    return row if same_day and 0 <= decision - row["ts"] <= 300 else None


coverage = []
for cycle in cycles:
    p = paths[cycle["key"]]
    endpoints = sorted({b[0] + 60 for b in p["rows"] if b[0] >= cycle["start"] and b[0] + 60 <= cycle["end"]})
    root = cycle["root"]
    direct = root in lookup
    matched = sum(join(root, t) is not None for t in endpoints) if direct else 0
    entry_row = join(root, cycle["start"]) if direct else None
    coverage.append({**cycle, "direct_chain_root_supported": direct,
                     "observed_full_minute_endpoints": len(endpoints),
                     "direct_window_matches_sample_age_le_300s": matched,
                     "entry_direct_sample_match": entry_row is not None,
                     "entry_sample_age_seconds": cycle["start"] - entry_row["ts"] if entry_row else None,
                     "NIFTY_market_proxy_matches": sum(join("NIFTY", t) is not None for t in endpoints),
                     "BANKNIFTY_market_proxy_matches": sum(join("BANKNIFTY", t) is not None for t in endpoints),
                     "availability_certified": False})
out["asof_coverage"] = coverage
out["asof_limit"] = "Join uses sample ts <= decision, same IST day, sample age <=300s. No provider quote time/fetch completion/available_at exists; actual availability at decision is uncertified. Index-market context for stocks is a proxy, never direct chain sentiment. Missing is unknown, not neutral."
out["rows_sha256"] = hashlib.sha256(json.dumps(out["queries"], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
HERE.mkdir(parents=True, exist_ok=True)
target = HERE / "availability_evidence.json"
target.write_text(json.dumps(out, separators=(",", ":")) + "\n")
print(json.dumps({"file": str(target), "bytes": target.stat().st_size,
                  "cohort_root_counts": out["cohort_root_counts"],
                  "query_errors": {k: v["error"] for k, v in out["queries"].items() if "error" in v},
                  "direct_cycles_with_entry_sample": sum(r["entry_direct_sample_match"] for r in coverage),
                  "direct_observed_minute_endpoints": sum(r["observed_full_minute_endpoints"] for r in coverage if r["direct_chain_root_supported"]),
                  "direct_matches": sum(r["direct_window_matches_sample_age_le_300s"] for r in coverage)}, indent=2))
