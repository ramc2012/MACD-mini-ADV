"""Create the reviewed research note, executed notebook and aggregate Sources receipt."""
import datetime as dt
import json
import os
from pathlib import Path
import nbformat
from nbclient import NotebookClient
import pandas as pd

ROOT = Path(__file__).resolve().parent
NAMES = {'turn':'MACD turn','closed_st':'Completed premium ST','preview_st':'Forming premium ST','above_p':'Above daily P','reclaim_p':'Recent P reclaim','preview_st_p':'Forming ST + above P','preview_st_room':'Forming ST + pivot room','underlying_context':'Premium ST + underlying context','cross5':'Cross + 5 min','cross1':'Cross + 1 min'}

def table(columns, rows):
    return '\n'.join(['| '+' | '.join(columns)+' |','|'+'|'.join(['---']*len(columns))+'|']+['| '+' | '.join(map(str,r))+' |' for r in rows])

def main():
    behavior=pd.read_csv(ROOT/'behavior_and_event_returns.csv')
    h=behavior[behavior.period=='holdout']
    b=pd.read_csv(ROOT/'book_results.csv')
    tests=pd.read_csv(ROOT/'holdout_tests.csv')
    model=json.loads((ROOT/'prediction_models.json').read_text())
    validation=json.loads((ROOT/'VALIDATION.json').read_text())
    selected=json.loads((ROOT/'training_selection.json').read_text())['rule']
    old=pd.read_csv(ROOT/'source_sensitivity_old/book_results.csv')
    def block(cap=5000000, period='holdout', cost=0, timeout=30):
        return b[(b.capital==cap)&(b.period==period)&(b.extra_bps==cost)&(b.timeout==timeout)]
    bt=table(['Trigger after negative MACD starts rising','Trials','Cross ≤30 min','Cross by close','Early fills / timeouts','Median timely signal lead'],[[NAMES[a.rule],int(a.trials),f'{a.cross_30_pct:.1f}%',f'{a.cross_by_close_pct:.1f}%',f'{int(a.early_fills)} / {int(a.early_timeouts)}',f'{a.median_timely_lead_minutes:g} min'] for a in h.itertuples()])
    def portfolio_table(cap, period='holdout'):
        return table(['Policy','New positions / early','Fully closed wins','Closed PF','Marked P&L, ₹L','Δ vs cross+5, ₹L'],[[NAMES[a.rule],f'{int(a.new_trades)} / {int(a.early_trades)}',f'{a.closed_win_pct:.1f}%',f'{a.closed_pf:.2f}',f'{a.marked_pnl/1e5:+.2f}',f'{a.delta_pnl/1e5:+.2f}'] for a in block(cap,period).itertuples()])
    costrows=[]
    for cap in [100000,5000000]:
        for rule in ['reclaim_p','preview_st','preview_st_p','preview_st_room']:
            costrows.append([f'₹{cap/1e5:g} L',NAMES[rule]]+[f'{block(cap,cost=cost).set_index("rule").loc[rule,"delta_pnl"]/1e5:+.2f}' for cost in [0,20,50]])
    costtable=table(['Capital','Hybrid','Base Δ, ₹L','+20 bp and fees Δ','+50 bp and fees Δ'],costrows)
    timerows=[]
    for cap in [100000,5000000]:
        for rule in ['preview_st','preview_st_p','preview_st_room']:
            timerows.append([f'₹{cap/1e5:g} L',NAMES[rule]]+[f'{block(cap,timeout=t).set_index("rule").loc[rule,"delta_pnl"]/1e5:+.2f}' for t in [15,30,60]])
    timeouttable=table(['Capital','Hybrid','15-min Δ, ₹L','30-min Δ','60-min Δ'],timerows)
    oldh=old[(old.capital==5000000)&(old.period=='holdout')&(old.extra_bps==0)&(old.timeout==30)]
    sourcetable=table(['Same frozen rule','Current-first Δ, ₹L','Old-priority Δ, ₹L'],[[NAMES[r],f'{block().set_index("rule").loc[r,"delta_pnl"]/1e5:+.2f}',f'{oldh.set_index("rule").loc[r,"delta_pnl"]/1e5:+.2f}'] for r in ['reclaim_p','preview_st','preview_st_p','preview_st_room']])
    calibration=pd.read_csv(ROOT/'probability_diagnostics.csv')
    caltable=table(['Model probability band','Trials','Cross ≤30 min','Early fills','Mean early-minus-wait / trial'],[[a.probability_band,int(a.trials),f'{a.cross_30_pct:.1f}%',int(a.early_fills),f'{a.mean_pair_delta_pct:+.2f} pp'] for a in calibration[calibration.model=='st_pivot_probability'].itertuples()])
    note=f'''# Premium MACD before zero: Supertrend and pivots

3 October 2026. **Decision: indicators help forecast the cross, but the tested broad early-entry rules do not establish a profitable improvement over cross + 5 minutes.** A small high-confidence model group is an exploratory shadow candidate. Source apps, positions, ledgers and settings remain unchanged. This is an independent analysis using September market data; no September-trained Smart scorer or Claude conclusions enter it.

## What happens before a zero cross?

Premium can already be rising and above bullish Supertrend or its daily pivot while the slower MACD remains negative. Among eventual held-out crosses, the median premium 15 minutes beforehand was 6.8% below its price at the cross. Forming premium Supertrend was bullish in 65.7% of the available observations and premium was above P in 80.8%. Five minutes beforehand, those proportions were 67.8% and 84.0%. **These cross-aligned statistics condition on a future successful cross. They explain the path; they cannot estimate an early strategy's success rate.** Available observations differ by offset and are retained in `cross_aligned_summary.csv`.

For production MACD(12,26,9), let F and S be the last committed fast and slow EMAs on session-anchored 30-minute candles. The exact premium required to reach zero in the forming candle is:

`Z = (325*S − 297*F)/28`, and `preview MACD = (28/351)*(premium − Z)`.

Z is fixed within that candle and updates at the next committed candle. This gives a measurable distance to zero, rather than guessing from a visual upward bend. It does not forecast whether premium will reach Z. The signal-line histogram turning positive while MACD is below zero is the first setup condition; it is a different event from a MACD zero cross.

## Predictive study, including failures

Development uses nine September 1–11 sessions. September 12–14 is an embargo; holdout is twelve sessions September 15–30. Base trigger requires MACD below zero, above its preview signal, rising on three consecutive minute steps in the same forming candle, and linear projected time to zero no more than 30 minutes. All four observations must be contiguous and have option volume. Only the earliest trigger per contract/bar/rule is kept, before any cross in that bar, up to 14:45 IST. Each row below is a separately filtered population; do not sum them.

{bt}

The useful combined setup is rising negative premium MACD + bullish forming premium Supertrend + premium above its **own prior-session daily P**. It raises the observed 30-minute cross rate from 24.9% to 38.4%. It also has a 78.0% cross rate by the close, but a much later cross cannot justify a trade with a 30-minute deadline. Of 679 executable early entries, 396 exit for no-cross timeout. Stops and other exits account for some further failures. A sampled positive preview is also weaker than a completed positive 30-minute MACD: only 31.1% have that confirmation within 60 minutes.

Successful timely combined-rule signals lead the cross by a median 12 minutes. Among the 194 cases with both an executable early fill and cross+5 fill, the latter premium is a median 7.7% higher than the early premium. That gain is conditional on obtaining both fills. Across **all 705 trials**, early-minus-wait mean return is −0.13 percentage points per trial, including false attempts and cancellations. Three early positions remain unresolved in this event experiment; marked values are disclosed separately, never classified as realised exits. Equal-ticket closed event profit factor is 1.19; it is not the cash-weighted portfolio PF below.

A separate L2 logistic cross classifier was trained only on September 1–11. MACD geometry/context features alone give held-out AUC 0.850 and Brier 0.138. Adding premium/underlying ST and pivot features gives AUC 0.868 and Brier 0.126. The AUC gain's 2,000-replicate session-block bootstrap interval is −0.005 to +0.036. Mean-day Brier improves on ten of twelve sessions, raw paired p=0.043, but Holm p=0.727 across the predefined family of seventeen comparisons. These results support a useful diagnostic, not a statistically cleared trading edge. Matching on day, call/put side, distance to zero and projected time preserves positive ST/above-P associations in common strata; recent P reclaim is worse. Matched association is not proof of causation.

## Can high-confidence predictions support a narrower entry?

{caltable}

The >0.8 model band contains **26 trials across nine held-out sessions; all 26 cross within 30 minutes**. Only 16 permit an early fill; ten cross before the next eligible entry or lack an eligible fill. Median signal-to-cross lead is 2.5 minutes; among the filled subset, median entry-to-cross lead is seven minutes. Those different populations explain the different medians. The band is inspected descriptively after the model evaluation and **was not a selected or tested portfolio policy**.

Its apparent paired return improvement is concentrated: the top three observations contribute 233.4 percentage points, more than the entire group's 189.1-point sum. Removing those three makes the remaining sum negative. The 0.5–0.8 band forecasts crosses well (81.4%) but has negative early-minus-wait return. A cross-probability model therefore cannot be promoted as a profitability model from this sample. Preserve the >0.8 band only as a hypothesis for prospective shadow observation.

## Trade count and trade quality in funded chronological books

These are **unranked intraday entry-isolation experiments**, not live-book profits, Smart-lane results or the full production multi-day/pyramid ladder. Baselines take all eligible crosses, including unexpected ones without an early precursor. A hybrid enters early when its rule fires and otherwise waits for cross+5. One pending/open position per contract; an unfunded/cancelled attempt releases the contract for later signals. Same-timestamp orders use alphabetical symbol order. Cash is applied in chronological order.

All policies share a 30% hard stop, trail armed at +30% with 25% off peak and round-trip breakeven floor, post-cross completed-negative-MACD invalidation when MFE <10%, and fresh-quote intraday closure from 15:25. Early entries without a timely cross exit after 30 minutes. No pyramids or staged exits. Quotes require nonzero volume; absent quotes leave marked unresolved inventory. Ticket is min(₹1 L, 10% equity), with exchange lots, maximum 125 lots and 2% prior-session volume cap. Base assumed paper slippage is five bp per side; this is not fee-complete real execution.

₹50 L, September 15–30:

{portfolio_table(5000000)}

Forming ST + P adds 395 positions (43.1%) and 590 early trades, while gaining only ₹7,745 over baseline. Closed PF drops 1.40 to 1.30 and win rate 42.1% to 41.5%. Daily-difference t=0.014 and corrected p=1.0. The development-selected rule was **{NAMES[selected]}**, which loses ₹4.23 L relative to baseline on holdout. No hybrid clears the multiple-testing correction against either cross+5 or cross+1.

₹1 L, same holdout:

{portfolio_table(100000)}

The combined ST+P rule loses ₹25,876 relative to baseline. Capital constraints change eligible fills, funding and compounding, so a ₹50 L improvement cannot be scaled to a ₹1 L book.

Period marked P&L is close equity minus the previous session's close equity. Books run continuously from September 1; holdout funding inherits each development path. Intraday exit attempts can leave unresolved carried positions, so period P&L can include carry-in marks. Position counts/quality refer to entries within the period and fully closed by its end. PF uses net positive/negative cash P&L on those closed positions. These are simulated funded paths, not independent equal-size randomised bets.

## Costs, timeout and source sensitivity

Same-cost policy comparisons add 20/50 bp per side plus assumed ₹20 per order to both early and baseline books. Fees are reserved for closing held positions; whole-lot funding can change the traded population.

{costtable}

The ₹50 L ST+P difference changes from +₹0.08 L to −₹1.76 L and −₹7.76 L. This erases the small apparent primary advantage. Costs are sensitivity scenarios, not a reconstruction of spreads, taxes or market impact.

Timeouts were fixed descriptive checks, not tuned on held-out profits:

{timeouttable}

A 15-minute deadline improves the ₹50 L ST+P path but worsens its ₹1 L path; a 60-minute deadline worsens the ₹50 L path. Selecting the best timeout after seeing these results would reuse the holdout and requires new data to validate.

Old-priority versus primary current-first candles:

{sourcetable}

The old-priority ₹50 L cross+5 baseline earns ₹30.64 L, versus ₹29.56 L current-first. The selected development winner also changes with source precedence. The same fixed ST+P rule retains a small advantage under old priority, but neither source produces a dependable margin after costs. Detailed September 18–29 and 19–29 comparisons are retained in the CSV; they are overlapping descriptive windows, not additional independent validation samples.

## Data, validation and practical conclusion

Available catalogs request 3,036 options and 214 underlyings; 2,993 series have observations. The universe is retrospective, not a decision-time archived chain. Daily eligible contracts use the first completed opening underlying minute, nearest unexpired expiry, prior-session option volume and the production 1.5× volume selector. Historical OI, spread/depth and tick quotes are unavailable. Regular-session one-minute archives supply observed prefixes to 30-minute MACD/ST calculations. Prior P/R1/R2/S1/S2 require adequate previous-session observations; missing levels remain unknown. Current-first and old-priority source conflicts are material, hence the repeated analysis.

Supertrend uses ATR(10), multiplier three, with completed and forming states kept separate. Forming bands use only high/low/close observed so far and can revert. Pivots use previous-session OHLC, never the completed current-session range. Definitions were checked against [TradingView Supertrend](https://www.tradingview.com/support/solutions/43000634738-supertrend/) and [Traditional pivot documentation](https://www.tradingview.com/support/solutions/43000521824-pivot-points-standard/).

Ten focused tests passed for zero-price/production preview parity, causal feature prefixes, prior-pivot and ST logic, event censoring, failed-trigger inclusion, delayed-entry cancellation and whole-lot cash handling. Independent fill reconciliation passes on all {len(validation['book_checks'])} primary books: terminal cash/quantity/equity, nonnegative cash and no pyramids/oversells. Source SHA/ledger and current positions/cash are unchanged; broker-order calls are zero and live orders remain locked. Raw aggregate evidence, model coefficients/preprocessing, source sensitivity, all predefined rules and seventeen corrected tests are saved here.

**Keep the current entry policy unchanged.** For shadow research, track negative-but-rising MACD's distance to the exact zero premium alongside forming/completed premium ST and prior daily pivots. Log every forecast, cancelled entry and failure, with quote/spread information and a predeclared deadline. A fresh frozen-model prospective comparison must demonstrate incremental net profit and stable quality before early entry can be enabled. This study has temporal holdout for prediction, but one month, retrospective catalog selection and twelve test sessions do not validate deployment or a Smart-lane change.

Reproduce using `analyze.py` (primary), `analyze.py --old-source`, `test_analyze.py`, `preserve.py`, `assess.py`, then `deliver.py`, with the bundled Python runtime and the two research dependency directories on PYTHONPATH. `PLAN.md` records the frozen design. `analysis.ipynb` is an executed companion against the saved study outputs; rerunning it does not contact brokers or modify books.
'''
    (ROOT/'FINDINGS.md').write_text(note)
    nb=nbformat.v4.new_notebook()
    nb.cells=[nbformat.v4.new_markdown_cell('# Premium MACD before zero\n\nST/pivots improve forecast quality; a cross forecast is not a profitable early-entry policy. Read FINDINGS.md and the frozen PLAN.md for source, execution and multiple-testing limits.'),nbformat.v4.new_code_cell(f"from pathlib import Path\nimport pandas as pd, json, numpy as np\nimport matplotlib.pyplot as plt\nROOT=Path({str(ROOT)!r})\nh=pd.read_csv(ROOT/'behavior_and_event_returns.csv').query('period == \"holdout\"')\nb=pd.read_csv(ROOT/'book_results.csv')\nnames={NAMES!r}\nh[['rule','trials','cross_30_pct','cross_by_close_pct','early_fills','early_timeouts','median_timely_lead_minutes','mean_pair_delta_pct_per_trial']]"),nbformat.v4.new_code_cell("a=pd.read_csv(ROOT/'cross_aligned_summary.csv').query('period == \"holdout\" and offset_minutes <= 0')\nfig,axes=plt.subplots(1,2,figsize=(12,4.5),layout='constrained')\naxes[0].plot(a.offset_minutes,a.median_premium_vs_cross_pct,marker='o',color='#226d82')\naxes[0].axhline(0,color='grey',lw=.7);axes[0].set_ylabel('Median premium vs future cross price (%)')\nfor col,label,color in [('preview_st_bull_pct','Forming premium ST bullish','#226d82'),('closed_st_bull_pct','Completed premium ST bullish','#69788b'),('above_p_pct','Premium above prior daily P','#b27421')]:axes[1].plot(a.offset_minutes,a[col],marker='o',label=label,color=color)\naxes[1].set_ylabel('Available observations (%)');axes[1].set_ylim(0,100);axes[1].legend(fontsize=8,loc='lower right')\nfor ax in axes:ax.set_xlabel('Minutes before sampled zero cross');ax.spines[['top','right']].set_visible(False)\nfig.suptitle('Eventual crosses only · hindsight description, not forecast success',fontsize=11)\nfig.savefig(ROOT/'cross_path.png',dpi=150,bbox_inches='tight');plt.show()"),nbformat.v4.new_code_cell("rules=['turn','closed_st','preview_st','preview_st_p','reclaim_p']\nz=h.set_index('rule').loc[rules];x=np.arange(len(z))\nfig,ax=plt.subplots(figsize=(11,4.5),layout='constrained')\nax.bar(x-.18,z.cross_30_pct,width=.36,label='Within 30 minutes',color='#226d82')\nax.bar(x+.18,z.cross_by_close_pct,width=.36,label='By close (may be too late)',color='#a7bdc5')\nax.set_xticks(x,[names[r]+'\\n'+str(int(z.loc[r,'trials']))+' trials' for r in rules],fontsize=9)\nax.set_ylim(0,100);ax.set_ylabel('Cross rate (%)');ax.legend();ax.spines[['top','right']].set_visible(False)\nax.set_title('All eligible held-out triggers, including failures · 15–30 September')\nfig.savefig(ROOT/'cross_forecast.png',dpi=150,bbox_inches='tight');plt.show()"),nbformat.v4.new_code_cell("z=b.query('period == \"holdout\" and extra_bps == 0 and timeout == 30')\nfig,axes=plt.subplots(1,2,figsize=(12,5),layout='constrained')\nrules=['cross5','closed_st','preview_st','preview_st_p','reclaim_p']\nfor ax,cap in zip(axes,[100000,5000000]):\n    s=z[z.capital==cap].set_index('rule').loc[rules];vals=s.delta_pnl.to_numpy()/1e5\n    ax.barh([names[r] for r in rules],vals,color=['#69788b','#a7bdc5','#a7bdc5','#226d82','#b27421']);ax.invert_yaxis();ax.axvline(0,color='grey',lw=.8)\n    for i,(v,n,pf) in enumerate(zip(vals,s.new_trades,s.closed_pf)):ax.text(.98,i,f'{v:+.2f} L · {int(n)} trades · PF {pf:.2f}',transform=ax.get_yaxis_transform(),ha='right',va='center',fontsize=8.5)\n    ax.set_xlim(min(vals)-.1*max(1,np.ptp(vals)),max(vals)+1.2*max(1,np.ptp(vals)));ax.set_xlabel('P&L difference vs cross + 5 (INR lakh)');ax.set_title(f'Initial capital INR {cap/1e5:g} lakh');ax.spines[['top','right']].set_visible(False)\nfig.suptitle('Held-out unranked intraday entry study · simulated funding, base costs',fontsize=11)\nfig.savefig(ROOT/'book_quality.png',dpi=150,bbox_inches='tight');plt.show()\nz[['capital','rule','new_trades','early_trades','closed_win_pct','closed_pf','marked_pnl','delta_pnl']]"),nbformat.v4.new_code_cell("v=json.loads((ROOT/'VALIDATION.json').read_text())\nassert v['checks_passed']==10\nassert all(x['cash_reconciles'] and x['no_negative_cash'] and x['no_pyramids_or_oversells'] for x in v['book_checks'].values())\nassert v['source_preservation']['old_ledger_unchanged']\npd.read_csv(ROOT/'holdout_tests.csv')")]
    nb.metadata={'kernelspec':{'name':'python3','display_name':'Python 3','language':'python'},'language_info':{'name':'python'}}
    os.environ['PYTHONPATH']=os.pathsep.join(str(ROOT.parent/p) for p in ['september_supertrend_pivots_20261003/followup/.deps','supertrend_pivots_20261003/.deps'])
    NotebookClient(nb,timeout=180,kernel_name='python3',resources={'metadata':{'path':str(ROOT)}}).execute()
    nbformat.write(nb,ROOT/'analysis.ipynb')
    at=dt.datetime.now(dt.UTC).isoformat()
    def records(frame): return json.loads(frame.round(6).to_json(orient='records'))
    def item(id,title,files,definitions,caveats,rows):
        return {'id':id,'title':title,'queries':[{'id':id+'-evidence','source':{'label':'Read-only September minute archives and isolated research books','files':[{'label':f} for f in files],'metricDefinitions':[{'label':k,'definition':val} for k,val in definitions.items()],'filters':['Forecast trials: negative rising premium MACD; at most one event per contract/bar/rule before a cross.','Session eligibility: opening spot, nearest unexpired contracts and prior-session option volume.','Evaluation: September 15–30; all eight frozen rules retained.'],'caveats':caveats,'executedAt':at,'evidenceFlow':[{'kind':'validation','title':'Reviewed methodology and arithmetic','body':'Frozen rule list; causal feature prefixes; temporal holdout; failed triggers and costs retained. Independent cash reconciliation and unchanged source books checked.'}]},'reportingPeriod':'Development September 1–11; held-out September 15–30, 2026','capturedAt':at,'columns':list(rows[0]),'rows':rows,'preview':{'kind':'aggregate','note':'Aggregate study evidence; no raw account identifiers or contract-level records.'}}]}
    items=[item('forecast','Can ST and pivots anticipate a cross?',['PLAN.md','behavior_and_event_returns.csv','prediction_models.json','holdout_tests.csv'],{'Trial':'Earliest eligible negative rising MACD event per contract/bar/rule, with all false attempts retained.','Cross':'First subsequent executable positive preview relative to a nonpositive committed MACD.','Model':'Own L2 cross classifier, preprocessing and fit on development data only.'},['Different rule rows select different populations; counts cannot be summed.','Forming ST may revert; by-close crosses may arrive after the entry deadline.','Twelve test sessions; corrected model p=0.727. Retrospective contract catalog, no historical OI/spreads/ticks.'],records(h[['rule','trials','cross_30_pct','cross_by_close_pct','confirmed_60_pct','early_fills','early_timeouts','median_timely_lead_minutes','mean_pair_delta_pct_per_trial']])),item('economics','Do early entries improve profit and trade quality?',['book_results.csv','book_daily.csv','books.json.gz','VALIDATION.json'],{'Position':'One entry episode, no pyramids or staged exits. Closed quality excludes unresolved positions.','P&L':'Period equity change on continuous simulated funding paths, including possible carried inventory.','Costs':'Base five bp per side; extra-cost scenarios apply the same additional bp and assumed order fees to both controls and hybrids.'},['Unranked intraday entry-isolation study; not Smart, live-book or full production strategy returns.','Retrospective universe and one-minute sampled execution, without historical spreads/depth/ticks.','No hybrid passes seventeen-comparison Holm correction. Base rule winner was selected only on development.','Cost and timeout sensitivities are descriptive, not holdout optimisation.'],records(b[(b.period=='holdout')&b.rule.isin(['cross5','reclaim_p','preview_st_p'])][['capital','rule','extra_bps','timeout','new_trades','early_trades','closed_win_pct','closed_pf','marked_pnl','delta_pnl']])),item('narrow-band','Small high-confidence band: diagnostic only',['probability_diagnostics.csv','prediction_holdout.csv','VALIDATION.json'],{'Probability bands':'Descriptive held-out cross forecasts, not a validated portfolio gate.','Paired difference':'Early-minus-wait return per trial, including no-cross attempts and cancellations.'},['The >0.8 group has only 26 trials across nine sessions and sixteen actual early fills.','Top three observations contribute more than the entire group paired return gain.','Threshold was not a selected or portfolio-tested trading policy; no deployment conclusion.'],records(calibration[calibration.model=='st_pivot_probability']))]
    for card in items:
        query=card['queries'][0]
        query['source']['filters']=['Evaluation period: September 15–30, 2026.','Contract selection: opening spot, nearest unexpired expiry, prior-session option volume.']
        if card['id']=='forecast':
            query['source']['filters']+=['Forecast population: negative rising premium MACD, at most one event per contract/bar/rule before any cross.','Early signals: no later than 14:45 IST.']
        elif card['id']=='economics':
            query['source']['filters']+=['Displayed policies: cross+5 control, development-selected P reclaim, forming ST+P hybrid.','Baseline: all eligible crosses, including those with no early precursor.','Independent capital scenarios: INR 1 lakh and INR 50 lakh.']
        else:
            query['source']['filters']+=['Forecast population: common MACD-turn events.','Model: trained only on September 1–11; displayed probability bands inspected descriptively.']
    modelrows=[{'model':label,**model[key]['metrics']} for key,label in [('macd_only','MACD geometry/context'),('macd_st_pivot','MACD + ST/pivots')]]
    modelcard=item('model-comparison','Incremental held-out cross prediction',['prediction_models.json','prediction_holdout.csv','holdout_tests.csv','VALIDATION.json'],{'AUC':'Ranking of eventual crosses versus no cross within thirty minutes.','Brier':'Mean squared probability error; lower is better.','Model comparison':'Own fixed L2 classifiers with training-only preprocessing and no Smart scores.'},['Twelve test-session clusters; AUC gain interval includes zero.','Mean-day Brier raw p=0.043, Holm p=0.727 across seventeen planned comparisons.','Cross prediction is not a validated profitability or entry model.'],modelrows)
    modelcard['queries'][0]['source']['filters']=['Development: September 1–11, 2026.','Holdout: September 15–30, 2026.','Population: common negative rising MACD-turn triggers, including failures.']
    items.insert(1,modelcard)
    (ROOT/'reviewed_sources.json').write_text(json.dumps({'schemaVersion':1,'items':items},indent=2,allow_nan=False))
    (ROOT/'DELIVERY_VALIDATION.json').write_text(json.dumps({'notebook_executed':True,'code_cells':sum(c.cell_type=='code' for c in nb.cells),'notebook_errors':sum(o.output_type=='error' for c in nb.cells if c.cell_type=='code' for o in c.outputs),'figures':['cross_path.png','cross_forecast.png','book_quality.png'],'review_status':'Share with caveats','source_books_unchanged':True},indent=2))
    print('Research note, five-cell executed notebook, three figures and reviewed Sources saved.')

if __name__=='__main__': main()
