"""The strategy / desk split: the bus contract, the strategy role's stand-in
desk, and parity between the desk process and the single-process desk."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from macd_trader.bus import TickBusSubscriber, decode_tick, encode_tick, tick_subject
from macd_trader.config import Settings
from macd_trader.contracts import OptionContract
from macd_trader.engine import TradingEngine
from macd_trader.models import Tick

IST_1100 = datetime(2026, 9, 28, 5, 30, tzinfo=UTC)  # Monday 11:00 IST
DESK_CSV = "NSE:NIFTY26OCTFUT,NSE:RELIANCE-EQ"


def _settings(tmp_path: Path, role: str, **extra) -> Settings:
    return Settings(
        symbols_csv="NSE:NIFTY50-INDEX,NSE:RELIANCE-EQ", feed_mode="simulation", execution_mode="paper",
        mp_symbols_csv=DESK_CSV, engine_role=role,
        database_path=str(tmp_path / f"{role}-macd.sqlite3"),
        research_database_path=str(tmp_path / f"{role}-historical.sqlite3"),
        mp_database_path=str(tmp_path / f"{role}-mp.sqlite3"),
        blast_database_path=str(tmp_path / f"{role}-blast.sqlite3"),
        tick_database_path=str(tmp_path / f"{role}-ticks.sqlite3"),
        runtime_settings_path=str(tmp_path / f"{role}-settings.json"),
        tick_capture_enabled=False, **extra,
    )


def _contracts() -> dict[str, OptionContract]:
    rows = [
        OptionContract("NIFTY", "NSE:NIFTY50-INDEX", "CE", "NSE:NIFTY26OCT24100CE", 24100, "2026-10-27", 90, lot_size=75),
        OptionContract("NIFTY", "NSE:NIFTY50-INDEX", "PE", "NSE:NIFTY26OCT24100PE", 24100, "2026-10-27", 85, lot_size=75),
        OptionContract("RELIANCE", "NSE:RELIANCE-EQ", "CE", "NSE:RELIANCE26OCT1400CE", 1400, "2026-10-27", 30, lot_size=500),
        OptionContract("RELIANCE", "NSE:RELIANCE-EQ", "CE", "NSE:RELIANCE26OCT1500CE", 1500, "2026-10-27", 5,
                       lot_size=500, moneyness="OTM2", analysis_only=True),
    ]
    return {row.symbol: row for row in rows}


def _configure(engine: TradingEngine) -> None:
    engine.contract_selector.contracts = _contracts()
    engine.futures_rollover = {}
    engine.option_symbols = [s for s, c in engine.contract_selector.contracts.items() if not c.analysis_only]
    engine.analysis_option_symbols = {s for s, c in engine.contract_selector.contracts.items() if c.analysis_only}
    engine.all_symbols = [*engine.settings.symbols, "NSE:NIFTY26OCTFUT", *engine.contract_selector.contracts]
    tradable = {s: engine.contract_selector.contracts[s].lot_size for s in engine.option_symbols}
    engine.execution.set_tradable_contracts(tradable)
    engine.configure_desk(tradable)


# -- the bus contract --------------------------------------------------------

def test_bus_message_round_trips_every_tick_field():
    tick = Tick("NSE:NIFTY26OCT24100CE", 91.25, volume=123456, timestamp=IST_1100 + timedelta(microseconds=250000),
                prev_close=88.0, change=3.25, change_pct=3.69, bid=91.2, ask=91.3, bid_qty=750, ask_qty=1500,
                last_qty=75, total_buy_qty=90000, total_sell_qty=81000, open_interest=2_500_000, avg_trade_price=90.1)
    payload = encode_tick(tick, seq=42, analysis_only=True, received_at=IST_1100 + timedelta(seconds=1))
    decoded, analysis_only, seq = decode_tick(payload)
    assert (decoded, analysis_only, seq) == (tick, True, 42)
    message = json.loads(payload)
    # What the Rust analytics service reads, unchanged from the gateway's contract.
    assert message["v"] == 1 and message["exchange_ts_ms"] == 1790573400250
    assert message["gateway_ts_ms"] == message["received_ts_ms"] == 1790573401000
    assert tick_subject("NSE:M&M-EQ") == "md.tick.NSE:M&M-EQ" and tick_subject("A.B*C") == "md.tick.A_B_C"


def test_subscriber_counts_loss_and_publisher_restarts():
    received = []

    async def handler(tick, analysis_only):
        received.append((tick.symbol, analysis_only))

    subscriber = TickBusSubscriber("nats://unused", handler)

    class Message:
        def __init__(self, seq):
            self.data = encode_tick(Tick("NSE:X", 1.0, timestamp=IST_1100), seq=seq,
                                    analysis_only=False, received_at=IST_1100)

    async def feed():
        for seq in (1, 2, 5, 6, 1, 2):
            await subscriber._on_message(Message(seq))
        await subscriber._on_message(type("Bad", (), {"data": b"not json"})())

    asyncio.run(feed())
    assert len(received) == 6
    assert (subscriber.lost, subscriber.publisher_restarts, subscriber.undecodable) == (2, 1, 1)


# -- the strategy role -------------------------------------------------------

def test_strategy_role_keeps_a_stand_in_desk_that_never_touches_its_book(tmp_path):
    engine = TradingEngine(_settings(tmp_path, "strategy"))
    try:
        assert not engine.desk_local
        assert not (tmp_path / "strategy-mp.sqlite3").exists(), "the desk's book belongs to the desk process"
        assert engine.mp.settings.enabled is False

        fed = []
        engine._desk_on_tick = lambda tick, analysis_only: fed.append(tick)  # would be the in-process desk
        published = []

        class Bus:
            async def publish(self, tick, *, analysis_only, received_at):
                published.append((tick.symbol, analysis_only))

        engine.bus = Bus()
        _configure(engine)
        engine.status = "connected"
        now = datetime.now(UTC)
        asyncio.run(engine.on_tick(Tick("NSE:NIFTY26OCT24100CE", 90, timestamp=now)))
        asyncio.run(engine.on_tick(Tick("NSE:RELIANCE26OCT1500CE", 5, timestamp=now)))
        assert fed == []
        assert published == [("NSE:NIFTY26OCT24100CE", False), ("NSE:RELIANCE26OCT1500CE", True)]
    finally:
        engine.repository.close()


def test_strategy_role_remembers_desk_holdings_across_a_restart(tmp_path):
    settings = _settings(tmp_path, "strategy")
    engine = TradingEngine(settings)
    engine.all_symbols = ["NSE:NIFTY26OCT24100CE"]
    assert engine.update_desk_holdings({"NSE:NIFTY26OCT24100CE": 75, "NSE:NIFTY26SEP24000CE": 75, "NSE:X": 0}) == [
        "NSE:NIFTY26SEP24000CE"]
    engine.repository.close()
    restarted = TradingEngine(settings)
    try:
        assert restarted.desk_holdings() == {"NSE:NIFTY26OCT24100CE": 75, "NSE:NIFTY26SEP24000CE": 75}
        assert "NSE:NIFTY26SEP24000CE" in restarted.warmup_symbols()
    finally:
        restarted.repository.close()


def test_internal_settings_route_saves_only_desk_settings(monkeypatch):
    import importlib

    from fastapi import HTTPException

    module = importlib.import_module("macd_trader.app")
    saved = []
    monkeypatch.setattr(module, "persist_settings", saved.append)
    original = module.engine.settings
    try:
        result = asyncio.run(module.internal_settings({"mp_max_positions": 3}))
        assert result == {"saved": ["mp_max_positions"]} and saved[-1].mp_max_positions == 3
        with pytest.raises(HTTPException):
            asyncio.run(module.internal_settings({"auto_trade": True}))
    finally:
        module.engine.settings = original


# -- the desk process --------------------------------------------------------

def _desk(tmp_path):
    from macd_trader.desk_app import DeskEngine
    return DeskEngine(_settings(tmp_path, "desk", nats_url="nats://unused"))


def test_desk_process_configures_itself_exactly_like_the_single_process(tmp_path):
    single = TradingEngine(_settings(tmp_path, "all"))
    strategy = TradingEngine(_settings(tmp_path, "strategy"))
    desk = _desk(tmp_path)
    try:
        _configure(single)
        _configure(strategy)
        desk.apply_context(json.loads(json.dumps(strategy.desk_context())))  # as it crosses HTTP
        assert desk.context["loaded"]
        assert desk.mp_spot_symbols() == single.mp_spot_symbols()
        assert desk.desk_option_map() == single.desk_option_map()
        assert desk.mp_universe() == single.mp_universe()
        assert desk.mp.option_map == single.mp.option_map
        assert desk.mp.universe == single.mp.universe
        assert desk.mp.directional_scope == single.mp.directional_scope
        assert desk.analysis_option_symbols == single.analysis_option_symbols
    finally:
        single.repository.close()
        strategy.repository.close()


@pytest.mark.usefixtures("fixed_auction_session_clock")
def test_desk_process_builds_the_same_auction_from_the_bus(tmp_path):
    single = TradingEngine(_settings(tmp_path, "all"))
    strategy = TradingEngine(_settings(tmp_path, "strategy"))
    desk = _desk(tmp_path)
    try:
        _configure(single)
        _configure(strategy)
        desk.apply_context(strategy.desk_context())
        desk.active = True  # what the context loop sets once the strategy confirms its role
        start = datetime(2026, 9, 28, 5, 30, tzinfo=UTC)
        prices = [24100, 24102, 24098, 24105, 24101, 24110, 24090, 24095]
        ticks = [Tick("NSE:NIFTY26OCTFUT", price, volume=1000 + 75 * i, timestamp=start + timedelta(seconds=20 * i),
                      bid=price - 0.5, ask=price + 0.5, last_qty=75) for i, price in enumerate(prices)]

        async def run():
            for seq, tick in enumerate(ticks, 1):
                await single._desk_on_tick(tick, False)
                decoded, analysis_only, _ = decode_tick(
                    encode_tick(tick, seq=seq, analysis_only=False, received_at=tick.timestamp))
                await desk._on_bus_tick(decoded, analysis_only)

        asyncio.run(run())
        symbol = "NSE:NIFTY26OCTFUT"
        assert single.mp.profiles.get(symbol) is not None
        assert desk.mp.profiles.get(symbol).snapshot() == single.mp.profiles.get(symbol).snapshot()
        assert desk.mp.flow.snapshot(symbol) == single.mp.flow.snapshot(symbol)
        assert desk.latest_ticks[symbol] == ticks[-1]
    finally:
        single.repository.close()
        strategy.repository.close()


def test_desk_proxies_rebuild_broker_types(tmp_path):
    from macd_trader.brokers import OptionChain, OptionChainEntry
    from macd_trader.events import dumps
    from macd_trader.rollover import Expiry

    chain = OptionChain(expiry="2026-10-01", spot_price=24100.0,
                        entries=[OptionChainEntry(24100, "CE", "NSE:NIFTY26O0124100CE", ltp=90, oi=100, prev_oi=80)],
                        expiries=[Expiry("2026-10-01", "123")], fp=24130.0, vix=12.5)
    quote = Tick("NSE:NIFTY26OCTFUT", 24130, open_interest=1_000_000, timestamp=IST_1100)

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/api/internal/option-chain/"):
            return httpx.Response(200, content=dumps(chain))
        if request.url.path == "/api/internal/quotes":
            return httpx.Response(200, content=dumps({quote.symbol: quote}))
        return httpx.Response(502, json={"detail": "broker quotes failed: 429 Too Many Requests"})

    desk = _desk(tmp_path)
    desk.strategy._client = httpx.AsyncClient(base_url="http://strategy", transport=httpx.MockTransport(respond))

    async def run():
        assert await desk.strategy.option_chain("NSE:NIFTY50-INDEX") == chain
        assert await desk.strategy.quotes([quote.symbol]) == {quote.symbol: quote}
        with pytest.raises(RuntimeError, match="429"):
            await desk.strategy._broker("GET", "/api/internal/elsewhere")

    asyncio.run(run())


def test_desk_status_hands_whale_alerts_over_once(tmp_path):
    desk = _desk(tmp_path)
    desk.whale_alert_queue.append({"alert_id": "a1", "root": "NIFTY"})
    first = desk.status(drain_alerts=True)
    assert first["whale_alerts"] == [{"alert_id": "a1", "root": "NIFTY"}] and first["role"] == "desk"
    assert desk.status(drain_alerts=True)["whale_alerts"] == []


def test_desk_process_stays_idle_beside_an_engine_that_runs_its_own_desk(tmp_path):
    single = TradingEngine(_settings(tmp_path, "all"))
    desk = _desk(tmp_path)
    try:
        _configure(single)
        desk.apply_context(single.desk_context())
        assert not desk.context["loaded"] and "stays idle" in desk.context["error"]
        assert desk.contract_selector.contracts == {}

        async def run():
            await desk._set_active(desk.context.get("strategy_role") == "strategy" and desk.context["loaded"])
            await desk._on_bus_tick(Tick("NSE:NIFTY26OCTFUT", 24100, timestamp=IST_1100), False)

        asyncio.run(run())
        assert not desk.active and desk.latest_ticks == {}
    finally:
        single.repository.close()
