import asyncio
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
import smart_replay as r

SYMBOL='NSE:NIFTY26SEP24000CE'
SPOT='NSE:NIFTY50-INDEX'

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.lanes=[];r.CLOCK=r.st.epoch('2026-09-01','10:00');r.install_clock()

    def tearDown(self):
        for lane in self.lanes:lane._db.close();lane.repository._connection.close()
        self.tmp.cleanup()

    def lane(self,rule='baseline',price=10):
        s=r.Settings(_env_file=None,execution_mode='paper',slippage_bps=0,allow_live_orders=False)
        lane=r.OverlayLane('test',100000,s,r.NoOrders(),r.EventHub(),str(Path(self.tmp.name)/(str(len(self.lanes))+'.sqlite3')),rule=rule,archive=object())
        lane.set_tradable_contracts({SYMBOL:100});lane.set_contract_context({SYMBOL:{'expiry':'2026-09-29','option_type':'CE','strike':24000,'spot_symbol':SPOT,'volume':1000000,'oi':0}})
        lane.set_resolvers(macd_confirm=lambda *a:True,dispersion=lambda:None);lane.execution.set_quote(SYMBOL,price,r.real_time(r.CLOCK));self.lanes.append(lane);return lane

    def offer(self,lane,price=10):
        with patch.object(r.sm,'score',lambda f:.9):
            lane.on_macd_signal(r.Signal(SYMBOL,'BUY','TEST',price,.1,0.,.1,timestamp=r.real_time(r.CLOCK),evaluated_candle_timestamp=int(r.CLOCK)), 'CE',{'test':1},now=r.real_time(r.CLOCK))

    def advance(self,lane,seconds,price=10,fresh=True):
        r.CLOCK+=seconds
        if fresh:lane.execution.set_quote(SYMBOL,price,r.real_time(r.CLOCK))
        asyncio.run(lane.step(r.real_time(r.CLOCK)))
        self.assertIsNone(lane.error)

    def test_delay_and_fresh_quote_gate(self):
        lane=self.lane();self.offer(lane);self.advance(lane,300);self.assertFalse(lane.portfolio.positions)
        self.advance(lane,60);self.assertIn(SYMBOL,lane.portfolio.positions)
        trades=lane.repository.rows('trades',100);self.assertEqual(len(trades),1)
        self.assertEqual(dt.datetime.fromisoformat(trades[0]['timestamp']).timestamp(),r.CLOCK)
        self.assertGreaterEqual(r.CLOCK-r.st.epoch('2026-09-01','10:00'),315)

    def test_cached_minute_cannot_pass_actual_quote_age(self):
        lane=self.lane();self.offer(lane);self.advance(lane,360,fresh=False)
        self.assertFalse(lane.portfolio.positions);self.assertEqual(lane.counts,{'NO_PRICE':1})

    def test_younger_candidate_can_join_mature_rank_batch_at_five_minutes(self):
        lane=self.lane();other='NSE:NIFTY26SEP24100CE'
        context=lane._contracts[SYMBOL].copy();lane.set_tradable_contracts({SYMBOL:100,other:100});lane.set_contract_context({SYMBOL:context,other:context})
        self.offer(lane);r.CLOCK+=60
        with patch.object(r.sm,'score',lambda f:.9):
            lane.on_macd_signal(r.Signal(other,'BUY','TEST',10,.1,0.,.1,timestamp=r.real_time(r.CLOCK),evaluated_candle_timestamp=int(r.CLOCK)), 'CE',{'test':1},now=r.real_time(r.CLOCK))
        r.CLOCK+=240;asyncio.run(lane.step(r.real_time(r.CLOCK)));self.assertFalse(lane.portfolio.positions)
        r.CLOCK+=60
        for s in [SYMBOL,other]:lane.execution.set_quote(s,10,r.real_time(r.CLOCK))
        asyncio.run(lane.step(r.real_time(r.CLOCK)))
        self.assertEqual(set(lane.portfolio.positions),{SYMBOL,other})
        self.assertIsNone(lane.error)

    def test_real_production_stages_target_and_accounting(self):
        lane=self.lane();self.offer(lane);self.advance(lane,360)
        for price in [13,15,17.5,20]:
            r.CLOCK+=60;asyncio.run(lane.execution.on_tick(SYMBOL,price,r.real_time(r.CLOCK)))
        self.assertFalse(lane.portfolio.positions)
        fills=sorted(lane.repository.rows('trades',100),key=lambda t:t['timestamp'])
        self.assertEqual([x['side'] for x in fills],['BUY','SELL','SELL','SELL','SELL'])
        self.assertEqual(sum(x['quantity'] for x in fills if x['side']=='BUY'),sum(x['quantity'] for x in fills if x['side']=='SELL'))
        cash=100000-sum(x['quantity']*x['price']*(1 if x['side']=='BUY' else -1) for x in fills)
        self.assertAlmostEqual(cash,lane.portfolio.cash);self.assertAlmostEqual(cash-100000,lane.portfolio.realized_pnl)
        self.assertGreaterEqual(lane.portfolio.cash,0)

    def test_overlay_rejects_and_unknown_preserves(self):
        lane=self.lane('premium_st');lane.archive=type('A',(),{'minutes':{},'tables':{}})();self.offer(lane)
        with patch.object(r.st,'features',return_value=({'premium_st':False},{})):self.advance(lane,360)
        self.assertFalse(lane.portfolio.positions);self.assertEqual(lane.counts,{'OVERLAY_REJECTED':1})
        self.offer(lane)
        with patch.object(r.st,'features',return_value=({'premium_st':None},{})):self.advance(lane,360)
        self.assertIn(SYMBOL,lane.portfolio.positions);self.assertEqual(lane.overlay_counts['unknown_passed'],1)

    def test_whole_lot_ticket_and_broker_guard(self):
        lane=self.lane(price=200);self.offer(lane,200);self.advance(lane,360,200)
        self.assertFalse(lane.portfolio.positions);self.assertEqual(lane.counts,{'LOT_ABOVE_TICKET':1});self.assertEqual(lane.repository.rows('trades',100),[])

    def test_serialization_preserves_real_aware_datetimes(self):
        at=r.real_time(r.CLOCK)
        self.assertTrue(isinstance(at,r.ReplayDatetime));self.assertEqual(r.repository._encode(at),at.isoformat())

    def test_expired_inventory_is_valued_without_execution_or_cash_release(self):
        lane=self.lane();self.offer(lane);self.advance(lane,360)
        r.CLOCK=r.st.epoch('2026-09-30','10:00')
        c={SYMBOL:{'spot_symbol':SPOT,'side':'CE','strike':24000}}
        a=np.array([[r.st.epoch('2026-09-29','15:29'),10,10,10,10,0.]])
        u=a.copy();u[:,1:5]=24123
        archive=r.Archive({SYMBOL:a,SPOT:u},{},c,{SYMBOL:'2026-09-29'})
        cash=lane.portfolio.cash;qty=lane.portfolio.positions[SYMBOL].quantity;n=len(lane.repository.rows('trades',100))
        with patch.dict(r.st.CONTRACTS,c):v=r.value_residuals(lane,archive,r.CLOCK)
        self.assertEqual(lane.portfolio.positions[SYMBOL].last_price,123)
        self.assertEqual(lane.portfolio.positions[SYMBOL].quantity,qty);self.assertEqual(lane.portfolio.cash,cash)
        self.assertEqual(len(lane.repository.rows('trades',100)),n);self.assertIsNone(archive.quote(SYMBOL,r.CLOCK,86400))
        self.assertEqual(v['expired_residual_value'],123*qty)
        self.assertEqual(v['mark_kinds'],{'expiry-intrinsic-scenario':1})

    def test_zero_volume_valuation_cannot_become_an_execution_quote(self):
        lane=self.lane();self.offer(lane);self.advance(lane,360)
        c={SYMBOL:{'spot_symbol':SPOT,'side':'CE','strike':24000}}
        a=np.array([[r.CLOCK-60,12,12,12,12,0.]])
        archive=r.Archive({SYMBOL:a},{},c,{SYMBOL:'2026-09-29'})
        oldquote=lane.execution.last_prices[SYMBOL];cash=lane.portfolio.cash
        r.value_residuals(lane,archive,r.CLOCK)
        self.assertEqual(lane.portfolio.positions[SYMBOL].last_price,12)
        self.assertEqual(lane.execution.last_prices[SYMBOL],oldquote);self.assertEqual(lane.portfolio.cash,cash)
        self.assertIsNone(archive.quote(SYMBOL,r.CLOCK,0))

    def test_archive_feature_prefix_and_zero_volume_quotes(self):
        contracts={SYMBOL:{'root':'NIFTY','side':'CE','spot_symbol':SPOT,'strike':24000.,'lot_size':100,'catalog_expiry':'2026-09-29'}}
        a=[]
        for day in ['2026-08-27','2026-08-28','2026-08-31','2026-09-01']:
            for i,t in enumerate(range(r.st.epoch(day,'09:15'),r.st.epoch(day,'15:30'),60)):
                price=10+np.sin(i/45);a.append([t,price,price+.2,price-.2,price,10])
        a=np.asarray(a,dtype=float);spot=a.copy();spot[:,1:5]=24000+(spot[:,1:5]-10)*50
        at=r.st.epoch('2026-09-01','10:35');row={'symbol':SYMBOL,'ts':at,'bucket':r.st.epoch('2026-09-01','10:15'),'price':float(a[a[:,0]+60<=at][-1,4]),'macd':.2}
        full={SYMBOL:a.copy(),SPOT:spot.copy()};prefix={s:x[x[:,0]+60<=at].copy() for s,x in full.items()}
        af=r.Archive(full,r.augment_tables(full),contracts,{SYMBOL:'2026-09-29'});ap=r.Archive(prefix,r.augment_tables(prefix),contracts,{SYMBOL:'2026-09-29'})
        self.assertEqual(af.features(row,(3,2)),ap.features(row,(3,2)))
        self.assertEqual(af.select('2026-09-01'),ap.select('2026-09-01'))
        k=af.index(SYMBOL,at);af.minutes[SYMBOL][k,5]=0;self.assertIsNone(af.quote(SYMBOL,at,0))

if __name__=='__main__':unittest.main(verbosity=2)
