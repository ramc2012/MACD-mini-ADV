import hashlib
from datetime import datetime, timedelta
import os
from pathlib import Path

os.environ.setdefault("TRADING_CALENDAR_FILE", "/tmp/macd-shadow-test-calendar.json")

import httpx
import pytest

from shadow_lanes.store import ShadowStore
from shadow_lanes import source
from shadow_lanes.service import create_app, ShadowWorker, FEATURE_STEPS

NOW = datetime(2026, 10, 26, 14, 50, tzinfo=source.IST)


def ready(now=NOW):
    return dict(status="ok", source_session=str(now.date()), policy="frozen-test-policy", blockers=[],
                watchlist=[{"instrument": "NSE_FO|123", "symbol": "SBIN", "source_mark": 10, "actionable": True}])


def test_publications_are_immutable_and_never_actionable(tmp_path):
    store = ShadowStore(tmp_path / "fresh.sqlite3")
    first = ready()
    assert store.publish("vanguard", NOW.date(), NOW.isoformat(), "frozen-test-policy", first)
    assert not store.publish("vanguard", NOW.date(), NOW.isoformat(), "changed-policy", {**first, "watchlist": [{"symbol": "OTHER"}]})
    row = store.publications("vanguard")[0]
    assert row["watchlist"][0]["symbol"] == "SBIN"
    assert row["watchlist"][0]["actionable"] is False
    assert row["execution_enabled"] is False and row["shadow_only"] is True
    assert row["policy"] == "frozen-test-policy"


@pytest.mark.parametrize("payload", [dict(status="blocked", watchlist=[{}]), dict(status="ok", watchlist=[])])
def test_incomplete_inputs_never_create_a_frozen_cohort(tmp_path, payload):
    with pytest.raises(ValueError):
        ShadowStore(tmp_path / "fresh.sqlite3").publish("vanguard", NOW.date(), NOW, "policy", payload)


def test_outcomes_are_append_only_exact_identity_observations(tmp_path):
    store = ShadowStore(tmp_path / "fresh.sqlite3")
    store.publish("vanguard", NOW.date(), NOW, "policy", ready())
    identity = store.publications()[0]["id"]
    result = dict(baseline_price=10, observed_price=11, observed_return=.1)
    assert store.record_outcome(identity, "NSE_FO|123", "D1", NOW, result)
    assert not store.record_outcome(identity, "NSE_FO|123", "D1", NOW, {**result, "observed_price": 20})
    assert store.snapshot("vanguard")["outcomes"][0]["observed_price"] == 11


def test_no_historical_publication_or_database_access_outside_window(tmp_path, monkeypatch):
    store = ShadowStore(tmp_path / "fresh.sqlite3")
    monkeypatch.setattr(source, "connect", lambda **kwargs: pytest.fail("No input query outside the window"))
    ShadowWorker(store).check(NOW - timedelta(days=1))
    assert store.publications() == []
    assert store.snapshot("vanguard")["readiness"]["status"] == "awaiting_window"


def test_diagnostic_check_does_not_turn_old_candidates_into_new_decisions(tmp_path, monkeypatch):
    class Connection:
        def close(self): pass
    monkeypatch.setattr(source, "connect", lambda **kwargs: Connection())
    monkeypatch.setattr(source, "vanguard", lambda connection, now: ready())
    monkeypatch.setattr(source, "cbe", lambda connection, now: ready())
    store = ShadowStore(tmp_path / "fresh.sqlite3")
    ShadowWorker(store).check(NOW - timedelta(days=1), diagnostic=True)
    assert store.publications() == []
    assert store.snapshot("vanguard")["readiness"]["diagnostic"] is True


def test_worker_freezes_once_without_calling_any_legacy_publisher(tmp_path, monkeypatch):
    class Connection:
        def close(self): pass
    monkeypatch.setattr(source, "connect", lambda **kwargs: Connection())
    monkeypatch.setattr(source, "vanguard", lambda connection, now: ready(now))
    monkeypatch.setattr(source, "observe_outcomes", lambda *args: [])
    store = ShadowStore(tmp_path / "fresh.sqlite3")
    worker = ShadowWorker(store)
    worker.check(NOW)
    monkeypatch.setattr(source, "vanguard", lambda *args: pytest.fail("Frozen membership cannot be rescored"))
    worker.check(NOW + timedelta(minutes=1))
    assert len(store.publications()) == 1
    assert store.snapshot("vanguard")["readiness"]["status"] == "frozen"


@pytest.mark.parametrize("lane,hour,minute,expected", [
    ("vanguard", 14, 44, False), ("vanguard", 14, 45, True), ("vanguard", 15, 15, False),
    ("cbe", 15, 15, False), ("cbe", 15, 30, False), ("cbe", 15, 31, True), ("cbe", 16, 30, False),
])
def test_causal_publication_windows(lane, hour, minute, expected):
    assert source.publication_window(NOW.replace(hour=hour, minute=minute), lane) is expected


def test_weekends_cannot_publish_and_horizons_follow_sessions():
    sunday = NOW - timedelta(days=1)
    assert not source.publication_window(sunday, "vanguard")
    assert str(source.source_session(sunday, "vanguard")) == "2026-10-23"
    assert str(source.next_session(source.source_session(sunday, "vanguard"))) == "2026-10-26"


def test_feature_allowlist_has_no_execution_or_broker_ingest_commands():
    assert FEATURE_STEPS
    assert all(step[1].startswith("features/") for step in FEATURE_STEPS)
    assert not any(word in step[1] for step in FEATURE_STEPS for word in ("paper/", "fusion/", "ingest/", "train_"))


def test_failed_feature_stage_blocks_vanguard_publication(tmp_path, monkeypatch):
    class Connection:
        def close(self): pass
    monkeypatch.setattr(source, "connect", lambda **kwargs: Connection())
    monkeypatch.setattr(source, "vanguard", lambda *args: ready())
    store = ShadowStore(tmp_path / "fresh.sqlite3")
    worker = ShadowWorker(store)
    worker.feature_error = "flow: permission denied"
    worker.check(NOW)
    assert not store.publications()
    assert "refresh failed" in store.snapshot("vanguard")["readiness"]["blockers"][0]


def test_managed_schema_needs_no_legacy_table_ownership(monkeypatch):
    from features.m2_flow import apply_schema
    queries = []
    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, sql): queries.append(sql)
    class Connection:
        def cursor(self): return Cursor()
    monkeypatch.setenv("VANGUARD_SCHEMA_MANAGED", "true")
    apply_schema(Connection())
    assert len(queries) == 1 and queries[0].startswith("SELECT ")


def test_outcomes_cannot_read_an_incomplete_future_bar():
    class Connection:
        def cursor(self): pytest.fail("The future bar must not be queried")
    publication = {"lane": "vanguard", "source_session": "2026-10-26", "watchlist": [{"instrument": "NSE_FO|123", "source_mark": 10}]}
    assert list(source.observe_outcomes(Connection(), publication, NOW)) == []


def test_http_get_is_read_only_and_there_is_no_execute_route(tmp_path, monkeypatch):
    import asyncio
    monkeypatch.setenv("MACD_API_TOKEN", "synthetic-test-token")
    store = ShadowStore(tmp_path / "fresh.sqlite3")
    api = create_app(store=store, worker_enabled=False)
    before = hashlib.sha256(store.path.read_bytes()).digest()
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as http:
            assert (await http.get("/api/shadow-lanes/vanguard")).status_code == 401
            headers = {"X-Macd-Token": "synthetic-test-token"}
            response = await http.get("/api/shadow-lanes/vanguard", headers=headers)
            assert response.status_code == 200 and response.json()["publications"] == []
            assert response.json()["execution_enabled"] is False
            assert (await http.post("/api/shadow-lanes/vanguard", headers=headers)).status_code == 405
            assert (await http.get("/api/shadow-lanes/other", headers=headers)).status_code == 404
    asyncio.run(scenario())
    assert hashlib.sha256(store.path.read_bytes()).digest() == before


def test_archive_reader_preserves_the_original_file(tmp_path, monkeypatch):
    import asyncio
    path = tmp_path / "old-paper.json"
    path.write_text('{"open_positions":[{"instrument":"SBIN","quantity":3}]}')
    before = path.read_bytes()
    monkeypatch.setenv("LEGACY_CBE_FILE", str(path))
    monkeypatch.delenv("MACD_API_TOKEN", raising=False)
    api = create_app(ShadowStore(tmp_path / "fresh.sqlite3"), worker_enabled=False)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as http:
            response = await http.get("/api/shadow-lanes/cbe/archive")
            assert response.json()["read_only"] is True
            assert response.json()["book"]["open_positions"][0]["quantity"] == 3
    asyncio.run(scenario())
    assert path.read_bytes() == before
