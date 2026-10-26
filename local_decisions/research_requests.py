"""Explicit reconstruction tier. Event-time features; historical availability unverified."""
from datetime import date,datetime,time
import argparse
import json
from pathlib import Path

from .build_corpus import IST,START,END,stamp
from shadow_lanes.vendor.daily_stock_selection import rank_daily


def requests(pg):
    out=[];runs={r['source_session']:r for r in pg['vanguard_swing_watchlist_runs']}
    for row in pg['vanguard_swing_watchlist_items']:
        run=runs.get(row['source_session'])
        if not run or run.get('is_replay'):continue
        at=stamp(run['decision_at'])
        if not date(2026,9,19)<=at.date()<END or stamp(row['source_mark_ts'])>at:continue
        h=int(row['horizon_sessions'])
        if h not in (1,2,3):continue
        out.append(dict(id='vanguard-reconstructed:'+str(row['id']),lane='vanguard',decision_at=at.isoformat(),
            instrument=row['instrument'],symbol=row['symbol'],side=row['option_type'],expiry=row['expiry'],strike=float(row['strike']),
            reference_price=float(row['source_mark']),horizon=h,
            features={k:row[k] for k in ('rank','horizon_sessions','direction_score','contract_score','combined_score','option_volume','option_oi') if row.get(k) is not None}))
    rows=[{**r,'session':date.fromisoformat(r['session']),'available_at':stamp(r['available_at'])} for r in pg['cbe_daily']]
    days=sorted({r['session'] for r in rows if r['underlying']=='NIFTY'})
    for day in days:
        if not START<=day<END:continue
        # No future event-date bars. Ingestion proof is deliberately not asserted in this tier.
        ranked=rank_daily([r for r in rows if r['session']<=day],day,(1,))
        if ranked['status']!='ok':continue
        for row in [r for r in ranked['results'] if r.get('eligible') and r['direction']=='LONG'][:10]:
            out.append(dict(id=f'cbe-reconstructed:{day}:{row["instrument"]}',lane='cbe',
                decision_at=datetime.combine(day,time(15,25),IST).isoformat(),instrument=row['instrument'],symbol=row['instrument'],
                side='LONG',reference_price=row['latest_close'],horizon=1,
                features={k:row[k] for k in ('score','momentum_fast','momentum_slow','relative_momentum','daily_volatility','horizon_sessions')}))
    return out


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('pg');p.add_argument('output');a=p.parse_args()
    rows=requests(json.loads(Path(a.pg).read_text()));Path(a.output).write_text(json.dumps(rows));print('Reconstruction requests',len(rows))
