"""Frozen September/October corpora with entry-only inputs and purged date splits.

Historical recorded paper outcomes and reconstructed price-direction proxies are
kept distinguishable. None is evidence of prospective profitability.
"""
from collections import Counter,defaultdict
from datetime import datetime,time,timedelta,timezone,date
import argparse
import hashlib
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo
from local_decisions.policy import SYSTEM,SYSTEM_SHA256

IST=ZoneInfo('Asia/Kolkata')
START=date(2026,9,1);VALID=date(2026,10,12);TEST=date(2026,10,19);END=date(2026,10,24)


def stamp(value):
    d=datetime.fromisoformat(str(value).replace('Z','+00:00'))
    if d.tzinfo is None:raise ValueError('Timestamp has no timezone')
    return d.astimezone(IST)


def payloads(rows):return {r.get('order_id') or r.get('signal_id'):json.loads(r['payload']) for r in rows}


def split(record):
    at=stamp(record['decision_at']);end=stamp(record['label_available_at'])
    if not START<=at.date()<END:return None
    if end<at:raise ValueError('Outcome precedes decision')
    if at.date()<VALID:return 'train' if end.date()<VALID else 'purged'
    if at.date()<TEST:return 'valid' if end.date()<TEST else 'purged'
    return 'test' if end.date()<END else 'purged'


def smart_records(source):
    t=source['tables'];signals=payloads(t.get('signals',[]));orders=payloads(t.get('orders',[]));buys={}
    rejected=Counter();groups=defaultdict(list)
    for row in t.get('trades',[]):
        p=json.loads(row['payload'])
        if p.get('side')=='BUY':buys[(p['symbol'],stamp(p['timestamp']))]=p
    for row in t.get('closed_positions',[]):groups[row['position_id']].append(json.loads(row['payload']))
    result=[]
    for identity,closed in groups.items():
        try:
            first=closed[0];entry=stamp(first['entry_time']);symbol=first['symbol']
            if not START<=entry.date()<END:rejected['outside_period']+=1;continue
            if not symbol.endswith(('CE','PE')):rejected['not_option']+=1;continue
            last=max(closed,key=lambda r:stamp(r['exit_time']))
            if last.get('partial') or last.get('remaining_quantity',0):rejected['still_open_or_partial']+=1;continue
            trade=buys.get((symbol,entry));order=orders.get((trade or {}).get('order_id'));signal=signals.get((order or {}).get('signal_id'))
            if not signal:rejected['missing_exact_signal_link']+=1;continue
            at=stamp(signal['timestamp'])
            if at>entry or (entry-at).total_seconds()>1800:rejected['decision_entry_time_mismatch']+=1;continue
            if not time(9,15)<=at.time()<time(15,30):rejected['outside_regular_session']+=1;continue
            if signal['symbol']!=symbol or signal.get('side')!='BUY':rejected['identity_or_side_mismatch']+=1;continue
            quantity=sum(r['quantity'] for r in closed)
            if quantity!=trade['quantity']:rejected['incomplete_position_close']+=1;continue
            entry_value=first['entry_price']*quantity;exit_value=sum(r['exit_price']*r['quantity'] for r in closed)
            gross=sum(r['gross_pnl'] for r in closed);fees=max(sum(r.get('fees',0) for r in closed),.001*(entry_value+exit_value))
            if not all(math.isfinite(v) for v in (gross,fees,entry_value,exit_value)) or entry_value<=0:
                raise ValueError('Nonfinite outcome')
            features={f'signal_{k}':float(signal[k]) for k in ('macd','signal','histogram','price')}
            if not all(math.isfinite(v) for v in features.values()) or features['signal_price']<=0:raise ValueError('Invalid input')
            features.update(minutes_since_open=(at.hour*60+at.minute-555),source='macd')
            result.append(dict(id='smart:'+signal['signal_id'],lane='smart',decision_at=at.isoformat(),
                label_available_at=stamp(last['exit_time']).isoformat(),instrument=symbol,side=symbol[-2:],
                reference_price=float(signal['price']),features=features,net_return=(gross-fees)/entry_value,
                stress_return=(gross-max(fees,.005*(entry_value+exit_value)))/entry_value,
                label_kind='recorded_paper_close_cost_floor',source=source['source'],selection_bias='Only executed historical signals have observed trade outcomes'))
        except (KeyError,ValueError,TypeError,ZeroDivisionError):rejected['invalid_or_missing_record']+=1
    return result,dict(rejected)


def vanguard_records(pg):
    runs={r['source_session']:r for r in pg['vanguard_swing_watchlist_runs']};result=[];rejected=Counter()
    for row in pg['vanguard_swing_watchlist_items']:
        try:
            run=runs.get(row['source_session'])
            if not run or run.get('is_replay'):rejected['missing_or_replayed_run']+=1;continue
            at=stamp(run['decision_at']);entry=stamp(row['entry_ts']);horizon=int(row['horizon_sessions'])
            if not START<=at.date()<END:continue
            if horizon not in (1,2,3):rejected['incompatible_horizon']+=1;continue
            if stamp(row['source_mark_ts'])>at or entry<at:rejected['future_or_retroactive_entry']+=1;continue
            # Frozen model artifacts created after a purported decision cannot train a causal decision.
            versions=str(run.get('direction_model_version',''))+str(run.get('contract_model_version',''))
            if '20260919' in versions and at.date()<date(2026,9,19):rejected['artifact_postdates_decision']+=1;continue
            key=f'day_{horizon}';end=stamp(row[key+'_ts']);mark=float(row[key+'_mark']);price=float(row['entry_mark'])
            if end<=entry or min(mark,price)<=0:raise ValueError('Invalid outcome')
            features={k:row[k] for k in ('rank','direction_score','contract_score','combined_score','horizon_sessions','option_volume','option_oi') if row.get(k) is not None}
            result.append(dict(id='vanguard:'+str(row['id']),lane='vanguard',decision_at=at.isoformat(),label_available_at=end.isoformat(),
                instrument=row['instrument'],side=row['option_type'],reference_price=float(row['source_mark']),features=features,
                net_return=mark/price-1-max(.01,float(row.get('cost_pct') or 0)),stress_return=mark/price-1-.02,
                label_kind='frozen_watchlist_quote_return_proxy',source='retained_watchlist',
                selection_bias='Selected legacy option cohorts; proxy quote returns, not next-morning L1 fills'))
        except (KeyError,ValueError,TypeError,ZeroDivisionError):rejected['missing_completed_horizon']+=1
    return result,dict(rejected)


def cbe_records(pg):
    from shadow_lanes.vendor.daily_stock_selection import rank_daily
    rows=[{**r,'session':date.fromisoformat(r['session']),'available_at':stamp(r['available_at'])} for r in pg['cbe_daily']]
    by={(r['underlying'],r['session']):r for r in rows};dates=sorted(r['session'] for r in rows if r['underlying']=='NIFTY')
    opens={(r['underlying'],stamp(r['time']).date()):r for r in pg['cbe_next_open']}
    result=[];rejected=Counter()
    for i,day in enumerate(dates[:-1]):
        if not START<=day<END:continue
        at=datetime.combine(day,time(15,25),IST);next_day=dates[i+1]
        # Every historical feature must actually have been available by the decision cutoff.
        available=[r for r in rows if r['session']<=day and r['available_at']<=at]
        ranked=rank_daily(available,day,(1,))
        if ranked['status']!='ok':rejected['missing_point_in_time_breadth_session']+=1;continue
        for row in [r for r in ranked['results'] if r.get('eligible') and r['direction']=='LONG'][:10]:
            symbol=row['instrument'];opening=opens.get((symbol,next_day));closing=by.get((symbol,next_day))
            if not opening or not closing:rejected['missing_next_session_open_or_close']+=1;continue
            entry=float(opening['open'])*1.0005;exit=float(closing['close'])*.9995
            if min(entry,exit)<=0:continue
            cost=.001*(1+exit/entry)
            end=max(stamp(opening['synced_at']),closing['available_at'],datetime.combine(next_day,time(15,15),IST))
            features={k:row[k] for k in ('score','momentum_fast','momentum_slow','relative_momentum','daily_volatility','horizon_sessions')}
            result.append(dict(id=f'cbe:{day}:{symbol}',lane='cbe',decision_at=at.isoformat(),label_available_at=end.isoformat(),
                instrument=symbol,side='LONG',reference_price=row['latest_close'],features=features,
                net_return=exit/entry-1-cost,stress_return=exit/entry-1-cost-.01,
                label_kind='reconstructed_daily_direction_proxy',source='completed_broker_bars',
                selection_bias='Reconstructed current policy; open/close proxy ignores stops, depth and queues; no executed historical claim'))
    return result,dict(rejected)


def message(record):
    verdict='approve' if record['net_return']>.005 else 'reject' if record['net_return']<-.005 else 'wait'
    candidate=dict(id='candidate',instrument=record['instrument'],side=record['side'],reference_price=record['reference_price'],source_at=record['decision_at'],facts=record['features'])
    reason={'approve':'The learned filter favors this input pattern.','reject':'The learned filter does not favor this input pattern.','wait':'The learned filter has insufficient margin.'}[verdict]
    return {'messages':[{'role':'system','content':SYSTEM},{'role':'user','content':json.dumps({'policy':'qwen35-review-v1','lane':record['lane'],'candidates':[candidate]},sort_keys=True,separators=(',',':'))},
                        {'role':'assistant','content':json.dumps({'reviews':[{'id':'candidate','verdict':verdict,'reason':reason}]})}]}


def write_corpus(raw,output):
    raw=Path(raw);output=Path(output);output.mkdir(parents=True,exist_ok=False);pg=json.loads((raw/'pg.json').read_text())
    smart=[];audit={}
    for name in ('macd-legacy','macd'):
        rows,exclusions=smart_records(json.loads((raw/(name+'.json')).read_text()));smart+=rows;audit[name]=exclusions
    unique={r['id']:r for r in smart};vanguard,audit['vanguard']=vanguard_records(pg);cbe,audit['cbe']=cbe_records(pg)
    manifest={'period':'2026-09-01 through 2026-10-23','valid_from':str(VALID),'test_from':str(TEST),
              'system_sha256':SYSTEM_SHA256,
              'sources':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in raw.glob('*.json')},
              'quarantine':audit,'live_promotion_allowed':False,'lanes':{}}
    for lane,records in [('smart',list(unique.values())),('vanguard',vanguard),('cbe',cbe)]:
        folder=output/lane;folder.mkdir();parts=defaultdict(list)
        for r in sorted(records,key=lambda r:(r['decision_at'],r['id'])):
            kind=split(r)
            if kind:parts[kind].append(r)
        for kind in ('train','valid','test','purged'):
            (folder/(kind+'.records.jsonl')).write_text(''.join(json.dumps(r,sort_keys=True)+'\n' for r in parts[kind]))
            if kind!='purged':(folder/(kind+'.jsonl')).write_text(''.join(json.dumps(message(r),sort_keys=True)+'\n' for r in parts[kind]))
        manifest['lanes'][lane]={k:{'rows':len(parts[k]),'positive':sum(r['net_return']>.005 for r in parts[k]),'negative':sum(r['net_return']<-.005 for r in parts[k])} for k in ('train','valid','test','purged')}
        manifest['lanes'][lane]['label_kinds']=dict(Counter(r['label_kind'] for r in records))
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2));return manifest


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--raw',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    print(json.dumps(write_corpus(a.raw,a.output),indent=2))
