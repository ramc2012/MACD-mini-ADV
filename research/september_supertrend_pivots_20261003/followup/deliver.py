"""Publish reviewed follow-up tables, narrative, sources receipt and notebook."""
import datetime as dt
import gzip
import json
import os
from pathlib import Path
import nbformat
from nbclient import NotebookClient
import pandas as pd

ROOT=Path(__file__).resolve().parent
VIS=Path('/Users/ramachandran/.codex/visualizations/2026/10/03/01a1010e-16a9-73a0-809c-3a36c922e4d9')
RULES=['baseline','premium_st','spot_st','pivot_room','both_st_room']
NAMES={'baseline':'Baseline','premium_st':'Premium Supertrend','spot_st':'Underlying Supertrend','pivot_room':'Pivot headroom','both_st_room':'Both Supertrends + headroom'}

def table(headers,rows):
    return '\n'.join(['| '+' | '.join(headers)+' |','|'+'|'.join(['---']*len(headers))+'|']+['| '+' | '.join(map(str,row))+' |' for row in rows])

def main():
    q=pd.read_csv(ROOT/'actual_trade_quality.csv');s=pd.read_csv(ROOT/'smart_comparisons.csv');g=pd.read_csv(ROOT/'old_gap_inventory.csv');v=json.loads((ROOT/'smart_validation.json').read_text())
    old=q[(q.cohort=='old_active')&q.rule.isin(RULES)].set_index('rule').loc[RULES]
    ot=table(['Rule','New positions','Closed / open','Closed wins','Closed PF','Mean return / median','New-position marked P&L, ₹L'],[[NAMES[r],int(a.new_episodes),f'{int(a.closed_new_episodes)} / {int(a.open_new_episodes)}',f'{a.closed_win_rate_pct:.1f}%',f'{a.closed_profit_factor:.2f}',f'{a.closed_mean_return_pct:+.2f}% / {a.closed_median_return_pct:+.2f}%',f'{a.all_new_marked_pnl_rupees/1e5:+.2f}'] for r,a in old.iterrows()])
    gap=g[g.rule.isin(RULES)].set_index(['begin','rule'])
    gt=table(['Rule','Sep 18–29 new positions','Sep 18–29 marked P&L, ₹L','Sep 19–29 new positions','Sep 19–29 marked P&L, ₹L'],[[NAMES[r],int(gap.loc[('2026-09-18',r),'new_trades']),f"{gap.loc[('2026-09-18',r),'marked_pnl']/1e5:+.2f}",int(gap.loc[('2026-09-19',r),'new_trades']),f"{gap.loc[('2026-09-19',r),'marked_pnl']/1e5:+.2f}"] for r in RULES])
    def smart_table(cap,cohort):
        d=s[(s.capital==cap)&(s.extra_bps==0)&(s.cohort==cohort)].set_index('rule').loc[RULES]
        return table(['Rule','New / fully closed','Closed wins','Closed PF','Mean / median return','Marked P&L / Δ, ₹L','Daily drawdown, ₹L'],[[NAMES[r],f'{int(a.new_trades)} / {int(a.closed_new_trades)}',f'{a.closed_win_rate_pct:.1f}%',f'{a.closed_profit_factor:.2f}',f'{a.closed_mean_return_pct:+.2f}% / {a.closed_median_return_pct:+.2f}%',f'{a.marked_pnl/1e5:+.2f} / {a.delta_pnl/1e5:+.2f}',f'{a.daily_drawdown/1e5:.2f}'] for r,a in d.iterrows()])
    ct=s[(s.cohort.isin(['full_sep','period_18_29']))&s.rule.isin(['premium_st','pivot_room','both_st_room'])]
    costs=table(['Capital','Period','Rule','Δ at base / +20 / +50 bp, ₹L'],[[f'₹{cap/1e5:g} L',cohort,NAMES[r],' / '.join(f"{ct[(ct.capital==cap)&(ct.cohort==cohort)&(ct.rule==r)&(ct.extra_bps==cost)].iloc[0].delta_pnl/1e5:+.2f}" for cost in [0,20,50])] for cap in [100000,5000000] for cohort in ['full_sep','period_18_29'] for r in ['premium_st','pivot_room','both_st_room']])
    terminal=s[(s.cohort=='full_sep')&(s.extra_bps==0)&s.rule.isin(RULES)]
    residuals=table(['Capital','Rule','Open / expired residuals','Expiry-scenario residual value, ₹'],[[f'₹{a.capital/1e5:g} L',NAMES[a.rule],f'{int(a.closing_positions)} / {int(a.expired_residuals)}',f'{a.expired_residual_value:,.0f}'] for a in terminal.itertuples()])
    note=f'''# Trade counts, trade quality, September 18–29 and Smart

3 October 2026. Research continuation of the matched September study. Source books, held positions and trading settings were read only. The running Smart books have no fills or journal decisions; every Smart number below is a separate retrospective reconstruction.

## Counts and quality in the old active book: September 1–18

{ot}

One trade means one flat-to-flat position episode. Pyramid additions and staged exits are fills within that trade. Baseline has 1,127 BUY fills and 1,188 SELL fills for these 621 new positions; premium ST has 715 and 780 for 395 positions. Carry-in is excluded from this quality table. Fully closed trades are measured at the stated period end; still-open positions do not receive a realised win/loss label. PF is total positive closed P&L divided by absolute total negative closed P&L. Percentage returns divide an episode's net cash P&L by all its purchase cost; the reported average gives every episode equal weight. The source fills have zero booked fees, so these are not fee-complete returns.

Premium ST removes 226 trades (36.4%). Win rate rises 2.3 percentage points and PF 1.14 to 1.34, while equal-episode mean return remains negative. Mean winning / losing cash P&L changes from ₹28,825 / −₹17,166 to ₹30,056 / −₹16,755. The combined filter removes 356 trades (57.3%); closed PF is higher but total active-book marked P&L is lower. These comparisons change the traded population and funding; they are descriptive, not a paired causal estimate.

The parent's active portfolio totals include carry-in: baseline +₹26.85 L, premium ST +₹27.65 L. New September positions alone contribute +₹19.18 L versus +₹18.55 L. Carry-in contributes +₹7.66 L versus +₹9.11 L as freed cash changes later additions to existing positions. Thus the +₹0.80 L total improvement does not show that newly selected signals earned more money. Daily marked drawdown falls from ₹10.97 L to ₹4.75 L. Premium ST still underperforms in the separate Sep 29–30 actual-entry book. The parent's funding correction, source-priority sensitivity and holdout conclusions remain applicable.

## Recheck of September 18–29

All six readable old MACD ledgers/backups were inspected. The retained old book has 284 fills on September 18, with its final fill at 15:30 IST. There are no MACD fills on September 19–29 in any of these books. September 28 has three stored stock-underlying signals, not option entries. Old marks continuing into Sep 28/pre-open Sep 29 do not establish continuous trading. The separate migrated book's Sep 29 trades are kept separate.

{gt}

For the entry-filter rows, Sep 19–29 changes are losses/gains on inventory already carried at Sep 18, with zero new trades. They must not be presented as returns from a running strategy over the missing execution period. Including Sep 18 includes that day's new entries. Indicator-exit scenarios are also retained in `old_gap_inventory.csv`; their exits are explicitly modelled, not source fills.

The market-data gap is different from the execution gap. The old archive has full-session option/underlying observations on Sep 21–25 and Sep 28, and the current archive covers Sep 29–30. Sep 19–20 and Sep 26–27 are weekends. Coverage counts in `minute_coverage_by_day.csv` refer to the matched 702-option/210-underlying universe; Smart preparation expands the requested universe to 3,036 options and 214 underlyings, with 2,993 actual series. The old source is stopped and was not restarted.

## Smart: reconstructed September 18–29

**These are not live-book or out-of-sample profits.** The exported production ranker was fitted on Aug 25–Oct 1, including these September outcomes. Its metadata's walk-forward AUC does not turn this final-model replay into an independent test. No historical per-session models/trainer are available to remove that leakage. Use this only to inspect policy mechanics and exposure to the filters.

₹50 L book:

{smart_table(5000000,'period_18_29')}

₹1 L book:

{smart_table(100000,'period_18_29')}

P&L measures equity at Sep 29 close less Sep 17 close, so it includes carried positions. Trade counts and closed quality include only entries made Sep 18–29 and closed by Sep 29. There are eight trading sessions in this window. The comparable Sep 19–29 rows, with seven sessions, are saved in `smart_comparisons.csv`. Neither book is merged with the old app or the other Smart capital.

Excluding September 18 changes the inference. ₹50 L Smart, September 19–29:

{smart_table(5000000,'period_19_29')}

The combined filter's relative improvement falls from +₹16.96 L with Sep 18 included to +₹1.85 L after Sep 18; its Sep 19–29 marked P&L is still −₹1.56 L. Premium ST improves that later interval by ₹6.24 L, while pivot headroom improves it by ₹7.45 L. Each is a separate funded path with its own opening inventory. Do not attribute the whole Sep 18–29 difference to superior new entries after the old app stopped.

## Smart: entire September

₹50 L:

{smart_table(5000000,'full_sep')}

₹1 L:

{smart_table(100000,'full_sep')}

Daily drawdown is measured at session closes and can be much smaller than the sampled intraday drawdown stored in `smart_validation.json`. Full-month closed quality still excludes unfinished positions. Closed-P&L and average winner/loss values, the MACD/Blast source split, all eight fixed entry overlays and every cost/window comparison are retained in the CSVs.

## Costs, implementation and expiry limitations

Extra cost is applied per side on top of the configured five-bp paper slippage; each filter is compared with baseline at the same cost, allowing funding and whole-lot sizes to change. Commissions and spreads are not reconstructed. No overlay threshold was tuned on this follow-up.

{costs}

The actual production Smart source and model match the running container's SHA256. The reconstruction uses its rank cutoff, five-minute delay, 15-second ranking wait quantized to the next fresh sampled minute (normally six minutes after a sampled MACD cross), dispersion sizing, 10% equity tickets, cash and exchange-lot limits, no pyramids, staged exits, stops/trails/targets, maximum holding-period logic and Blast intake. It uses an order broker that raises if a real order is attempted. Local book files live in a temporary research directory. The actual ten-second quote-age gate is preserved; zero-volume premium bars cannot become executable quotes.

The contract universe comes from available retrospective catalogs and observed traded metadata, not a point-in-time daily chain. Daily selection uses the first completed opening spot minute, nearest unexpired contract, production 1.5×-volume selector and previous-session option volume; unknown OI remains zero. Blast liquidity also uses this known selection-time volume proxy. Breadth and current-day features use observations available at the decision time. This differs from the exact historical live selector, spread/depth and tick stream. Current candles take priority for full-session Smart coverage; the parent matched-ledger study prioritizes old candles. Their profit totals are not comparable baselines.

Some Smart positions cannot be exited by the execution quote gate. Inventory valuation is separate: valid observed OHLC, including zero-volume bars, can value an open position without creating a quote or an exit. After expiry, residual holdings use intrinsic value against the observed expiry-day underlying close proxy. This is a valuation scenario, not official settlement, exercise/physical delivery, realised P&L or released cash. Quantities and cash remain tied up when there is no fill. This adjustment is made causally during the replay, so later equity-based sizing does not use a frozen pre-expiry premium. No absent quote is silently treated as a zero-price fill.

{residuals}

## Decision and validation

Review status: **Share with caveats**. Closed-trade arithmetic and isolated-policy mechanics are checked; Smart out-of-sample edge and official expiry settlement are unavailable.

The trade-count reduction and closed-quality changes are real arithmetic effects within the matched old schedule. They do not establish a stable incremental edge: the old later-book/holdout results differ, carry-in financing contributes, and Smart's final ranker has seen September labels. Keep live/paper entry rules unchanged. Retain premium ST as a shadow risk-control candidate; the hard combined filters sacrifice many trades. Any Smart profit or filter gain here requires a prospective comparison with a frozen model and decision-time chains/quotes before it can support deployment.

Ten focused tests pass: causal feature prefix and opening selection, quote age, entry delay including mature-batch handling, actual staged target accounting, overlay handling, whole-lot funding, real-aware timestamp serialization, and valuation without invented expiry/zero-volume execution. The rank wait applies to the oldest due candidate in a batch, so some MACD entries join at exactly five minutes and some Blast entries join immediately; an isolated MACD candidate normally fills at the sixth sampled minute. Every reconstructed book reconciles episode cash flows to terminal equity, has nonnegative cash, valid exchange-lot fills, no pyramids/oversells, causal entry delays and no unpriced daily holdings. Live books remain unchanged; final checks are in `PRESERVATION.json`.

Reproduce with the bundled Python and `PYTHONPATH=followup/.deps:../supertrend_pivots_20261003/.deps` from the parent research folder: `python followup/test_smart_replay.py`, `python followup/smart_replay.py`, `python followup/summarize.py`, `python followup/deliver.py`. The saved evidence can be reviewed without broker access. `analysis.ipynb` is executed against the saved tables.
'''
    (ROOT/'FINDINGS.md').write_text(note)
    nb=nbformat.v4.new_notebook()
    nb.cells=[nbformat.v4.new_markdown_cell('# Counts, quality, September gap and Smart\n\nSmart is a retrospective policy reconstruction with a September-trained final model and residual expiry-value scenario. Read FINDINGS.md for execution and selection limitations.'),nbformat.v4.new_code_cell(f"from pathlib import Path\nimport pandas as pd,json,gzip,numpy as np\nimport matplotlib.pyplot as plt\nROOT=Path({str(ROOT)!r})\nq=pd.read_csv(ROOT/'actual_trade_quality.csv')\ns=pd.read_csv(ROOT/'smart_comparisons.csv')\ng=pd.read_csv(ROOT/'old_gap_inventory.csv')\nrules={RULES!r}\nq[(q.cohort=='old_active')&q.rule.isin(rules)]"),nbformat.v4.new_code_cell("g[g.rule.isin(rules)]"),nbformat.v4.new_code_cell("s[(s.extra_bps==0)&s.rule.isin(rules)&s.cohort.isin(['period_18_29','period_19_29','full_sep'])][['capital','cohort','rule','new_trades','closed_new_trades','closed_win_rate_pct','closed_profit_factor','marked_pnl','delta_pnl','expired_residuals']]"),nbformat.v4.new_code_cell("fig,axes=plt.subplots(1,2,figsize=(12,4.8),layout='constrained')\nlabels=['Baseline','Premium ST','Underlying ST','Pivot room','Both ST + room']\nfor ax,cap in zip(axes,[100000,5000000]):\n    block=s[(s.capital==cap)&(s.extra_bps==0)&(s.cohort=='period_18_29')].set_index('rule').loc[rules]\n    values=100*block.marked_pnl.to_numpy()/cap\n    ax.barh(labels,values,color=['#64788b','#28786f','#64788b','#b08135','#64788b'])\n    ax.invert_yaxis();ax.axvline(0,color='#384754',lw=1);ax.set_xlabel('Marked P&L / initial capital (%)');ax.set_title(f'Smart ₹{cap/100000:g} L · 18–29 Sep')\n    for i,(value,n) in enumerate(zip(values,block.new_trades)):ax.text(.98,i,f'{value:+.1f}% · {int(n)} entries',transform=ax.get_yaxis_transform(),ha='right',va='center',fontsize=9)\n    ax.set_xlim(min(min(values),0)-10,max(max(values),0)+75);ax.spines[['top','right']].set_visible(False)\nfig.suptitle('Retrospective Smart comparison · fitted model, sampled execution, expiry scenario',fontsize=11)\nfig.savefig(ROOT/'smart_18_29.png',dpi=150,bbox_inches='tight');plt.show()"),nbformat.v4.new_code_cell("validation=json.loads((ROOT/'smart_validation.json').read_text())\nassert validation['unit_checks_passed']==10\nassert all(b['cash_nonnegative'] and b['no_unpriced_daily_positions'] for b in validation['books'].values())\n{'tested_books':len(validation['books']),'unit_checks':10,'out_of_sample':validation['out_of_sample'],'broker_order_calls':validation['broker_order_calls']}"),nbformat.v4.new_markdown_cell('No promotion follows from this reconstruction. Entries are position episodes, not the number of partial-exit fills. Old carried inventory, current actual-entry schedules and retrospective Smart books remain separate.')]
    nb.metadata={'kernelspec':{'name':'python3','display_name':'Python 3','language':'python'},'language_info':{'name':'python'}}
    os.environ['PYTHONPATH']=str(ROOT/'.deps')+os.pathsep+str(ROOT.parent.parent/'supertrend_pivots_20261003/.deps')
    NotebookClient(nb,timeout=180,kernel_name='python3',resources={'metadata':{'path':str(ROOT)}}).execute()
    nbformat.write(nb,ROOT/'analysis.ipynb')
    at=dt.datetime.now(dt.UTC).isoformat()
    def item(id,title,files,definitions,caveats,rows,period):
        return {'id':id,'title':title,'queries':[{'id':id+'-evidence','source':{'label':'Local read-only sources and isolated research books','files':[{'label':f} for f in files],'metricDefinitions':[{'label':k,'definition':v} for k,v in definitions.items()],'caveats':caveats,'executedAt':at,'evidenceFlow':[{'kind':'validation','title':'Reviewed evidence','body':'Period, trade grain and model/valuation limitations retained. Cash accounting and source preservation checked.'}]},'reportingPeriod':period,'capturedAt':at,'columns':list(rows[0]),'rows':rows,'preview':{'kind':'aggregate','note':'Aggregate study comparisons; no raw account identifiers.'}}]}
    items=[item('closed-quality','New-trade count and closed quality',['actual_trade_quality.csv','audit_quality.py','books.json.gz'],{'Trade':'One flat-to-flat episode; pyramids and partial exits are fills within an episode.','Closed quality':'Only positions fully closed by September 18. Profit factor = positive closed P&L / absolute negative closed P&L.'},['Funded actual-entry schedule; zero source booked fees.','Carry-in is excluded; closed-only quality omits open outcomes.'],[{'rule':r,'new_trades':int(a.new_episodes),'fully_closed':int(a.closed_new_episodes),'closed_win_pct':round(a.closed_win_rate_pct,3),'closed_pf':round(a.closed_profit_factor,4),'closed_mean_return_pct':round(a.closed_mean_return_pct,4)} for r,a in old.iterrows()],'September 1–18, 2026'),item('gap-check','Old recorded book September 18–29',['old_backup_audit.json','minute_coverage_by_day.csv','old_gap_inventory.csv'],{'Gap P&L':'Equity change on carried inventory, not a continuously trading strategy.'},['No old MACD fills after September 18 in any of six readable ledgers/backups.','September 29 migrated book is separate.'],g[g.rule.isin(RULES)].round(4).to_dict('records'),'September 18–29 and 19–29, 2026'),item('smart-policy','Reconstructed Smart: counts, quality and filter effect',['smart_comparisons.csv','smart_runtime_audit.json','smart_source_receipt.json','smart_replay.py','smart_validation.json'],{'Smart books':'Isolated production-policy reconstruction at separate ₹1 L and ₹50 L capital.','Marked P&L':'Period equity difference; incomplete/expired positions retain disclosed scenario values without invented fills.'},['Final ranker trained through Oct 1 including September: not out of sample or live results.','Retrospective universe and prior-volume ATM selector; no observed chain OI, spreads or ticks.','Executable quotes require volume and actual quote-age guard; valuation is separate.','Expired residuals use underlying-close intrinsic scenario, not official settlement or cash release.'],s[(s.extra_bps==0)&s.rule.isin(RULES)&s.cohort.isin(['period_18_29','period_19_29','full_sep'])][['capital','cohort','rule','new_trades','closed_new_trades','closed_win_rate_pct','closed_profit_factor','marked_pnl','delta_pnl','expired_residuals']].round(4).to_dict('records'),'September 2026, retrospective policy reconstruction')]
    (ROOT/'reviewed_sources.json').write_text(json.dumps({'schemaVersion':1,'items':items},indent=2,allow_nan=False))
    print('Report, five-cell executed companion and reviewed sources saved.')

if __name__=='__main__':main()
