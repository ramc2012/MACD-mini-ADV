"""Generate a source-backed research note and execute its companion notebook."""
import base64,datetime as dt,hashlib,html,json,os,pathlib,sys
import numpy as np
import pandas as pd
import nbformat
from nbclient import NotebookClient
from study import ROOT

s=pd.read_csv(ROOT/'results.csv');cap=pd.read_csv(ROOT/'capital_results.csv')
quality=json.loads((ROOT/'quality.json').read_text());daily=json.loads((ROOT/'daily_deltas.json').read_text())
manifest=json.loads((ROOT/'source_manifest.json').read_text());books=json.loads((ROOT/'capital_books.json').read_text())
rules=['baseline','premium_st30_10_3_align','spot_st30_10_3_align','spot_st30_7_2_align','spot_daily_room','combined_st_pivot']
names={'baseline':'MACD baseline','premium_st30_10_3_align':'Premium Supertrend (10,3)','spot_st30_10_3_align':'Underlying Supertrend (10,3)','spot_st30_7_2_align':'Underlying Supertrend (7,2)','spot_daily_room':'Pivot headroom','combined_st_pivot':'Joint Supertrend + pivot'}
table=['| Entry rule | Standard mean net return | Smart mean net return |','|---|---:|---:|']
for rule in rules:
    vals=[s[(s.lane==lane)&(s.period=='holdout')&(s.rule==rule)].iloc[0].mean_net_pct for lane in ['standard','smart']]
    table.append(f'| {names[rule]} | {vals[0]:+.2f}% | {vals[1]:+.2f}% |')
cash=['| Lane | Baseline P&L | Joint filter P&L | Joint maximum drawdown | Paired daily t | Corrected p |','|---|---:|---:|---:|---:|---:|']
for lane in ['standard','smart']:
    b=cap[(cap.lane==lane)&(cap.period=='holdout')&(cap.rule=='baseline')].iloc[0]
    j=cap[(cap.lane==lane)&(cap.period=='holdout')&(cap.rule=='combined_st_pivot')].iloc[0]
    cash.append(f'| {lane} | Rs {b.pnl/100000:+.2f} L | Rs {j.pnl/100000:+.2f} L | Rs {j.max_drawdown/100000:.2f} L | {j.daily_difference_t:.2f} | {j.p_holm:.2f} |')
holdout_days=sum(d>=daily['split'] for d in daily['days'])
comparisons=2*(s.rule.nunique()-1)
note=f'''# Independent MACD / Supertrend / pivot experiment

Study date: 3 October 2026. Original application candles, independently written code; earlier research outputs and models were not used. Engine, configuration, paper positions and trading books were not changed.

## Finding

**Conclusions about improving the actual MACD strategies are suspended: the research baseline was not reconciled to them.** The original negative replay does not contradict the positive returns reported in the supplied Claude study or the positive latest MACD paper-book equity. This experiment changed entry timing, event selection, sizing, execution granularity, holding limits, allocation and costs. Its numerical results are retained below only as a simplified-rule diagnostic. The original recommendation to carry the joint filter forward as the preferred addition is withdrawn pending a faithful baseline comparison.

The subsequent [September study](../september_supertrend_pivots_20261003/FINDINGS.md) confirms the positive old ledger and applies the overlays to its actual recorded entry schedule, including funding, carry-in positions and a separate month-end valuation scenario. Use that study for the September question.

## Reconciliation correction, 3 October 2026

The supplied report describes 3,298 signals from 25 August–1 October, minute-by-minute execution, pyramiding/staged exits and ranked allocation with dispersion sizing. It reports current-rules unlimited-cash P&L of +Rs 53.2 L and a ranked Rs 50 L book P&L of +Rs 63.5 L over 19 out-of-sample sessions. Its first-come book instead lost Rs 10.9 L. These are report-provided benchmarks, not independently reproduced figures or September-only live account profit.

This independent replay also happens to contain 3,298 events, but covers 20 August–25 September, uses a 10–25 September holdout and first-come cash allocation, and excludes 2,188 otherwise eligible events with incomplete compared paths. The equal event counts do not establish matching event sets. Five-minute OHLC execution, four initial lots, no pyramiding, a four-session standard-lane cap, omitted live ranker/dispersion sizing, and extra assumed fees make a direct P&L comparison invalid. The supplied report also shows that allocation alone can change the sign of a book result; that does not quantify which differences caused this replay's losses.

At the read-only audit timestamp 2026-10-03 15:48 IST, the actual MACD lane's API and rendered app show initial capital Rs 50 L, equity Rs 51.86297212 L, realised P&L -Rs 1.547352325 L, unrealised P&L +Rs 3.410324445 L, net marked P&L +Rs 1.86297212 L and 61 open positions. Independently replaying all 517 durable fills reproduces API cash, realised P&L, quantities and average entry prices to less than one paisa in aggregate cash/P&L. These are cached marks during market closure, not newly executable quotes.

The active ledger's first fill is 29 September. Its last retained 30 September equity mark is Rs 47.958482485 L (-Rs 2.041517515 L from capital), while the latest book is positive after 1 October. It does not contain a complete September account history. Both smart books have no fills yet; their positive historical benchmark figures must not be described as those books' live realised profit. See `RECONCILIATION.md`, `paper_book_reconciliation.json` and the hashed read-only source capture for scope and evidence.

**Next required validation:** freeze the real entry/sizing/exit rules; reproduce event-level entries, additions, partial exits, cash and equity under a minute-level replay; identify any gaps against actual fills; then compare only Supertrend/pivot changes against that identical baseline. The supplied Claude benchmark needs its event/fill ledger and walk-forward allocation records for exact independent reproduction. Neither a negative simplified replay nor a profitable MACD book establishes the incremental benefit of these indicators.

## Entry comparisons

Chronological holdout: {daily['split']} through {daily['days'][-1]}, {holdout_days} entry days. Returns are net percentages of a four-lot research ticket among executed, retained trades. They are **not** the realised return of the running app. Smart signals that fail the five-minute recheck are not counted as trades.

{chr(10).join(table)}

The joint rule requires bullish premium Supertrend(10,3), underlying Supertrend(10,3) bullish for CE or bearish for PE, and >=0.5 five-minute ATR of headroom to the next prior-day P/R1/R2/S1/S2 level in the trade direction. Supertrend uses the last completed 30-minute bar. This is a fixed rule, not a parameter combination selected after observing returns.

The CE/PE split matters. Under the smart joint rule, calls average {s.query('lane=="smart" and period=="holdout" and rule=="combined_st_pivot"').iloc[0].CE_mean_pct:+.2f}% and puts {s.query('lane=="smart" and period=="holdout" and rule=="combined_st_pivot"').iloc[0].PE_mean_pct:+.2f}%. The apparent improvement therefore needs a different market regime before it can be trusted.

Underlying Supertrend(7,2) is a useful sensitivity finding rather than grounds to tune the live strategy. Its day-level result reduces losses, but the weekly check has only three holdout clusters; the default (10,3) does not replicate that significance. Cash-book and higher-cost checks for (7,2) are included in the result CSVs. There is insufficient independent time coverage to confirm robustness.

## Cash-constrained comparison

Each book starts independently with Rs 50 L, uses tickets up to Rs 1 L, buys whole exchange lots, and rejects a second position in the same contract only after an entry is funded. Partial exits free cash at their simulated time. Equity includes daily marks and the post-entry runoff period. Current lot sizes are an explicit assumption.

{chr(10).join(cash)}

The joint filter helps allocation as well as signal selection: fewer early entries leave cash for later signals, so retained signal averages and funded book results need not move by the same amount. Even the smart joint book has a large drawdown relative to Rs 50 L. Its uncorrected daily-difference t near 2 does not survive the full test family. The no-trade control has zero P&L and zero drawdown.

See `capital_cost_sensitivity.csv` for the same books with 5/25/50 bp slippage per side. Signal averages are also tested at all three costs and remain negative for the joint rule.

## Supertrend and pivot exits

The exit experiments add an adverse premium Supertrend reading, an adverse underlying reading, or loss of the prior-day premium pivot. Baseline risk controls stay active. A lower average loss under a pivot exit did not reliably improve the cash book: cutting a position changes which subsequent signals can be funded. No exit overlay survives corrected holdout testing. The current Supertrend exit overlay is evaluated at a new completed 30-minute close; it is not an intrabar stop at the Supertrend line.

## Walk-forward check

Entry filters are selected only on earlier fully resolved outcomes, with a four-entry-session purge, and tested in subsequent blocks. There are only two test blocks, totalling seven entry days; this is a pilot, not sufficient prospective validation. The chosen standard-rule filter was premium inside weekly CPR; the smart choices were underlying above/below weekly P. The smart selector underperformed its baseline overall. Selection must also compete with no trade, rather than being forced to choose a losing rule.

## Data and limitations

- Source: `historical_candles`, current MACD-mini-ADV Docker data volume, read with `mode=ro`, `query_only` and a single read transaction. {manifest['counts']['raw']:,} one-minute rows were processed across all assets; {quality['counts']['option_series']:,} option contracts were present. The original inventory counted 33,965,356 option rows. Option coverage is 29 July–1 October; underlying coverage is shorter. This is not a ten-year test.
- Fixed five-minute extract SHA256: `{manifest['sha256']}`. The six inspected strategy/indicator/session modules match the deployed source hashes.
- {quality['counts']['eligible']:,} resolved eligible cross opportunities on {len(daily['days'])} entry days, {daily['days'][0]}–{daily['days'][-1]}. New entries stop early enough to allow exits before the data endpoint. Candidate exclusions, missing history, gaps and incomplete bars are retained in `quality.json`.
- {quality['counts']['censored']:,} otherwise eligible signals were excluded because at least one compared exit path was incomplete. This complete-path sample can be biased toward more liquid contracts; results must not be generalised to the excluded contracts. {quality['counts']['missing_expiry_series']} sufficiently long series still lacked usable expiry dates. Dates recovered from sibling strikes have explicit provenance; no calendar expiry dates were guessed.
- Baselines use completed 30-minute zero crosses, four starting lots, staged profits, no pyramiding, and a shared four-session cap. The standard live lane has no such cap. The smart replay has no deployed ranker, dispersion sizing or mixed blast intake. Live intrabar signals, historical ATM selection, exchange spread/impact, corporate actions and changing historical lots are not reconstructed. This tests indicator overlays on controlled MACD rules, not exact deployment parity or actual account performance. The separate blast signal-line strategy is outside this zero-cross experiment.
- Five-minute bars cannot identify the order of intrabar high/low. Stops are processed before favourable targets/partial exits, new trails only apply to later bars, and close-driven exits use the next available open. Missing five-minute execution paths are censored, not interpolated or booked as wins.
- Costs use current 5 bp slippage plus scenario charges of 10 bp buy/20 bp sell and Rs 20 per fill. These charges are disclosed research assumptions, not a statutory calculation. Higher slippage scenarios are supplied.
- Effective sample size is entry days, not 3,298 independent observations. Fixed comparisons use entry-day clustered differences and an ISO-week clustering sensitivity for overlapping trades; cash books use paired daily equity differences. The small holdout and correlated trades do not justify promotion. Longer serial dependence and an independent call-favouring sample remain untested.

## Decision

Keep the engine unchanged. Establish baseline parity before choosing or rejecting any Supertrend/pivot addition to the actual strategy. Joint alignment/headroom and Supertrend(7,2) remain observations about the simplified experiment only; neither is a preferred live candidate on this evidence. The baseline's changed allocation and trade management prevent transferring even its relative filter improvements directly to the deployed strategy.

## Reproduction and definitions

`PLAN.md` contains the frozen design and recorded refinements. `study.py`, `capital.py`, `test_study.py`, the fixed candle extract, manifests, event table and result CSVs contain the inspectable computations. `study.ipynb` is the executed results companion. Seven causal/accounting checks pass. Re-run from this folder using the bundled Python and `PYTHONPATH=.deps`: `python study.py`, `python capital.py`, `python test_study.py`, `python deliver.py`. Data extraction requires access to the original container; saved calculations require only the local extract.

Indicator definitions: [TradingView Supertrend](https://www.tradingview.com/support/solutions/43000634738-supertrend/) and [traditional pivots](https://www.tradingview.com/support/solutions/43000521824-pivot-points-standard/). These sources define calculations and do not establish profitability. CPR is calculated explicitly in `study.py`.
'''
(ROOT/'FINDINGS.md').write_text(note)

nb=nbformat.v4.new_notebook()
nb.cells=[nbformat.v4.new_markdown_cell('# Independent MACD: Supertrend and pivots\n\n## Baseline correction\nConclusions about the actual strategy are suspended. This replay changes the signal sample, execution, sizing, allocation, costs and trade management. Its negative results do not contradict the supplied positive benchmark or the actual MACD paper book. The live book reconciles to +Rs 1.86 L marked profit, including +Rs 3.41 L unrealised and -Rs 1.55 L realised; its durable fill history starts on 29 September. Read `RECONCILIATION.md` and the updated `FINDINGS.md`. Tables below remain simplified-rule diagnostics, not validated recommendations.'),
nbformat.v4.new_markdown_cell('## Context & Methods\nCompare completed 30-minute MACD zero-cross rules with independently calculated Supertrend and prior-period pivots. Read `PLAN.md` and `FINDINGS.md` for rules, selection and censoring.\n\n### Key Assumptions\nFour-lot signal replay, a shared four-session cap, conservative five-minute OHLC execution, current lot inventory, scenario fees, and no deployed ranker or pyramiding. The cash replay uses whole lots up to Rs 1 lakh tickets. No trading settings were changed.'),
nbformat.v4.new_code_cell(f"from pathlib import Path\nimport json, pandas as pd, numpy as np\nimport matplotlib.pyplot as plt\nROOT=Path({str(ROOT)!r})\nsummary=pd.read_csv(ROOT/'results.csv')\ncapital=pd.read_csv(ROOT/'capital_results.csv')\nbooks=json.loads((ROOT/'capital_books.json').read_text())\nplt.rcParams.update({{'font.size':11,'axes.spines.top':False,'axes.spines.right':False,'figure.facecolor':'white'}})"),
nbformat.v4.new_markdown_cell('## Data\nThe immutable local five-minute extract comes from the original application candle table. Missing or incomplete compared paths are excluded and counted. Source fields and hash are in `source_manifest.json`; expiry recovery provenance is separate.'),
nbformat.v4.new_code_cell("quality=json.loads((ROOT/'quality.json').read_text())\npd.DataFrame.from_dict(quality['counts'],orient='index',columns=['Count'])"),
nbformat.v4.new_markdown_cell('## Results\n### Entry filters\nConditional mean returns can improve because fewer signals are traded. They must be compared with total opportunity returns and a funded book, and tested across sides.'),
nbformat.v4.new_code_cell("rules=['baseline','premium_st30_10_3_align','spot_st30_10_3_align','spot_daily_room','combined_st_pivot']\nlabels=['MACD baseline','Premium Supertrend','Underlying Supertrend','Pivot headroom','Joint filter']\nfig,axes=plt.subplots(1,2,figsize=(13,5),sharex=True)\nfor ax,lane in zip(axes,['standard','smart']):\n    block=summary[(summary.lane==lane)&(summary.period=='holdout')].set_index('rule').loc[rules]\n    values=block.mean_net_pct.to_numpy()\n    ax.barh(labels,values,color=['#7d8794']+['#2c5c85']*3+['#b48128'])\n    for i,v in enumerate(values):ax.text(v-.1,i,f'{v:+.2f}%',ha='right',va='center')\n    ax.axvline(0,color='#30343a',lw=1);ax.invert_yaxis();ax.set_xlim(-4.8,.2)\n    ax.set_title(lane.capitalize()+' MACD rules');ax.set_xlabel('Mean net return per executed trade (%)')\nfig.suptitle('Chronological holdout: entry filters',fontweight='bold')\nfig.text(.5,.01,'10–25 September 2026 · 11 entry days · conditional means, not account returns',ha='center',fontsize=10)\nfig.tight_layout(rect=[0,.05,1,.94]);fig.savefig(ROOT/'entry_comparison.png',dpi=150);plt.show()"),
nbformat.v4.new_code_cell("summary[(summary.period=='holdout')&summary.rule.isin(rules)][['lane','rule','trades','mean_net_pct','CE_mean_pct','PE_mean_pct','daily_delta_t','p_holm','weekly_p_holm']]"),
nbformat.v4.new_markdown_cell('### Sequential cash books\nIndependent Rs 50 lakh books, whole lots, tickets up to Rs 1 lakh, simultaneous-contract exclusion after funding, and cash released at partial exits. Daily equity includes the runoff after the last entry.'),
nbformat.v4.new_code_cell("fig,axes=plt.subplots(1,2,figsize=(13,5),sharey=True)\nfor ax,lane in zip(axes,['standard','smart']):\n    for rule,label,color in [('baseline','MACD baseline','#7d8794'),('combined_st_pivot','Joint filter','#b48128')]:\n        marks=books[f'{lane}|holdout|{rule}']['marks']\n        dates=pd.to_datetime([m['day'] for m in marks]);equity=np.array([m['equity'] for m in marks])/100000\n        ax.plot(dates,equity,label=label,color=color,lw=2,marker='o',markersize=3)\n    ax.axhline(50,color='#30343a',ls='--',label='No trade',lw=1)\n    ax.set_title(lane.capitalize()+' MACD rules');ax.set_ylabel('Marked equity (Rs lakh)');ax.set_ylim(0, max(m['equity']/100000 for key,b in books.items() if '|holdout|' in key and (key.endswith('|baseline') or key.endswith('|combined_st_pivot')) for m in b['marks'])*1.08);ax.legend(frameon=False)\n    ticks=dates[[0,len(dates)//2,-1]];ax.set_xticks(ticks);ax.set_xticklabels([d.strftime('%d %b') for d in ticks]);ax.grid(axis='y',alpha=.2)\nfig.suptitle('Holdout cash replay: joint filter versus baseline',fontweight='bold')\nfig.tight_layout(rect=[0,0,1,.94]);fig.savefig(ROOT/'capital_comparison.png',dpi=150);plt.show()"),
nbformat.v4.new_code_cell("capital[(capital.period=='holdout')&capital.rule.isin(['baseline','combined_st_pivot'])][['lane','rule','pnl','max_drawdown','entries','daily_difference_t','p_holm']]"),
nbformat.v4.new_markdown_cell('### Cost and walk-forward checks\nThe forward selector purges four entry sessions and uses only resolved training labels. Only seven entry days are available across two test blocks.'),
nbformat.v4.new_code_cell("pd.read_csv(ROOT/'capital_cost_sensitivity.csv')"),
nbformat.v4.new_code_cell("pd.read_csv(ROOT/'walk_forward.csv')"),
nbformat.v4.new_markdown_cell('## Takeaways\nKeep the engine unchanged. Conclusions about Supertrend/pivots on the actual strategy, including the earlier joint-filter preference, are suspended until baseline parity is established. Match event selection, minute-level trade management, ranking, dispersion sizing and chronological cash accounting before testing one overlay at a time. Negative returns here describe only the simplified replay. Consult `RECONCILIATION.md` and `FINDINGS.md`.')]
kernel=ROOT/'jupyter/kernels/stpivot';kernel.mkdir(parents=True,exist_ok=True)
(kernel/'kernel.json').write_text(json.dumps({'argv':[sys.executable,'-m','ipykernel_launcher','-f','{connection_file}'],'display_name':'Supertrend pivot research','language':'python','env':{'PYTHONPATH':str(ROOT/'.deps')}}))
os.environ['JUPYTER_PATH']=str(ROOT/'jupyter')
nb.metadata.kernelspec={'name':'stpivot','display_name':'Supertrend pivot research','language':'python'}
NotebookClient(nb,timeout=180,kernel_name='stpivot').execute(cwd=str(ROOT))
nbformat.validate(nb);nbformat.write(nb,ROOT/'study.ipynb')
blocks=[]
for cell in nb.cells:
    if cell.cell_type=='markdown':
        lines=cell.source.split('\n');parts=[]
        for line in lines:
            if line.startswith('#'):
                n=len(line)-len(line.lstrip('#'));parts.append(f'<h{n}>{html.escape(line[n:].strip())}</h{n}>')
            elif line:parts.append('<p>'+html.escape(line)+'</p>')
        blocks.append(''.join(parts))
    else:
        for out in cell.get('outputs',[]):
            data=out.get('data',{})
            if 'image/png' in data:blocks.append('<img src="data:image/png;base64,'+data['image/png']+'">')
            elif 'text/html' in data:blocks.append(data['text/html'])
            elif 'text/plain' in data:blocks.append('<pre>'+html.escape(data['text/plain'])+'</pre>')
preview='<!doctype html><meta charset="utf-8"><title>Independent MACD study</title><style>body{font:16px system-ui;max-width:1180px;margin:40px auto;padding:0 30px;color:#253141}h1{font-size:30px}h2{margin-top:35px}img{width:100%;height:auto}table{font-size:13px;border-collapse:collapse;display:block;overflow:auto}td,th{padding:7px;border-bottom:1px solid #dde2e8;text-align:right}pre{font-size:13px;overflow:auto;background:#f3f5f7;padding:16px}p{line-height:1.55}</style>'+''.join(blocks)
(ROOT/'notebook_preview.html').write_text(preview)
print('Saved FINDINGS.md, executed study.ipynb, figures and notebook preview')
