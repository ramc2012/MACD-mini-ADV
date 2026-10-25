from datetime import date, datetime, timedelta
import importlib.util
import os
from pathlib import Path

os.environ.setdefault("TRADING_CALENDAR_FILE", "/tmp/macd-shadow-test-calendar.json")
import pytest

path = Path(__file__).resolve().parents[2] / "vanguard/ingest/hub_feed.py"
spec = importlib.util.spec_from_file_location("lane_feed_test", path)
feed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(feed)
NOW = datetime(2026, 10, 26, 10, 0, tzinfo=feed.IST)


def candles():
    start = NOW.replace(hour=9, minute=15)
    return [[(start + timedelta(minutes=3 * i)).isoformat(), 100+i, 102+i, 99+i, 101+i, 5, 0] for i in range(10)]


def test_wakeup_just_after_deadline_retains_due_job():
    when = NOW.replace(hour=9, minute=45, second=25)
    assert (when, "spot") in feed.next_jobs(when + timedelta(seconds=1))
    assert (when, "spot") not in feed.next_jobs(when + timedelta(minutes=6))


def test_derivative_close_and_cash_stub_are_separate():
    assert feed.bar_ends(NOW.date())[-1].strftime("%H:%M") == "15:30"
    assert feed.bar_ends(NOW.date(), derivatives=True)[-1].strftime("%H:%M") == "15:40"
    assert feed.next_jobs(NOW - timedelta(days=1)) == []


def test_only_contiguous_completed_3m_bars_become_a_30m_bar():
    result = feed.aggregate_30minute(candles(), NOW)
    assert len(result) == 1
    assert result[0]["time"].strftime("%H:%M") == "09:15"
    assert result[0]["open"] == 100 and result[0]["close"] == 110 and result[0]["volume"] == 50
    assert feed.aggregate_30minute(candles()[:-1], NOW) == []
    assert feed.aggregate_30minute(candles(), NOW.replace(hour=9, minute=44)) == []


def test_chain_units_and_cumulative_volume_baselines():
    body = {"data": [{"strike_price": 100, "underlying_spot_price": 100, "call_options": {
        "instrument_key": "NSE_FO|1", "market_data": {"ltp": 5, "volume": 100, "oi": 20}, "option_greeks": {"iv": 25}}}]}
    previous = {}
    row = feed.chain_rows("TEST", date(2026, 11, 24), body, NOW, previous)[0]
    assert row[10] is None and row[12] == .25
    body["data"][0]["call_options"]["market_data"]["volume"] = 130
    row = feed.chain_rows("TEST", date(2026, 11, 24), body, NOW, previous)[0]
    assert row[10] == 30
    assert feed.iv_fraction(float("inf")) is None


def test_stale_or_invalid_spot_bars_cannot_be_relabelled_today():
    old = [[(datetime.fromisoformat(r[0]) - timedelta(days=1)).isoformat(), *r[1:]] for r in candles()]
    assert feed.aggregate_30minute(old, NOW) == []
    invalid = candles()
    invalid[3][2] = float("inf")
    assert feed.aggregate_30minute(invalid, NOW) == []


def test_hub_failure_and_unquoted_contracts_are_not_healthy_data():
    import httpx
    def handler(request): return httpx.Response(200, json={"status": "error"})
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(RuntimeError): feed.hub(http, "option/chain")
    body = {"data": [{"call_options": {"instrument_key": "NSE_FO|1", "market_data": {"ltp": float("inf")}}}]}
    assert feed.chain_rows("TEST", NOW.date(), body, NOW, {}) == []


def test_contract_rollover_uses_hub_reference_metadata_without_touching_coverage(monkeypatch):
    import httpx
    writes, requests = [], []
    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, *args): pass
        def fetchall(self): return [("TEST", "STOCK")]
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def cursor(self): return Cursor()
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=[dict(upstox="NSE_FO|42", canonical="NSE:TEST26NOV100CE", lot_size=100,
            expiry_ms=int(NOW.timestamp()*1000)+30*86400000, option_type="CE", strike=100, tick_size=.05)])
    monkeypatch.setattr(feed, "execute_values", lambda cur, sql, rows, **kw: writes.append((sql, rows)))
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        assert feed.refresh_contract_catalog(Connection(), http) == 1
    assert requests[0].url.path == "/v1/options" and requests[0].url.params["expiries"] == "3"
    sql, rows = writes[0]
    assert rows[0][0] == "NSE_FO|42" and rows[0][6] == 100
    assert "sync_status=" not in sql and "candle_count=" not in sql and "DELETE" not in sql


def test_failed_commit_does_not_advance_in_memory_baseline(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None): return NOW.astimezone(tz) if tz else NOW
    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *args): return False
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def cursor(self): return Cursor()
    body = {"data": [{"strike_price": 100, "call_options": {"instrument_key": "NSE_FO|1", "market_data": {"ltp": 5, "volume": 130}}}]}
    monkeypatch.setattr(feed, "datetime", Clock)
    monkeypatch.setattr(feed, "hub", lambda *args, **kwargs: body)
    monkeypatch.setattr(feed, "publish_snapshot", lambda *args: None)
    monkeypatch.setattr(feed, "execute_values", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("mock failed commit")))
    previous = {"NSE_FO|1": (NOW.date(), 100)}
    with pytest.raises(RuntimeError):
        feed.chain_sweep(Connection(), None, [("TEST", "key", date(2026, 11, 24))], previous)
    assert previous == {"NSE_FO|1": (NOW.date(), 100)}
