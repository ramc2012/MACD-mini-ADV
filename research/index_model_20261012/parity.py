import json, sqlite3, sys
sys.path.insert(0, '/tmp/newsrc')
from macd_trader import index_scorecard as sc
db = sqlite3.connect('/tmp/scratch.sqlite3')
db.execute("ATTACH DATABASE 'file:/app/data/historical.sqlite3?mode=ro' AS live")
sc.ensure_schema(db)
seed = json.load(open('/tmp/seed.json'))
CUT = '2026-08-31'
with db:
    db.executemany("INSERT OR REPLACE INTO agent_index_days (underlying, day, open, high, low, close, features, ce_path, ce_r, pe_path, pe_r, ce_sel, pe_sel, source, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   [(r['underlying'], r['day'], r['open'], r['high'], r['low'], r['close'], json.dumps(r.get('features')) if r.get('features') else None,
                     r.get('ce_path'), r.get('ce_r'), r.get('pe_path'), r.get('pe_r'), r.get('ce_sel'), r.get('pe_sel'), 'fyers-research', 'seed') for r in seed if r['day'] <= CUT])
print('update', sc.update_history(db, '2026-10-12'))
ref = {(r['underlying'], r['day']): r for r in seed}
keys = ['u_ret_1d', 'u_ret_5d', 'u_ret_20d', 'u_range20_pos', 'gap', 'last_hour', 'move_atr', 'rsi_d', 'rsi_h', 'trend_d', 'trend_w', 'trend_h']
diffs = {k: [] for k in keys}; same_side = {'ce': 0, 'pe': 0, 'n': 0}
model = {'ce': ['last_hour', 'rsi_h_follow', 'gap_follow'], 'pe': ['last_hour', 'move_follow'], 'min_net': 2}
agree = n = 0
for und, day, f, cp, pp in db.execute("SELECT underlying, day, features, ce_path, pe_path FROM agent_index_days WHERE day > ? AND features IS NOT NULL", (CUT,)):
    r = ref.get((und, day))
    if not r or not r.get('features'): continue
    a, b = json.loads(f), r['features']
    for k in keys: diffs[k].append(abs(a[k] - b[k]))
    n += 1; agree += sc.decide(a, model)['side'] == sc.decide(b, model)['side']
    if cp: same_side['n'] += 1; same_side['ce'] += cp == r['ce_path']; same_side['pe'] += pp == r['pe_path']
print('compared days', n, '| scorecard decision identical on', agree)
for k, v in diffs.items(): print(f'  {k:14s} median |diff| {sorted(v)[len(v)//2]:.5f}  max {max(v):.5f}')
print('labels same path: CE', same_side['ce'], 'PE', same_side['pe'], 'of', same_side['n'])
print('selection on seed+app history:', {k: v for k, v in sc.select(db, 2026).items() if k in ('ce', 'pe', 'years', 'rows')})
