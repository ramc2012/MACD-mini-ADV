"""The snapshot frame and the analysis-leg tick fan-out.

Both were measured on the live desk on 2026-09-01: a 2.9 MiB snapshot of which
1.1 MiB was duplicated, and 6.5M of 15.6M ticks broadcast for contracts the
browser only needs an LTP for.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from macd_trader.config import Settings
from macd_trader.contracts import OptionContract
from macd_trader.engine import ANALYSIS_TICK_PUBLISH_SECONDS, TradingEngine
from macd_trader.models import Tick


def _engine() -> TradingEngine:
    return TradingEngine(Settings(symbols_csv="NSE:SBIN-EQ", feed_mode="simulation"))


def _tick(symbol: str, ltp: float = 100.0) -> Tick:
    return Tick(symbol=symbol, ltp=ltp, volume=1, timestamp=datetime.now(UTC))


def _contract(symbol: str, moneyness: str) -> OptionContract:
    return OptionContract(
        underlying="SBIN", spot_symbol="NSE:SBIN-EQ", option_type="CE", symbol=symbol,
        strike=800.0, expiry="2026-09-29", selection_price=800.0, moneyness=moneyness,
        analysis_only=moneyness != "ATM",
    )


def test_snapshot_does_not_repeat_quotes_already_carried_by_the_other_lists():
    engine = _engine()
    engine.contract_selector.contracts = {"NSE:SBIN26SEP800CE": _contract("NSE:SBIN26SEP800CE", "ATM")}
    engine.all_symbols = ["NSE:SBIN-EQ", "NSE:SBIN26SEP800CE", "NSE:NIFTY50-INDEX"]
    engine.latest_ticks = {symbol: _tick(symbol) for symbol in engine.all_symbols}

    payload = engine.snapshot()

    # The spot and option lists already carry these two rows with their ticks.
    assert [row["symbol"] for row in payload["watchlist"]] == ["NSE:NIFTY50-INDEX"]
    assert payload["watchlist"][0]["tick"]["symbol"] == "NSE:NIFTY50-INDEX"
    # The residual list is quotes only; no client ever read its indicator half.
    assert "indicator" not in payload["watchlist"][0]


def test_snapshot_carries_one_latest_indicator_map_not_two():
    engine = _engine()

    strategy = engine.snapshot()["strategy"]

    assert "indicator_history" in strategy
    assert "indicators" not in strategy


def test_analysis_legs_are_rationed_to_one_frame_a_second():
    """The ratio chart reads these legs from the database. Only the watch tab's
    LTP column needs them live, and it cannot show 300 updates a second."""
    engine = _engine()
    engine.analysis_option_symbols = {"NSE:SBIN26SEP820CE"}
    published: list[str] = []
    engine.events.publish = lambda kind, data: published.append(getattr(data, "symbol", ""))

    for _ in range(50):
        asyncio.run(engine.on_tick(_tick("NSE:SBIN26SEP820CE")))

    assert len(published) == 1, "a burst of analysis ticks became one frame"
    # The snapshot still reports the true latest price for that leg.
    assert engine.latest_ticks["NSE:SBIN26SEP820CE"].ltp == 100.0

    # Once the interval has elapsed the next tick goes out.
    engine._analysis_published["NSE:SBIN26SEP820CE"] -= ANALYSIS_TICK_PUBLISH_SECONDS
    asyncio.run(engine.on_tick(_tick("NSE:SBIN26SEP820CE")))
    assert len(published) == 2


def test_tradable_legs_are_never_rationed():
    engine = _engine()
    engine.analysis_option_symbols = set()
    published: list[str] = []
    engine.events.publish = lambda kind, data: published.append(kind)

    for _ in range(5):
        asyncio.run(engine.on_tick(_tick("NSE:SBIN-EQ")))

    assert published.count("tick") == 5


def test_nightly_retries_a_locked_database_instead_of_writing_the_day_off(monkeypatch):
    """The ordinary failure is a research job holding the file. One lock must
    not cost the whole day's memory."""
    import asyncio
    from macd_trader import engine as engine_module
    engine = _engine()
    calls = {"n": 0}

    def flaky(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("database is locked")
        return {"day": "2026-09-02", "sessions": 1}

    from macd_trader import nightly
    monkeypatch.setattr(nightly, "run", flaky)
    fixed = datetime(2026, 9, 2, 16, 5, tzinfo=engine_module.IST)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed if tz is None else fixed.astimezone(tz)

    # The nightly loop is desk code (macd_trader.desk); patch its clock there.
    from macd_trader import desk as desk_module
    monkeypatch.setattr(desk_module, "datetime", Clock)
    sleeps = {"n": 0}

    async def fast_sleep(_seconds):
        sleeps["n"] += 1
        if sleeps["n"] > 3:
            raise asyncio.CancelledError

    monkeypatch.setattr(engine_module.asyncio, "sleep", fast_sleep)
    try:
        asyncio.run(engine._nightly_loop())
    except asyncio.CancelledError:
        pass

    assert calls["n"] == 2
    assert engine._nightly_done == "2026-09-02"
    assert engine.nightly_report == {"day": "2026-09-02", "sessions": 1}


def test_a_quotes_rate_limit_stands_the_futures_oi_call_down(monkeypatch):
    """Retrying a 429 every minute keeps the bucket full and sustains the
    refusal; futures OI is worth less than the quota."""
    import asyncio
    from macd_trader import engine as engine_module
    engine = _engine()
    calls = {"n": 0}

    async def limited(_symbols):
        calls["n"] += 1
        raise RuntimeError("Client error '429 Too Many Requests' for url ...")

    engine.broker.quotes = limited
    engine._whale_roots = lambda: {"NIFTY": "NSE:NIFTY26SEPFUT"}
    engine._whale_futures = lambda: ["NSE:NIFTY26SEPFUT"]
    engine._whale_minute = lambda *a, **k: ({}, None)
    monkeypatch.setattr(engine_module.whale, "snapshot_stamp", lambda: 1)

    async def no_chain(*_a, **_k):
        return None

    engine.broker.option_chain = no_chain
    asyncio.run(engine._chain_minute(1))
    assert calls["n"] == 1
    assert engine._quotes_blocked_until > 0
    assert engine._quotes_backoff == engine_module.QUOTES_BACKOFF_SECONDS * 2

    # The next minute must not spend another call.
    asyncio.run(engine._chain_minute(2))
    assert calls["n"] == 1
    assert "stood down" in engine.chain_status["whale_error"]
