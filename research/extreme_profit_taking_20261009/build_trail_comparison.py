"""Illustrative trigger arithmetic, not backtest performance."""
import datetime
import json
import pathlib

OUT = pathlib.Path('/Users/ramachandran/.codex/visualizations/2026/10/09/01a12164-c033-7fc3-ac5d-b5fa2f121649')
OUT.mkdir(parents=True, exist_ok=True)
rows=[]
for gain in [30,40,50,75,100,150,200]:
    rows.append({'peak_gain_pct':gain,'policy':'Current 25% price trail','stop_gain_pct':round(max(0,(1+gain/100)*0.75-1)*100,4)})
    rows.append({'peak_gain_pct':gain,'policy':'Candidate 25% profit giveback','stop_gain_pct':gain*0.75 if gain>=50 else None})
source={'label':'Illustrative trigger arithmetic based on reviewed MACD exit code',
        'files':[{'label':'execution.py — tick trailing and partial exits'},{'label':'POLICY.md — proposed safeguard'}],
        'metricDefinitions':[{'name':'Stop-trigger gain','definition':'Stop-trigger gain is the proposed trigger premium relative to a fixed entry premium of 100; it is not a guaranteed fill or a measured return.'}],
        'caveats':['Illustrative values; no trade outcomes are plotted. Costs and partial exits are omitted to isolate trail arithmetic.',
                   'The current rule has a slippage-adjusted breakeven floor; this example approximates it as zero gross gain.',
                   'The candidate arms at a 50% gain and is unvalidated. Tighter stops can forfeit subsequent winners.',
                   'Gaps, absent bids and slippage can produce fills below either trigger.'],
        'evidenceFlow':[{'kind':'calculation','title':'Compared trigger definitions','detail':'Current gross trigger = max(100, 0.75 × peak premium). Candidate gross trigger = 100 + 0.75 × (peak premium − 100), after a 50% gain.'}]}
now=datetime.datetime.now(datetime.timezone.utc).isoformat()
payload={'schemaVersion':1,'id':'extreme-profit-trails','queryId':'illustrative-trail-calculation',
         'title':'Price trail versus profit giveback',
         'description':'Illustrative stop thresholds before costs, with entry premium fixed at 100.',
         'chart':{'type':'line','x':'peak_gain_pct','y':'stop_gain_pct','series':'policy',
                  'xLabel':'Peak premium gain (%)','yLabel':'Stop-trigger gain (%)','showXAxisLabel':True},
         'rows':rows,'source':source,'theme':'codex-classic','generatedAt':now,'height':300}
(OUT/'trail-comparison.json').write_text(json.dumps(payload,indent=2)+'\n')
receipt={'schemaVersion':1,'items':[{'id':'trail-arithmetic','title':'What the two trailing definitions protect',
           'queries':[{'id':'illustrative-trail-calculation','source':source,'rows':rows,
           'columns':[{'field':'peak_gain_pct','label':'Peak premium gain (%)'},'policy',{'field':'stop_gain_pct','label':'Stop-trigger gain (%)'}],
           'capturedAt':now,'methods':[{'language':'calculation','code':'Current gross trigger = max(100, 0.75 * peak).\nCandidate gross trigger = 100 + 0.75 * (peak - 100), armed when peak >= 150.'}]}]}]}
(OUT/'trail-sources.json').write_text(json.dumps(receipt,indent=2)+'\n')
print(json.dumps({'rows':len(rows),'at_50_pct':rows[4:6],'at_100_pct':rows[8:10]}))
