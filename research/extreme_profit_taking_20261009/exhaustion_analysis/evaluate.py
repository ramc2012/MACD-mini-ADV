"""Evaluate only the alternatives frozen in PLAN.md on identical paper entries.

Read-only offline runner. No production imports, data downloads or thresholds
selected from outcomes. detector.py is authored separately from this accounting.
"""
from __future__ import annotations

import csv
import datetime as dt
import gzip
import hashlib
import importlib.util
import json
import math
import pathlib
from zoneinfo import ZoneInfo

HERE=pathlib.Path(__file__).resolve().parent
IST=ZoneInfo("Asia/Kolkata")
HOLIDAYS={dt.date(2026,9,14),dt.date(2026,10,2)}
POLICIES=("band_failure","divergence_failure","gain_giveback50")
def stamp(x):return dt.datetime.fromisoformat(x).timestamp()
def iso(x):return dt.datetime.fromtimestamp(x,dt.timezone.utc).isoformat()
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def money(x):return round(x,2)
def valid(r):return min(r[1:5])>0 and r[2]>=max(r[1],r[4]) and r[3]<=min(r[1],r[4]) and r[5]>=0
def grid(start,end):
    result=[]
    for t in range(math.ceil(start/60)*60,math.floor(end/60)*60+1,60):
        x=dt.datetime.fromtimestamp(t,IST)
        if x.weekday()<5 and x.date() not in HOLIDAYS and (9,15)<=(x.hour,x.minute)<(15,40):result.append(t)
    return result

def reference_1m(raw,entry_ts,end,entry_price,initial_qty,sells):
    """The original named reference, unchanged: completed1m close arm/trail.

    No new RSI, divergence, positive signal-volume or residual-profit gate is
    grafted onto this reference. Its original rules remain inspectable.
    """
    expected=grid(entry_ts,end);peak=entry_price;armed=False
    for i,t in enumerate(expected[:-1]):
        if t+60>end:break
        close=raw[t][4];peak=max(peak,close)
        if peak>=entry_price*1.5:armed=True
        if armed and close<=entry_price+.75*(peak-entry_price):
            fill=expected[i+1]
            prior=[s for s in sells if stamp(s["timestamp"])<=fill]
            residual=initial_qty-sum(s["quantity"] for s in prior)
            if residual<=0:return None
            return {"decision_ts":t+60,"fill_ts":fill,"policy":"gain_giveback50", "reasons":["completed_minute_close_arm_50pct","25pct_favorable_profit_giveback"],
                "features":{"close":close,"peak_completed_close":peak,"profit_giveback_threshold":entry_price+.75*(peak-entry_price),"signal_volume":raw[t][5]},"signal_start":t}
    return None

def account(position,raw,event):
    buy=position["single_entry"];sells=position["actual_sells"]
    entry,qty=buy["price"],buy["quantity"]
    actual_proceeds=sum(s["quantity"]*s["price"] for s in sells)
    actual_fixed_net=actual_proceeds-entry*qty-20*(1+len(sells))
    actual_recorded_net=actual_proceeds-entry*qty-buy["fees"]-sum(s["fees"] for s in sells)
    if event is None:
        return {"triggered":False,"decision_ts":None,"fill_ts":None,"new_exit_price":None,"remaining_quantity":0,"prior_actual_sell_count":len(sells),"alternative_sell_count":len(sells),
            "fill_bar_volume":None,"actual_net":actual_fixed_net,"alternative_net":actual_fixed_net,"delta":0.,
            "actual_stress_net":actual_proceeds*(1-.002)-entry*qty-20*(1+len(sells)),"alternative_stress_net":actual_proceeds*(1-.002)-entry*qty-20*(1+len(sells)),"stress_delta":0.,
            "actual_recorded_fee_net":actual_recorded_net,"alternative_recorded_fee_net":actual_recorded_net,"prior_actual_proceeds":actual_proceeds,"remaining_actual_future_proceeds":0.,
            "hypothetical_residual_proceeds":0.,"foregone_later_completed_minute_high_pct":None}
    decision,fill=event["decision_ts"],event["fill_ts"]
    if not position["entry_ts"]<=decision<=fill<=position["actual_final_exit_ts"]:raise ValueError("Noncausal or after-horizon event")
    if fill%60 or fill not in raw or fill not in grid(fill,fill):raise ValueError("Fill is not an available eligible minute open")
    eligible_after_decision=grid(decision,position["actual_final_exit_ts"])
    if not eligible_after_decision or fill!=eligible_after_decision[0]:raise ValueError("Fill skipped an earlier eligible minute open")
    prior=[s for s in sells if stamp(s["timestamp"])<=fill]
    later=[s for s in sells if stamp(s["timestamp"])>fill]
    residual=qty-sum(s["quantity"] for s in prior)
    if residual<=0:return account(position,raw,None)
    if residual%buy["lot_size"]:raise ValueError("Fractional residual lot")
    if sum(s["quantity"] for s in later)!=residual:raise ValueError("Future sales do not reconcile to residual quantity")
    exit_price=raw[fill][1]*(1-.0005)
    prior_proceeds=sum(s["quantity"]*s["price"] for s in prior)
    future_proceeds=sum(s["quantity"]*s["price"] for s in later)
    alt_proceeds=prior_proceeds+residual*exit_price
    alt_orders=1+len(prior)
    alt_net=alt_proceeds-entry*qty-20*(1+alt_orders)
    actual_stress=actual_proceeds*(1-.002)-entry*qty-20*(1+len(sells))
    alt_stress=alt_proceeds*(1-.002)-entry*qty-20*(1+alt_orders)
    # Uses only completed minutes by the original actual final exit. This is
    # explicitly a hindsight diagnostic, never passed to a detector.
    later_highs=[r[2] for t,r in raw.items() if t>=fill and t+60<=position["actual_final_exit_ts"] and t in grid(t,t)]
    return {"triggered":True,"decision_ts":decision,"fill_ts":fill,"new_exit_price":exit_price,"remaining_quantity":residual,"prior_actual_sell_count":len(prior),"alternative_sell_count":alt_orders,
        "fill_bar_volume":raw[fill][5],"actual_net":actual_fixed_net,"alternative_net":alt_net,"delta":alt_net-actual_fixed_net,
        "actual_stress_net":actual_stress,"alternative_stress_net":alt_stress,"stress_delta":alt_stress-actual_stress,
        "actual_recorded_fee_net":actual_recorded_net,"alternative_recorded_fee_net":alt_proceeds-entry*qty-buy["fees"]-sum(s["fees"] for s in prior)-20,
        "prior_actual_proceeds":prior_proceeds,"remaining_actual_future_proceeds":future_proceeds,"hypothetical_residual_proceeds":residual*exit_price,
        "foregone_later_completed_minute_high_pct":100*(max(later_highs)-exit_price)/max(later_highs) if later_highs else None}

def aggregate(rows):
    return {"positions":len(rows),"triggered":sum(r["triggered"] for r in rows),"baseline_profitable_positions":sum(r["actual_net"]>0 for r in rows),
        "baseline_net":money(sum(r["actual_net"] for r in rows)),"alternative_net":money(sum(r["alternative_net"] for r in rows)),"delta":money(sum(r["delta"] for r in rows)),
        "improved":sum(r["delta"]>1e-8 for r in rows),"worsened":sum(r["delta"] < -1e-8 for r in rows),"unchanged":sum(abs(r["delta"])<=1e-8 for r in rows),
        "baseline_stress_net":money(sum(r["actual_stress_net"] for r in rows)),"alternative_stress_net":money(sum(r["alternative_stress_net"] for r in rows)),"stress_delta":money(sum(r["stress_delta"] for r in rows)),
        "sum_negative_deltas":money(sum(min(0,r["delta"]) for r in rows)),
        "sum_negative_deltas_on_actual_winners":money(sum(min(0,r["delta"]) for r in rows if r["actual_net"]>0)),"worst_difference":money(min((r["delta"] for r in rows),default=0)),
        "actual_recorded_fee_net":money(sum(r["actual_recorded_fee_net"] for r in rows)),"alternative_recorded_fee_net":money(sum(r["alternative_recorded_fee_net"] for r in rows))}

def main():
    inputs_path=HERE/"inputs.json.gz"
    with gzip.open(inputs_path,"rt") as f:inputs=json.load(f)
    plan=HERE/"PLAN.md";detector_path=HERE/"detector.py"
    spec=importlib.util.spec_from_file_location("exhaustion_detector",detector_path)
    detector=importlib.util.module_from_spec(spec);spec.loader.exec_module(detector)
    exclusions=[];results=[];events={};warmup_checks=[]
    for key,p in inputs["positions"].items():
        rows=inputs["histories"][p["symbol"]]["rows"]
        bars=detector.enrich(detector.build_bars(rows,start=p["context_start"],end=max(int(r[0]) for r in rows)+60))
        pre=[b for b in bars if b["end"]<=p["entry_ts"]]
        last=pre[-1] if pre else None
        warmup=bool(last and last.get("valid") and last.get("count",0)>=100)
        original_raw={int(r[0]):r for r in p["original_holding_rows"]}
        expected=grid(p["entry_ts"],p["actual_final_exit_ts"])
        missing=[t for t in expected if t not in original_raw]
        invalid=[t for t in expected if t in original_raw and not valid(original_raw[t])]
        revisions=p["original_holding_revision_check"]
        reasons=[]
        if not warmup:reasons.append("fewer_than_100_consecutive_complete_3m_bars_immediately_before_entry")
        if not expected:reasons.append("subminute_or_no_eligible_holding_minutes")
        if missing:reasons.append("original_holding_missing_minutes")
        if invalid:reasons.append("original_holding_invalid_minutes")
        if revisions["changed_existing_rows"] or revisions["added_rows"] or revisions["removed_rows"]:reasons.append("historical_holding_rows_revised_since_original_snapshot")
        warmup_checks.append({"position_key":key,"detector_warmup_valid":warmup,"detector_last_preentry_count":last.get("count") if last else None,
            "collector_warmup_valid":p["coverage"]["preentry_last_100_grid_bins_complete"],"last_preentry_bar_end":iso(last["end"]) if last else None})
        if reasons:
            exclusions.append({"position_key":key,"lane":p["lane"],"symbol":p["symbol"],"reasons":";".join(reasons),"missing_holding_minutes":len(missing),"invalid_holding_minutes":len(invalid),"last_preentry_count":last.get("count") if last else None})
            continue
        # Overlapping holding rows were checked identical; original rows are
        # authoritative for fills/reference while new context supplies history.
        for policy in POLICIES:
            if policy=="gain_giveback50":event=reference_1m(original_raw,p["entry_ts"],p["actual_final_exit_ts"],p["single_entry"]["price"],p["single_entry"]["quantity"],p["actual_sells"])
            else:event=detector.first_exit(bars,p["entry_ts"],p["actual_final_exit_ts"],p["single_entry"]["price"],p["single_entry"]["quantity"],p["actual_sells"],policy)
            row={"position_key":key,"lane":p["lane"],"cohort":p["cohort"],"symbol":p["symbol"],"policy":policy,"entry_time":p["single_entry"]["timestamp"],
                "actual_final_exit_time":iso(p["actual_final_exit_ts"]),"initial_quantity":p["single_entry"]["quantity"],"lot_size":p["single_entry"]["lot_size"],"actual_sell_count":len(p["actual_sells"]),**account(p,original_raw,event)}
            if event is not None:events[key+"::"+policy]=event
            results.append(row)
    bad={r["position_key"] for r in results if r["triggered"] and r["fill_bar_volume"]<=0}
    primary=[r for r in results if r["position_key"] not in bad]
    lanes=sorted({p["lane"] for p in inputs["positions"].values()})
    summary={"input_positions":len(inputs["positions"]),"quality_eligible_positions_before_zero_volume_filter":len(results)//3,"primary_shared_positions":len(primary)//3,
        "quality_excluded_positions":len(exclusions),"warmup_gate_disagreements":sum(x["detector_warmup_valid"]!=x["collector_warmup_valid"] for x in warmup_checks),
        "zero_volume_positions_removed_from_every_policy":len(bad),"zero_volume_events":[r for r in results if r["triggered"] and r["fill_bar_volume"]<=0],
        "quality_exclusion_reason_counts":{reason:sum(reason in x["reasons"].split(";") for x in exclusions) for reason in sorted({reason for x in exclusions for reason in x["reasons"].split(";")})},
        "primary":{},"unfiltered_zero_volume_sensitivity":{},"hashes":{"PLAN.md":sha(plan),"detector.py":sha(detector_path),"evaluate.py":sha(pathlib.Path(__file__)),"inputs.json.gz":sha(inputs_path),"original_evidence":inputs["original_evidence_sha256"]},
        "accounting":"Identical actual single-BUY entries and quantities; preserve actual prior SELL cash at/beforealternativefill; full residualwholelots; INR20perorderlegbothchoices; newexit5bpsonce; originalactualfillsnotreslipped; additional20bpsexitstressequalboth.",
        "reference_scope":"gain_giveback50 reproduces original1minute rule, without new RSI/signalvolume/residualprofit gates. Band/divergence use frozenPLANpositivevolume/profit gates.",
        "limitations":["Retrospective mixed-policy completed single-BUY sample; additions and open winners excluded; no untouched holdout, no calibrated maximum probability or full strategy/portfolio replay.",
            "Common positive fill-minute volume filter is retrospective data-quality selection. Full-minute volume is unknown at its opening print and positive volume does not establish opening liquidity/spread/depth.",
            "Downloaded historical OHLCV may not have been available to the live feed at that time; source-mode provenance is absent. Missing sentiment/flow is unavailable rather than neutral.",
            "Foregone later high is descriptive hindsight using only fullycompletedminutesbyactualfinalexit; no detector consumes later highs. Costs omit complete taxes/spread/impact."]}
    for target,rs in (("primary",primary),("unfiltered_zero_volume_sensitivity",results)):
        for policy in POLICIES:
            selected=[r for r in rs if r["policy"]==policy]
            summary[target][policy]={"all":aggregate(selected),"by_lane":{lane:aggregate([r for r in selected if r["lane"]==lane]) for lane in lanes},
                "by_cohort":{kind:aggregate([r for r in selected if r["cohort"]==kind]) for kind in ("single_full_close","actual_partial_sells")}}
    HERE.joinpath("evaluation_summary.json").write_text(json.dumps(summary,indent=2,allow_nan=False))
    HERE.joinpath("trigger_events.json").write_text(json.dumps(events,indent=2,allow_nan=False))
    for name,rs in (("comparison.csv",primary),("comparison_optimistic.csv",results),("quality_exclusions.csv",exclusions),("warmup_checks.csv",warmup_checks)):
        if rs:
            with HERE.joinpath(name).open("w",newline="") as f:
                w=csv.DictWriter(f,fieldnames=list(rs[0]));w.writeheader();w.writerows(rs)
    print(json.dumps({k:summary[k] for k in ("input_positions","quality_eligible_positions_before_zero_volume_filter","primary_shared_positions","quality_excluded_positions","warmup_gate_disagreements","zero_volume_positions_removed_from_every_policy","primary")},indent=2))

if __name__=="__main__":main()
