import datetime as dt
import sys
import unittest
from unittest.mock import patch
import numpy as np
import pandas as pd
import analyze as r
from macd_trader.indicators import IncrementalMACD

S='NSE:NIFTY26SEP24000CE';U='NSE:NIFTY50-INDEX'

class Tests(unittest.TestCase):
    def test_zero_threshold_equals_production_preview(self):
        m=IncrementalMACD()
        for p in 10+np.sin(np.arange(50)/5):m.update(float(p))
        zero=r.zero_price(m.fast_ema,m.slow_ema)
        self.assertAlmostEqual(m.preview(zero).macd,0,places=12)
        self.assertGreater(m.preview(zero+.1).macd,0);self.assertLess(m.preview(zero-.1).macd,0)
        before=(m.fast_ema,m.slow_ema,m.count);m.preview(zero);self.assertEqual(before,(m.fast_ema,m.slow_ema,m.count))

    def test_forming_st_matches_final_committed_bar(self):
        c=100+np.sin(np.arange(120)/7)*10+np.arange(120)/8;g=pd.DataFrame({'h':c+2,'l':c-2,'c':c})
        atr,direction,line=r.st.st(g);u,l=r.bands(g)
        for i in range(11,len(g)):
            a,b,z=r.preview_st(c[i],c[i]+2,c[i]-2,c[i-1],atr[i-1],u[i-1],l[i-1],direction[i-1])
            self.assertEqual(a,direction[i]);self.assertAlmostEqual(b,line[i]);self.assertAlmostEqual(z,atr[i])

    def test_first_per_bucket_and_prior_cross_exclusion(self):
        j=np.array([1,1,1,2,2]);mask=np.array([False,True,True,True,True])
        np.testing.assert_array_equal(r.first_per_bucket(mask,j),[1,3])
        first=np.array([20,60]);times=np.array([10,20,30,40,60]);keys=np.array([0,0,0,1,1]);allowed=times<first[keys]
        np.testing.assert_array_equal(allowed,[True,False,False,True,False])

    def test_horizon_censoring_and_failure_are_distinct(self):
        data=object.__new__(r.Data);day='2026-09-01';t=r.st.epoch(day,'14:45')
        data.series={S:{'cross_t':np.array([],dtype=int),'positive_closed':np.array([],dtype=int)}}
        e={'symbol':S,'ts':t,'day':day,'price':10};data.labels(e)
        self.assertEqual(e['cross_30'],0);self.assertIsNone(e['cross_60']);self.assertIsNone(e['cross_120']);self.assertEqual(e['cross_by_close'],0)

    def test_timeout_includes_failed_entry_and_exact_deadline_cross(self):
        t=r.st.epoch('2026-09-01','10:00');p={'early':True,'signal_ts':t,'cross_seen':False,'peak':10.,'entry':10.,'entry_day':'2026-09-01'}
        self.assertEqual(r.exit_reason(p,9.,t+1800,None,False,.0005),'NO_CROSS_TIMEOUT')
        p={'early':True,'signal_ts':t,'cross_seen':False,'peak':10.,'entry':10.,'entry_day':'2026-09-01'}
        self.assertIsNone(r.exit_reason(p,10.,t+1800,None,True,.0005));self.assertTrue(p['cross_seen'])

    def test_stop_and_invalidation_do_not_preempt_waiting_for_cross(self):
        t=r.st.epoch('2026-09-01','10:00');p={'early':True,'signal_ts':t,'cross_seen':False,'peak':10.,'entry':10.,'entry_day':'2026-09-01'}
        self.assertIsNone(r.exit_reason(p,9.5,t+600,-.1,False,.0005))
        self.assertEqual(r.exit_reason(p,9.5,t+660,-.1,True,.0005),'MACD_INVALIDATED')
        p.update(cross_seen=False);self.assertEqual(r.exit_reason(p,6.9,t+720,None,False,.0005),'HARD_STOP')

    def test_whole_lot_funding_and_cash_fee_accounting(self):
        book=r.Book(100000,'turn',20);day='2026-09-01';t=r.st.epoch(day,'10:00')
        data=type('D',(),{'archive':type('A',(),{'contracts':{S:{'lot_size':100}}})(),'context':{day:{S:{'volume':100000}}}})()
        book.buy(data,S,10,t,True,{'ts':t-60,'day':day});p=book.positions[S]
        self.assertEqual(p['qty']%100,0);self.assertGreaterEqual(book.cash,0)
        book.sell(S,12,t+60,'TEST');self.assertAlmostEqual(book.cash-100000,book.realized)
        self.assertEqual([x['side'] for x in book.fills],['BUY','SELL'])

    def test_missed_early_entry_frees_contract_for_actual_cross(self):
        day='2026-09-01';t=r.st.epoch(day,'10:00');times=np.array([t,t+60,t+360]);a=np.column_stack([times-60,np.full((3,4),10.),np.ones(3)*100])
        archive=type('A',(),{'contracts':{S:{'lot_size':100}},'expiries':{S:'2026-09-29'}})()
        data=type('D',(),{'archive':archive,'context':{day:{S:{'volume':100000}}},'series':{S:{'t':times,'a':a,'m':np.array([-.1,.1,.2]),'cross_t':np.array([t+60]),'closed':np.full(3,-1),'table':{}}}})()
        book=r.Book(100000,'turn');e={'symbol':S,'ts':t,'day':day}
        book.offer(e,True);book.step(data,t+60);self.assertFalse(book.positions);self.assertFalse(book.pending)
        book.offer({'symbol':S,'ts':t+60,'day':day},False);book.step(data,t+360)
        self.assertEqual(len(book.episodes),1);self.assertFalse(book.episodes[0]['early'])

    def test_prediction_preprocessing_never_reads_holdout_labels(self):
        rng=np.random.default_rng(7);train=pd.DataFrame({'x':rng.normal(size=100)});train['cross_30']=(train.x>0).astype(int)
        test=pd.DataFrame({'x':[-.5,.5,np.nan],'cross_30':[0,1,1]})
        p,m=r.logistic(train,test,['x']);test.cross_30=1-test.cross_30;q,n=r.logistic(train,test,['x'])
        np.testing.assert_array_equal(p,q);self.assertEqual(m,n)

    def test_features_and_opening_selector_prefix_invariance(self):
        c={S:{'root':'NIFTY','side':'CE','spot_symbol':U,'strike':24000.,'lot_size':100,'catalog_expiry':'2026-09-29'}}
        a=[]
        for day in ['2026-08-27','2026-08-28','2026-08-31','2026-09-01']:
            for i,t in enumerate(range(r.st.epoch(day,'09:15'),r.st.epoch(day,'15:30'),60)):
                price=10+np.sin(i/45);a.append([t,price,price+.2,price-.2,price,10])
        a=np.asarray(a,dtype=float);u=a.copy();u[:,1:5]=24000+(u[:,1:5]-10)*50
        at=r.st.epoch('2026-09-01','10:35');full={S:a.copy(),U:u.copy()};prefix={s:z[z[:,0]+60<=at].copy() for s,z in full.items()}
        af=r.helper.Archive(full,r.helper.augment_tables(full),c,{S:'2026-09-29'});ap=r.helper.Archive(prefix,r.helper.augment_tables(prefix),c,{S:'2026-09-29'})
        with patch.object(r,'END','2026-09-01'):
            df=r.Data(af);dp=r.Data(ap)
        kf=np.searchsorted(df.series[S]['t'],at);kp=np.searchsorted(dp.series[S]['t'],at)
        x=df.features(S,int(kf));y=dp.features(S,int(kp))
        for key in x:
            if isinstance(x[key],float) and np.isnan(x[key]):self.assertTrue(np.isnan(y[key]))
            else:self.assertEqual(x[key],y[key],key)
        self.assertEqual(af.select('2026-09-01'),ap.select('2026-09-01'))

if __name__=='__main__':unittest.main(verbosity=2)
