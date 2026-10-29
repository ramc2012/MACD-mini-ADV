from datetime import datetime
from fastapi.testclient import TestClient
from shadow_lanes import daily_market,source
from shadow_lanes.service import create_app
from shadow_lanes.store import ShadowStore


def test_market_and_cbe_scan_show_underlying_data_without_old_cohort_or_false_freshness(tmp_path,monkeypatch):
    monkeypatch.delenv('MACD_API_TOKEN',raising=False)
    monkeypatch.setenv('PAPER_LANES_ENABLED','false')
    market=dict(source_session='2026-10-26',observed_at='2026-10-27T08:00:00+05:30',expected=213,
        results=[dict(instrument='ABC',symbol='ABC',score=80,direction='LONG',eligible=True,sector='TEST',
        history=[dict(close=100)],latest_close=100)])
    monkeypatch.setattr(daily_market,'view',lambda *args,**kw:market)
    from shadow_lanes import daily_context
    old_context={**market,'context_policy':'retained-vanguard-context'}
    monkeypatch.setattr(daily_context,'view',lambda *args,**kw:old_context)
    client=TestClient(create_app(ShadowStore(tmp_path/'shadow.sqlite3'),False))
    assert client.get('/api/nomad/vanguard/market-context').json()==old_context
    r=client.get('/api/nomad/cbe/latest')
    assert r.status_code==200
    d=r.json()
    assert len(d['results'])==1 and d['watchlist_count']==0 and not d['watchlist']
    assert d['source_as_of']=='2026-10-26T15:30:00+05:30'
    assert d['created_at']=='2026-10-27T08:00:00+05:30'
    assert client.get('/api/nomad/invalid/market-context').status_code==404
    assert client.get('/api/nomad/vanguard/market-context?session=bad').status_code==422
