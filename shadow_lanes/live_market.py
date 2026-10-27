"""One consumer of the existing MI ticks and computed flow, shared by both lanes.

No broker socket, historical download or duplicate order-flow tracker. Source
timestamps survive seed/reconnect; a closed market is never labelled live.
"""
import asyncio
from datetime import datetime,time,timedelta
import json,math,os,sqlite3
from pathlib import Path
from urllib.parse import urlparse
import httpx,psycopg2
from . import source


def canonical(symbol):
    return symbol if ':' in symbol else 'NSE:'+symbol+'-EQ'


class LiveMarket:
    def __init__(self):
        self.quotes={};self.flows={};self.allowed=set();self.sequence=0;self.connected=False;self.last_error=None
        self.received=0;self.reused=0;self.last_message_at=None
        self.store=None;self.option_keys=[]
        self.contracts={}
        self.seeded_quotes=0;self.live_received_ticks=0

    def ingest(self,subject,row,now=None):
        now=now or datetime.now(source.IST)
        symbol=row.get('symbol')
        if not symbol or symbol not in self.allowed:return False
        if subject.startswith('md.tick.'):
            at=row.get('exchange_ts_ms');price=row.get('ltp')
            if not isinstance(at,(int,float)) or not isinstance(price,(int,float)) or not math.isfinite(price) or price<=0:return False
            if at>now.timestamp()*1000+5000:return False
            old=self.quotes.get(symbol)
            if old and at<old['exchange_ts_ms']:return False
            self.quotes[symbol]={k:row.get(k) for k in ('symbol','broker','ltp','exchange_ts_ms','received_ts_ms','bid','ask',
                'bid_qty','ask_qty','cum_volume','oi','avg_price','prev_close','day_open','day_high','day_low','iv','delta','gamma','theta','vega')}
            self.received+=1
            if subject.endswith('seed'):self.seeded_quotes+=1
            else:self.live_received_ticks+=1
        else:
            at=row.get('computed_at_ms') or row.get('minute_ts',0)*1000
            if not at or at>now.timestamp()*1000+5000:return False
            if self.flows.get(symbol,{}).get('computed_at_ms',0)>at:return False
            self.flows[symbol]={'symbol':symbol,'source':'mi-features','computed_at_ms':at,
                'session_cvd':row.get('session_cvd'),'session_total_volume':row.get('session_total_volume',row.get('session_volume')),
                'session_classified_volume':row.get('session_classified_volume'),'trades':row.get('trades',row.get('session_trades')),
                'minute_delta':row.get('delta'),'minute_volume':row.get('volume'),
                'window':'producer session state; restart completeness unverified'}
            self.reused+=1
        self.sequence+=1;self.last_message_at=now.isoformat();return True

    def snapshot(self):
        now=datetime.now(source.IST)
        return {'sequence':self.sequence,'connected':self.connected,'received_ticks':self.received,
            'reused_features':self.reused,'last_message_at':self.last_message_at,'error':self.last_error,
            'seeded_quotes':self.seeded_quotes,'live_received_ticks':self.live_received_ticks,
            'cash_open':source.is_session(now.date()) and time(9,15)<=now.time()<time(15,30),
            'market_open':source.is_session(now.date()) and time(9,15)<=now.time()<time(15,40),
            'server_at':now.isoformat(),'quotes':dict(self.quotes),'flow':dict(self.flows),'contracts':dict(self.contracts),'source':'MI shared ticks + mi-features',
            'broker_requests':0,'duplicate_flow_trackers':0}

    def contexts(self,rows):
        out={}
        for r in rows:
            symbol=canonical(r.get('facts',{}).get('underlying') or r.get('symbol') or r['instrument'])
            q=self.quotes.get(symbol,{});f=self.flows.get(symbol,{})
            now=datetime.now(source.IST);age=(now.timestamp()*1000-q['exchange_ts_ms'])/1000 if q else None
            snap=self.snapshot()
            opened=snap['market_open'] if r.get('side') in ('CE','PE') or r['instrument'].startswith(('NSE_FO|','BSE_FO|')) else snap['cash_open']
            result={'stream.status':'fresh' if opened and age is not None and 0<=age<=30 else 'market_closed' if not opened else 'stale_or_missing',
                    'stream.source':'MI shared ticks and mi-features','stream.age_seconds':round(age,2) if age is not None else None}
            if q:result.update({'stream.ltp':q['ltp'],'stream.quote_at':datetime.fromtimestamp(q['exchange_ts_ms']/1000,source.IST).isoformat()})
            if f:
                result.update({'stream.cvd':f.get('session_cvd'),'stream.volume':f.get('session_total_volume'),
                    'stream.feature_at':datetime.fromtimestamp(f['computed_at_ms']/1000,source.IST).isoformat(),
                    'stream.window':f['window']})
            out[r['id']]=result
        return out

    def seed(self):
        from .contracts import hub_url
        with httpx.Client(timeout=8,trust_env=False) as h:
            r=h.get(hub_url()+'/v1/universe');r.raise_for_status();u=r.json()
        self.allowed|={r['canonical'] for group in ('fno_stocks','front_futures','indices') for r in u.get(group,[]) if r.get('canonical')}
        # Reuse the recorder's last actual observation, including when closed.
        c=psycopg2.connect(host=os.environ.get('MI_PG_HOST','nomadcurie_db'),user=os.environ['MI_PG_USER'],
            password=os.environ['MI_PG_PASSWORD'],dbname='market_intelligence',connect_timeout=5)
        c.set_session(readonly=True)
        try:
            with c.cursor() as q:
                q.execute("SET statement_timeout='15s'")
                q.execute('''SELECT DISTINCT ON(symbol) symbol,broker,ts,ltp,bid,ask,bid_qty,ask_qty,cum_volume,oi
                  FROM md_ticks WHERE symbol=ANY(%s) AND ts>now()-interval '5 days' ORDER BY symbol,ts DESC''',(list(self.allowed),))
                for symbol,broker,at,price,bid,ask,bq,aq,volume,oi in q.fetchall():
                    self.ingest('md.tick.seed',dict(symbol=symbol,broker=broker,exchange_ts_ms=int(at.timestamp()*1000),ltp=price,
                        bid=bid,ask=ask,bid_qty=bq,ask_qty=aq,cum_volume=volume,oi=oi))
                q.execute('''SELECT DISTINCT ON(symbol) symbol,minute_ts,session_cvd,session_volume,session_trades,delta,volume,computed_at_ms
                  FROM ft_flow_1m WHERE symbol=ANY(%s) AND minute_ts>now()-interval '5 days' ORDER BY symbol,minute_ts DESC''',(list(self.allowed),))
                for symbol,at,cvd,volume,trades,delta,mvol,computed in q.fetchall():
                    self.ingest('ft.flow.1m.seed',dict(symbol=symbol,minute_ts=int(at.timestamp()),session_cvd=cvd,
                        session_volume=volume,session_trades=trades,delta=delta,volume=mvol,computed_at_ms=computed))
        finally:c.close()

    async def run(self):
        try:await asyncio.to_thread(self.seed)
        except Exception as exc:self.last_error='recorded_seed_'+type(exc).__name__
        target=urlparse(os.environ.get('LANE_LIVE_NATS_URL','nats://market-intelligence-nats-1:4222'))
        if target.hostname not in ('market-intelligence-nats-1','127.0.0.1','host.docker.internal'):raise ValueError('Shared local NATS required')
        while True:
            writer=None
            try:
                reader,writer=await asyncio.open_connection(target.hostname,target.port or 4222)
                await reader.readline()
                writer.write(b'CONNECT {"verbose":false,"name":"macd-lanes-shared-live"}\r\nSUB md.tick.> 1\r\nSUB fts.flow.> 2\r\nSUB ft.flow.1m.> 3\r\nPING\r\n')
                await writer.drain();self.connected=True;self.last_error=None
                while True:
                    line=await asyncio.wait_for(reader.readline(),timeout=150)
                    if not line:raise ConnectionError('Shared bus disconnected')
                    if line.startswith(b'PING'):
                        writer.write(b'PONG\r\n');await writer.drain()
                    elif line.startswith(b'MSG '):
                        parts=line.split();size=int(parts[-1])
                        if size>2_000_000:raise ValueError('Oversized bus message')
                        payload=(await reader.readexactly(size+2))[:-2]
                        try:self.ingest(parts[1].decode(),json.loads(payload))
                        except (ValueError,TypeError,KeyError):continue
            except asyncio.CancelledError:raise
            except Exception as exc:self.connected=False;self.last_error='shared_bus_'+type(exc).__name__;await asyncio.sleep(3)
            finally:
                self.connected=False
                if writer:writer.close();await writer.wait_closed()

    def follow_candidates(self):
        if not self.store:return
        from .contracts import resolve,matches,hub_url
        pubs=self.store.publications('vanguard')
        rows=pubs[0].get('watchlist',[]) if pubs else []
        master=resolve([r['instrument'] for r in rows])
        current={r['instrument']:master[r['instrument']] for r in rows if matches(r,master.get(r['instrument']))}
        self.contracts=current
        fresh_keys=sorted(current)
        added={r['canonical'] for r in current.values()}-self.allowed
        self.allowed.update(added)
        if added:
            c=psycopg2.connect(host=os.environ.get('MI_PG_HOST','nomadcurie_db'),user=os.environ['MI_PG_USER'],
                password=os.environ['MI_PG_PASSWORD'],dbname='market_intelligence',connect_timeout=5)
            c.set_session(readonly=True)
            try:
                with c.cursor() as q:
                    q.execute("SET statement_timeout='8s'")
                    q.execute('''SELECT DISTINCT ON(symbol) symbol,broker,ts,ltp,bid,ask,bid_qty,ask_qty,cum_volume,oi
                      FROM md_ticks WHERE symbol=ANY(%s) AND ts>now()-interval '5 days' ORDER BY symbol,ts DESC''',(list(added),))
                    for sym,broker,at,ltp,bid,ask,bq,aq,vol,oi in q.fetchall():
                        self.ingest('md.tick.seed',dict(symbol=sym,broker=broker,exchange_ts_ms=int(at.timestamp()*1000),
                            ltp=ltp,bid=bid,ask=ask,bid_qty=bq,ask_qty=aq,cum_volume=vol,oi=oi))
            finally:c.close()
        if self.snapshot()['market_open'] and fresh_keys!=self.option_keys:
            with httpx.Client(timeout=8,trust_env=False) as h:
                r=h.post(hub_url()+'/v1/subscriptions',json={'caller':'macd-lane-live','upstox':fresh_keys});r.raise_for_status()
            self.option_keys=fresh_keys

    async def run_follow(self):
        while True:
            try:await asyncio.to_thread(self.follow_candidates)
            except asyncio.CancelledError:raise
            except Exception as exc:self.last_error='option_reference_'+type(exc).__name__
            await asyncio.sleep(30)
