import hashlib,json,datetime as dt
from pathlib import Path
ROOT=Path(__file__).resolve().parent
STUDY=ROOT.parent
a=json.loads((ROOT/'statistical_audit.json').read_text())
p=json.loads((ROOT/'independent_source_checks.json').read_text())
revision=hashlib.sha256((STUDY/'FINDINGS.md').read_bytes()).hexdigest()
definitions=[
('usefulness','Usefulness and completeness','The cross-forecast and simplified entry questions are answered. Full production/Smart transfer is explicitly untested.'),
('clarity','Analytical clarity','Actual test-period opening equity needs prominence; the fee-scenario floor is not net of fixed fees.'),
('visual','Visual and interaction consistency','Three saved figures inspected; labels, signs and visible marks agree. Receipt uses the previously checked renderer.'),
('authority','Source authority and confidence','Archive/catalog provenance retained. Historical point-in-time chain and tick/spread evidence is unavailable.'),
('accuracy','Value accuracy','Headline rates, prediction metrics, seventeen corrected tests and main book quality recomputed. Fee-floor definition has a minor mismatch.'),
('chart','Within-chart agreement','All three figure comparisons match their saved tables; hindsight conditioning is visible.'),
('sources','Complete source details','Four existing source-card payloads retain denominators and limitations. Shared interaction path was previously checked.'),
('consistency','Cross-artifact consistency','Report, notebook outputs, figures and aggregate tables agree on the scoped comparisons.'),
('quality','Data-quality controls','No future-price use found in 74 sampled MACD/zero-price prefixes. Full source-row verification and historical universe completeness are unavailable.'),
('conclusion','Conclusion support','Retaining cross+5 is supported. No cleared incremental trading edge or valid Smart promotion is claimed.')]
categories=[{'id':i,'label':label,'applicable':True,'assessment':s} for i,label,s in definitions]
specs=[
('path','Cross-aligned descriptive path',['usefulness','clarity','visual','authority','accuracy','chart','consistency','conclusion']),
('forecast','All-trigger cross forecast',['usefulness','clarity','visual','authority','accuracy','chart','sources','consistency','quality','conclusion']),
('model','Cross classifier and corrected tests',['usefulness','clarity','authority','accuracy','sources','consistency','quality','conclusion']),
('narrow','High-probability exploratory group',['usefulness','clarity','authority','accuracy','sources','consistency','quality','conclusion']),
('book50','50-lakh continuous book comparison',['usefulness','clarity','visual','authority','accuracy','chart','sources','consistency','quality','conclusion']),
('book1','1-lakh continuous book comparison',['usefulness','clarity','authority','accuracy','consistency','quality','conclusion']),
('costs','Cost scenarios',['usefulness','clarity','authority','accuracy','consistency','quality','conclusion']),
('timeout','Timeout scenarios',['usefulness','clarity','authority','accuracy','consistency','conclusion']),
('source','Source precedence sensitivity',['usefulness','clarity','authority','accuracy','consistency','quality','conclusion']),
('method','Selection and execution definitions',['usefulness','clarity','authority','accuracy','consistency','quality','conclusion']),
('application','Production and Smart applicability',['usefulness','clarity','authority','quality','conclusion'])]
units=[]
for id,label,cats in specs:
    mappings={}
    for cat in cats:
        state='passed';finding=[]
        evidence=['statistical_audit.json','independent_source_checks.json','REVIEW.md']
        if (id in ['book50','book1'] and cat=='clarity') or (id=='method' and cat in ['clarity','accuracy']):
            state='failed';finding=[{'id':'funding-presentation' if id.startswith('book') else 'fee-floor-definition','status':'open','checkIds':['review']}]
        if id in ['method','application'] and cat in ['authority','quality']:
            state='unverified';evidence=[]
        if id in ['costs','timeout','source'] and cat=='accuracy':
            state='unverified';evidence=[]
        mappings[cat]={'checks':[{'id':'review','state':state,'revision':revision,'evidence':evidence}],'findings':finding}
    units.append({'id':id,'label':label,'kind':'question' if id=='application' else 'component','revision':revision,'categories':mappings})
record={'version':1,'artifact':{'identity':'Premium MACD pre-cross study','revision':revision,'view':'Current study report, saved comparisons, three figures and four Sources cards; prior studies inspected for scope and reuse only'},'categories':categories,'units':units}
(ROOT/'coverage.json').write_text(json.dumps(record,indent=2))
capital=[]
for key,z in a['portfolio'].items():
    cap,rule,_,_=key.split('|')
    capital.append({'initial_september_capital':int(cap),'policy':rule,'test_opening_equity':z['opening_mark']['equity'],'test_opening_cash':z['opening_mark']['cash'],'test_open_positions':z['opening_mark']['open'],'test_pnl':z['holdout_marked_pnl'],'test_return_pct':z['holdout_return_pct'],'new_trades':z['new_trades'],'closed_pf':z['closed_pf']})
(ROOT/'capital_comparison.json').write_text(json.dumps(capital,indent=2))
note='''# Review of premium MACD, Supertrend and pivot research

3 October 2026. **Share with caveats. Coverage: Partial**, because source-row reconstruction was sampled and historical decision-time chains, quotes/spreads and production-policy transfer cannot be established from these inputs. No P0/P1 computational error was found in the current study. No study, strategy or book was changed; the items below are proposed P2 refinements. This review covers the latest pre-cross study deeply. Earlier September and Smart reports were inspected for context and compatibility, not re-audited end to end.

## Prioritized problems and proposed fixes

1. **P2 — The test-period cash comparison inherits different development results.** In FINDINGS.md, the ₹50 L and ₹1 L headings identify September 1 initial funding, not equal capital on September 15. For ₹50 L, cross+5 enters the test with ₹38,71,934.64 equity; ST+P with ₹38,84,842.58. Both carry the same IREDA position. The ₹7,745 additional ST+P profit is arithmetically correct, but percentage returns are 76.3486% for cross+5 and 76.2943% for ST+P. The ₹1 L paths likewise start the test with different equity (₹1,26,894.48 versus ₹1,24,831.98). The original report discloses continuity later, so this is a presentation/comparability weakness, not hidden cash creation. **Fix (Proposed):** put opening equity, cash, inventory and period return beside rupee P&L; use identical starting states for a clean new-policy experiment. Normalising returns is informative but does not recreate an equal-funded path because sizing and eligibility depend on cash.

2. **P2 — “Round-trip breakeven floor” excludes fixed order fees.** FINDINGS.md describes the trailing floor this way, but analyze.py:143 computes entry/(1−slippage). It covers the price/slippage component; the ₹20 entry and ₹20 exit fees in stress scenarios are still deducted. There are 64 negative-net trailing exits across the saved fee-scenario books whose gross pre-fee return was nonnegative; these are repeated scenario observations, not 64 independent trading opportunities, and their exit prices may also reflect gaps. Base-cost headline comparisons have no fixed fee and are unaffected by this wording issue. **Fix (Proposed):** call it a slippage-adjusted price floor excluding fixed fees, or define a net-cost threshold as (total entry debit + closing fee)/(quantity × (1−slippage)) and rerun affected scenarios. A threshold still cannot guarantee a breakeven fill after price gaps.

3. **Research limitation — Temporal exclusion is not an untouched research holdout.** The new classifier's fitting/preprocessing uses September 1–11 only, which the audit reproduces. However, earlier reports in this same research series already inspected September 15–30. Selection of this follow-up question therefore occurred with knowledge of that month's behaviour. The seventeen-test Holm family covers the frozen current comparisons, not every earlier analysis, timeout inspection or probability-band exploration. **Fix (Proposed):** describe these as exploratory time-split results and freeze rules/model before observing new sessions. This is not evidence that held-out labels were used in the classifier fit.

## What was verified

- Independent row-based arithmetic reproduces 341/1,371 base crosses, 197/494 completed-ST crosses, 271/705 ST+P crosses and 42/324 pivot-reclaim crosses within thirty minutes; 679 early fills and 396 ST+P timeouts also match.
- Independent prediction calculations reproduce AUC 0.8501039 → 0.8678444 and Brier 0.1377696 → 0.1260413. The twelve-day Brier test has raw p=0.0427521 and Holm p=0.7267851. The claimed corrected significance failure is correct.
- The >0.8 band has 26/26 crosses across nine days, 16 early fills and fifteen paired fills. The top three paired differences exceed the whole-group sum, so the remaining observations contribute −44.36 percentage points in total. This remains exploratory; it is not an evaluated portfolio gate.
- Independent cash-book episode/mark calculations reproduce 917 versus 1,312 positions, PF 1.4023 versus 1.3034 and win rates 42.09% versus 41.46% for the main ₹50 L comparison. Saved fill reconciliation evidence remains tied to the current hashes.
- From original old/current minute exports, an independent candle-prefix calculation verifies entry-observation timing, premium price, MACD and exact zero premium for 74 events across 65 contracts, including every >0.8 prediction plus a random sample. Maximum zero-price discrepancy is below 1e−12. This is a sample, not exhaustive proof of every feature. Historical indicator candles follow the app's instrument-specific session calendar; the experiment's execution/label cutoff at 15:30 is a separate policy choice.
- All three figures were visually inspected. Cross-conditioned descriptions are labelled as hindsight, cross-rate bars include failures and start at zero, and portfolio difference signs/counts match tables. Existing receipt payloads preserve the main qualifications. Prior rendered receipt inspection is reused for the unchanged shared interaction path.

## Decision

The caution in the original conclusion is justified. ST/pivot states are associated with more subsequent crosses in this sample, but there is no validated incremental net-profit edge. “A useful exploratory cross forecast” is a better description than “an established early-entry advantage.” The 26/26 group is too small, concentrated and selectively executable to justify deployment.

Keep cross+5 as the comparison baseline. The next decisive test would use new data, a frozen model/rule, identical starting cash/inventory, complete costs and the actual strategy/Smart execution policy. No current study result establishes the effect of early entry on the production Smart lane.

## Scope and evidence

The saved report, frozen plan, replay/feature code, model outputs, event records, book marks/episodes, source metadata, prior study conclusions and all three figures were inspected. Exact price/EMA source reconstruction was sampled; source-priority and timeout tables were checked against saved outputs rather than rerunning all raw-data replays. Historical universe completeness, tick-level execution, official exchange-session assumptions and a fresh prospective test remain unverified. The ten existing focused tests were rerun successfully but are supporting checks, not independent proof of edge.

Evidence: `statistical_audit.json`, `independent_prefix_checks.csv`, `independent_source_checks.json`, `fee_floor_examples.csv`, `capital_comparison.json`, `coverage.json`. Checks can be reproduced with `review_probe.py` and the saved inputs; raw evidence and study results were preserved. Scorecard counts below describe scoped components, not percentages of correctness, and categories overlap.
'''
(ROOT/'REVIEW.md').write_text(note)
at=dt.datetime.now(dt.UTC).isoformat()
def item(id,title,files,definitions,caveats,rows):
    return {'id':id,'title':title,'queries':[{'id':id+'-evidence','source':{'label':'Local study review evidence','files':[{'label':x} for x in files],'metricDefinitions':[{'label':k,'definition':v} for k,v in definitions.items()],'filters':['Study: premium MACD before zero, eight predefined rules.','Evaluation period: September 15–30, 2026.'],'caveats':caveats,'executedAt':at},'reportingPeriod':'September 2026 research, reviewed October 3','capturedAt':at,'rows':rows,'columns':list(rows[0]),'preview':{'kind':'aggregate','note':'Reviewed aggregate results; no individual account or contract records.'}}]}
items=[item('funding','The small nominal gain does not establish a better funded return',['capital_comparison.json','statistical_audit.json','books.json.gz'],{'Opening equity':'Last marked equity before September 15, inherited from each policy development path.','Period return':'September 15–30 equity gain divided by that path opening equity.'},['September 1 initial funding differs from test-period opening funding.','Different path returns are descriptive; dividing by capital is not a reset-capital replay.','Both principal policies carry the same position within each capital scenario.'],capital),item('verified','Core numerical conclusions reproduce',['statistical_audit.json','events.csv.gz','prediction_holdout.csv'],{'Trial':'One earliest qualifying rule event per contract/bar; failed forecasts retained.','AUC':'Ranking metric for the thirty-minute cross label.','Holm':'Correction over seventeen fixed comparisons; it excludes research-wide reuse of September.'},['The time split excludes test labels from this model fit but the month was previously examined.','High-probability bin is exploratory, not a tested entry policy.'],[{'comparison':'MACD geometry','n':1371,'auc':a['prediction']['macd_probability']['auc'],'brier':a['prediction']['macd_probability']['brier']},{'comparison':'MACD + ST/pivots','n':1371,'auc':a['prediction']['st_pivot_probability']['auc'],'brier':a['prediction']['st_pivot_probability']['brier']}]),item('scope','Review coverage and execution limitations',['independent_source_checks.json','independent_prefix_checks.csv','REVIEW.md','analyze.py'],{'Prefix sample':'Reconstructed archived prices, completed candle EMA state and zero premium using only observations available by each sampled event.','Trailing floor':'The current threshold covers slippage but excludes fixed order fees.'},['Seventy-four source events across sixty-five contracts were sampled; this is not exhaustive feature verification.','No Smart or full production strategy edge is established by the simplified intraday experiment.','Decision-time chain completeness, historical spreads/ticks and prospective performance remain unavailable.'],[{'sampled_events':p['sampled_events'],'sampled_contracts':p['sampled_contracts'],'sampled_clock_price_macd_checks_passed':p['all_sampled_prefix_macd_zero_and_prices_match'],'max_zero_price_error':p['maximum_zero_price_error'],'fixed_roundtrip_fee_in_cost_scenarios':40}])]
labels={'initial_september_capital':'Initial capital (₹)','policy':'Policy','test_opening_equity':'Opening equity (₹)','test_opening_cash':'Opening cash (₹)','test_open_positions':'Carried positions','test_pnl':'Period P&L (₹)','test_return_pct':'Period return (%)','new_trades':'New positions','closed_pf':'Closed PF'}
items[0]['queries'][0]['columns']=[{'field':key,'label':labels[key]} for key in capital[0]]
(ROOT/'reviewed_sources.json').write_text(json.dumps({'schemaVersion':1,'items':items},indent=2,allow_nan=False))
print('Review notes, coverage inventory and aggregate receipt saved.')
