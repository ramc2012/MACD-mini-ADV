import unittest
import numpy as np
import pandas as pd
from study import aggregate, atr, supertrend, pivots, indicators, conditions, net_return, holm

class ResearchChecks(unittest.TestCase):
    def fixture(self):
        rows=[];rng=np.random.default_rng(23);price=100
        # Monday 3 Aug 2026; advance around the known September holiday.
        for day in [20668,20669,20670,20671,20672,20675,20676,20677,20678,20679]:
            for k in range(75):
                t=day*86400-19800+33300+k*300
                c=price+rng.normal(0,.5)
                rows.append([t,price,max(price,c)+.2,min(price,c)-.2,c,500,5,t+300,day]);price=c
        return pd.DataFrame(rows,columns=['t','o','h','l','c','v','n','end','day'])

    def test_supertrend_causal_prefix(self):
        f=self.fixture();all_values=supertrend(f)
        for n in [11,77,129,350]:
            prefix=supertrend(f.iloc[:n])
            for key in ['atr','line','dir']:
                np.testing.assert_allclose(prefix[key],all_values[key][:n],equal_nan=True)

    def test_pivots_do_not_read_current_period(self):
        f=self.fixture();p=pivots(f);w=pivots(f,True)
        day=f.day.iloc[-1];mut=f.copy();mut.loc[mut.day==day,['h','l','c']]=[1000,1,900]
        np.testing.assert_allclose(p[f.day==day],pivots(mut)[f.day==day],equal_nan=True)
        np.testing.assert_allclose(w[f.day==day],pivots(mut,True)[f.day==day],equal_nan=True)
        # No prior session: no fabricated zero pivot.
        self.assertTrue(np.isnan(p[f.day==f.day.iloc[0]]).all())

    def test_session_anchor(self):
        f=self.fixture();g=aggregate(f,30)
        self.assertEqual(int((g.t.iloc[0]+19800)%86400),33300)
        self.assertEqual(int(g.end.iloc[0]-g.t.iloc[0]),1800)
        self.assertEqual(int(g.end.iloc[12]-g.t.iloc[12]),900)

    def test_atr_seed_and_costs(self):
        f=self.fixture();a=atr(f,10);self.assertTrue(np.isnan(a[:9]).all());self.assertGreater(a[9],0)
        trade={'entry':10,'fills':[(100,12,1,'partial'),(200,15,3,'exit')]}
        values=[net_return(trade,100,s) for s in [5,25,50]]
        self.assertTrue(values[0]>values[1]>values[2])

    def test_multiple_testing(self):
        np.testing.assert_allclose(holm([.01,.03,.2]),[.03,.06,.2])

    def test_boolean_features_and_ce_pe_mapping(self):
        f=self.fixture();idx=indicators(f);i=350;time=int(f.end.iloc[i])
        ce=conditions(f,idx,i,f,idx,1,time);pe=conditions(f,idx,i,f,idx,-1,time)
        self.assertTrue(all(v is None or type(v) is bool for v in ce.values()))
        self.assertNotEqual(ce['spot_st30_10_3_align'],pe['spot_st30_10_3_align'])

    def test_unfunded_entry_does_not_block_later_contract(self):
        from capital import replay,INITIAL
        day=20668;start=day*86400-19800+33300
        def event(price,t):
            tr={'entry':price,'entry_ts':t,'exit_ts':t+600,'fills':[(t+600,price*1.2,4,'exit')]}
            return {'symbol':'TEST','lot':1,'day':day,'features':{},'trades':{'standard_baseline':tr}}
        rows=[event(200000,start),event(10,start+300)]
        prices={'TEST':(np.array([start,start+900]),np.array([10,12]))}
        r=replay(rows,prices,'standard','baseline',{day},[day])
        self.assertEqual(r['entries'],1);self.assertEqual(r['blocked']['cash_or_ticket'],1)
        self.assertGreater(r['pnl'],0)
        self.assertAlmostEqual(r['marks'][-1]['equity'],INITIAL+r['pnl'])
        zero=replay(rows,prices,'standard','no_trade',{day},[day]);self.assertEqual(zero['pnl'],0)

if __name__=='__main__':unittest.main()
