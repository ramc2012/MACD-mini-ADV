"""Market-data-only hub adapter and independent marking/exit and local-entry loops."""
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


# Continuous sessions since 3 Aug 2026 (engine sessions.py regime 2026-08-session): F&O to 15:40; the cash book keeps its
# 15:30 marking clock (its entries end 09:55 and its sales settle next session).
SESSION_END={'vanguard':time(15,40),'cbe':time(15,30)}


def session_open(now,lane='cbe'):
    return is_session(now.date()) and time(9,15)<=now.time()<SESSION_END[lane]


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
        self.live=None;self.reference={}
        self.reference_day=None

    def quotes(self,instruments):
        keys=sorted(set(instruments))
        if not keys and self.last_subscriptions is None:return {}
        if len(keys)>100:raise ValueError('Paper quote universe exceeds its bounded budget')
        with httpx.Client(timeout=12,trust_env=False,follow_redirects=False) as c:
            if keys!=self.last_subscriptions:
                response=c.post(self.hub+'/v1/subscriptions',json={'caller':'macd-paper-lanes','upstox':keys})
                response.raise_for_status();self.last_subscriptions=keys
            if not keys:return {}
            reused={}
            if self.live:
                from shadow_lanes.contracts import resolve
                if self.reference_day!=datetime.now(IST).date():self.reference={};self.reference_day=datetime.now(IST).date()
                missing=[k for k in keys if k not in self.reference]
                if missing:self.reference.update(resolve(missing))
                for key in keys:
                    tick=self.live.quotes.get((self.reference.get(key) or {}).get('canonical'),{})
                    if not tick:continue
                    q={'instrument':key,'at':datetime.fromtimestamp(tick['exchange_ts_ms']/1000,IST).isoformat(),
                       'bid':tick.get('bid'),'ask':tick.get('ask'),'bid_qty':tick.get('bid_qty'),
                       'ask_qty':tick.get('ask_qty'),'volume':tick.get('cum_volume')}
                    if fresh(q,datetime.now(IST)):reused[key]=q
                if len(reused)==len(keys):return reused
            response=c.post(self.hub+'/v1/request',json={'broker':'upstox','endpoint':'market-quote/quotes',
                'query':{'instrument_key':','.join(k for k in keys if k not in reused)},'caller':'macd-paper-lanes','priority':'critical','ttl_ms':1000})
            response.raise_for_status();return {**reused,**parse_quotes(response.json(),set(keys)-set(reused))}

    def metadata(self,lane,rows):
        from shadow_lanes import source
        c=source.connect(readonly=True)
        try:
            with c.cursor(cursor_factory=RealDictCursor) as cur:
                if lane=='vanguard':
                    from shadow_lanes.contracts import resolve,matches,expiry,option_rows
                    master=resolve([r['instrument'] for r in rows]);out={}
                    for candidate in rows:
                        key=candidate['instrument'];r=master.get(key)
                        if not matches(candidate,r):continue
                        cur.execute('SELECT spot_instrument_key,sector20 FROM fo_underlying_catalog LEFT JOIN sector_taxonomy USING(symbol) WHERE symbol=%s',(r['underlying'],))
                        stock=cur.fetchone()
                        if not stock:continue
                        strikes=sorted({float(s['strike']) for s in option_rows(r['underlying'])
                            if s.get('upstox') and s.get('expiry_ms')==r['expiry_ms'] and s.get('strike')})
                        out[key]={'instrument':key,'symbol':r['underlying'],'expiry':expiry(r),'lot_size':r['lot_size'],
                            'tick':float(r['tick_size']),'sector':stock['sector20'],'canonical':r['canonical'],
                            'atm_guard':{'strike':float(r['strike']),'spot_instrument':stock['spot_instrument_key'],'strikes':strikes}}
                    return out
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
        if os.environ.get('CBE_DAILY_SWING_ENABLED')=='true':self.books['cbe'].enable_daily_swing(now)
        self.market=market or MarketData();self.last_quotes={};self.last_error=None;self.last_poll=None
        self.last_review={};self.daily_review_at=None

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
            daily=lane=='cbe' and book.terms.get('holding_mode')=='daily_trend'
            if daily:
                from .daily_swing import VERSION
                if candidate.get('strategy_version')!=VERSION:continue
            horizon=int(candidate.get('horizon_sessions') or 1)
            if not 1<=horizon<=3:continue
            due_day=next_session(day,horizon)
            expiry=meta.get('expiry')
            if lane=='vanguard' and not expiry:continue
            if lane=='vanguard' and date.fromisoformat(expiry)<due_day:
                # owner 28 Oct: every pick is entered; a series ending inside the hold exits on its expiry day
                if date.fromisoformat(expiry)<=day:continue
                due_day=date.fromisoformat(expiry)
            if lane=='vanguard' and (candidate.get('contract_kind')!='ATM' or not meta.get('atm_guard')):continue
            prepared.append({**meta,'sector':candidate.get('sector20') or candidate.get('sector') or meta.get('sector') or 'UNKNOWN',
                             'candidate':{**candidate,**({'atm_guard':meta['atm_guard']} if lane=='vanguard' else {})},
                             'exit_due':datetime(9999,12,31,tzinfo=IST) if daily else datetime.combine(due_day,time(15,10) if expiry==str(due_day) else time(15,15),IST)})
        entry_after=decided+timedelta(seconds=1) if lane=='vanguard' else datetime.combine(entry_day,time(9,20),IST)
        deadline=datetime.combine(day,time(15,15),IST) if lane=='vanguard' else datetime.combine(entry_day,time(9,55),IST)
        if entry_after>=deadline:return 0
        inserted=book.enqueue(publication['id'],prepared,decided,str(day),entry_after,deadline,publication['paper_account_id'])
        if len(prepared)<len(rows):
            with book.transaction() as db:
                book.event(db,datetime.now(IST),'METADATA_BLOCKED',None,{'cohort':publication['id'],'eligible':len(rows),'exact_metadata':len(prepared)})
        return inserted

    def convert_watchlist(self,publication,now=None):
        """Owner, 28 Oct 2026: enter an already-published watchlist that did not become positions. Same exact contracts and
        planned exits (source session + horizon); entry from now until 15:15 today; contracts whose planned exit is today
        or earlier are skipped. Idempotent per publication (cohort '<id>:owner-convert')."""
        now=(now or datetime.now(IST)).astimezone(IST);lane=publication['lane'];book=self.books[lane]
        if publication.get('paper_account_id')!=book.account_id or not session_open(now,lane):return 0
        day=date.fromisoformat(publication['source_session']);rows=publication['watchlist']
        metadata=self.market.metadata(lane,rows);prepared=[];skipped=[]
        with book.db() as db:
            held={r[0] for r in db.execute("SELECT instrument FROM orders WHERE state IN ('FILLED','APPROVED','PENDING_AI')")}
        for candidate in rows:
            key=candidate['instrument'] if lane=='vanguard' else candidate.get('symbol') or candidate['instrument']
            meta=metadata.get(key);horizon=int(candidate.get('horizon_sessions') or 1);due_day=next_session(day,horizon)
            if not meta or due_day<=now.date() or meta['instrument'] in held:
                skipped.append({'symbol':candidate.get('symbol'),'reason':'no exact metadata' if not meta else 'planned exit today or earlier' if due_day<=now.date() else 'already active'});continue
            expiry=meta.get('expiry')
            if lane=='vanguard' and (not expiry or date.fromisoformat(expiry)<due_day):
                skipped.append({'symbol':candidate.get('symbol'),'reason':'expires before planned exit'});continue
            prepared.append({**meta,'sector':candidate.get('sector20') or candidate.get('sector') or meta.get('sector') or 'UNKNOWN',
                             'candidate':{**candidate,'owner_conversion':True},
                             'exit_due':datetime.combine(due_day,time(15,10) if expiry==str(due_day) else time(15,15),IST)})
        deadline=datetime.combine(now.date(),time(15,15),IST)
        inserted=book.enqueue(publication['id']+':owner-convert',prepared,now,str(day),now+timedelta(seconds=1),deadline,
                              publication['paper_account_id'],owner_conversion=True)
        with book.transaction() as db:
            book.event(db,now,'OWNER_WATCHLIST_CONVERSION',None,{'publication':publication['id'],'queued':inserted,'skipped':skipped})
        return inserted

    def instruments(self):
        keys=set()
        for book in self.books.values():
            with book.db() as db:
                for r in db.execute("SELECT instrument,payload FROM orders WHERE state IN ('PENDING_AI','APPROVED','FILLED')"):
                    keys.add(r[0]);spot=json.loads(r[1]).get('atm_guard',{}).get('spot_instrument')
                    if spot:keys.add(spot)
        return keys

    def poll(self,now=None):
        now=(now or datetime.now(IST)).astimezone(IST);opened=any(session_open(now,lane) for lane in self.books)
        if opened:
            try:self.last_quotes=self.market.quotes(self.instruments());self.last_error=None
            except Exception as e:self.last_quotes={};self.last_error=f'Market quote request failed: {type(e).__name__}'
        else:self.last_quotes={}
        if opened and hasattr(self.market,'reference') and self.market.live:
            from shadow_lanes.contracts import matches
            with self.books['vanguard'].db() as db:
                for identity,payload in db.execute("SELECT instrument,payload FROM orders WHERE state IN ('PENDING_AI','APPROVED','FILLED')"):
                    if not matches(json.loads(payload),self.market.reference.get(identity)):
                        self.last_quotes.pop(identity,None);self.last_error='Option identity mismatch; original contract quote required'
        settlement=datetime.combine(next_session(now.date()),time(9,15),IST)
        for book in self.books.values():
            try:book.cycle(self.last_quotes,now,session_open(now,book.lane),settlement)
            except Exception as e:self.last_error=f'{book.lane} paper cycle failed: {type(e).__name__}'
        self.last_poll=now.isoformat()

    def review_once(self):
        if not any(session_open(datetime.now(IST),lane) for lane in self.books):return
        for lane,book in self.books.items():
            now=datetime.now(IST);orders=book.review_candidates(dict(self.last_quotes),now,limit=10,local=True)
            if not orders:continue
            candidates=[]
            for o in orders:
                row=json.loads(o['payload']);facts={'phase':'fresh_entry_review','source_session':o['source_session'],
                    'underlying':row.get('symbol') or row.get('instrument'),'strike':row.get('strike'),'expiry':row.get('expiry'),
                    'rank':row.get('rank') or row.get('side_rank') or len(candidates)+1,'planned_quantity':o['planned_quantity'],
                    'spread_pct':(o['quote']['ask']/o['quote']['bid']-1)*100,
                    'position_limit_pct':book.terms['position_fraction']*100,
                    'hard_stop_pct':book.terms['hard_stop']*100,'target_pct':book.terms['target']*100 if book.terms.get('target') else None,
                    'horizon_sessions':row.get('horizon_sessions',1),'exit_due':o['exit_due'],'signal_close':row.get('source_mark')}
                for key in ('direction_score','contract_score','combined_score','momentum_fast','momentum_slow','relative_momentum','score',
                            'expected_net_return','option_volume','option_oi','conviction',
                            'ema20','ema50','macd_line','macd_signal','macd_hist','rsi_14','underlying_signal_close'):
                    value=row.get(key)
                    if isinstance(value,(int,float)) and math.isfinite(value):facts[key]=value
                guard=row.get('atm_guard') or {};spot=self.last_quotes.get(guard.get('spot_instrument'))
                if row.get('ranker_entry_basis'):facts['ranker_entry_basis']=row['ranker_entry_basis']
                if spot and fresh(spot,now):
                    facts.update(current_underlying_bid=spot['bid'],current_underlying_ask=spot['ask'],
                        underlying_quote_at=spot['at'],atm_strike=guard['strike'])
                candidates.append({'id':o['id'],'instrument':o['instrument'],'side':row.get('option_type') or 'LONG',
                                   'reference_price':o['quote']['ask'],'source_at':o['quote']['at'],'facts':facts})
            if lane=='cbe' and book.terms.get('holding_mode')=='daily_trend':
                from .daily_swing import VERSION
                t=datetime.now(IST)
                response={'reviews':[{'id':o['id'],'verdict':'approve','reason':'Frozen completed daily breakout; fresh cash/quote/risk checks','priority':json.loads(o['payload']).get('score',0)} for o in orders],
                          'model':VERSION,'digest':VERSION,'context_hash':None,'created_at':t.isoformat(),
                          'expires_at':(t+timedelta(seconds=30)).isoformat(),'decision_engine':'daily_rules'}
                book.apply_reviews(orders,response,t);self.last_review[lane]=response
                continue
            gate=book.terms.get('model_gate',True)
            try:
                from . import local_intelligence
                try:
                    response=local_intelligence.review(lane,candidates,self.contexts(candidates,now),datetime.now(IST))
                except Exception as exc:
                    if gate:raise
                    # owner 28 Oct: no model gate; enter the published watchlist with neutral priority
                    t=datetime.now(IST)
                    response={'reviews':[{'id':c['id'],'verdict':'approve','reason':'Owner: watchlist entered as published (context unavailable: '+type(exc).__name__+')','priority':None} for c in candidates],
                              'model':'owner-watchlist-entry','digest':None,'context_hash':None,'decision_engine':'local',
                              'created_at':t.isoformat(),'expires_at':(t+timedelta(seconds=30)).isoformat()}
                if not gate:
                    for r in response['reviews']:
                        if r['verdict']=='wait' or (r['verdict']=='reject' and 'ban' not in r.get('reason','').lower() and 'exact CE/PE' not in r.get('reason','')):
                            r['model_verdict'],r['verdict']=r['verdict'],'approve'
                            r['reason']='Owner: watchlist entered as published; model said '+r['model_verdict']+': '+r.get('reason','')
                book.apply_reviews(orders,response,datetime.now(IST));self.last_review[lane]=response
            except Exception as exc:
                book.review_failed(orders,datetime.now(IST),local=True)
                self.last_review[lane]={'error':type(exc).__name__,'reason':'Local context unavailable; no entry'}

    def review_daily_positions(self, now):
        book=self.books['cbe']
        if book.terms.get('holding_mode')!='daily_trend' or not is_session(now.date()) or now.time()<time(15,31):return
        with book.db() as db:
            if not db.execute('SELECT 1 FROM positions WHERE closed_at IS NULL').fetchone():return
        if self.daily_review_at and (now-self.daily_review_at).total_seconds()<300:return
        self.daily_review_at=now
        try:
            from shadow_lanes.daily_market import view
            data=view(as_of=now)
            book.apply_daily(data,now,datetime.combine(next_session(now.date()),time(9,15),IST))
        except Exception as exc:
            self.last_error='Daily exit inputs unavailable: '+type(exc).__name__

    def contexts(self,candidates,now):
        from shadow_lanes import source
        from shadow_lanes.decision_context import build
        c=source.connect(readonly=True)
        try:
            result=build(c,candidates,now,'fresh_entry_review')
            live=getattr(self.market,'live',None)
            if live:
                for key,fields in live.contexts(candidates).items():result[key].update(fields)
            return result
        finally:c.close()

    def snapshot(self,lane,since=None):
        from .local_intelligence import status
        daily=self.books[lane].terms.get('holding_mode')=='daily_trend'
        intelligence={'policy':self.books[lane].terms['strategy_version'],'mode':'completed_daily_rules','qwen_required':False,
                      'validation':'Prospective daily trend policy; profitability unproven'} if daily else status(lane)
        return {**self.books[lane].snapshot(datetime.now(IST),since),'market_error':self.last_error,'market_checked_at':self.last_poll,
                'model_required':not daily,'qwen_required':False,'intelligence':{**intelligence,'last_review':self.last_review.get(lane)},
                'live_orders_possible':False,'legacy_history_preserved':True}

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
