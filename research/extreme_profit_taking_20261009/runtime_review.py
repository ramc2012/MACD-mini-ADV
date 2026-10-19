"""Read-only deployed runtime receipt; run with bundled host Python."""
import datetime
import hashlib
import json
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent
CODE = r'''
import urllib.request,json,pathlib,hashlib,datetime,os
def get(e): return json.load(urllib.request.urlopen('http://localhost:8100'+e,timeout=20))
h=get('/api/system/health');s=get('/api/snapshot');p=get('/api/portfolio');settings=get('/api/settings')
def scalar(d): return {k:v for k,v in d.items() if not isinstance(v,(dict,list))}
out={'captured_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
 'health':{k:h.get(k) for k in ['status','error','uptime_seconds','ticks_total','tick_rate_per_second','last_tick_age_seconds','candles_closed','history_errors','warmup','warmup_report','session_open','observed_at','feed_alive','token_expired','lane_errors','loop_lag_ms','layout']},
 'execution':{'mode':s['execution'].get('mode'),'live_orders_unlocked':s['execution'].get('live_orders_unlocked'),'risk':s['execution'].get('risk')},
 'settings':{k:settings.get(k) for k in ['execution_mode','auto_trade','timeframe_seconds','hard_stop_pct','trailing_activation_pct','trailing_stop_pct','macd_invalidation_exit','macd_invalidation_max_mfe_pct','target_position_notional','slippage_bps']},
 'portfolio':scalar(p), 'position_count':len(p.get('positions',[])),
 'smart_lanes':get('/api/lanes'),
 'safe_environment':{k:os.getenv(k) for k in ['MACD_ENGINE_ROLE','MACD_EXECUTION_MODE','MACD_ALLOW_LIVE_ORDERS','MACD_AUTO_TRADE']},
 'source_hashes':{}}
out['portfolio_keys']=list(p)
for f in ['engine.py','smart_lane.py','execution.py','models.py','repository.py','blast_engine.py','mp_engine.py','portfolio.py']:
 p=pathlib.Path('/app/src/macd_trader')/f
 out['source_hashes'][f]=hashlib.sha256(p.read_bytes()).hexdigest()
print(json.dumps(out))
'''
result = subprocess.run(['docker','compose','exec','-T','engine','python','-c',CODE],check=True,capture_output=True,text=True)
out=json.loads(result.stdout)
source=ROOT.parents[1]/'engine/src/macd_trader'
out['local_source_hashes']={f:hashlib.sha256((source/f).read_bytes()).hexdigest() for f in out['source_hashes']}
out['source_parity']=out['source_hashes']==out['local_source_hashes']
out['note']='Post-close snapshot. Connected feed and warm indicators do not establish next-session readiness. Stored auto_trade=true permits paper entries; live orders remain locked.'
(ROOT/'runtime_receipt.json').write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps(out,indent=2))
