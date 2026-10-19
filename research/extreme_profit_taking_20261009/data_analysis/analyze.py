"""Reproducible diagnostic and deliberately small causal exit-only comparison.

Use frozen evidence.json; stdlib only. Writes research artifacts beside evidence.
"""
import collections
import csv
import datetime as dt
import hashlib
import json
import math
import pathlib
import statistics
import sys
from zoneinfo import ZoneInfo

HERE = pathlib.Path(__file__).resolve().parent
EVIDENCE = HERE / "evidence.json"
e = json.loads(EVIDENCE.read_text())
IST = ZoneInfo("Asia/Kolkata")
# Calendar source snapshot: this cohort spans Sep29-Oct9; Oct2 was an exchange holiday.
HOLIDAYS = {dt.date(2026, 10, 2)}
def stamp(x): return dt.datetime.fromisoformat(x).timestamp()
def pid(r): return r.get("position_id", r["symbol"] + "|" + r["entry_time"])
def money(x): return round(x, 2)
def day(x): return dt.datetime.fromtimestamp(stamp(x), IST).date().isoformat()
def expected_minutes(start, end):
    first, last = math.ceil(start / 60) * 60, math.floor(end / 60) * 60
    # Include a next-open fill minute up to original exit, ignore pre-entry partial bar.
    result = []
    for t in range(first, last + 1, 60):
        local = dt.datetime.fromtimestamp(t, IST)
        if local.weekday() < 5 and local.date() not in HOLIDAYS and (9,15) <= (local.hour,local.minute) < (15,40):
            result.append(t)
    return result

summary = {"captured_at_utc": e["captured_at_utc"], "frozen_input_sha256": hashlib.sha256(EVIDENCE.read_bytes()).hexdigest(),
           "policy_predeclaration": e["policy_predeclaration"], "calendar_provenance":e["calendar_provenance"], "cohorts": {}, "open_book": {}, "overlays": {}, "overlays_optimistic_zero_volume_allowed":{}, "limitations": []}
coverage, comparisons, examples = [], [], []
for name, data in e["databases"].items():
    rows = data["mp_closed_positions"] if name == "mp_trader" else data["closed_positions"]
    groups = collections.defaultdict(list)
    for r in rows: groups[pid(r)].append(r)
    complete = {k: sorted(v,key=lambda x:stamp(x["exit_time"])) for k,v in groups.items() if any(not x.get("partial") and not x.get("remaining_quantity",0) for x in v)}
    net = sum(r.get("realized_pnl",r.get("pnl",0)) for r in rows)
    mfe_known = [r for r in rows if r.get("max_price") is not None and r.get("max_return_pct") is not None]
    # Per-slice peak credits are hindsight independent maxima, never portfolio high water.
    observed_giveback = sum(max(0, (r["max_price"]-r["exit_price"])*r["quantity"]) for r in mfe_known)
    wtl = [r for r in mfe_known if r["max_return_pct"] >= 10 and r.get("realized_pnl",r.get("pnl",0)) < 0]
    summary["cohorts"][name] = {"fill_count":len(data["trades"]),"closing_slice_count":len(rows),"unique_position_count":len(groups),
        "fully_closed_position_count":len(complete),"partial_slice_count":sum(bool(r.get("partial")) for r in rows),
        "earliest_entry":min((r["entry_time"] for r in rows),key=stamp,default=None),"latest_exit":max((r["exit_time"] for r in rows),key=stamp,default=None),
        "exit_days_ist":dict(collections.Counter(day(r["exit_time"]) for r in rows)),"realized_pnl_retained_slices":money(net),
        "missing_MFE_slices":len(rows)-len(mfe_known),"slice_observed_peak_giveback_inr":money(observed_giveback),
        "slice_positive_at_least_10pct_then_net_loss":len(wtl),"slice_MFE_at_least_50pct":sum(r["max_return_pct"]>=50 for r in mfe_known),
        "slice_MFE_at_least_50pct_then_net_loss":sum(r["max_return_pct"]>=50 and r.get("realized_pnl",r.get("pnl",0))<0 for r in mfe_known),
        "slice_MFE_at_least_100pct":sum(r["max_return_pct"]>=100 for r in mfe_known),
        "reasons":dict(collections.Counter(r.get("exit_reason") for r in rows))}
    for r in mfe_known:
        examples.append({"lane":name,"position_id":pid(r),"symbol":r["symbol"],"entry_time":r["entry_time"],"exit_time":r["exit_time"],
            "quantity":r["quantity"],"partial":r.get("partial",False),"entry_price":r["entry_price"],"exit_price":r["exit_price"],
            "max_price":r["max_price"],"max_return_pct":r["max_return_pct"],"return_pct":r["return_pct"],
            "realized_pnl":r.get("realized_pnl",r.get("pnl",0)),"peak_giveback_inr":money(max(0,(r["max_price"]-r["exit_price"])*r["quantity"])),"exit_reason":r.get("exit_reason")})
    for key,path in e["paths"].items():
        if not key.startswith(name+"::"):continue
        r=groups[key.split("::",1)[1]][0]
        start,end=stamp(r["entry_time"]),stamp(r["exit_time"])
        candles={int(b[0]):b for b in path["rows"]}
        expected=expected_minutes(start,end)
        missing=[t for t in expected if t not in candles]
        invalid=[t for t in expected if t in candles and (min(candles[t][1:5])<=0 or candles[t][2]<max(candles[t][1],candles[t][4]) or candles[t][3]>min(candles[t][1],candles[t][4]))]
        c={"cohort":"single_full_close","lane":name,"position_id":pid(r),"symbol":r["symbol"],"expected_minutes":len(expected),"observed_minutes":len(candles),"missing_minutes":len(missing),"invalid_minutes":len(invalid),"eligible":bool(expected) and not missing and not invalid,"missing_first_utc":dt.datetime.fromtimestamp(missing[0],dt.timezone.utc).isoformat() if missing else None}
        coverage.append(c)
        if not c["eligible"]:continue
        entry,qty=r["entry_price"],r["quantity"]
        actual_exit=r["exit_price"]
        baseline_gross=(actual_exit-entry)*qty
        for arm in e["policy_predeclaration"]["arms_pct"]:
            peak=entry; armed=False; trigger=None; exit_price=actual_exit; exit_t=end
            for i,t in enumerate(expected[:-1]):
                # Decision only after this completed bar; next minute open is executable.
                if t+60>end:break
                close=candles[t][4]
                peak=max(peak,close)
                if peak>=entry*(1+arm/100):armed=True
                if armed and close <= entry+.75*(peak-entry):
                    nt=expected[i+1]
                    # Across an overnight/weekend jump, fill uses next actual session open.
                    if nt>end:break
                    trigger=t+60; exit_t=nt; exit_price=candles[nt][1]*(1-.0005); break
            alt_gross=(exit_price-entry)*qty
            # Both fixed entry and fixed exit are charged once under both choices.
            # Baseline actual fill already contains production 5bps and is never charged again.
            actual_standardized_net=baseline_gross-40
            overlay_standardized_net=alt_gross-40
            stress_actual=(actual_exit*(1-.002)-entry)*qty-40
            stress_overlay=(exit_price*(1-.002)-entry)*qty-40
            comparisons.append({"cohort":"single_full_close","lane":name,"position_id":pid(r),"symbol":r["symbol"],"arm_pct":arm,"quantity":qty,"lot_size":r["lot_size"],"entry_price":entry,
                "actual_exit_time":r["exit_time"],"actual_exit_price":actual_exit,"overlay_exit_time":dt.datetime.fromtimestamp(exit_t,dt.timezone.utc).isoformat(),
                "overlay_exit_price":exit_price,"triggered":trigger is not None,"triggered_at_utc":dt.datetime.fromtimestamp(trigger,dt.timezone.utc).isoformat() if trigger else None,
                "actual_net_fixed20_per_leg":actual_standardized_net,"overlay_net_fixed20_per_leg":overlay_standardized_net,"delta_inr":alt_gross-baseline_gross,
                "actual_stress_net":stress_actual,"overlay_stress_net":stress_overlay,"stress_delta_inr":stress_overlay-stress_actual,
                "actual_return_pct":100*(actual_exit/entry-1),"overlay_return_pct":100*(exit_price/entry-1),"actual_sell_count":1,"overlay_sell_count":1,
                "remaining_qty_on_trigger":qty if trigger else 0,"fill_bar_volume":candles[exit_t][5] if trigger else None,"recorded_fee_baseline_net":baseline_gross-path["single_entry"]["fees"]-path["single_exit"]["fees"],
                "recorded_fee_overlay_net":alt_gross-path["single_entry"]["fees"]-(20 if trigger else path["single_exit"]["fees"])})

# Extension: completed single-BUY lifecycles with multiple actual SELL slices.
# Preserve actual earlier partial cash, entry and initial quantity; close only residual whole lots.
for key,path in e.get("partial_paths",{}).items():
    name,p_id=key.split("::",1)
    slices=sorted([r for r in e["databases"][name]["closed_positions"] if pid(r)==p_id],key=lambda x:stamp(x["exit_time"]))
    r=slices[0]; buys=path["single_entry"]; sells=path["actual_sells"]
    start,end=stamp(buys["timestamp"]),stamp(sells[-1]["timestamp"])
    entry,qty=buys["price"],buys["quantity"]
    candles={int(b[0]):b for b in path["rows"]}
    expected=expected_minutes(start,end)
    missing=[t for t in expected if t not in candles]
    invalid=[t for t in expected if t in candles and (min(candles[t][1:5])<=0 or candles[t][2]<max(candles[t][1],candles[t][4]) or candles[t][3]>min(candles[t][1],candles[t][4]))]
    c={"cohort":"actual_partial_sells","lane":name,"position_id":p_id,"symbol":r["symbol"],"expected_minutes":len(expected),"observed_minutes":len(candles),"missing_minutes":len(missing),"invalid_minutes":len(invalid),"eligible":bool(expected) and not missing and not invalid,"missing_first_utc":dt.datetime.fromtimestamp(missing[0],dt.timezone.utc).isoformat() if missing else None}
    coverage.append(c)
    if not c["eligible"]:continue
    baseline_proceeds=sum(f["price"]*f["quantity"] for f in sells)
    baseline_net=baseline_proceeds-entry*qty-20*(1+len(sells))
    baseline_recorded_net=baseline_proceeds-entry*qty-buys["fees"]-sum(f["fees"] for f in sells)
    for arm in e["policy_predeclaration"]["arms_pct"]:
        peak=entry;armed=False;trigger=None;exit_t=end;overlay_proceeds=baseline_proceeds
        overlay_sells=len(sells);remaining=0;prior=sells
        for i,t in enumerate(expected[:-1]):
            if t+60>end:break
            close=candles[t][4];peak=max(peak,close)
            if peak>=entry*(1+arm/100):armed=True
            if armed and close<=entry+.75*(peak-entry):
                nt=expected[i+1]
                if nt>end:break
                prior=[f for f in sells if stamp(f["timestamp"])<=nt]
                remaining=qty-sum(f["quantity"] for f in prior)
                if remaining<=0:break
                if remaining%r["lot_size"]:raise ValueError("Residual is not a whole number of lots: "+p_id)
                trigger=t+60;exit_t=nt
                overlay_proceeds=sum(f["price"]*f["quantity"] for f in prior)+remaining*candles[nt][1]*(1-.0005)
                overlay_sells=len(prior)+1
                break
        overlay_net=overlay_proceeds-entry*qty-20*(1+overlay_sells)
        overlay_recorded_net=(overlay_proceeds-entry*qty-buys["fees"]-sum(f["fees"] for f in prior)-20) if trigger else baseline_recorded_net
        stress_actual=baseline_proceeds*(1-.002)-entry*qty-20*(1+len(sells))
        stress_overlay=overlay_proceeds*(1-.002)-entry*qty-20*(1+overlay_sells)
        comparisons.append({"cohort":"actual_partial_sells","lane":name,"position_id":p_id,"symbol":r["symbol"],"arm_pct":arm,"quantity":qty,"lot_size":r["lot_size"],"entry_price":entry,
            "actual_exit_time":sells[-1]["timestamp"],"actual_exit_price":baseline_proceeds/qty,"overlay_exit_time":dt.datetime.fromtimestamp(exit_t,dt.timezone.utc).isoformat(),
            "overlay_exit_price":overlay_proceeds/qty,"triggered":trigger is not None,"triggered_at_utc":dt.datetime.fromtimestamp(trigger,dt.timezone.utc).isoformat() if trigger else None,
            "actual_net_fixed20_per_leg":baseline_net,"overlay_net_fixed20_per_leg":overlay_net,"delta_inr":overlay_net-baseline_net,
            "actual_stress_net":stress_actual,"overlay_stress_net":stress_overlay,"stress_delta_inr":stress_overlay-stress_actual,
            "actual_return_pct":100*(baseline_proceeds/(qty*entry)-1),"overlay_return_pct":100*(overlay_proceeds/(qty*entry)-1),"actual_sell_count":len(sells),"overlay_sell_count":overlay_sells,
            "remaining_qty_on_trigger":remaining if trigger else 0,"fill_bar_volume":candles[exit_t][5] if trigger else None,"recorded_fee_baseline_net":baseline_recorded_net,"recorded_fee_overlay_net":overlay_recorded_net})

# API snapshots are captured after DB transactions; independent snapshots, not atomic to each other.
for name,endpoint in (("macd_trader","/api/portfolio"),("smart_50l","/api/lanes/smart_50l/snapshot"),("smart_1l","/api/lanes/smart_1l/snapshot"),("blast_trader","/api/blast/snapshot"),("mp_trader","/api/mp/snapshot")):
    x=e["api"].get(endpoint,{})
    if "error" in x:
        summary["open_book"][name]={"status":"unavailable","error":x["error"],"positions":None,"MFE_available_positions":None,"reported_unrealized_pnl":None}
        continue
    portfolio=x.get("portfolio",x)
    positions=portfolio.get("positions",[])
    if isinstance(positions,dict):positions=list(positions.values())
    known=[p for p in positions if isinstance(p,dict) and p.get("max_price") is not None and p.get("last_price") is not None and p.get("quantity") is not None]
    summary["open_book"][name]={"positions":len(positions),"MFE_available_positions":len(known),"reported_unrealized_pnl":portfolio.get("unrealized_pnl"),
        "sum_current_quantity_independent_peak_to_mark_gap_inr":money(sum(max(0,p["max_price"]-p["last_price"])*p["quantity"] for p in known)),
        "top_individual_gaps":[{"symbol":p["symbol"],"quantity":p["quantity"],"peak":p["max_price"],"mark":p["last_price"],"max_return_pct":p.get("max_return_pct"),"mark_return_pct":p.get("return_pct"),"gap_inr":money(max(0,p["max_price"]-p["last_price"])*p["quantity"])} for p in sorted(known,key=lambda x:(x["max_price"]-x["last_price"])*x["quantity"],reverse=True)[:5]]}

def agg(rs):
    return {"positions":len(rs),"triggered":sum(r["triggered"] for r in rs),"baseline_profitable_positions":sum(r["actual_net_fixed20_per_leg"]>0 for r in rs),"baseline_net_fixed20_per_leg":money(sum(r["actual_net_fixed20_per_leg"] for r in rs)),
        "overlay_net_fixed20_per_leg":money(sum(r["overlay_net_fixed20_per_leg"] for r in rs)),"delta_inr":money(sum(r["delta_inr"] for r in rs)),
        "improved":sum(r["delta_inr"]>0 for r in rs),"worsened":sum(r["delta_inr"]<0 for r in rs),"unchanged":sum(r["delta_inr"]==0 for r in rs),
        "largest_forfeited_tail_inr":money(min((r["delta_inr"] for r in rs),default=0)),"sum_forfeited_tail_inr":money(sum(min(0,r["delta_inr"]) for r in rs)),
        "stress_delta_inr":money(sum(r["stress_delta_inr"] for r in rs)),"median_delta_inr":money(statistics.median([r["delta_inr"] for r in rs])) if rs else None}
bad_fill_keys={(r["lane"],r["position_id"]) for r in comparisons if r["triggered"] and r["fill_bar_volume"]<=0}
primary=[r for r in comparisons if (r["lane"],r["position_id"]) not in bad_fill_keys]
summary["zero_volume_fill_sensitivity"]={"positions_excluded_from_both_arms":len(bad_fill_keys),"exclusion_is_data_quality_filter_not_live_rule":True,
    "events":[{k:r[k] for k in ("cohort","lane","position_id","symbol","arm_pct","delta_inr","fill_bar_volume")} for r in comparisons if r["triggered"] and r["fill_bar_volume"]<=0]}
for target,table in (("overlays",primary),("overlays_optimistic_zero_volume_allowed",comparisons)):
    for arm in e["policy_predeclaration"]["arms_pct"]:
        rs=[r for r in table if r["arm_pct"]==arm]
        summary[target][str(arm)]={"all":agg(rs),"by_cohort":{c:agg([r for r in rs if r["cohort"]==c]) for c in ("single_full_close","actual_partial_sells")},"by_lane":{n:agg([r for r in rs if r["lane"]==n]) for n in e["databases"]},
            "by_exit_day_ist":{d:agg([r for r in rs if day(r["actual_exit_time"])==d]) for d in sorted({day(r["actual_exit_time"]) for r in rs})}}
summary["coverage"]={"single_buy_completed_candidates":len(coverage),"single_buy_single_full_close_candidates":sum(c["cohort"]=="single_full_close" for c in coverage),"single_buy_actual_partial_candidates":sum(c["cohort"]=="actual_partial_sells" for c in coverage),"complete_path_candidates":sum(c["eligible"] for c in coverage),
    "missing_path_candidates":sum(c["missing_minutes"]>0 for c in coverage),"invalid_path_candidates":sum(c["invalid_minutes"]>0 for c in coverage),
    "subminute_or_no_eligible_session_minutes":sum(c["expected_minutes"]==0 for c in coverage),"primary_shared_cohort_positions":len(primary)//2,
    "by_lane":{n:{"candidates":sum(c["lane"]==n for c in coverage),"complete":sum(c["lane"]==n and c["eligible"] for c in coverage)} for n in e["databases"]}}
summary["limitations"]=["Retained fills have no historical feed/source mode tag. Current Fyers settings do not prove every historical fill was broker-fed; no independently certified real/simulation cohort.",
    "Mixed-policy paper history, Sep29-Oct9; retrospective exploratory work, no untouched holdout, no full portfolio replay or equal-capital reinvestment.",
    "Recorded MFE reflects sampled marks and changes in average basis after adds; independent per-slice or per-open-position highs cannot be added into a simultaneous portfolio high-water value.",
    "Overlay cohort excludes staged entries, scaled positions, incomplete minute paths and administrative expiry exits. Partial-aware cohort retains actual prior sells and closes remaining whole lots only; no interpolation or peak-price fills.",
    "Completed-minute closes ignore intraminute extreme spikes. Primary paired cohort excludes positions with a zero-volume hypothetical next-open fill under either arm; optimistic sensitivity is separate. Positive full-minute volume still does not prove execution at that minute open or size.",
    "Fixed INR20 per leg is a scenario. Baseline actual fill includes pre-existing production slippage; additional 20bps exit stress is applied equally. Statutory charges, impact and liquidity are not fully modeled.",
    "MP custom closed table retains only current visible closes; 12 rows cannot reconcile lifetime 96 fills. Smart1L has no closes. No evidence of deployable edge."]
HERE.joinpath("summary.json").write_text(json.dumps(summary,indent=2))
for filename,rows in (("coverage.csv",coverage),("comparison.csv",primary),("comparison_optimistic.csv",comparisons),("closed_slice_diagnostics.csv",sorted(examples,key=lambda r:r["peak_giveback_inr"],reverse=True))):
    if rows:
        with HERE.joinpath(filename).open("w",newline="") as f:
            w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
print(json.dumps({k:summary[k] for k in ("captured_at_utc","coverage","cohorts","open_book","overlays")},indent=2))
