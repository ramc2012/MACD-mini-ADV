from datetime import datetime,timedelta
import asyncio,json
from fastapi.testclient import TestClient
from shadow_lanes import live_market as module,source
from shadow_lanes.live_market import LiveMarket
from shadow_lanes.contracts import matches
from shadow_lanes.service import create_app
from shadow_lanes.store import ShadowStore

NOW=datetime(2026,10,27,10,tzinfo=source.IST)


def tick(price=100,at=NOW):
    return dict(symbol='NSE:TEST-EQ',ltp=price,broker='fyers',exchange_ts_ms=int(at.timestamp()*1000))


def test_shared_ticks_keep_exchange_time_and_never_regress_or_invent_prices():
    market=LiveMarket();market.allowed={'NSE:TEST-EQ'}
    assert market.ingest('md.tick.fyers.TEST',tick(),NOW)
    assert not market.ingest('md.tick.upstox.TEST',tick(99,NOW-timedelta(seconds=30)),NOW)
    assert not market.ingest('md.tick.fyers.TEST',tick(float('nan')),NOW)
    assert not market.ingest('md.tick.fyers.TEST',tick(101,NOW+timedelta(minutes=1)),NOW)
    assert market.quotes['NSE:TEST-EQ']['ltp']==100
    assert market.quotes['NSE:TEST-EQ']['exchange_ts_ms']==int(NOW.timestamp()*1000)


def test_reused_flow_does_not_turn_a_closed_market_or_old_quote_into_live(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return NOW.replace(hour=18)
    monkeypatch.setattr(module,'datetime',Clock)
    market=LiveMarket();market.allowed={'NSE:TEST-EQ'}
    market.ingest('md.tick.fyers.TEST',tick(),NOW)
    assert market.ingest('fts.flow.TEST',dict(symbol='NSE:TEST-EQ',computed_at_ms=int(NOW.timestamp()*1000),session_cvd=123),NOW)
    assert market.snapshot()['market_open'] is False and market.reused==1
    f=market.contexts([{'id':'test','instrument':'TEST'}])['test']
    assert f['stream.status']=='market_closed' and f['stream.cvd']==123 and f['stream.age_seconds']>0


def test_current_identity_rejects_same_recycled_token_with_wrong_expiry_or_strike():
    row=dict(instrument='NSE_FO|42',symbol='TEST',option_type='PE',strike=100,expiry='2026-11-23')
    meta=dict(upstox='NSE_FO|42',underlying='TEST',option_type='PE',strike=100,expiry_ms=int(datetime(2026,11,23,tzinfo=source.IST).timestamp()*1000),lot_size=50,tick_size=.05)
    assert matches(row,meta)
    assert not matches({**row,'expiry':'2026-06-30'},meta)
    assert not matches({**row,'strike':105},meta)
    assert not matches({**row,'symbol':'OTHER'},meta)


def test_live_snapshot_requires_auth_and_stream_preserves_the_same_cached_frame(tmp_path,monkeypatch):
    monkeypatch.setenv('MACD_API_TOKEN','test-only-token');monkeypatch.setenv('PAPER_LANES_ENABLED','false')
    app=create_app(ShadowStore(tmp_path/'journal.sqlite'),False)
    c=TestClient(app)
    assert c.get('/api/lane-live/snapshot').status_code==401
    assert c.get('/api/lane-live/snapshot',headers={'X-Macd-Token':'test-only-token'}).json()['broker_requests']==0
    endpoint=next(r.endpoint for r in app.routes if r.path=='/api/lane-live/stream')
    async def read_first():
        response=await endpoint();frame=await response.body_iterator.__anext__();await response.body_iterator.aclose()
        return json.loads(frame.removeprefix('data: ').strip())
    assert asyncio.run(read_first())['duplicate_flow_trackers']==0
