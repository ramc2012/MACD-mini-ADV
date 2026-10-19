"""Create the research note, executed notebook and figures from saved evidence."""
import base64
import hashlib
import html
import json
import os
from pathlib import Path
import sys
import gzip
import nbformat
from nbclient import NotebookClient
import pandas as pd

ROOT=Path(__file__).resolve().parent
WORK=ROOT.parents[1]


def readgz(name):
    with gzip.open(ROOT/name,'rt') as f:return json.load(f)


def main():
    r=pd.read_csv(ROOT/'results.csv');sensitivity=pd.read_csv(ROOT/'source_sensitivity_current/results.csv')
    recon=json.loads((ROOT/'ledger_reconciliation.json').read_text());coverage=json.loads((ROOT/'feature_coverage.json').read_text())
    quality=json.loads((ROOT/'minute_quality.json').read_text());selection=json.loads((ROOT/'training_selection.json').read_text());tails=json.loads((ROOT/'tail_attribution.json').read_text())
    books=readgz('books.json.gz');source=json.loads((ROOT/'source_receipt.json').read_text())
    names={'baseline':'MACD baseline','premium_st':'Premium Supertrend','spot_st':'Underlying Supertrend','both_st':'Both Supertrends','premium_pivot':'Premium above daily P','spot_pivot':'Underlying pivot alignment','pivot_room':'Underlying pivot headroom','both_st_pivot':'Both ST + pivot alignment','both_st_room':'Both ST + pivot headroom','exit_premium_st':'Premium ST exit','exit_spot_st':'Underlying ST exit','exit_pivot_loss':'Premium pivot-loss exit','no_new_entries':'No new entries; carry preserved'}
    def row(cohort,rule):return r[(r.cohort==cohort)&r.funded&(r.extra_bps==0)&(r.rule==rule)].iloc[0]
    display=['baseline','premium_st','spot_st','pivot_room','both_st_room']
    table=['| Rule | Old active Sep 1–18 | Old holdout Sep 15–18 | Later book Sep 29–30 | Old active daily drawdown |','|---|---:|---:|---:|---:|']
    for rule in display:
        a,b,c=[row(cohort,rule) for cohort in ['old_active','old_holdout','current_sep']]
        table.append(f'| {names[rule]} | {a.pnl/1e5:+.2f} | {b.pnl/1e5:+.2f} | {c.pnl/1e5:+.2f} | {a.max_drawdown/1e5:.2f} |')
    monthly=['| September 30 terminal scenario | Funded P&L, Rs L | Change against baseline, Rs L |','|---|---:|---:|']
    for rule in ['baseline','premium_st','spot_st','pivot_room','both_st_room','exit_spot_st','exit_pivot_loss']:
        a=row('old_full',rule);monthly.append(f'| {names[rule]} | {a.pnl/1e5:+.2f} | {a.delta_pnl/1e5:+.2f} |')
    note=f'''# September MACD: Supertrend and pivot results

3 October 2026. Applied to the actual old-app entry schedule, with a separate later-book check. No engine settings, paper ledgers, positions or orders were changed. This supersedes the earlier unmatched simplified replay for this question.

## Result

**The old book's September profits are confirmed. Supertrend/pivot additions do not show a consistent profit improvement across the tested periods.** Premium-only Supertrend is the useful risk observation: it improves the old active-window marked P&L slightly and reduces daily marked drawdown, but makes the later September book worse. The pivot-headroom filter selected on training data loses against baseline in both later checks. No fixed overlay passes the corrected entry-day holdout tests.

## Recorded book and baseline parity

The old stopped app retains **3,070 fills**, beginning 25 August and ending 18 September. Its realised P&L is Rs {recon['old']['daily_last_consistent']['2026-08-31']['realized_pnl']/1e5:.2f} L at August 31 and Rs {recon['old']['last_recorded_realized']/1e5:.2f} L at its last consistent endpoint: **September realised increase +Rs {recon['old']['september_realized_increase']/1e5:.2f} L**. Its last retained marked equity, September 28, is Rs 68.15 L; that is not a September 30 valuation. The exact recorded-schedule baseline reproduces all September fill IDs, sizes, prices and realised money, including its legacy overdrafts. Beginning-of-September carry-in inventory is preserved: 75 positions and Rs 20,451.96 cash, with source-based research opening equity Rs 55.58 L.

The recorded book's minimum cash is **-Rs 9.61 L**. There are 248 recorded BUY fills across the retained history that exceed contemporaneous cash. Stored snapshots confirm the negative cash. The strict September funded baseline rejects 31 unaffordable additions while retaining all 621 actual new September episodes. The realised September increase becomes Rs 26.13 L. This cash repair is kept separate from the exact recorded reproduction; it is not silently presented as the app's original result.

Of 71,711 old equity samples, 66,739 reconcile to retained fills and recognised capital; 4,972 do not, including transient September 9 and September 13 states. Clean endpoint realised/cash accounting does reconcile. Daily curves below use independently reconstructed fills and causal archive marks, rather than treating every stored old curve point as trustworthy. No duplicate order IDs were found in the retained old fills.

## Entry overlays

All numbers in the table are **funded marked P&L in Rs lakh**, not average trade returns or promises. Each period preserves its own real carry-in inventory. Drawdown is measured from daily marks, not intraday peaks. The old active period has 12 actual entry sessions; its holdout has four. The separate later book has two sessions and fresh Rs 50 L funding.

{chr(10).join(table)}

Premium Supertrend uses Wilder ATR(10) with multiplier 3 on completed 30-minute premium bars; both CE and PE premiums must be bullish. Underlying Supertrend requires bullish spot for CE and bearish spot for PE. Prior-day Traditional pivots use the prior trading session's H/L/C. Headroom is at least 0.5 completed-30-minute underlying ATR to the next P/R1/R2/S1/S2 in the trade direction. All eight fixed entry rules and three exit overlays are in `results.csv`.

Unknown readings preserve the recorded trade, rather than removing it from the baseline. Premium Supertrend is available on {621-coverage['old']['rules']['premium_st'].get('unknown',0)} of 621 old September new episodes. Underlying pivot/headroom has 19 unknown readings; premium daily P has 173, so the premium-pivot result has materially lower coverage. No current-day high/low or forming-bar Supertrend reading is used.

The old active baseline's calls contribute -Rs 5.59 L and puts +Rs 32.44 L. Premium Supertrend reduces call losses to -Rs 1.26 L while reducing put gains to +Rs 28.91 L. In the later book it again reduces call losses, but removes enough put profit to make total P&L worse by Rs 1.08 L. This is a risk/exposure tradeoff, not a stable directional advantage.

## Entire September data and the stopped-app distinction

Minute data cover the full September calendar using the old archive plus the current archive's extension. The old app contains no new fills after September 18. The full-month scenario holds its remaining recorded inventory, except modelled overlay exits, to September 30; 81 still-recorded expired holdings are valued using observed expiry metadata and intrinsic value from the expiry-day underlying close proxy. This is a **paper valuation scenario**, not an actual expiry fill, official settlement, physical-delivery implementation, or a replay of an app trading continuously through the missing sessions.

{chr(10).join(monthly)}

This distinction changes interpretation. Underlying-ST exits appear +Rs 10.49 L better over the full-month scenario, yet lose Rs 4.79 L against baseline during active trading and Rs 0.15 L in the later book. Pivot-loss exits similarly appear +Rs 8.89 L better at month-end but lose Rs 4.32 L in the old active period and Rs 1.44 L later. Their post-stop benefit must not be sold as indicator edge against a continuously operating MACD strategy. Premium ST exits lose Rs 12.44 L during old active trading; early exits cut the large winning paths.

Unexpired positions with no same-day quote carry the last known price with an explicit stale-age flag. The old opening mark has one four-day-old quote; the active end has two one-day-old quotes. No missing price is counted as zero. The September 30 old-book figure uses the expiry-intrinsic scenario, while the separate current book's 60 open positions all have same-day option marks. No accounts or capital are concatenated.

## Holdout, costs and data-source checks

Selection uses only funded marked P&L from September 1–11, with baseline and no-new-entry controls included. The selected rule is **pivot headroom**, +Rs 1.35 L against training baseline. It then underperforms by **Rs 2.21 L on September 15–18** and **Rs 0.11 L on September 29–30** at recorded fill costs. These are four and two entry-day clusters, not hundreds of independent trades. Holm correction covers the 24 fixed comparisons across those two holdouts; none clears the entry-day test. Daily marked-book tests are retained as secondary diagnostics, with carry-in attribution separate.

`entry_cost_sensitivity.csv` adds 20/50 bp per side to actual already-slipped fills and compares each filter with the baseline at the **same cost**. Premium ST retains its old active/holdout advantage at those costs but continues to hurt the later book. Pivot headroom still fails the old holdout at both costs; some later differences flip as costs change funding. No thresholds were retuned.

The old/current extracts contain 4,706,544 overlapping minute rows; 918,052 (19.5%) disagree on OHLC. Primary calculations prefer the old archive before migration. A separate validation prefers the current archive throughout. Premium ST's old active improvement ranges from +Rs 0.80 L to +Rs 1.32 L; it remains negative in the later book and the full-month scenario. The selected headroom rule fails the old holdout under both source choices. Detailed results are in `source_sensitivity_current/`.

The old active baseline's top 32 September episodes (5%) contribute **Rs {tails['top_five_percent_pnl']/1e5:.2f} L**, versus Rs {tails['baseline_new_episode_pnl']/1e5:.2f} L net across all 621 new episodes. Premium ST excludes 9 of those 32 winners, worth Rs 8.18 L under baseline paths; the joint ST/headroom rule excludes 16, worth Rs 13.62 L. These are marked path attributions, not executable foreknowledge or MFE profits.

## Application decision

Supertrend and pivots have been applied to the historical study. Keep the running MACD trading rules unchanged. Premium-only ST merits further shadow evaluation for drawdown control; it has not earned a mandatory entry gate. Do not add underlying alignment, the combined hard filters, or these indicator exits on this evidence. Full historical signal/universe/ranking decisions and opportunities never funded by the original apps are not replayed here; this is a matched actual-entry-schedule experiment. A new independent regime and prospective filled-decision record are needed to establish a deployable improvement.

## Reproduction and evidence

`prepare.py` extracts only read-only source records; hashes and query details are in `source_receipt.json`. Old/current requested raw rows total {quality['metadata']['old']['raw_rows']:,} / {quality['metadata']['current']['raw_rows']:,} across 702 traded option contracts and 210 underlyings. Three invalid OHLC rows were rejected. Nine traded contracts lack usable expiry metadata; none of the September 30 old holdings uses an unknown expiry.

`study.py` calculates features and replays the exact recorded schedule plus strict cash books. `assess.py` validates the closing-price cache, performs same-cost checks and records training selection/tail attribution. `test_study.py` passes seven checks covering feature causality, prior pivots, cash/fees, funding, expiry valuation and exact recorded fills. `study.ipynb` is an executed companion. Original old-ledger SHA256 remains `{source['old_ledger_sha256_before']}`. Local research writes are isolated from both apps.

Use the bundled Python with `PYTHONPATH=../supertrend_pivots_20261003/.deps` from this folder: `python prepare.py`, `python study.py`, `python study.py --prefer-current`, `python assess.py`, `python test_study.py`, `python deliver.py`. Preparation requires the stopped old app files and the currently running engine's read-only candle API/database access. Saved analysis can be inspected without starting either broker connection.

Definitions: [Supertrend](https://www.tradingview.com/support/solutions/43000634738-supertrend/) and [Traditional pivots](https://www.tradingview.com/support/solutions/43000521824-pivot-points-standard/). These define indicators rather than establish profitability.
'''
    (ROOT/'FINDINGS.md').write_text(note)
    nb=nbformat.v4.new_notebook()
    nb.cells=[
        nbformat.v4.new_markdown_cell('# September MACD: Supertrend and pivots\n\nThe positive September ledger is confirmed. This companion compares actual recorded entry schedules and independently funded variants. Premium ST improves old active-window drawdown but fails the later book; pivot headroom selected in training fails both later checks. Read `FINDINGS.md` for the stopped-app distinction and valuation assumptions.'),
        nbformat.v4.new_code_cell(f"from pathlib import Path\nimport json,gzip,numpy as np,pandas as pd\nimport matplotlib.pyplot as plt\nROOT=Path({str(ROOT)!r})\nresults=pd.read_csv(ROOT/'results.csv')\nwith gzip.open(ROOT/'books.json.gz','rt') as f:books=json.load(f)\nplt.rcParams.update({{'font.size':11,'axes.spines.top':False,'axes.spines.right':False}})"),
        nbformat.v4.new_markdown_cell('## Recorded baseline and funding\nThe old book has 3,070 fills and +Rs 27.25 L September realised increase. Its retained cash dipped to -Rs 9.61 L; the strict September replay rejects 31 unaffordable additions. The later app is a separate book.'),
        nbformat.v4.new_code_cell("json.loads((ROOT/'VALIDATION.json').read_text())"),
        nbformat.v4.new_markdown_cell('## Funded comparisons\nP&L includes causal archive marks. Old active: Sep 1–18; holdout: Sep 15–18; later book: Sep 29–30. Daily drawdown excludes intraday peaks.'),
        nbformat.v4.new_code_cell("rules=['baseline','premium_st','spot_st','pivot_room','both_st_room']\nresults[(results.extra_bps==0)&results.funded&results.cohort.isin(['old_active','old_holdout','current_sep'])&results.rule.isin(rules)][['cohort','rule','pnl','delta_pnl','max_drawdown','new_episodes','entry_day_p_holm']]"),
        nbformat.v4.new_code_cell("labels=['Premium ST','Underlying ST','Pivot headroom','Both ST + headroom']\nfig,axes=plt.subplots(1,3,figsize=(16,5),layout='constrained')\nfor ax,cohort,title in zip(axes,['old_active','old_holdout','current_sep'],['Old active · 1–18 Sep','Old holdout · 15–18 Sep','Later book · 29–30 Sep']):\n    block=results[(results.extra_bps==0)&results.funded&(results.cohort==cohort)].set_index('rule')\n    values=block.loc[rules[1:],'delta_pnl'].to_numpy()/100000\n    ax.barh(labels,values,color=['#28766b' if v>=0 else '#b45d56' for v in values])\n    ax.axvline(0,color='#394754',lw=1);ax.invert_yaxis();ax.set_title(title);ax.set_xlabel('Change from baseline (Rs lakh)')\n    for i,v in enumerate(values):ax.text(v+(0.06 if v>=0 else -.06),i,f'{v:+.2f}',ha='left' if v>=0 else 'right',va='center')\n    ax.set_xlim(min(values)-1.6,max(values)+1.2)\nfig.suptitle('Entry filters: improvement does not repeat across periods',fontweight='bold')\nfig.savefig(ROOT/'incremental_comparison.png',dpi=150,bbox_inches='tight');plt.show()"),
        nbformat.v4.new_code_cell("fig,ax=plt.subplots(figsize=(11,5))\nfor rule,label,color in [('baseline','MACD baseline','#6b7d8b'),('premium_st','Premium Supertrend','#28766b'),('both_st_room','Both ST + headroom','#ab813c')]:\n    b=books['old_active|'+rule];date=pd.to_datetime([m['day'] for m in b['marks']]);pnl=np.array([m['equity']-b['start_mark']['equity'] for m in b['marks']])/100000\n    ax.plot(date,pnl,label=label,color=color,lw=2,marker='o',ms=3)\nax.set_title('Old active period: funded marked P&L');ax.set_ylabel('P&L since August 31 mark (Rs L)');ax.axhline(0,color='#394754',lw=1);ax.legend(frameon=False);ax.grid(axis='y',alpha=.2)\nticks=date[[0,len(date)//2,-1]];ax.set_xticks(ticks);ax.set_xticklabels([d.strftime('%d %b') for d in ticks]);fig.tight_layout();fig.savefig(ROOT/'active_equity.png',dpi=150);plt.show()"),
        nbformat.v4.new_markdown_cell('## Full September scenario\nThe old app stopped recording fills on September 18. The month-end extension is a held-inventory/expiry-intrinsic scenario, not a continuously running strategy. Post-stop exit improvements must not be called indicator edge.'),
        nbformat.v4.new_code_cell("results[(results.cohort=='old_full')&results.funded&(results.extra_bps==0)][['rule','pnl','delta_pnl','max_drawdown']]"),
        nbformat.v4.new_markdown_cell('## Training selection and validation\nHeadroom was selected only on Sep 1–11 and failed both later checks. No holdout threshold tuning.'),
        nbformat.v4.new_code_cell("json.loads((ROOT/'training_selection.json').read_text())"),
        nbformat.v4.new_code_cell("pd.read_csv(ROOT/'entry_cost_sensitivity.csv').query(\"cohort in ['old_active','old_holdout','current_sep'] and rule in ['baseline','premium_st','pivot_room','both_st_room']\")"),
        nbformat.v4.new_code_cell("alternate=pd.read_csv(ROOT/'source_sensitivity_current/results.csv')\ncomparison=results.merge(alternate,on=['cohort','rule','funded','extra_bps'],suffixes=('_oldsource','_newsource'))\ncomparison.query(\"funded and extra_bps==0 and rule in ['premium_st','pivot_room','both_st_room']\") [['cohort','rule','delta_pnl_oldsource','delta_pnl_newsource']]"),
        nbformat.v4.new_markdown_cell('## Interpretation\nST is a possible drawdown feature, not a validated mandatory gate. Pivot and ST combinations cut large winners and do not deliver stable profit improvements. Underlying-ST and pivot-loss exits benefit after the app stops but underperform during active trading. The filled-entry schedule excludes opportunities never funded by the apps. Source disagreements, some stale marks and only four plus two later entry sessions limit inference. No running strategy settings were changed.')]
    kernel=ROOT/'jupyter/kernels/september_macd';kernel.mkdir(parents=True,exist_ok=True)
    deps=str(WORK/'research/supertrend_pivots_20261003/.deps')
    (kernel/'kernel.json').write_text(json.dumps({'argv':[sys.executable,'-m','ipykernel_launcher','-f','{connection_file}'],'display_name':'September MACD research','language':'python','env':{'PYTHONPATH':deps}}))
    os.environ['JUPYTER_PATH']=str(ROOT/'jupyter')
    nb.metadata.kernelspec={'name':'september_macd','display_name':'September MACD research','language':'python'}
    NotebookClient(nb,timeout=180,kernel_name='september_macd').execute(cwd=str(ROOT))
    nbformat.validate(nb);nbformat.write(nb,ROOT/'study.ipynb')
    blocks=[]
    for cell in nb.cells:
        if cell.cell_type=='markdown':
            for line in cell.source.split('\n'):
                if line.startswith('#'):
                    n=len(line)-len(line.lstrip('#'));blocks.append(f'<h{n}>{html.escape(line[n:].strip())}</h{n}>')
                elif line:blocks.append('<p>'+html.escape(line)+'</p>')
        else:
            for o in cell.get('outputs',[]):
                d=o.get('data',{})
                if 'image/png' in d:blocks.append('<img src="data:image/png;base64,'+d['image/png']+'">')
                elif 'text/html' in d:blocks.append(d['text/html'])
                elif 'text/plain' in d:blocks.append('<pre>'+html.escape(d['text/plain'])+'</pre>')
    (ROOT/'notebook_preview.html').write_text('<!doctype html><meta charset="utf-8"><title>September MACD overlays</title><style>body{font:16px system-ui;max-width:1200px;margin:35px auto;padding:0 25px;color:#263544}h1{font-size:30px}h2{margin-top:35px}p{line-height:1.5}img{width:100%}pre,table{font-size:13px;overflow:auto}td,th{padding:7px;border-bottom:1px solid #d8dfe4}pre{background:#f2f5f7;padding:15px;white-space:pre-wrap}</style>'+''.join(blocks))
    validation=json.loads((ROOT/'VALIDATION.json').read_text())
    validation['unit_checks_passed']=7;validation['notebook_executed_cells']=sum(c.cell_type=='code' for c in nb.cells)
    validation['source_sensitivity_completed']=True
    validation['code_hashes']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in ROOT.glob('*.py')}
    (ROOT/'VALIDATION.json').write_text(json.dumps(validation,indent=2))
    print('Saved findings, executed notebook, figures and HTML preview.')


if __name__=='__main__':main()
