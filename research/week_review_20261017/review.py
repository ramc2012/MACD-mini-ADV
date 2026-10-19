"""Week 12-16 Oct 2026: what each paper lane made, against the ideal. Read-only.

Lanes: realised P&L by day (closed positions, partial exits aggregated per position), equity change, win rate,
payoff, and CAPTURE: realised return against the best return the position reached while held (max_return_pct).

Ideal benchmarks from the market itself, per session, for every F&O underlying with stored bars:
  the ATM call and put at 09:15 (strike nearest the 09:15 spot, nearest expiry at least one day out), bought at
  the first minute's open:
    random side   -- average of CE and PE, held to 15:25          (what side-blind buying gets)
    perfect side  -- the better of CE and PE, held to 15:25        (perfect direction, no timing)
    perfect exit  -- the better of CE and PE, sold at its day high (perfect direction and timing)
  reported as the median underlying and the top-10 average per day.
"""
import json, re, sqlite3
from collections import defaultdict
import numpy as np, pandas as pd

pd.set_option('display.width', 250); pd.set_option('display.max_columns', 30)
DAYS = ['2026-10-12', '2026-10-13', '2026-10-14', '2026-10-15', '2026-10-16']
IST = 19800
LANES = {'macd_trader': 'MACD', 'smart_50l': 'Smart 50L', 'smart_1l': 'Smart 1L', 'blast_trader': 'Blast',
         'mp_trader': 'Auction desk', 'mp_index_shadow': 'Desk index shadow'}

# ------------------------------------------------------------------ lanes
rows = []
equity = {}
for db, name in LANES.items():
    c = sqlite3.connect(f'file:/data/{db}.sqlite3?mode=ro', uri=True)
    table = 'mp_closed_positions' if db.startswith('mp') else 'closed_positions'
    for (p,) in c.execute(f'select payload from {table}'):
        d = json.loads(p)
        exit_time = d.get('exit_time') or d.get('exit_at') or ''
        if exit_time[:10] < DAYS[0] or exit_time[:10] > DAYS[-1]:
            continue
        rows.append({'lane': name, 'position': d.get('position_id') or d.get('symbol') + d.get('entry_time', ''),
                     'symbol': d['symbol'], 'day': exit_time[:10], 'pnl': d.get('realized_pnl', d.get('pnl', 0)) or 0,
                     'cost': (d.get('entry_price') or 0) * (d.get('quantity') or 0),
                     'ret': d.get('return_pct'), 'max_ret': d.get('max_return_pct'), 'min_ret': d.get('min_return_pct'),
                     'reason': d.get('exit_reason') or d.get('reason'), 'partial': d.get('partial', False)})
    eq = c.execute("select timestamp, equity from equity_points where timestamp >= '2026-10-12' order by timestamp").fetchall()
    before = c.execute("select equity from equity_points where timestamp < '2026-10-12' order by timestamp desc limit 1").fetchone()
    if eq:
        equity[name] = {'start': before[0] if before else eq[0][1], 'end': eq[-1][1]}
L = pd.DataFrame(rows)
# one row per position: P&L summed over partial exits; return on the full cost; best return reached while held
P = L.groupby(['lane', 'position']).agg(day=('day', 'max'), symbol=('symbol', 'first'), pnl=('pnl', 'sum'),
                                        cost=('cost', 'sum'), max_ret=('max_ret', 'max'), min_ret=('min_ret', 'min')).reset_index()
P['ret'] = np.where(P.cost > 0, P.pnl / P.cost * 100, np.nan)
P['side'] = P.symbol.str[-2:]

print('== 1. Lanes, 12-16 Oct (closed positions; P&L in rupees)')
t = P.groupby('lane').agg(positions=('pnl', 'size'), realised=('pnl', 'sum'), win_rate=('pnl', lambda x: (x > 0).mean() * 100),
                          avg_ret=('ret', 'mean'), median_ret=('ret', 'median'),
                          avg_win=('ret', lambda x: x[x > 0].mean()), avg_loss=('ret', lambda x: x[x <= 0].mean()),
                          avg_best_reached=('max_ret', 'mean'))
t['payoff'] = (t.avg_win / -t.avg_loss)
t['capture_%'] = t.avg_ret / t.avg_best_reached * 100
for lane in t.index:
    if lane in equity:
        t.loc[lane, 'equity_change'] = equity[lane]['end'] - equity[lane]['start']
print(t.round(1).to_string())

print('\n== 2. Realised P&L by day (rupees)')
print(P.pivot_table(index='lane', columns='day', values='pnl', aggfunc='sum').round(0).fillna(0).to_string())

print('\n== 3. Giving back: positions that were up at least +30% while held')
up = P[P.max_ret >= 30]
g = up.groupby('lane').agg(n=('ret', 'size'), closed_negative=('ret', lambda x: (x <= 0).sum()),
                           avg_best=('max_ret', 'mean'), avg_realised=('ret', 'mean'))
g['gave_back_pp'] = g.avg_best - g.avg_realised
print(g.round(1).to_string())

print('\n== 4. Side: calls vs puts by lane (avg return %, count)')
print(P.pivot_table(index='lane', columns='side', values='ret', aggfunc=['mean', 'size']).round(1).to_string())

# ------------------------------------------------------------------ ideal benchmarks
MONTHLY = re.compile(r"^(?P<root>[A-Z0-9&\-]+?)(?P<yy>\d{2})(?P<mon>JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(?P<strike>\d+(?:\.\d+)?)(?P<right>CE|PE)$")
WEEKLY = re.compile(r"^(?P<root>[A-Z&\-]+?)(?P<yy>\d{2})(?P<m>[1-9OND])(?P<dd>\d{2})(?P<strike>\d+(?:\.\d+)?)(?P<right>CE|PE)$")
INDEX = {'NIFTY': 'NSE:NIFTY50-INDEX', 'BANKNIFTY': 'NSE:NIFTYBANK-INDEX', 'SENSEX': 'BSE:SENSEX-INDEX', 'MIDCPNIFTY': 'NSE:MIDCPNIFTY-INDEX'}
h = sqlite3.connect('file:/data/historical.sqlite3?mode=ro', uri=True)
bench = []
for day in DAYS:
    open_ts = int(pd.Timestamp(f'{day} 09:15', tz='Asia/Kolkata').timestamp())
    close_ts = int(pd.Timestamp(f'{day} 15:25', tz='Asia/Kolkata').timestamp())
    opts = h.execute("""select symbol, max(expiry) from historical_candles where timeframe_seconds=60 and timestamp>=? and timestamp<?
                        and (symbol like '%CE' or symbol like '%PE') group by symbol""", (open_ts, open_ts + 600)).fetchall()
    chains = defaultdict(list)
    for sym, exp in opts:
        m = MONTHLY.match(sym.split(':', 1)[1]) or WEEKLY.match(sym.split(':', 1)[1])
        if not m or not exp or exp[:10] <= day:
            continue
        chains[(m['root'], m['right'])].append((exp[:10], float(m['strike']), sym))
    for root in {r for r, _ in chains}:
        spot_sym = INDEX.get(root, f'NSE:{root}-EQ')
        sp = h.execute('select open from historical_candles where symbol=? and timeframe_seconds=60 and timestamp>=? order by timestamp limit 1',
                       (spot_sym, open_ts)).fetchone()
        if not sp:
            continue
        rec = {'day': day, 'root': root, 'index': root in INDEX}
        for right in ('CE', 'PE'):
            ch = chains.get((root, right))
            if not ch:
                break
            near = min(e for e, _, _ in ch)
            exp, strike, sym = min((c for c in ch if c[0] == near), key=lambda c: abs(c[1] - sp[0]))
            bars = h.execute('select open, high, close from historical_candles where symbol=? and timeframe_seconds=60 and timestamp>=? and timestamp<=? order by timestamp',
                             (sym, open_ts, close_ts)).fetchall()
            if len(bars) < 30 or bars[0][0] <= 0:
                break
            o = bars[0][0]
            rec[f'{right}_close'] = (bars[-1][2] / o - 1) * 100
            rec[f'{right}_high'] = (max(b[1] for b in bars) / o - 1) * 100
        else:
            bench.append(rec)
B = pd.DataFrame(bench)
B['random_side'] = (B.CE_close + B.PE_close) / 2
B['perfect_side'] = B[['CE_close', 'PE_close']].max(axis=1)
B['perfect_exit'] = np.where(B.CE_close >= B.PE_close, B.CE_high, B.PE_high)
print(f'\n== 5. Ideal benchmarks: ATM option bought at 09:15 ({B.root.nunique()} underlyings, {len(B)} underlying-days), % per trade')
rows = []
for day, g in B.groupby('day'):
    rows.append({'day': day, 'n': len(g), 'random_side_med': g.random_side.median(), 'perfect_side_med': g.perfect_side.median(),
                 'perfect_side_top10': g.perfect_side.nlargest(10).mean(), 'perfect_exit_med': g.perfect_exit.median(),
                 'perfect_exit_top10': g.perfect_exit.nlargest(10).mean(),
                 'calls_won_%': (g.CE_close > g.PE_close).mean() * 100})
print(pd.DataFrame(rows).round(1).to_string(index=False))
print('week:', {k: round(v, 1) for k, v in {'random_side_mean': B.random_side.mean(), 'perfect_side_mean': B.perfect_side.mean(),
                                            'perfect_exit_mean': B.perfect_exit.mean()}.items()})
print('\nlane average return per closed position, same days:', P.groupby('lane').ret.mean().round(1).to_dict())
P.to_csv('/w/positions.csv', index=False); B.to_csv('/w/benchmarks.csv', index=False)
