from datetime import datetime,date,timedelta
import pandas as pd
from shadow_lanes import incremental_regime as cache,source


def daily():
    return pd.DataFrame([dict(symbol='TEST',day=date(2026,10,26),ts=datetime(2026,10,26,15,15,tzinfo=source.IST),net_gex=100.,gamma_flip_level=95.),
        dict(symbol='TEST',day=date(2026,10,27),ts=datetime(2026,10,27,15,15,tzinfo=source.IST),net_gex=110.,gamma_flip_level=96.)])


def test_closed_session_baseline_reused_without_today_or_future_values(tmp_path,monkeypatch):
    monkeypatch.setenv('REGIME_BASELINE_DATABASE',str(tmp_path/'baseline.sqlite'))
    now=datetime(2026,10,27,18,tzinfo=source.IST)
    cache.store_baseline(daily(),now)
    for_today=cache.load_baseline(now)
    assert list(for_today.day)==[date(2026,10,26)]
    for_tomorrow=cache.load_baseline(now+timedelta(days=1))
    assert list(for_tomorrow.net_gex)==[100.,110.]


def test_intraday_cache_cannot_pretend_its_partial_session_was_a_close(tmp_path,monkeypatch):
    monkeypatch.setenv('REGIME_BASELINE_DATABASE',str(tmp_path/'baseline.sqlite'))
    now=datetime(2026,10,27,10,tzinfo=source.IST)
    cache.store_baseline(daily(),now)
    assert cache.load_baseline(now+timedelta(days=1)) is None
