"""Market-data-only hub adapter and independent marking/exit and Qwen-entry loops."""
from __future__ import annotations

import asyncio
from datetime import datetime,time,timedelta,timezone,date
import json
import math
import os
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from psycopg2.extras import RealDictCursor

from .book import Book,fresh

IST=ZoneInfo('Asia/Kolkata')


def is_session(day):
    from shadow_lanes.source import is_session as check
    return check(day)


def next_session(day,count=1):
    while count:
        day+=timedelta(days=1)
        if is_session(day):count-=1
    return day


def session_open(now):
    return is_session(now.date()) and time(9,15)<=now.time()<time(15,30)


def parse_quotes(body,requested):
    out={}
    if body.get('status')!='success':return out
    for row in (body.get('data') or {}).values():
        identity=row.get('instrument_token')
        if identity not in requested:continue
        try:
            stamp=row.get('timestamp') or row.get('last_trade_time')
            if str(stamp).replace('.','',1).isdigit():at=datetime.fromtimestamp(float(stamp)/1000,IST)
            else:at=datetime.fromisoformat(str(stamp).replace('Z','+00:00')).astimezone(IST) if '+' in str(stamp) or str(stamp).endswith('Z') else None
            if at is None:continue
            bid=(row.get('depth',{}).get('buy') or [{}])[0];ask=(row.get('depth',{}).get('sell') or [{}])[0]
            out[identity]={'instrument':identity,'at':at.isoformat(),'bid':float(bid.get('price') or 0),
                           'ask':float(ask.get('price') or 0),'bid_qty':int(bid.get('quantity') or 0),
                           'ask_qty':int(ask.get('quantity') or 0),'volume':int(row.get('volume') or 0)}
        except (ValueError,TypeError,OverflowError):continue
    return out


class MarketData:
    """Only market-data requests and subscription registration; no order API methods."""
    def __init__(self):
        self.hub=os.environ.get('PAPER_HUB_URL','http://hub:8400').rstrip('/')
        if self.hub not in ('http://hub:8400','http://host.docker.internal:8400','http://127.0.0.1:8400'):
            raise ValueError('Paper lane data must use the existing local hub')
        self.last_subscriptions=None

    def quotes(self,instruments):
        keys=sorted(set(instruments))
        if not keys and self.last_subscriptions is None:return {}
        if len(keys)>100:raise ValueError('Paper quote universe exceeds its bounded budget')
        with httpx.Client(timeout=12,trust_env=False,follow_redirects=False) as c:
            if keys!=self.last_subscriptions:
                response=c.post(self.hub+'/v1/subscriptions',json={'caller':'macd-paper-lanes','upstox':keys})
                response.raise_for_status();self.last_subscriptions=keys
            if not keys:return {}
            response=c.post(self.hub+'/v1/request',json={'broker':'upstox','endpoint':'market-quote/quotes',
                'query':{'instrument_key':','.join(keys)},'caller':'macd-paper-lanes','priority':'critical','ttl_ms':1000})
            response.raise_for_status();return parse_quotes(response.json(),set(keys))

    def metadata(self,lane,rows):
        from shadow_lanes import source
        c=source.connect(readonly=True)
        try:
            with c.cursor(cursor_factory=RealDictCursor) as cur:
                if lane=='vanguard':
                    cur.execute('''SELECT instrument_key,underlying AS symbol,expiry,lot_size,tick_size
                      FROM fo_contract_catalog WHERE instrument_key=ANY(%s)''',([r['instrument'] for r in rows],))
                    return {r['instrument_key']:{'instrument':r['instrument_key'],'symbol':r['symbol'],
                            'expiry':str(r['expiry']),'lot_size':r['lot_size'],'tick':float(r['tick_size'] or 0)} for r in cur.fetchall()}
                cur.execute('''SELECT u.symbol,u.spot_instrument_key,s.sector20 FROM fo_underlying_catalog u
                  LEFT JOIN sector_taxonomy s ON s.symbol=u.symbol WHERE u.symbol=ANY(%s) AND u.kind='STOCK' AND u.fno_active IS TRUE''',
                  ([r.get('symbol') or r['instrument'] for r in rows],))
                stocks=cur.fetchall()
                with httpx.Client(timeout=8,trust_env=False,follow_redirects=False) as client:
                    response=client.post(self.hub+'/v1/resolve/batch',json={'upstox':[r['spot_instrument_key'] for r in stocks]})
                    response.raise_for_status();master=response.json()
                return {r['symbol']:{'instrument':r['spot_instrument_key'],'symbol':r['symbol'],'lot_size':1,
                                    'tick':float(master[r['spot_instrument_key']]['tick_size']), 'sector':r['sector20']}
                        for r in stocks if master.get(r['spot_instrument_key']) and (master[r['spot_instrument_key']].get('tick_size') or 0)>0}
        finally:c.close()


class Manager:
    def __init__(self,folder=None,capital=None,now=None,market=None):
        now=(now or datetime.now(IST)).astimezone(IST)
        folder=Path(folder or os.environ.get('PAPER_LANES_DIR','/runtime/paper_lanes'))
        capital=float(capital if capital is not None else os.environ.get('PAPER_LANE_CAPITAL','1000000'))
        self.books={lane:Book(folder/(lane+'.sqlite3'),lane,capital,now) for lane in ('vanguard','cbe')}
        self.market=market or MarketData();self.last_quotes={};self.last_error=None;self.last_poll=None

    def offer(self,publication,metadata=None):
        lane=publication['lane'];book=self.books[lane]
        if publication.get('paper_account_id')!=book.account_id:return 0
        decided=datetime.fromisoformat(publication['decision_at']).astimezone(IST)
        day=date.fromisoformat(publication['source_session']);entry_day=next_session(day)
        rows=publication['watchlist'];metadata=self.market.metadata(lane,rows) if metadata is None else metadata
        prepared=[]
        for candidate in rows:
            key=candidate['instrument'] if lane=='vanguard' else candidate.get('symbol') or candidate['instrument']
            meta=metadata.get(key)
            if not meta:continue
            horizon=int(candidate.get('horizon_sessions') or 1)
            if not 1<=horizon<=3:continue
            due_day=next_session(day,horizon)
            expiry=meta.get('expiry')
            if lane=='vanguard' and (not expiry or date.fromisoformat(expiry)<due_day):continue
            prepared.append({**meta,'sector':candidate.get('sector20') or candidate.get('sector') or meta.get('sector') or 'UNKNOWN',
                             'candidate':candidate,'exit_due':datetime.combine(due_day,time(15,10) if expiry==str(due_day) else time(15,15),IST)})
        inserted=book.enqueue(publication['id'],prepared,decided,str(day),datetime.combine(entry_day,time(9,20),IST),
                              datetime.combine(entry_day,time(9,55),IST),publication['paper_account_id'])
        if len(prepared)<len(rows):
            with book.transaction() as db:
                book.event(db,datetime.now(IST),'METADATA_BLOCKED',None,{'cohort':publication['id'],'eligible':len(rows),'exact_metadata':len(prepared)})
        return inserted

    def instruments(self):
        keys=set()
        for book in self.books.values():
            with book.db() as db:
                keys.update(r[0] for r in db.execute("SELECT instrument FROM orders WHERE state IN ('PENDING_AI','APPROVED','FILLED')"))
        return keys

    def poll(self,now=None):
        now=(now or datetime.now(IST)).astimezone(IST);opened=session_open(now)
        if opened:
            try:self.last_quotes=self.market.quotes(self.instruments());self.last_error=None
            except Exception as e:self.last_quotes={};self.last_error=f'Market quote request failed: {type(e).__name__}'
        else:self.last_quotes={}
        settlement=datetime.combine(next_session(now.date()),time(9,15),IST)
        for book in self.books.values():
            try:book.cycle(self.last_quotes,now,opened,settlement)
            except Exception as e:self.last_error=f'{book.lane} paper cycle failed: {type(e).__name__}'
        self.last_poll=now.isoformat()

    def review_once(self):
        if not session_open(datetime.now(IST)):return
        for lane,book in self.books.items():
            now=datetime.now(IST);orders=book.review_candidates(dict(self.last_quotes),now,limit=1)
            if not orders:continue
            candidates=[]
            for o in orders:
                row=json.loads(o['payload']);facts={'phase':'fresh_entry_review','source_session':o['source_session'],
                    'underlying':row.get('symbol') or row.get('instrument'),'strike':row.get('strike'),'expiry':row.get('expiry'),
                    'rank':row.get('rank') or row.get('side_rank'),'planned_quantity':o['planned_quantity'],
                    'spread_pct':(o['quote']['ask']/o['quote']['bid']-1)*100,
                    'position_limit_pct':book.terms['position_fraction']*100,
                    'hard_stop_pct':book.terms['hard_stop']*100,'target_pct':book.terms['target']*100,
                    'horizon_sessions':row.get('horizon_sessions',1),'exit_due':o['exit_due']}
                for key in ('direction_score','contract_score','combined_score','momentum_fast','momentum_slow','relative_momentum','score',
                            'expected_net_return','option_volume','option_oi','conviction'):
                    value=row.get(key)
                    if isinstance(value,(int,float)) and math.isfinite(value):facts[key]=value
                candidates.append({'id':o['id'],'instrument':o['instrument'],'side':row.get('option_type') or 'LONG',
                                   'reference_price':o['quote']['ask'],'source_at':o['quote']['at'],'facts':facts})
            try:
                url=os.environ.get('LOCAL_DECISION_URL','http://local-decisions:8435')
                if url not in ('http://local-decisions:8435','http://127.0.0.1:8435'):raise ValueError('Invalid local model endpoint')
                r=httpx.post(url+'/review',json={'lane':lane,'snapshot_at':now.isoformat(),'candidates':candidates},
                             headers={'X-Macd-Token':os.environ.get('MACD_API_TOKEN','')},timeout=80,trust_env=False,follow_redirects=False)
                r.raise_for_status();book.apply_reviews(orders,r.json(),datetime.now(IST))
            except Exception:book.review_failed(orders,datetime.now(IST))

    def snapshot(self,lane):
        return {**self.books[lane].snapshot(datetime.now(IST)),'market_error':self.last_error,'market_checked_at':self.last_poll,
                'model_required':True,'live_orders_possible':False,'legacy_history_preserved':True}

    async def run_marks(self,store):
        while True:
            try:await asyncio.to_thread(self.poll)
            except asyncio.CancelledError:raise
            except Exception as e:self.last_error=f'Paper marking failed: {type(e).__name__}'
            await asyncio.sleep(5)

    async def run_reconcile(self,store):
        while True:
            try:
                # Reconcile only publications explicitly tied to this new account epoch.
                for publication in store.publications():
                    book=self.books.get(publication['lane'])
                    if book and publication.get('paper_account_id')==book.account_id:
                        with book.db() as db:exists=db.execute('SELECT 1 FROM cohorts WHERE id=? LIMIT 1',(publication['id'],)).fetchone()
                        if not exists:await asyncio.to_thread(self.offer,publication)
            except asyncio.CancelledError:raise
            except Exception as e:self.last_error=f'Paper manager failed: {type(e).__name__}'
            await asyncio.sleep(30)

    async def run_reviews(self):
        while True:
            try:await asyncio.to_thread(self.review_once)
            except asyncio.CancelledError:raise
            except Exception as e:self.last_error=f'Paper entry review failed: {type(e).__name__}'
            await asyncio.sleep(2)
