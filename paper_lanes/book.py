"""Transactional paper accounting from fresh displayed quotes, with immutable entry terms."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime,timedelta,timezone
from decimal import Decimal,ROUND_HALF_UP,ROUND_CEILING,ROUND_FLOOR
import hashlib
import json
import math
from pathlib import Path
import sqlite3
from uuid import uuid4

UTC=timezone.utc


def money(value):
    return int((Decimal(str(value))*100).quantize(Decimal('1'),rounding=ROUND_HALF_UP))


def price(value,tick,buy):
    d=Decimal(str(value))/Decimal(str(tick))
    return float(d.quantize(Decimal('1'),rounding=ROUND_CEILING if buy else ROUND_FLOOR)*Decimal(str(tick)))


def policy(lane):
    return {'version':f'macd-{lane}-qwen-paper-v1','position_fraction':.05 if lane=='vanguard' else .10,
            'gross_fraction':.40 if lane=='vanguard' else 1.,'sector_fraction':.20 if lane=='vanguard' else .30,
            'max_positions':10,'hard_stop':.15 if lane=='vanguard' else .05,
            'target':.30 if lane=='vanguard' else .10,'slippage_bps':15 if lane=='vanguard' else 5,
            'fee_bps':10,'max_spread':.03 if lane=='vanguard' else .01,'volume_fraction':.01,
            'quote_age_seconds':10,'daily_loss_stop':.03,'review_attempts':3,
            'fill_basis':'all_or_none_displayed_L1_with_adverse_slippage','stock_settlement':'next_session_open'}


def fresh(q,now,limit=10):
    try:
        at=datetime.fromisoformat(q['at'])
        numbers=[float(q[k]) for k in ('bid','ask','bid_qty','ask_qty','volume')]
        return (at.tzinfo is not None and at.astimezone(now.tzinfo).date()==now.date() and -2<=(now-at).total_seconds()<=limit
                and all(math.isfinite(v) and v>0 for v in numbers) and q['ask']>=q['bid'])
    except (TypeError,ValueError,KeyError):return False


class Book:
    def __init__(self,path,lane,capital,now):
        if lane not in ('vanguard','cbe') or not math.isfinite(capital) or capital<=0:raise ValueError('Invalid paper account')
        self.path=Path(path);self.lane=lane;self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS account(id INTEGER PRIMARY KEY CHECK(id=1),account_id TEXT NOT NULL,
              activated_at TEXT NOT NULL,initial_paise INTEGER NOT NULL,cash_paise INTEGER NOT NULL,paused INTEGER NOT NULL,
              policy TEXT NOT NULL,peak_paise INTEGER NOT NULL,max_drawdown REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS orders(id TEXT PRIMARY KEY,cohort TEXT NOT NULL,instrument TEXT NOT NULL,symbol TEXT NOT NULL,
              sector TEXT NOT NULL,lot_size INTEGER NOT NULL,tick REAL NOT NULL,expiry TEXT,source_session TEXT NOT NULL,
              created_at TEXT NOT NULL,entry_after TEXT NOT NULL,deadline TEXT NOT NULL,exit_due TEXT NOT NULL,payload TEXT NOT NULL,
              state TEXT NOT NULL,reason TEXT NOT NULL,rank INTEGER NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,retry_after TEXT,approval TEXT);
            CREATE TABLE IF NOT EXISTS cohorts(id TEXT PRIMARY KEY,at TEXT NOT NULL,orders INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS positions(id TEXT PRIMARY KEY REFERENCES orders(id),quantity INTEGER NOT NULL,entry REAL NOT NULL,
              entry_fee INTEGER NOT NULL,opened_at TEXT NOT NULL,stop REAL NOT NULL,target REAL NOT NULL,
              last_bid REAL NOT NULL,last_at TEXT NOT NULL,exit REAL,exit_fee INTEGER,closed_at TEXT,exit_reason TEXT,pnl_paise INTEGER);
            CREATE TABLE IF NOT EXISTS fills(id TEXT PRIMARY KEY,order_id TEXT NOT NULL,side TEXT NOT NULL,quantity INTEGER NOT NULL,
              price REAL NOT NULL,notional_paise INTEGER NOT NULL,fee_paise INTEGER NOT NULL,at TEXT NOT NULL,quote_at TEXT NOT NULL,
              evidence TEXT NOT NULL,UNIQUE(order_id,side));
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,at TEXT NOT NULL,kind TEXT NOT NULL,order_id TEXT,detail TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS decisions(id TEXT PRIMARY KEY,order_id TEXT NOT NULL,at TEXT NOT NULL,verdict TEXT NOT NULL,payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS equity(at TEXT PRIMARY KEY,day TEXT NOT NULL,equity_paise INTEGER NOT NULL,cash_paise INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS settlements(order_id TEXT PRIMARY KEY,due TEXT NOT NULL,amount_paise INTEGER NOT NULL,settled INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS status(id INTEGER PRIMARY KEY CHECK(id=1),at TEXT NOT NULL,payload TEXT NOT NULL);
            ''')
            if 'service_failures' not in {r[1] for r in db.execute('PRAGMA table_info(orders)')}:
                db.execute('ALTER TABLE orders ADD COLUMN service_failures INTEGER NOT NULL DEFAULT 0')
            db.execute('INSERT OR IGNORE INTO account VALUES(1,?,?,?,?,0,?,?,0)',
                (uuid4().hex,now.isoformat(),money(capital),money(capital),json.dumps(policy(lane)),money(capital)))
            a=db.execute('SELECT * FROM account').fetchone()
            if a['initial_paise']!=money(capital):raise ValueError('Existing paper capital cannot be rewritten')
            self.account_id=a['account_id'];self.activated_at=datetime.fromisoformat(a['activated_at']);self.terms=json.loads(a['policy'])
            if not db.execute('SELECT 1 FROM equity LIMIT 1').fetchone():
                db.execute('INSERT INTO equity VALUES(?,?,?,?)',(now.isoformat(),str(now.date()),a['initial_paise'],a['cash_paise']))

    def db(self):
        db=sqlite3.connect(self.path,timeout=5);db.row_factory=sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON');db.execute('PRAGMA busy_timeout=5000')
        return db

    @contextmanager
    def transaction(self):
        db=self.db()
        try:
            db.execute('BEGIN IMMEDIATE');yield db;db.commit()
        except BaseException:db.rollback();raise
        finally:db.close()

    def event(self,db,now,kind,order_id,detail):
        db.execute('INSERT INTO events(at,kind,order_id,detail) VALUES(?,?,?,?)',
                   (now.isoformat(),kind,order_id,json.dumps(detail,default=str)))

    def enqueue(self,cohort,rows,decided_at,source_session,entry_after,deadline,account_id):
        if account_id!=self.account_id or decided_at<self.activated_at or str(decided_at.date())!=source_session:
            return 0
        inserted=0
        with self.transaction() as db:
            if db.execute('SELECT 1 FROM cohorts WHERE id=?',(cohort,)).fetchone():return 0
            for r in rows:
                if not r['instrument'].startswith('NSE_FO|' if self.lane=='vanguard' else 'NSE_EQ|'):raise ValueError('Exact instrument class required')
                if r['lot_size']<1 or r['tick']<=0 or not math.isfinite(r['tick']):raise ValueError('Valid contract metadata required')
                if not decided_at<entry_after<deadline or r['exit_due']<=entry_after:raise ValueError('Invalid prospective windows')
                identity=hashlib.sha256(f"{self.account_id}:{cohort}:{r['instrument']}:{r['exit_due'].isoformat()}".encode()).hexdigest()[:32]
                n=db.execute('''INSERT OR IGNORE INTO orders(id,cohort,instrument,symbol,sector,lot_size,tick,expiry,source_session,
                    created_at,entry_after,deadline,exit_due,payload,state,reason,rank) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,'PENDING_AI','Awaiting entry window and fresh Qwen review',?)''',
                    (identity,cohort,r['instrument'],r['symbol'],r.get('sector') or 'UNKNOWN',r['lot_size'],r['tick'],r.get('expiry'),
                     source_session,decided_at.isoformat(),entry_after.isoformat(),deadline.isoformat(),r['exit_due'].isoformat(),json.dumps(r['candidate'],default=str),int(r['candidate'].get('rank') or r['candidate'].get('side_rank') or 999))).rowcount
                if n:self.event(db,decided_at,'QUEUED',identity,{'instrument':r['instrument'],'cohort':cohort,'policy':self.terms['version']});inserted+=1
            db.execute('INSERT INTO cohorts VALUES(?,?,?)',(cohort,decided_at.isoformat(),inserted))
        return inserted

    def rows(self,db):
        return [dict(r) for r in db.execute('''SELECT o.*,p.quantity,p.entry,p.entry_fee,p.opened_at,p.stop,p.target,p.last_bid,p.last_at,
          p.exit,p.exit_fee,p.closed_at,p.exit_reason,p.pnl_paise FROM positions p JOIN orders o ON o.id=p.id''')]

    def valuation(self,db):
        a=dict(db.execute('SELECT * FROM account').fetchone());positions=self.rows(db);open_rows=[p for p in positions if not p['closed_at']]
        value=sum(money(self.exit_price(p['last_bid'],p['tick'])*p['quantity']) for p in open_rows)
        fees=sum(self.fee(money(self.exit_price(p['last_bid'],p['tick'])*p['quantity'])) for p in open_rows)
        unsettled=db.execute('SELECT coalesce(sum(amount_paise),0) FROM settlements WHERE settled=0').fetchone()[0]
        equity=a['cash_paise']+unsettled+value-fees
        return a,positions,equity,unsettled

    def fee(self,notional):return money(notional/100*self.terms['fee_bps']/10000)

    def exit_price(self,bid,tick):return price(bid*(1-self.terms['slippage_bps']/10000),tick,False)

    def checks(self,db,o,q,now):
        a,positions,equity,_=self.valuation(db);held=[p for p in positions if not p['closed_at']]
        if a['paused']:return 0,'New entries paused'
        if not datetime.fromisoformat(o['entry_after'])<=now<datetime.fromisoformat(o['deadline']):return 0,'Outside entry window'
        if not fresh(q,now,self.terms['quote_age_seconds']):return 0,'Fresh two-sided quote and displayed quantity required'
        if any(p['instrument']==o['instrument'] for p in held):return 0,'Already held'
        if len(held)>=self.terms['max_positions']:return 0,'Position limit reached'
        if any((now-datetime.fromisoformat(p['last_at'])).total_seconds()>60 for p in held):return 0,'Held marks are stale'
        if (q['ask']-q['bid'])/q['ask']>self.terms['max_spread']:return 0,'Spread exceeds lane limit'
        if o['expiry'] and datetime.fromisoformat(o['exit_due']).date()>datetime.fromisoformat(o['expiry']).date():return 0,'Contract expires before its planned exit'
        first=db.execute('SELECT equity_paise FROM equity WHERE day=? ORDER BY at LIMIT 1',(str(now.date()),)).fetchone()
        if first and equity<first[0]*(1-self.terms['daily_loss_stop']):return 0,'Daily loss stop blocks new entries'
        fill=price(q['ask']*(1+self.terms['slippage_bps']/10000),o['tick'],True)
        gross=sum(money(p['last_bid']*p['quantity']) for p in held)
        sector=sum(money(p['last_bid']*p['quantity']) for p in held if p['sector']==o['sector'])
        budget=min(equity*self.terms['position_fraction'],equity*self.terms['gross_fraction']-gross,
                   equity*self.terms['sector_fraction']-sector,a['cash_paise']/(1+self.terms['fee_bps']/10000))
        lot=o['lot_size'];qty=max(0,int(budget/money(fill))//lot*lot)
        qty=min(qty,int(q['ask_qty'])//lot*lot,int(q['bid_qty'])//lot*lot,int(q['volume']*self.terms['volume_fraction'])//lot*lot)
        while qty>0 and money(fill*qty)+self.fee(money(fill*qty))>a['cash_paise']:qty-=lot
        if qty<lot:return 0,'Cash, exposure or displayed-liquidity budget cannot fund one lot'
        return qty,''

    def review_candidates(self,quotes,now,limit=3):
        result=[]
        with self.transaction() as db:
            for row in db.execute("SELECT * FROM orders WHERE state='PENDING_AI' ORDER BY created_at,rank,coalesce(CAST(json_extract(payload,'$.score') AS REAL),0) DESC,id").fetchall():
                o=dict(row)
                if now>=datetime.fromisoformat(o['deadline']):self.set_state(db,o['id'],'EXPIRED','Entry window missed; no historical fill',now);continue
                if o['attempts']>=self.terms['review_attempts']:self.set_state(db,o['id'],'REJECTED','Qwen retry limit reached',now);continue
                if o['retry_after'] and now<datetime.fromisoformat(o['retry_after']):continue
                atm_reason=self.atm_reason(o,quotes,now)
                if atm_reason:db.execute('UPDATE orders SET reason=? WHERE id=?',(atm_reason,o['id']));continue
                q=quotes.get(o['instrument']);qty,reason=self.checks(db,o,q,now)
                if not qty:db.execute('UPDATE orders SET reason=? WHERE id=?',(reason,o['id']));continue
                o['quote']=q;o['planned_quantity']=qty;result.append(o)
                if len(result)>=limit:break
        return result

    def atm_reason(self,order,quotes,now):
        guard=json.loads(order['payload']).get('atm_guard')
        if not guard:return ''  # Old queued orders keep their immutable entry terms.
        spot=quotes.get(guard.get('spot_instrument'))
        if not fresh(spot,now,self.terms['quote_age_seconds']):return 'Fresh underlying quote required to verify ATM'
        strikes=guard.get('strikes') or []
        if not strikes:return 'ATM strike catalog unavailable'
        nearest=min(strikes,key=lambda strike:(abs(strike-(spot['bid']+spot['ask'])/2),strike))
        return '' if nearest==guard['strike'] else 'Selected strike is no longer ATM; no substituted contract or fill'

    def set_state(self,db,identity,state,reason,now):
        db.execute('UPDATE orders SET state=?,reason=? WHERE id=?',(state,reason,identity));self.event(db,now,state,identity,{'reason':reason})

    def apply_reviews(self,orders,response,now):
        expected={o['id']:o for o in orders};rows=response.get('reviews',[])
        if len(rows)!=len(expected) or {r.get('id') for r in rows}!=set(expected):raise ValueError('Review identities mismatch')
        if datetime.fromisoformat(response['expires_at'])<=now:raise ValueError('Expired entry approval')
        with self.transaction() as db:
            for r in rows:
                if r.get('verdict') not in ('approve','reject','wait'):raise ValueError('Invalid model verdict')
                o=expected[r['id']];current=db.execute('SELECT state FROM orders WHERE id=?',(o['id'],)).fetchone()
                if not current or current[0]!='PENDING_AI':continue
                record={**r,**{k:response[k] for k in ('model','digest','context_hash','created_at','expires_at')},'reference_price':o['quote']['ask'],'reviewed_quantity':o['planned_quantity']}
                db.execute('INSERT INTO decisions VALUES(?,?,?,?,?)',(uuid4().hex,o['id'],now.isoformat(),r['verdict'],json.dumps(record)))
                db.execute('UPDATE orders SET attempts=attempts+1,retry_after=?,approval=? WHERE id=?',
                           ((now+timedelta(seconds=60)).isoformat(),json.dumps(record),o['id']))
                state={'approve':'APPROVED','reject':'REJECTED','wait':'PENDING_AI'}[r['verdict']]
                if now>=datetime.fromisoformat(o['deadline']):state='EXPIRED'
                self.set_state(db,o['id'],state,r['reason'],now)

    def review_failed(self,orders,now):
        with self.transaction() as db:
            for o in orders:
                failures=db.execute('SELECT service_failures FROM orders WHERE id=?',(o['id'],)).fetchone()[0]+1
                delay=min(120,15*2**min(failures-1,3))
                db.execute("UPDATE orders SET service_failures=service_failures+1,retry_after=?,reason='Qwen service unavailable; no verdict or entry' WHERE id=? AND state='PENDING_AI'",
                           ((now+timedelta(seconds=delay)).isoformat(),o['id']))
                self.event(db,now,'QWEN_UNAVAILABLE',o['id'],{'reason':'Local review failed; no deterministic fallback entry'})

    def cycle(self,quotes,now,session_open,settle_at):
        with self.transaction() as db:
            if session_open and not db.execute('SELECT 1 FROM equity WHERE day=? LIMIT 1',(str(now.date()),)).fetchone():
                a,_,baseline,_=self.valuation(db)
                db.execute('INSERT INTO equity VALUES(?,?,?,?)',((now-timedelta(microseconds=1)).isoformat(),str(now.date()),baseline,a['cash_paise']))
            for s in db.execute('SELECT * FROM settlements WHERE settled=0 AND due<=?',(now.isoformat(),)).fetchall():
                db.execute('UPDATE account SET cash_paise=cash_paise+?',(s['amount_paise'],));db.execute('UPDATE settlements SET settled=1 WHERE order_id=?',(s['order_id'],))
                self.event(db,now,'CASH_SETTLED',s['order_id'],{'amount_paise':s['amount_paise']})
            for o in db.execute("SELECT id FROM orders WHERE state IN ('PENDING_AI','APPROVED') AND deadline<=?",(now.isoformat(),)).fetchall():
                self.set_state(db,o['id'],'EXPIRED','Entry window missed; no historical fill',now)
            if session_open:
                # Exits precede entries and never call Qwen.
                for p in self.rows(db):
                    if p['closed_at']:continue
                    q=quotes.get(p['instrument'])
                    if not fresh(q,now,self.terms['quote_age_seconds']):continue
                    db.execute('UPDATE positions SET last_bid=?,last_at=? WHERE id=?',(q['bid'],q['at'],p['id']))
                    reason=('STOP' if q['bid']<=p['stop'] else 'TARGET' if q['bid']>=p['target'] else
                            'TIME_EXIT' if now>=datetime.fromisoformat(p['exit_due']) else None)
                    if not reason:continue
                    if q['bid_qty']<p['quantity']:
                        self.event(db,now,'EXIT_WAITING_DEPTH',p['id'],{'required':p['quantity'],'available':q['bid_qty']});continue
                    fill=self.exit_price(q['bid'],p['tick']);notional=money(fill*p['quantity']);fee=self.fee(notional)
                    pnl=notional-fee-money(p['entry']*p['quantity'])-p['entry_fee']
                    db.execute('UPDATE positions SET exit=?,exit_fee=?,closed_at=?,exit_reason=?,pnl_paise=? WHERE id=?',
                               (fill,fee,now.isoformat(),reason,pnl,p['id']))
                    db.execute('INSERT INTO fills VALUES(?,?,?,?,?,?,?,?,?,?)',
                               (uuid4().hex,p['id'],'SELL',p['quantity'],fill,notional,fee,now.isoformat(),q['at'],json.dumps(q)))
                    if self.lane=='cbe':db.execute('INSERT INTO settlements VALUES(?,?,?,0)',(p['id'],settle_at.isoformat(),notional-fee))
                    else:db.execute('UPDATE account SET cash_paise=cash_paise+?',(notional-fee,))
                    self.set_state(db,p['id'],'CLOSED',reason,now)
        # Exit fills are committed before any entry processing can fail.
        with self.transaction() as db:
            if session_open:
                for row in db.execute("SELECT * FROM orders WHERE state='APPROVED' ORDER BY created_at,rank,coalesce(CAST(json_extract(payload,'$.score') AS REAL),0) DESC,id").fetchall():
                    o=dict(row);q=quotes.get(o['instrument']);review=json.loads(o['approval'])
                    atm_reason=self.atm_reason(o,quotes,now)
                    if atm_reason:db.execute('UPDATE orders SET reason=? WHERE id=?',(atm_reason,o['id']));continue
                    if datetime.fromisoformat(review['expires_at'])<=now:
                        self.set_state(db,o['id'],'PENDING_AI','Entry approval expired; fresh review required',now);continue
                    qty,reason=self.checks(db,o,q,now)
                    if not qty:db.execute('UPDATE orders SET reason=? WHERE id=?',(reason,o['id']));continue
                    qty=min(qty,review['reviewed_quantity'])
                    if abs(q['ask']/review['reference_price']-1)>.02:
                        self.set_state(db,o['id'],'PENDING_AI','Quote moved more than 2%; fresh review required',now);continue
                    fill=price(q['ask']*(1+self.terms['slippage_bps']/10000),o['tick'],True);notional=money(fill*qty);fee=self.fee(notional)
                    db.execute('UPDATE account SET cash_paise=cash_paise-? WHERE cash_paise>=?',(notional+fee,notional+fee))
                    if db.execute('SELECT changes()').fetchone()[0]!=1:raise ValueError('Paper cash invariant violated')
                    db.execute('INSERT INTO positions(id,quantity,entry,entry_fee,opened_at,stop,target,last_bid,last_at) VALUES(?,?,?,?,?,?,?,?,?)',
                        (o['id'],qty,fill,fee,now.isoformat(),fill*(1-self.terms['hard_stop']),fill*(1+self.terms['target']),q['bid'],q['at']))
                    db.execute('INSERT INTO fills VALUES(?,?,?,?,?,?,?,?,?,?)',
                        (uuid4().hex,o['id'],'BUY',qty,fill,notional,fee,now.isoformat(),q['at'],json.dumps(q)))
                    self.set_state(db,o['id'],'FILLED','Fresh displayed ask, adverse slippage and costs',now)
            a,positions,equity,unsettled=self.valuation(db)
            if a['cash_paise']<0:raise ValueError('Negative paper cash')
            db.execute('UPDATE account SET peak_paise=max(peak_paise,?),max_drawdown=max(max_drawdown,(peak_paise-?)*1.0/peak_paise)',(equity,equity))
            last=db.execute('SELECT equity_paise,day FROM equity ORDER BY at DESC LIMIT 1').fetchone()
            if session_open and (not last or last[0]!=equity or last[1]!=str(now.date())):
                db.execute('INSERT OR IGNORE INTO equity VALUES(?,?,?,?)',(now.isoformat(),str(now.date()),equity,a['cash_paise']))
            state={'state':'armed' if session_open else 'market_closed','paper_only':True,'paused':bool(a['paused'])}
            db.execute('INSERT INTO status VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET at=excluded.at,payload=excluded.payload',(now.isoformat(),json.dumps(state)))

    def control(self,pause,now):
        with self.transaction() as db:
            db.execute('UPDATE account SET paused=?',(int(pause),));self.event(db,now,'PAUSE_ENTRIES' if pause else 'RESUME_ENTRIES',None,{'exits_remain_managed':True})

    def snapshot(self,now):
        with self.db() as db:
            db.execute('BEGIN')
            a,positions,equity,unsettled=self.valuation(db);held=[p for p in positions if not p['closed_at']];closed=[p for p in positions if p['closed_at']]
            realized=sum(p['pnl_paise'] for p in closed);unrealized=equity-a['initial_paise']-realized
            orders=[dict(r) for r in db.execute('SELECT * FROM orders ORDER BY created_at DESC,id LIMIT 300')]
            curves=[dict(r) for r in db.execute('SELECT * FROM (SELECT * FROM equity ORDER BY at DESC LIMIT 2000) ORDER BY at')]
            events=[{**dict(r),'detail':json.loads(r['detail'])} for r in db.execute('SELECT * FROM events ORDER BY id DESC LIMIT 100')]
            decisions=[{**dict(r),'payload':json.loads(r['payload'])} for r in db.execute('SELECT * FROM decisions ORDER BY at DESC LIMIT 100')]
            fills=[dict(r) for r in db.execute('SELECT * FROM fills ORDER BY at DESC LIMIT 300')]
            total_fees=db.execute('SELECT coalesce(sum(fee_paise),0) FROM fills').fetchone()[0]
            state=db.execute('SELECT * FROM status').fetchone()
            stale=sum((now-datetime.fromisoformat(p['last_at'])).total_seconds()>self.terms['quote_age_seconds'] for p in held)
            return {'lane':self.lane,'account_id':self.account_id,'activated_at':a['activated_at'],'mode':'paper','execution_enabled':True,
                    'initial_capital':a['initial_paise']/100,'cash':a['cash_paise']/100,'unsettled_cash':unsettled/100,'equity':equity/100,
                    'realized_pnl':realized/100,'unrealized_pnl':unrealized/100,'return_pct':(equity/a['initial_paise']-1)*100,
                    'max_drawdown_pct':a['max_drawdown']*100,'closed_count':len(closed),'open_count':len(held),
                    'win_rate':sum(p['pnl_paise']>0 for p in closed)/len(closed) if closed else None,
                    'expectancy':realized/100/len(closed) if closed else None,'paused':bool(a['paused']),'stale_positions':stale,
                    'policy':self.terms,'positions':held,'closed':closed[-300:],'orders':orders,'equity_curve':curves,
                    'events':events,'decisions':decisions,'fills':fills,'fees_paid':total_fees/100,'checked_at':state['at'] if state else None,
                    'status':json.loads(state['payload']) if state else {'state':'awaiting_first_check'}}
