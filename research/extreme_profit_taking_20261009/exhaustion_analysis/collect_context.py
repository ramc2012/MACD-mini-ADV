"""Collection only: freeze exact-contract minute context without evaluating a policy.

Run with the bundled Python from the repository checkout. All output stays in
this research directory. The Docker child uses SQLite mode=ro/query_only and
only indexed SELECTs. No application code is imported or production file edited.
"""
from __future__ import annotations

import collections
import csv
import datetime as dt
import gzip
import hashlib
import json
import math
import pathlib
import subprocess
from zoneinfo import ZoneInfo

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
ORIGINAL = HERE.parent / "data_analysis" / "evidence.json"
IST = ZoneInfo("Asia/Kolkata")
HOLIDAYS = {dt.date(2026, 9, 14), dt.date(2026, 10, 2)}
MIN_CONTEXT_SESSIONS = 3
MAX_CONTEXT_SESSIONS = 10

def stamp(x): return dt.datetime.fromisoformat(x).timestamp()
def iso(x): return dt.datetime.fromtimestamp(x, dt.timezone.utc).isoformat()
def digest(x): return hashlib.sha256(json.dumps(x, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
def prior_sessions(day, count):
    found=[]
    while len(found)<count:
        day -= dt.timedelta(days=1)
        if day.weekday()<5 and day not in HOLIDAYS: found.append(day)
    return list(reversed(found))
def bounds(day):
    # This input spans only the post-3-Aug-2026 derivatives regime.
    if not dt.date(2026,8,3)<=day<dt.date(2027,1,1):
        raise ValueError("Calendar regime outside frozen collection scope")
    return (int(dt.datetime.combine(day,dt.time(9,15),IST).timestamp()),
            int(dt.datetime.combine(day,dt.time(15,40),IST).timestamp()))
def minute_grid(start,end,completed=False):
    lo=math.ceil(start/60)*60
    hi=(math.floor((end-60)/60)*60 if completed else math.floor(end/60)*60)
    result=[]
    for t in range(lo,hi+1,60):
        local=dt.datetime.fromtimestamp(t,IST)
        if local.weekday()<5 and local.date() not in HOLIDAYS and (9,15)<=(local.hour,local.minute)<(15,40):result.append(t)
    return result
def grid_3m(start,end):
    day=dt.datetime.fromtimestamp(start,IST).date()
    last=dt.datetime.fromtimestamp(end,IST).date()
    result=[]
    while day<=last:
        if day.weekday()<5 and day not in HOLIDAYS:
            opened,closed=bounds(day)
            # A 385-minute derivatives session has 128 complete 3m bars plus
            # one final minute; that 15:39 minute is never fabricated into 3m.
            result.extend(t for t in range(opened,closed-179,180) if t>=start and t+180<=end)
        day+=dt.timedelta(days=1)
    return result
def valid(row):
    return (min(row[1:5])>0 and row[2]>=max(row[1],row[4]) and row[3]<=min(row[1],row[4]) and row[5]>=0)

DATABASE_SCRIPT=r'''
import datetime as dt,hashlib,json,pathlib,sqlite3,sys
m=json.load(sys.stdin)
c=sqlite3.connect("file:/app/data/historical.sqlite3?mode=ro",uri=True)
c.execute("PRAGMA query_only=ON");c.execute("PRAGMA busy_timeout=5000");c.execute("BEGIN")
sql="SELECT timestamp,open,high,low,close,volume,asset_type,expiry,downloaded_at FROM historical_candles WHERE symbol=? AND timeframe_seconds=60 AND timestamp>=? AND timestamp<=? ORDER BY timestamp"
out={"captured_at_utc":dt.datetime.now(dt.timezone.utc).isoformat(),"histories":{},"deployed_context_sources":{}}
out["schema"]=c.execute("SELECT sql FROM sqlite_master WHERE name='historical_candles'").fetchone()[0]
out["columns"]=["timestamp","open","high","low","close","volume","asset_type","expiry","downloaded_at"]
out["index_list"]=list(c.execute("PRAGMA index_list(historical_candles)"))
for symbol,r in m["ranges"].items():
 rows=list(c.execute(sql,(symbol,r["start"],r["end"])))
 out["histories"][symbol]={"query_start":r["start"],"query_end":r["end"],"rows":rows}
 if "query_plan" not in out:out["query_plan"]=list(c.execute("EXPLAIN QUERY PLAN "+sql,(symbol,r["start"],r["end"])))
for name in ("sessions.py","market_calendar.py","regimes.py"):
 p=pathlib.Path("/app/src/macd_trader")/name;b=p.read_bytes()
 out["deployed_context_sources"][name]={"sha256":hashlib.sha256(b).hexdigest(),"text":b.decode()}
c.rollback();c.close();print(json.dumps(out,separators=(",",":")))
'''

def query(ranges):
    p=subprocess.run(["docker","compose","exec","-T","engine","python","-c",DATABASE_SCRIPT],
        input=json.dumps({"ranges":ranges}),text=True,capture_output=True,cwd=ROOT,check=True)
    return json.loads(p.stdout)

def context_quality(rows,start,entry,end):
    raw={int(r[0]):r for r in rows}
    pre=minute_grid(start,entry,completed=True)
    holding=minute_grid(math.ceil(entry/60)*60,end)
    bins=grid_3m(start,entry)
    complete=[t for t in bins if all(t+i in raw and valid(raw[t+i]) for i in (0,60,120))]
    tail=0
    for t in reversed(bins):
        if t not in complete:break
        tail+=1
    return {"preentry_expected_completed_minutes":len(pre),"preentry_present_minutes":sum(t in raw for t in pre),
        "preentry_missing_minutes":sum(t not in raw for t in pre),"preentry_invalid_minutes":sum(t in raw and not valid(raw[t]) for t in pre),
        "preentry_zero_volume_minutes":sum(t in raw and raw[t][5]==0 for t in pre),
        "preentry_expected_complete_3m_bins":len(bins),"preentry_observed_complete_3m_bins":len(complete),
        "preentry_trailing_contiguous_complete_3m_bins":tail,"preentry_at_least_100_complete_3m_bins":len(complete)>=100,
        "preentry_last_100_grid_bins_complete":tail>=100,
        "holding_expected_minutes":len(holding),"holding_missing_minutes":sum(t not in raw for t in holding),
        "holding_invalid_minutes":sum(t in raw and not valid(raw[t]) for t in holding),
        "holding_zero_volume_minutes":sum(t in raw and raw[t][5]==0 for t in holding),
        "last_completed_preentry_minute_utc":iso(max((t for t in pre if t in raw),default=0)) if any(t in raw for t in pre) else None}

original_bytes=ORIGINAL.read_bytes()
original=json.loads(original_bytes)
positions={}
for kind,paths in (("single_full_close",original["paths"]),("actual_partial_sells",original["partial_paths"])):
    for key,p in paths.items():
        buy=p["single_entry"];sell=p["single_exit"]
        entry,end=stamp(buy["timestamp"]),stamp(sell["timestamp"])
        entry_day=dt.datetime.fromtimestamp(entry,IST).date()
        days=prior_sessions(entry_day,MIN_CONTEXT_SESSIONS)
        expanded=prior_sessions(entry_day,MAX_CONTEXT_SESSIONS)
        lane,pid=key.split("::",1)
        closes=[r for r in original["databases"][lane]["closed_positions"] if r["position_id"]==pid]
        positions[key]={"lane":lane,"cohort":kind,"symbol":buy["symbol"],"position_id":pid,"single_entry":buy,
            "actual_sells":p["actual_sells"],"closed_slices":sorted(closes,key=lambda r:stamp(r["exit_time"])),
            "entry_ts":entry,"actual_final_exit_ts":end,"prior_three_session_dates":[d.isoformat() for d in days],
            "three_session_start":bounds(days[0])[0],"expanded_ten_session_start":bounds(expanded[0])[0],
            "context_start":bounds(days[0])[0],"original_holding_rows":p["rows"],"original_holding_row_sha256":p["row_sha256"]}

ranges={}
for p in positions.values():
    symbol=p["symbol"]
    if symbol not in ranges:ranges[symbol]={"start":p["context_start"],"end":int(p["actual_final_exit_ts"]//60*60)}
    else:
        ranges[symbol]["start"]=min(ranges[symbol]["start"],p["context_start"])
        ranges[symbol]["end"]=max(ranges[symbol]["end"],int(p["actual_final_exit_ts"]//60*60))
frozen=query(ranges)
# Verify the previously frozen session/calendar source did not drift.
for name in ("sessions.py","market_calendar.py"):
    if frozen["deployed_context_sources"][name]["sha256"]!=original["deployed_source_hashes"][name]:
        raise ValueError("Deployed context source changed since original evidence: "+name)

expanded_symbols={}
for p in positions.values():
    rows=frozen["histories"][p["symbol"]]["rows"]
    q=context_quality(rows,p["context_start"],p["entry_ts"],p["actual_final_exit_ts"])
    if q["preentry_observed_complete_3m_bins"]<100:
        p["context_start"]=p["expanded_ten_session_start"]
        symbol=p["symbol"]
        expanded_symbols.setdefault(symbol,{"start":ranges[symbol]["start"],"end":ranges[symbol]["end"]})
        expanded_symbols[symbol]["start"]=min(expanded_symbols[symbol]["start"],p["context_start"])
if expanded_symbols:
    extra=query(expanded_symbols)
    for name,data in extra["deployed_context_sources"].items():
        if data["sha256"]!=frozen["deployed_context_sources"][name]["sha256"]:raise ValueError("Source drift during extraction")
    frozen["histories"].update(extra["histories"])
    frozen["expanded_query_captured_at_utc"]=extra["captured_at_utc"]

coverage=[];revisions=[]
for key,p in positions.items():
    rows=frozen["histories"][p["symbol"]]["rows"]
    q=context_quality(rows,p["context_start"],p["entry_ts"],p["actual_final_exit_ts"])
    p["coverage"]=q
    raw={int(r[0]):r for r in rows}
    prior_counts=[]
    for day in p["prior_three_session_dates"]:
        lo,hi=bounds(dt.date.fromisoformat(day));expected=list(range(lo,hi,60))
        prior_counts.append({"date":day,"expected_minutes":len(expected),"present_minutes":sum(t in raw for t in expected),
            "missing_minutes":sum(t not in raw for t in expected),"invalid_minutes":sum(t in raw and not valid(raw[t]) for t in expected)})
    p["prior_three_session_coverage"]=prior_counts
    old={int(r[0]):r for r in p["original_holding_rows"]}
    lo=int(p["entry_ts"]//60*60);hi=int(p["actual_final_exit_ts"]//60*60)
    new={t:r[:6] for t,r in raw.items() if lo<=t<=hi}
    changed=[t for t in old.keys()&new.keys() if old[t]!=new[t]]
    added=sorted(new.keys()-old.keys());removed=sorted(old.keys()-new.keys())
    revision={"position_key":key,"changed_existing_rows":len(changed),"added_rows":len(added),"removed_rows":len(removed),
        "changed_timestamp_samples":changed[:10],"added_timestamp_samples":added[:10],"removed_timestamp_samples":removed[:10]}
    p["original_holding_revision_check"]=revision
    revisions.append(revision)
    coverage.append({"position_key":key,"lane":p["lane"],"cohort":p["cohort"],"symbol":p["symbol"],"entry_time":p["single_entry"]["timestamp"],
        "context_start_utc":iso(p["context_start"]),"context_expanded_to_ten_sessions":p["context_start"]!=p["three_session_start"],
        "all_three_prior_sessions_complete":all(x["missing_minutes"]==0 and x["invalid_minutes"]==0 for x in prior_counts),**q,
        "original_holding_changed_rows":len(changed),"original_holding_added_rows":len(added),"original_holding_removed_rows":len(removed)})

for symbol,h in frozen["histories"].items():
    h["row_sha256"]=digest(h["rows"])
    h["row_count"]=len(h["rows"])
frozen.update({"original_evidence_path":str(ORIGINAL),"original_evidence_sha256":hashlib.sha256(original_bytes).hexdigest(),
    "positions":positions,"research_script_sha256":hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),
    "collection_scope":{"timeframe_seconds":60,"requested_prior_sessions":MIN_CONTEXT_SESSIONS,"maximum_prior_sessions_if_under_100_complete_3m_bins":MAX_CONTEXT_SESSIONS,
        "clock":"09:15 <= minute start < 15:40 IST, post 2026-08-03 derivatives regime", "three_minute_alignment":"Session anchored at09:15; require all3constituent minutes; omit incomplete15:39finalminute from3m bars",
        "holidays_in_extraction_scope":[d.isoformat() for d in sorted(HOLIDAYS)],"no_policy_or_performance_evaluation":True,
        "historical_data_asof_warning":"Downloaded OHLCV is retrospective; downloaded_at may be after entry. This does not establish live feature availability then.",
        "no_fabrication":"Missing or invalid candles remain missing/invalid; no forward fill, interpolation, replacement contracts or continuous-series substitution."}})

summary={"captured_at_utc":frozen["captured_at_utc"],"original_evidence_sha256":frozen["original_evidence_sha256"],
    "positions":len(positions),"unique_contract_symbols":len(frozen["histories"]),"unique_minute_rows":sum(h["row_count"] for h in frozen["histories"].values()),
    "earliest_row_utc":iso(min((r[0] for h in frozen["histories"].values() for r in h["rows"]),default=0)),
    "latest_row_utc":iso(max((r[0] for h in frozen["histories"].values() for r in h["rows"]),default=0)),
    "all_three_prior_sessions_complete_positions":sum(c["all_three_prior_sessions_complete"] for c in coverage),
    "preentry_at_least_100_complete_3m_positions":sum(c["preentry_at_least_100_complete_3m_bins"] for c in coverage),
    "preentry_last100_grid_bins_complete_positions":sum(c["preentry_last_100_grid_bins_complete"] for c in coverage),
    "context_expanded_positions":sum(c["context_expanded_to_ten_sessions"] for c in coverage),"context_expanded_unique_symbols":len(expanded_symbols),
    "holding_complete_valid_positions":sum(c["holding_missing_minutes"]==0 and c["holding_invalid_minutes"]==0 and c["holding_expected_minutes"]>0 for c in coverage),
    "holding_subminute_positions":sum(c["holding_expected_minutes"]==0 for c in coverage),
    "historical_revision_counts":{"changed_existing_rows":sum(r["changed_existing_rows"] for r in revisions),"added_rows":sum(r["added_rows"] for r in revisions),"removed_rows":sum(r["removed_rows"] for r in revisions)},
    "per_lane":{lane:{"positions":sum(c["lane"]==lane for c in coverage),
        "at_least100_preentry_3m":sum(c["lane"]==lane and c["preentry_at_least_100_complete_3m_bins"] for c in coverage),
        "trailing100_preentry_3m_complete":sum(c["lane"]==lane and c["preentry_last_100_grid_bins_complete"] for c in coverage)} for lane in sorted({c["lane"] for c in coverage})},
    "columns":frozen["columns"],"schema":frozen["schema"],"query_plan":frozen["query_plan"],"collection_scope":frozen["collection_scope"],
    "source_hashes":{name:data["sha256"] for name,data in frozen["deployed_context_sources"].items()}}
serialized=json.dumps(frozen,separators=(",",":")).encode()
target=HERE/"inputs.json.gz"
with target.open("wb") as f:
    with gzip.GzipFile(fileobj=f,mode="wb",mtime=0) as z:z.write(serialized)
summary["inputs_uncompressed_sha256"]=hashlib.sha256(serialized).hexdigest()
summary["inputs_gzip_sha256"]=hashlib.sha256(target.read_bytes()).hexdigest()
HERE.joinpath("collection_summary.json").write_text(json.dumps(summary,indent=2))
with HERE.joinpath("lookback_coverage.csv").open("w",newline="") as f:
    w=csv.DictWriter(f,fieldnames=list(coverage[0]));w.writeheader();w.writerows(coverage)
print(json.dumps(summary,indent=2))
