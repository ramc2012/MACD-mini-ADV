"""Causality, funding and matched-ledger checks; never writes an app book."""
import datetime as dt
import json
import unittest
import numpy as np
import study as s


class StudyChecks(unittest.TestCase):
    def synthetic(self):
        symbol=next(k for k,v in s.CONTRACTS.items() if v['side']=='CE')
        spot=s.CONTRACTS[symbol]['spot_symbol']
        raw={}
        for name,close in [(symbol,940),(spot,915)]:
            rows=[];day=dt.date(2026,8,17)
            while day<=dt.date(2026,9,2):
                if s.is_trading_day(day):
                    for m in range(555,close):
                        t=s.epoch(day.isoformat(),'09:15')+(m-555)*60
                        c=100+len(rows)*.002+np.sin(len(rows)/50)
                        rows.append([t,c,c+.2,c-.2,c,10])
                day+=dt.timedelta(days=1)
            raw[name]=np.asarray(rows,dtype=float)
        e={'symbol':symbol,'entry_ts':s.epoch('2026-09-01','11:07')+23,'first_fill_price':100.05}
        return raw,e

    def test_future_prices_cannot_change_entry_readings(self):
        raw,e=self.synthetic();full=s.build_indicators(raw)
        prefix={k:a[a[:,0]+60<=e['entry_ts']] for k,a in raw.items()}
        short=s.build_indicators(prefix)
        a,visibility=s.features(e,raw,full);b,_=s.features(e,prefix,short)
        self.assertEqual(a,b)
        for key in ['premium_st_bar_end','spot_st_bar_end','spot_quote_end']:
            if visibility[key] is not None:self.assertLessEqual(visibility[key],e['entry_ts'])

    def test_current_day_extremes_cannot_change_prior_pivots(self):
        raw,e=self.synthetic();tables=s.build_indicators(raw)
        changed={k:a.copy() for k,a in raw.items()}
        for a in changed.values():a[a[:,0]>=s.epoch('2026-09-01','00:00'),1:5]*=100
        future=s.build_indicators(changed)
        for symbol in raw:self.assertEqual(s.previous_pivots(tables[symbol],e['entry_ts']),s.previous_pivots(future[symbol],e['entry_ts']))

    def test_fee_and_cash_identity(self):
        b=s.Book(1000)
        b.fill({'symbol':'test','side':'BUY','quantity':10,'price':10,'fees':2})
        b.fill({'symbol':'test','side':'SELL','quantity':10,'price':12,'fees':2})
        self.assertAlmostEqual(b.cash,1016);self.assertAlmostEqual(b.realized,16)
        self.assertFalse(b.positions)

    def test_unfunded_episode_does_not_block_later_same_contract(self):
        symbol=next(iter(s.CONTRACTS));start=s.epoch('2026-09-29','00:00')
        records=[]
        for i,(side,price) in enumerate([('BUY',6000000),('SELL',6000000),('BUY',100000),('SELL',110000)]):
            when=s.epoch('2026-09-29','10:00')+i*60
            records.append({'symbol':symbol,'side':side,'price':price,'quantity':1,'lot_size':1,'lots':1,'fees':0,'trade_id':str(i),'order_id':str(i),'timestamp':dt.datetime.fromtimestamp(when,dt.UTC).isoformat()})
        trades,eps=s.episodes({'trades':records},'test')
        for e in eps.values():e['features']={r:True for r in s.ENTRY_RULES}
        minutes={symbol:np.array([[s.epoch('2026-09-29','15:39'),100,100,100,100,1]],float)}
        funded=s.simulate(trades,eps,'baseline',start,s.epoch('2026-09-29'),minutes,{}, {})
        recorded=s.simulate(trades,eps,'baseline',start,s.epoch('2026-09-29'),minutes,{}, {},funded=False)
        self.assertEqual(funded['status']['unaffordable_entries'],1)
        self.assertEqual(funded['status']['new_episodes'],1)
        self.assertAlmostEqual(funded['pnl'],10000)
        self.assertGreaterEqual(funded['min_cash'],0)
        self.assertLess(recorded['min_cash'],0)
        self.assertEqual(len(recorded['applied_fills']),4)

    def test_expired_in_money_option_is_not_valued_as_zero(self):
        for side,shift in [('CE',10),('PE',-10)]:
            symbol=next(k for k,v in s.CONTRACTS.items() if v['side']==side)
            c=s.CONTRACTS[symbol];under=c['spot_symbol'];price=c['strike']+shift
            a=np.array([[s.epoch('2026-09-29','15:14'),price,price,price,price,1]],float)
            value,kind=s.mark_price(symbol,s.epoch('2026-09-30'),{under:a},{symbol:'2026-09-29'})
            self.assertEqual(value,10);self.assertEqual(kind,'expiry-intrinsic-scenario')

    def test_actual_baseline_fill_ids_and_realized_money(self):
        books=s.load_gzip(s.INPUT_ROOT/'books.json.gz')
        for cohort,name,begin,end in [('old_full','old','2026-09-01','2026-09-30'),('current_sep','current','2026-09-29','2026-09-30')]:
            source=s.load_gzip(s.INPUT_ROOT/('old_book_source.json.gz' if name=='old' else 'current_book_source.json.gz'))
            expected=[t for t in source['trades'] if s.epoch(begin,'00:00')<=s.stamp(t['timestamp'])<=s.epoch(end)]
            actual=books[cohort+'|baseline|recorded']['applied_fills']
            self.assertEqual([t['trade_id'] for t in expected],[t['trade_id'] for t in actual])
        reconciliation=json.loads((s.INPUT_ROOT/'ledger_reconciliation.json').read_text())
        self.assertAlmostEqual(books['old_full|baseline|recorded']['scenario_realized_pnl'],reconciliation['old']['september_realized_increase'],places=6)

    def test_funded_books_never_oversell_or_overdraw(self):
        books=s.load_gzip(s.INPUT_ROOT/'books.json.gz')
        for b in books.values():
            if b['funded']:self.assertGreaterEqual(b['min_cash'],-.01)
            for f in b['applied_fills']:
                self.assertGreater(f['quantity'],0)
                if f['modelled']:self.assertGreaterEqual(f['ts'],f['condition_ts'])


if __name__=='__main__':unittest.main()
