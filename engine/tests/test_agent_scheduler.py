"""Scheduled and manual writes share a lock, and missing bars can recover."""
import asyncio
import threading
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException

from macd_trader import agent_equity, desk, desk_routes, option_agent


NOW = datetime(2026, 9, 28, 14, 48, tzinfo=ZoneInfo("Asia/Kolkata"))  # after the 14:47 emission


@pytest.fixture
def scheduler(monkeypatch):
    host = desk.DeskMixin()
    host.init_desk_state()
    host.agent_status["prepared_day"] = NOW.date().isoformat()
    host.mp = SimpleNamespace(lot_sizes={})
    host.settings = SimpleNamespace(contract_snapshot_path=None)
    host._agent_job = lambda fn: fn(None)
    lock = asyncio.Lock()
    monkeypatch.setattr(desk, "AGENT_JOB_LOCK", lock)
    monkeypatch.setattr(desk_routes, "_agent_lock", lock)
    monkeypatch.setattr(desk_routes, "engine", host)
    monkeypatch.setattr(desk_routes, "_agent_lots", lambda: {})
    monkeypatch.setattr(agent_equity, "known_lots", lambda *args: {})
    return host


@pytest.mark.asyncio
async def test_manual_emit_is_busy_during_a_scheduled_write(scheduler, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls = []

    def emit(*args, **kwargs):
        calls.append("emit")
        entered.set()
        assert release.wait(3)
        return {"status": "emitted", "run_id": 1}

    monkeypatch.setattr(option_agent, "emit", emit)
    scheduled = asyncio.create_task(scheduler._agent_step(NOW))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        assert (await desk_routes.agent_status())["busy"] is True
        with pytest.raises(HTTPException) as error:
            await desk_routes.agent_run(NOW.date().isoformat())
        assert error.value.status_code == 409
        duplicate = asyncio.create_task(scheduler._agent_step(NOW))
        await asyncio.sleep(0)
        assert not duplicate.done()
    finally:
        release.set()
        await scheduled
    await duplicate
    assert calls == ["emit"]
    assert (await desk_routes.agent_status())["busy"] is False


@pytest.mark.asyncio
async def test_incomplete_emission_retries_after_backoff_then_finishes(scheduler, monkeypatch):
    elapsed = [100.0]
    monkeypatch.setattr(desk, "time", SimpleNamespace(monotonic=lambda: elapsed[0]))
    results = iter([{"status": "no_eligible_candidates"}, {"status": "emitted", "run_id": 1}])
    calls = []

    def emit(*args, **kwargs):
        calls.append("emit")
        return next(results)

    monkeypatch.setattr(option_agent, "emit", emit)
    assert await scheduler._agent_step(NOW)
    assert scheduler.agent_status["emitted_day"] is None
    elapsed[0] += 5
    assert not await scheduler._agent_step(NOW)
    assert calls == ["emit"]
    elapsed[0] += desk.AGENT_RETRY_SECONDS
    assert await scheduler._agent_step(NOW)
    assert scheduler.agent_status["emitted_day"] == NOW.date().isoformat()
    assert not await scheduler._agent_step(NOW)
    assert calls == ["emit", "emit"]
