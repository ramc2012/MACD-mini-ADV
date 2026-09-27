"""Changes to feed subscriptions must rebuild the feed, not just indicators."""

import asyncio
import importlib
from types import SimpleNamespace

from macd_trader.config import Settings


def test_put_symbols_reports_pending_and_reconfigures_the_feed(monkeypatch):
    module = importlib.import_module("macd_trader.app")
    current = Settings(symbols_csv="NSE:A-EQ")
    calls = []
    completed = asyncio.Event()

    async def full_reconfigure(settings):
        calls.append(("feed", settings.symbols))
        fake.settings = settings
        fake.status = "connected"
        completed.set()

    async def strategy_reconfigure(settings):
        calls.append(("strategy", settings.symbols))

    fake = SimpleNamespace(
        settings=current, status="connected", error=None,
        events=SimpleNamespace(publish=lambda *args: None),
        broker_status=lambda: {"status": fake.status, "error": fake.error},
        reconfigure=full_reconfigure,
        reconfigure_strategy=strategy_reconfigure,
    )
    monkeypatch.setattr(module, "engine", fake)
    monkeypatch.setattr(module, "settings_store", SimpleNamespace(save=lambda data: None))
    monkeypatch.setattr(module, "persist_credentials", lambda settings: None)

    async def run():
        result = await module.update_settings(module.SettingsInput(symbols=["NSE:B-EQ"]))
        assert result["symbols"] == ["NSE:B-EQ"]
        assert result["connect_state"] == "connecting"
        assert result["broker"]["status"] == "connecting"
        assert fake.settings.symbols == ["NSE:A-EQ"]
        await completed.wait()

    asyncio.run(run())
    assert calls == [("feed", ["NSE:B-EQ"])]
    assert fake.settings.symbols == ["NSE:B-EQ"]


def test_put_strategy_only_does_not_reconnect(monkeypatch):
    module = importlib.import_module("macd_trader.app")
    current = Settings(symbols_csv="NSE:A-EQ")
    calls = []

    async def full_reconfigure(settings):
        calls.append("feed")

    async def strategy_reconfigure(settings):
        calls.append("strategy")
        fake.settings = settings

    fake = SimpleNamespace(
        settings=current, status="connected", error=None,
        broker_status=lambda: {"status": fake.status},
        reconfigure=full_reconfigure,
        reconfigure_strategy=strategy_reconfigure,
    )
    monkeypatch.setattr(module, "engine", fake)
    monkeypatch.setattr(module, "settings_store", SimpleNamespace(save=lambda data: None))
    monkeypatch.setattr(module, "persist_credentials", lambda settings: None)

    result = asyncio.run(module.update_settings(module.SettingsInput(fast_period=10)))
    assert calls == ["strategy"]
    assert result["fast_period"] == 10


def test_save_during_feed_rebuild_keeps_the_new_token(monkeypatch):
    """A second save while the feed rebuilds must not restore the old token."""
    module = importlib.import_module("macd_trader.app")
    monkeypatch.setattr(module, "_pending_settings", None)
    current = Settings(symbols_csv="NSE:A-EQ", fyers_access_token="old-token", fast_period=12)
    applied = []
    saved = []
    release = asyncio.Event()
    lock = asyncio.Lock()

    async def full_reconfigure(settings):
        async with lock:
            await release.wait()  # the rebuild takes minutes in production
            applied.append(("feed", settings.fyers_access_token, settings.fast_period))
            fake.settings = settings

    async def strategy_reconfigure(settings):
        async with lock:
            applied.append(("strategy", settings.fyers_access_token, settings.fast_period))
            fake.settings = settings

    fake = SimpleNamespace(
        settings=current, status="connected", error=None,
        events=SimpleNamespace(publish=lambda *args: None),
        broker_status=lambda: {"status": fake.status},
        reconfigure=full_reconfigure,
        reconfigure_strategy=strategy_reconfigure,
    )
    monkeypatch.setattr(module, "engine", fake)
    monkeypatch.setattr(module, "settings_store", SimpleNamespace(save=saved.append))
    monkeypatch.setattr(module, "persist_credentials", lambda settings: None)

    async def run():
        await module.update_settings(module.SettingsInput(fyers_access_token="new-token"))
        await asyncio.sleep(0)  # the rebuild starts and holds the engine lock
        second = await module.update_settings(module.SettingsInput(fast_period=10))
        assert second["connect_state"] == "connecting"  # queued, not blocked
        assert (await module.get_settings())["fast_period"] == 10
        release.set()
        while module._settings_tasks:
            await asyncio.gather(*module._settings_tasks)

    asyncio.run(run())
    assert applied == [("feed", "new-token", 12), ("strategy", "new-token", 10)]
    assert fake.settings.fyers_access_token == "new-token"
    assert fake.settings.fast_period == 10
    assert module._pending_settings is None
