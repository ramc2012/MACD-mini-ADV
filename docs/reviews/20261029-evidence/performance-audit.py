import json,sqlite3,urllib.request,collections,statistics
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
IST=ZoneInfo('Asia/Kolkata');start=datetime(2026,10,29,tzinfo=IST);end=datetime(2026,10,30,tzinfo=IST)
def dt(x): return datetime.fromisoformat(x.replace('Z','+00:00'))
def today(x): return start<=dt(x)<end
def get(ep): return json.load(urllib.request.urlopen('http://localhost:8100'+ep,timeout=45))
def summary_portfolio(p):
 keys=['initial_capital','cash','market_value','equity','realized_pnl','unrealized_pnl']
 out={k:p.get(k) for k in keys};pos=p.get('positions',[]);out['open_positions']=len(pos)
 out['open_ce']=sum(x['symbol'].endswith('CE') for x in pos);out['open_pe']=sum(x['symbol'].endswith('PE') for x in pos)
 out['largest_open']=sorted([{'symbol':x['symbol'],'market_value':x.get('quantity',0)*x.get('last_price',0),'unrealized_pnl':x.get('unrealized_pnl'),'opened_at':x.get('opened_at')} for x in pos],key=lambda x:abs(x['market_value']),reverse=True)[:5]
 out['open_unrealized_top']=sorted([{'symbol':x['symbol'],'unrealized_pnl':x.get('unrealized_pnl',0)} for x in pos],key=lambda x:x['unrealized_pnl'],reverse=True)[:3]
 out['open_unrealized_bottom']=sorted([{'symbol':x['symbol'],'unrealized_pnl':x.get('unrealized_pnl',0)} for x in pos],key=lambda x:x['unrealized_pnl'])[:3]
 return out
blast=get('/api/blast/snapshot'); main=get('/api/portfolio');lanes=get('/api/lanes')['lanes'];health=get('/api/system/health')
live={'blast':summary_portfolio(blast['portfolio']),'macd':summary_portfolio(main)}
for l in lanes:
 name=l.get('name') or l.get('lane')
 live[name]={k:l.get(k) for k in ['equity','cash','open_positions']}; live[name].update(initial_capital=l.get('capital'),realized_pnl=l.get('realized'),unrealized_pnl=l.get('unrealized'))
cbe=get('/api/shadow-lanes/cbe');cbe_today=[x for x in cbe['outcomes'] if today(x['observed_at'])]
out={'as_of':datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S IST'),'window':{'from':start.strftime('%Y-%m-%d %H:%M:%S IST'),'until_exclusive':end.strftime('%Y-%m-%d %H:%M:%S IST')},'live':live,'macd_api_day_baseline_equity':health.get('day_baseline_equity'),'blast_settings':blast['settings'],'blast_health':blast['health'],'books':{},'cbe':{'checked_at':cbe.get('checked_at'),'readiness':cbe['readiness'],'shadow_only':cbe['shadow_only'],'execution_enabled':cbe['execution_enabled'],'publications':[{k:v for k,v in x.items() if k!='payload'} for x in cbe['publications']],'today_outcomes':cbe_today,'today_outcome_count':len(cbe_today),'today_equal_weight_quote_return':statistics.mean([x['observed_return'] for x in cbe_today]) if cbe_today else None,'today_positive':sum(x['observed_return']>0 for x in cbe_today),'method':cbe['method']}}
active_ids=set()
for name,f in [('blast','blast_trader.sqlite3'),('blast_before_reset','blast_trader.before-hmakama-reset-20261029.sqlite3'),('macd','macd_trader.sqlite3'),('smart_1l','smart_1l.sqlite3'),('smart_50l','smart_50l.sqlite3'),('mp','mp_trader.sqlite3'),('agentic','agentic_trader.sqlite3')]:
 c=sqlite3.connect('file:/app/data/'+f+'?mode=ro',uri=True);c.row_factory=sqlite3.Row
 trades=[json.loads(x['payload']) for x in c.execute('select payload from trades order by timestamp')];td=[x for x in trades if today(x['timestamp'])]
 if name=='blast': active_ids={x['trade_id'] for x in trades}
 closed=[json.loads(x['payload']) for x in c.execute('select payload from closed_positions order by exit_time')];cd=[x for x in closed if today(x['exit_time'])]
 cp=[x for x in cd if x['realized_pnl']>0];cl=[x for x in cd if x['realized_pnl']<0]
 orders=[json.loads(x['payload']) for x in c.execute('select payload from orders order by created_at')];od=[x for x in orders if today(x['created_at'])]
 eq=[dict(x) for x in c.execute('select * from equity_points order by timestamp')];before=[x for x in eq if dt(x['timestamp'])<start];during=[x for x in eq if today(x['timestamp'])]
 base=before[-1] if before else during[0] if during else None
 capital=[dict(x) for x in c.execute('select * from capital_changes')];delta=sum(x['delta'] for x in capital if base and dt(x['timestamp'])>dt(base['timestamp']))
 b={'database':f,'total_fills':len(trades),'first_fill':trades[0]['timestamp'] if trades else None,'last_fill':trades[-1]['timestamp'] if trades else None,'today_fills':len(td),'today_buys':sum(x['side']=='BUY' for x in td),'today_sells':sum(x['side']=='SELL' for x in td),'today_fill_fees':sum(x.get('fees',0) for x in td),'today_buy_notional':sum(x['price']*x['quantity'] for x in td if x['side']=='BUY'),'today_sell_notional':sum(x['price']*x['quantity'] for x in td if x['side']=='SELL'),'today_orders':len(od),'today_order_status':dict(collections.Counter(x['status'] for x in od)),'today_closing_slices':len(cd),'today_closed_position_ids':len(set(x['position_id'] for x in cd)),'today_full_close_slices':sum(not x.get('partial') for x in cd),'today_gross_closed_pnl':sum(x['gross_pnl'] for x in cd),'today_closed_fees':sum(x['fees'] for x in cd),'today_net_closed_pnl':sum(x['realized_pnl'] for x in cd),'today_winners':len(cp),'today_losers':len(cl),'today_breakeven':len(cd)-len(cp)-len(cl),'today_avg_win':statistics.mean([x['realized_pnl'] for x in cp]) if cp else None,'today_avg_loss':statistics.mean([x['realized_pnl'] for x in cl]) if cl else None,'today_profit_factor':sum(x['realized_pnl'] for x in cp)/abs(sum(x['realized_pnl'] for x in cl)) if cl else None,'today_exit_reasons':dict(collections.Counter(x.get('exit_reason') for x in cd)),'today_best_closed':sorted(cd,key=lambda x:x['realized_pnl'],reverse=True)[:3],'today_worst_closed':sorted(cd,key=lambda x:x['realized_pnl'])[:3],'equity_sample_count':len(eq),'today_equity_sample_count':len(during),'last_equity':eq[-1] if eq else None,'day_equity_anchor':base,'day_equity_anchor_is_prior_day':bool(before),'capital_added_after_anchor':delta}
 if name in live and base:
  b['day_equity_change']=live[name]['equity']-base['equity']-delta;b['day_equity_change_pct']=100*b['day_equity_change']/(base['equity']+delta)
 if name=='blast_before_reset':
  old=[x for x in cd if x['exit_trade_id'] not in active_ids];b['old_strategy_today_closed_count']=len(old);b['old_strategy_today_closed_pnl']=sum(x['realized_pnl'] for x in old);b['today_same_hma_fills']=sum(x['trade_id'] in active_ids for x in td);b['today_old_non_hma_fills']=sum(x['trade_id'] not in active_ids for x in td)
 if name.startswith('blast'):
  js=[dict(x) for x in c.execute('select id,at,day,reason,taken,payload from blast_journal where day=?',('2026-10-29',))]
  b['today_journal_count']=len(js);b['today_journal_reasons']=dict(collections.Counter(x['reason'] for x in js));b['today_journal_strategy']=dict(collections.Counter(json.loads(x['payload']).get('strategy','legacy') for x in js));b['today_taken_by_strategy']=dict(collections.Counter(json.loads(x['payload']).get('strategy','legacy') for x in js if x['taken']))
 out['books'][name]=b;c.close()
print(json.dumps(out,indent=2))
