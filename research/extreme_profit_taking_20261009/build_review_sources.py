"""Build the answer's source receipt from frozen, independently checked files."""
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
OUT = Path('/Users/ramachandran/.codex/visualizations/2026/10/09/01a12164-c033-7fc3-ac5d-b5fa2f121649')
receipt = json.loads((OUT / 'trail-sources.json').read_text())
checked = json.loads((BASE / 'root_aggregate_check.json').read_text())
paired_rows = []
lane_rows = []
for arm, result in checked.items():
    paired_rows.append(dict(activation_pct=int(arm), positions=result['n'],
        actual_net_inr=round(result['actual_net_fixed20_per_leg'], 2),
        alternative_net_inr=round(result['overlay_net_fixed20_per_leg'], 2),
        difference_inr=round(result['delta_inr'], 2),
        improved=result['better'], worsened=result['worse']))
    lane_rows.extend(dict(activation_pct=int(arm), lane=lane,
        difference_inr=round(value, 2)) for lane, value in result['by_lane'].items())

receipt['items'].append({
    'id': 'paired-exits',
    'title': 'Historical exit comparison: same 145 completed positions',
    'queries': [{
        'id': 'frozen-paired-aggregate',
        'source': {
            'label': 'Frozen read-only paper trades and exact-contract minute candles',
            'files': [{'label': name} for name in (
                'data_analysis/evidence.json — frozen inputs',
                'data_analysis/comparison.csv — paired position-level results',
                'data_analysis/FINDINGS.md — methodology and exclusions',
                'code_review/DATA_AUDIT.md — independent accounting audit',
                'root_aggregate_check.json — independent aggregate sums')],
            'metricDefinitions': [
                {'name': 'Alternative exit', 'definition': 'After +50% or +100% completed-close gain, a later close giving back 25% of favorable premium gain triggers the next eligible minute-open sale of residual whole lots. Earlier actual partial sales remain included.'},
                {'name': 'Net P&L', 'definition': 'Sale proceeds less actual entry cost and INR20 per entry/sale order. Actual fill slippage is retained; a new modeled exit has 5 bps adverse slippage.'},
                {'name': 'Difference', 'definition': 'Alternative net P&L minus actual-exit net P&L for the identical selected positions; this is not a portfolio return.'}],
            'caveats': [
                'Both alternatives remain net negative. Main MACD differences are only INR34,465 and INR11,912; one Smart trade contributes all its gain.',
                'The +50% arm forgoes INR173,696 across five eventual winners. Tighter protection can cut large winners.',
                'Missing paths, open positions, scaled-in positions, administrative expiry and three zero-volume hypothetical fill minutes are excluded.',
                'Complete-path and full-minute-volume eligibility are retrospective filters. Positive minute volume cannot establish an executable opening bid or sufficient size.',
                'No untouched holdout, full portfolio replay, re-entry/cash effects, bid/ask depth or comprehensive statutory costs. Parameters are exploratory.'],
            'evidenceFlow': [
                {'kind': 'file', 'title': 'Frozen inputs', 'detail': 'Readonly SQLite and HTTP collection captured 9 October 2026, 21:40:22 IST; source hashes are saved.'},
                {'kind': 'calculation', 'title': 'Causal minute calculation', 'detail': 'Only completed closes enter the running peak; trigger fills occur on the next eligible minute, by actual final exit time. Prior partial cash and residual quantities reconcile.'},
                {'kind': 'validation', 'title': 'Independent checks', 'detail': '3,377 accounting and timestamp comparisons passed; root separately summed the paired CSV without the analysis helpers.'}]
        },
        'rows': paired_rows,
        'columns': ['activation_pct', 'positions', 'actual_net_inr', 'alternative_net_inr', 'difference_inr', 'improved', 'worsened'],
        'capturedAt': '2026-10-09T16:10:22+00:00',
        'methods': [{'language': 'python', 'code': "rows = list(csv.DictReader(open('comparison.csv')))\nfor arm in ('50', '100'):\n    group = [r for r in rows if r['arm_pct'] == arm]\n    actual = math.fsum(float(r['actual_net_fixed20_per_leg']) for r in group)\n    alternative = math.fsum(float(r['overlay_net_fixed20_per_leg']) for r in group)\n    difference = math.fsum(float(r['delta_inr']) for r in group)"}]
    }, {
        'id': 'lane-contributions',
        'source': {'label': 'Paired CSV aggregated separately by lane',
            'files': [{'label': 'data_analysis/comparison.csv'}],
            'caveats': ['Different lane capitals and concentrated trades are not independent equal-sized replications.']},
        'rows': lane_rows,
        'columns': ['activation_pct', 'lane', 'difference_inr'],
        'capturedAt': '2026-10-09T16:10:22+00:00'
    }]
})
receipt['items'].append({
    'id': 'exit-state-review', 'title': 'Reproduced exit-state defects',
    'queries': [{
        'id': 'disposable-book-reproductions',
        'source': {
            'label': 'Reviewed deployed-matching Python source and disposable-book reproductions',
            'files': [{'label': name} for name in (
                'code_review/REVIEW.md', 'code_review/reproduce_exit_review.py',
                'code_review/reproduction_results.jsonl', 'data_analysis/source_hashes.json')],
            'caveats': ['These are deterministic implementation reproductions, not estimates of historical incident frequency.',
                'Production books/settings were preserved; the broker stub rejects attempted broker orders.']},
        'rows': [
            {'priority': 'P1', 'component': 'Smart restore', 'observed': 'Persisted exit stage 1 restores as 0, so an unchanged quote repeats stage 1.'},
            {'priority': 'P1', 'component': 'Blast checkpoint', 'observed': 'An unclean restart loses the intratrade peak and armed trailing stop.'},
            {'priority': 'P2', 'component': 'Peak restoration', 'observed': 'A valuation-only high becomes an execution peak at restart and tightens the trail.'}],
        'columns': ['priority', 'component', 'observed'],
        'methods': [{'language': 'python', 'code': 'Disposable repositories and a rejecting broker stub exercise deployed-matching restore, maintenance and tick paths. See reproduce_exit_review.py for full reproduction.'}]
    }]
})
(OUT / 'review-sources.json').write_text(json.dumps(receipt, indent=2) + '\n')
print('Saved receipt: three source cards, frozen comparison and reproduced defects.')
