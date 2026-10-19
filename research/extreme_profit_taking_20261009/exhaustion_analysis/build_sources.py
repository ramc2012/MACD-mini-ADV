"""Build a small reviewed-data receipt; no production reads or writes."""
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
OUT = Path('/Users/ramachandran/.codex/visualizations/2026/10/09/01a12164-c033-7fc3-ac5d-b5fa2f121649')
checked = json.loads((BASE / 'root_aggregate_check.json').read_text())
names = {'band_failure':'Premium extreme then rejection',
         'divergence_failure':'Confirmed momentum divergence then breakdown',
         'gain_giveback50':'Earlier one-minute gain-giveback reference'}
rows = [dict(policy=names[k], positions=v['n'], actual_net_inr=round(v['actual_net'],2),
             alternative_net_inr=round(v['alternative_net'],2), difference_inr=round(v['delta'],2),
             improved=v['better'], worsened=v['worse']) for k,v in checked.items()]
lanes = [dict(policy=names[k], lane=lane, difference_inr=round(value,2))
         for k,v in checked.items() for lane,value in v['by_lane'].items()]
receipt = {'schemaVersion':1, 'items':[
    {'id':'premium-exhaustion', 'title':'Frozen exact-option exhaustion comparison', 'queries':[
        {'id':'same-position-results', 'source':{
            'label':'Frozen exact-contract minute OHLCV and actual paper entries/sales',
            'files':[{'label':x} for x in ('exhaustion_analysis/PLAN.md', 'exhaustion_analysis/inputs.json.gz',
                'exhaustion_analysis/detector.py', 'exhaustion_analysis/evaluate.py',
                'exhaustion_analysis/comparison.csv', 'exhaustion_analysis/root_aggregate_check.json',
                'exhaustion_review/detector_accounting_audit.json')],
            'metricDefinitions':[
                {'name':'Paired population','definition':'111 identical completed single-BUY lifecycles, including actual partial-sale cash. Require 100 complete pre-entry 3-minute bars and complete holding-minute paths; exclude seven positions with zero-volume modeled fill minutes under any compared policy.'},
                {'name':'Band rejection','definition':'Prior-20 premium z>=2 and RSI14>=70 arms; a later close back below the band and below the preceding low triggers within the frozen session/10-bar state rules.'},
                {'name':'Divergence','definition':'Confirmed higher OHLC high with weaker closing RSI or MACD histogram, then intervening-trough break. A pivot is available only after two right-hand bars close.'},
                {'name':'Net P&L','definition':'Actual entry cost and earlier partial proceeds retained; residual whole lots exit at next eligible minute open with 5 bps adverse slippage. INR20 per entry/sale order on both alternatives. Actual fills are not reslipped.'}],
            'caveats':[
                'Every pooled alternative remains net negative; differences are not portfolio returns or calibrated maximum-move probabilities.',
                'Band rejection helps the retained MACD subset and harms Blast; divergence shows the opposite signs. No universal exit advantage is established.',
                'The rejection rule sacrifices INR303,747 across thirteen actual winners. Existing partial-sale lifecycles collectively worsen under both technical rules.',
                'Missing paths, short warm-up, open/addition lifecycles and administrative closes are excluded. No untouched holdout or capital/re-entry simulation.',
                'The common positive fill-minute-volume filter uses retrospective information. Positive minute volume does not prove an opening bid/depth, and historical candles may have been downloaded later.',
                'Full remaining-quantity exits are tested. A half-position harvest plus runner and sentiment-gated exits remain untested.'],
            'evidenceFlow':[
                {'kind':'file','title':'Frozen source','detail':'220 original lifecycles; 169 exact symbols; 309,609 contextual minute rows. Overlapping original holding rows were unchanged.'},
                {'kind':'calculation','title':'Completed-bar features','detail':'Missing expected 3-minute bars reset state. Lagged bands and past-only indicators; pivots publish on confirmation bars; next-minute fills preserve prior partial cash.'},
                {'kind':'validation','title':'Independent check','detail':'Zero errors in 104,244 assertions: 220 lifecycle/warm-up checks, 354 cash rows, 1,563 independently reconciled feature bars and 234 policy-prefix cases. Root separately aggregated paired CSV rows.'}]},
         'rows':rows, 'columns':['policy','positions','actual_net_inr','alternative_net_inr','difference_inr','improved','worsened'],
         'methods':[{'language':'python','code':"rows = list(csv.DictReader(open('comparison.csv')))\nfor policy in ('band_failure','divergence_failure','gain_giveback50'):\n    selected = [r for r in rows if r['policy'] == policy]\n    actual = math.fsum(float(r['actual_net']) for r in selected)\n    alternative = math.fsum(float(r['alternative_net']) for r in selected)\n    difference = math.fsum(float(r['delta']) for r in selected)"}]},
        {'id':'lane-differences', 'source':{'label':'Same paired CSV aggregated by lane',
            'files':[{'label':'exhaustion_analysis/comparison.csv'}],
            'caveats':['Subgroup results have been inspected and cannot become a new independent validation set.']},
         'rows':lanes,'columns':['policy','lane','difference_inr']}]},
    {'id':'sentiment-coverage','title':'Actual sentiment and volatility availability', 'queries':[
        {'id':'capability-audit','source':{
            'label':'Bounded read-only audit of active application schemas, source and stored rows',
            'files':[{'label':x} for x in ('exhaustion_review/AVAILABILITY.md','exhaustion_review/collect_availability.py',
                'exhaustion_review/availability_evidence.json','exhaustion_review/PROSPECTIVE.md')],
            'caveats':[
                'NIFTY/BANKNIFTY market context is not stock-specific option-chain sentiment.',
                'Collector-start timestamps precede requests/computation; no reliable available_at exists. No causal sentiment improvement is credited.',
                'PCR/OI and model IV are not independent, directional top probabilities. Missing/stale/fallback fields remain unknown.'],
            'evidenceFlow':[{'kind':'file','title':'Scope and timing','detail':'Only nine of 220 original lifecycles have direct chain-root coverage; all 3,471 bounded composites are null. Provider/arrival timestamps are needed before prospective sentiment gating.'}]},
         'rows':[
             {'feature':'Direct NIFTY/BANKNIFTY chain context','observed':'9 of 220 lifecycles; 211 lack their own chain history'},
             {'feature':'Numeric calibrated whale composite','observed':'0 of 3,471 bounded rows; warm-up insufficient'},
             {'feature':'IV/OI/PCR histories','observed':'Sampled index fields exist; usable availability time unverified'},
             {'feature':'News sentiment and persisted IV skew','observed':'Not found in inspected active source/schemas'}],
         'columns':['feature','observed']}]}
]}
(OUT/'exhaustion-sources.json').write_text(json.dumps(receipt,indent=2)+'\n')
print('Saved two-card exhaustion source receipt.')
