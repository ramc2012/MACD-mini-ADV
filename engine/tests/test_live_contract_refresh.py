"""10 Nov 2026: an intraday ladder refresh no longer closes the feed, and a shutdown always reaches the marker.

Until then every changed ladder closed the socket and rebuilt the whole feed (25-56 s without a tick, 15 times on
6 Nov and 18 on 9 Nov), and a stop either ran out of Docker's 3 s grace or died on the first task that had failed.
"""
import asyncio
import os
import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from macd_trader import engine as engine_module
from macd_trader.brokers import FyersBroker, SimulationBroker
from macd_trader.config import Settings
from macd_trader.contracts import OptionContract
from macd_trader.engine import TradingEngine, live_contract_refresh_enabled
from macd_trader.models import Candle, Tick
from macd_trader.strategy import MACDStrategyManager

IST = ZoneInfo("Asia/Kolkata")
SPOT = "NSE:TEST-EQ"
OLD, KEEP, CTX, NEW, NEW_CTX = ("NSE:TEST99DEC100CE", "NSE:TEST99DEC100PE", "NSE:TEST99DEC90CE",
                                "NSE:TEST99DEC110CE", "NSE:TEST99DEC120CE")


# ---- broker: subscribe on the open socket ----

class FakeSdkSocket:
    def __init__(self):
        self._FyersDataSocket__ws_object = object()
        self.calls: list[tuple[list[str], str]] = []
        self.threads: list[str] = []

    def subscribe(self, symbols, data_type):
        self.threads.append(threading.current_thread().name)
        self.calls.append((list(symbols), data_type))


def fyers_broker(connected=True):
    broker = FyersBroker(Settings(feed_mode="fyers", fyers_client_id="APP-100", fyers_access_token="tok"))
    socket = FakeSdkSocket()
    broker._socket = socket
    broker._subscribe_lock = threading.Lock()
    broker._stream_symbols = ["A", "B"]
    broker.connected = connected
    return broker, socket


def test_add_symbols_sends_only_the_new_ones_and_grows_the_reconnect_list():
    broker, socket = fyers_broker()
    assert asyncio.run(broker.add_symbols(["B", "C", "D", "C"])) is True
    assert socket.calls == [(["C", "D"], "SymbolUpdate")]
    assert broker._stream_symbols == ["A", "B", "C", "D"]       # what an SDK reconnect resubscribes
    assert socket.threads[0] != threading.current_thread().name  # the SDK call stays off the event loop


def test_add_symbols_with_nothing_new_sends_nothing():
    broker, socket = fyers_broker()
    assert asyncio.run(broker.add_symbols(["A", "B"])) is True
    assert socket.calls == [] and broker._stream_symbols == ["A", "B"]


def test_add_symbols_without_a_live_stream_reports_failure():
    broker, socket = fyers_broker(connected=False)
    assert asyncio.run(broker.add_symbols(["C"])) is False
    assert socket.calls == [] and broker._stream_symbols == ["A", "B"]
    fresh = FyersBroker(Settings(feed_mode="fyers", fyers_client_id="APP-100", fyers_access_token="tok"))
    fresh.connected = True                                       # no socket yet
    assert asyncio.run(fresh.add_symbols(["C"])) is False


def test_add_symbols_between_sockets_leaves_it_to_the_reconnect():
    broker, socket = fyers_broker()
    socket._FyersDataSocket__ws_object = None                   # the SDK is reconnecting
    assert asyncio.run(broker.add_symbols(["C"])) is True
    assert socket.calls == []
    assert broker._stream_symbols == ["A", "B", "C"]            # its on_connect subscribes the grown list


def test_add_symbols_waits_for_a_subscription_in_progress():
    broker, socket = fyers_broker()

    async def scenario():
        broker._subscribe_lock.acquire()                         # a reconnect is subscribing right now
        pending = asyncio.create_task(broker.add_symbols(["C"]))
        await asyncio.sleep(0.1)
        assert socket.calls == [] and not pending.done()
        broker._subscribe_lock.release()
        assert await asyncio.wait_for(pending, 2) is True
        assert socket.calls == [(["C"], "SymbolUpdate")]

    asyncio.run(scenario())


def test_simulation_broker_add_symbols():
    broker = SimulationBroker()
    assert asyncio.run(broker.add_symbols(["A"])) is False       # not connected

    async def scenario():
        await broker.connect()
        broker._stream_symbols = ["A"]
        assert await broker.add_symbols(["A", "B", "B"]) is True
        assert broker._stream_symbols == ["A", "B"]

    asyncio.run(scenario())


# ---- strategy: one symbol's state can be dropped ----

def test_strategy_forget_drops_one_symbol_only(tmp_path):
    strategy = make_engine(tmp_path).strategy
    assert isinstance(strategy, MACDStrategyManager)
    for symbol in ("X", "Y"):
        for index in range(5):
            strategy.on_closed_candle(Candle(symbol, 60 * index, 10, 11, 9, 10 + index, 100, True), warmup=True)
    strategy.intrabar_fired = {("X", 60), ("Y", 60)}
    assert "X" in strategy.indicators and "Y" in strategy.indicators
    strategy.forget("X")
    for store in (strategy.indicators, strategy.bollinger, strategy.kama, strategy.kama_rsi, strategy.kama_roc,
                  strategy.previous_kama, strategy.volumes, strategy.points, strategy.point_history,
                  strategy.evaluations):
        assert "X" not in store
    assert "Y" in strategy.indicators and strategy.intrabar_fired == {("Y", 60)}
    strategy.forget("NEVER-SEEN")                                # no state: nothing to do


# ---- engine: the refresh applied on the running feed ----

def leg(symbol, *, analysis_only=False, moneyness="ATM", option_type="CE", strike=100.0):
    return OptionContract(underlying="TEST", spot_symbol=SPOT, option_type=option_type, symbol=symbol, strike=strike,
                          expiry="2099-12-31", selection_price=5.0, lot_size=50, moneyness=moneyness,
                          analysis_only=analysis_only, selected_on="2026-11-10")


def make_engine(tmp_path):
    return TradingEngine(Settings(
        symbols_csv=SPOT, tick_capture_enabled=False,
        database_path=str(tmp_path / "book.sqlite3"),
        mp_database_path=str(tmp_path / "mp.sqlite3"),
        research_database_path=str(tmp_path / "history.sqlite3"),
        tick_database_path=str(tmp_path / "ticks.sqlite3"),
        contract_snapshot_path=str(tmp_path / "contracts.json"),
    ))


def candle(symbol, at=1_800):
    return Candle(symbol, at, 10, 11, 9, 10, 100, True)


def refreshed_engine(tmp_path, *, quotes=True, subscribed=True):
    """An engine mid-session whose selector has just replaced the 100 CE with the 110 CE."""
    engine = make_engine(tmp_path)
    engine.option_symbols = [OLD, KEEP]
    engine.analysis_option_symbols = {CTX}
    engine.all_symbols = [SPOT, OLD, KEEP, CTX]
    for symbol in (OLD, KEEP):
        engine.history[symbol].append(candle(symbol))
        engine.strategy.on_closed_candle(candle(symbol), warmup=True)
    for symbol in engine.all_symbols:
        engine.latest_ticks[symbol] = Tick(symbol, 10.0)
    rows = [leg(OLD, analysis_only=True), leg(KEEP, option_type="PE"), leg(CTX, analysis_only=True, moneyness="ITM", strike=90),
            leg(NEW, strike=110), leg(NEW_CTX, analysis_only=True, moneyness="OTM", strike=120)]
    contracts = {f"PREV-{OLD}": rows[0], **{row.symbol: row for row in rows[1:]}}
    engine.contract_selector = SimpleNamespace(
        contracts=contracts, new_symbols=[KEEP, NEW, NEW_CTX],
        todays_atm=lambda: [row for row in contracts.values() if row.moneyness == "ATM" and not row.analysis_only])
    seen = SimpleNamespace(subscribe=[], warmed=[], blast=[], gate=[], backfill=[], restarts=0)

    async def add_symbols(symbols):
        seen.subscribe.append(list(symbols))
        if quotes:
            for symbol in symbols:                               # Fyers answers a subscribe with a snapshot each
                engine.latest_ticks[symbol] = Tick(symbol, 5.0)
        return subscribed

    async def warm_symbol(symbol, run):
        assert symbol in engine._warming_symbols                 # parked before its history is replayed
        seen.warmed.append(symbol)
        if symbol in run.new_contracts:
            run.history_backfill.append(symbol)
        engine._finish_symbol_warmup(symbol, [candle(symbol, 3_600)])

    async def blast_warm(path, symbols):
        seen.blast.append(list(symbols))

    async def gate(symbols):
        seen.gate.append(list(symbols))

    async def backfill(symbols, expiries):
        seen.backfill.append(list(symbols))

    async def restart_stream():
        seen.restarts += 1

    engine.broker.add_symbols = add_symbols
    engine._warm_symbol = warm_symbol
    engine.blast.warm_from_store = blast_warm
    engine.entry_gate.refresh_reference = gate
    engine._backfill_contract_history = backfill
    engine.restart_stream = restart_stream
    return engine, seen


def test_live_refresh_subscribes_and_warms_only_the_new_strikes(tmp_path):
    engine, seen = refreshed_engine(tmp_path)
    kept_history = list(engine.history[KEEP])
    kept_state = engine.strategy.indicators[KEEP]

    async def scenario():
        outcome = await engine._apply_ladder_refresh_live()
        await asyncio.sleep(0)                                   # the entry-gate and backfill tasks
        await engine._history_backfill_task
        return outcome

    outcome = asyncio.run(scenario())
    assert seen.subscribe == [[NEW, NEW_CTX]]                    # nothing already on the feed is sent again
    assert seen.warmed == [NEW] and seen.blast == [[NEW]]
    assert seen.restarts == 0
    assert outcome["mode"] == "live" and outcome["feed_down_seconds"] == 0.0
    assert (outcome["subscribed"], outcome["quoted"], outcome["warmed"], outcome["demoted"]) == (2, 2, 1, 1)
    assert engine.option_symbols == [KEEP, NEW]
    assert engine.analysis_option_symbols == {OLD, CTX, NEW_CTX}
    assert engine.all_symbols == [SPOT, OLD, KEEP, CTX, NEW, NEW_CTX]
    # every other symbol keeps its bars and indicator state; the replaced leg's running state is gone
    assert list(engine.history[KEEP]) == kept_history and engine.strategy.indicators[KEEP] is kept_state
    assert OLD not in engine.strategy.indicators
    assert [row.timestamp for row in engine.history[NEW]] == [3_600] and NEW in engine.strategy.indicators
    assert not engine._warming_symbols
    assert engine.execution.tradable_symbols == {KEEP, NEW}
    assert engine.execution.entry_universe == {KEEP, NEW}
    assert seen.gate == [[KEEP, NEW, SPOT]]                      # the FULL list: the gate replaces its reference
    assert seen.backfill == [[NEW]]                              # KEEP was already streaming: not a new contract


def test_live_refresh_warms_a_new_strike_through_the_ordinary_warmup(tmp_path):
    """The real per-symbol warm-up (stored bars, chain marks, broker history), not the stand-in above."""
    engine, seen = refreshed_engine(tmp_path)
    del engine._warm_symbol                                      # back to the engine's own method

    async def scenario():
        await engine.broker.connect()
        outcome = await engine._apply_ladder_refresh_live()
        task = engine._history_backfill_task
        if task is not None:
            await task
        return outcome

    outcome = asyncio.run(scenario())
    assert outcome["mode"] == "live" and not engine._warming_symbols
    assert len(engine.history[NEW]) > 0 and NEW in engine.strategy.indicators
    assert NEW not in engine.history_errors
    assert seen.backfill == [[NEW]]


def test_live_refresh_reopens_the_socket_when_no_quotes_arrive(tmp_path, monkeypatch):
    monkeypatch.setattr(engine_module, "LIVE_SUBSCRIBE_CONFIRM_SECONDS", 0.3)
    engine, seen = refreshed_engine(tmp_path, quotes=False)
    kept_history = list(engine.history[KEEP])
    outcome = asyncio.run(engine._apply_ladder_refresh_live())
    assert outcome["mode"] == "socket_restart" and seen.restarts == 1
    assert outcome["quoted"] == 0 and outcome["feed_down_seconds"] is not None
    assert list(engine.history[KEEP]) == kept_history            # still no state lost
    assert engine.option_symbols == [KEEP, NEW] and not engine._warming_symbols


def test_live_refresh_reopens_the_socket_at_once_when_the_subscribe_did_not_go_out(tmp_path):
    engine, seen = refreshed_engine(tmp_path, quotes=False, subscribed=False)
    outcome = asyncio.run(engine._apply_ladder_refresh_live())
    assert outcome["mode"] == "socket_restart" and seen.restarts == 1
    assert outcome["seconds"] < 5                                # no 15 s wait for quotes that cannot come


def test_live_refresh_survives_a_subscribe_error_and_a_failed_restart(tmp_path):
    engine, seen = refreshed_engine(tmp_path)

    async def broken(symbols):
        raise RuntimeError("socket gone")

    async def failing_restart():
        raise RuntimeError("no route")

    engine.broker.add_symbols = broken
    engine.restart_stream = failing_restart
    outcome = asyncio.run(engine._apply_ladder_refresh_live())
    assert outcome["mode"] == "socket_restart" and outcome["feed_down_seconds"] is None
    assert "no route" in outcome["error"] and "socket restart failed" in engine.error
    assert seen.warmed == [NEW] and not engine._warming_symbols  # the strikes are still warmed and released


def test_live_refresh_never_leaves_a_strike_parked(tmp_path):
    engine, seen = refreshed_engine(tmp_path)

    async def broken_warm(symbol, run):
        raise RuntimeError("store unreadable")

    engine._warm_symbol = broken_warm
    try:
        asyncio.run(engine._apply_ladder_refresh_live())
    except RuntimeError:
        pass
    assert not engine._warming_symbols


def test_a_successful_live_refresh_clears_its_own_earlier_error(tmp_path):
    engine, _ = refreshed_engine(tmp_path)
    engine.error = "live subscribe failed: socket gone"
    asyncio.run(engine._apply_ladder_refresh_live())
    assert engine.error is None


# ---- engine: which path a changed ladder takes ----

def routed_engine(tmp_path, *, live=None):
    engine = make_engine(tmp_path)
    engine._contracts_live_since = datetime.now(UTC) - timedelta(hours=1)
    engine.latest_ticks[SPOT] = Tick(SPOT, 100.0)
    seen = SimpleNamespace(live=0, rebuild=[], events=[])

    async def refresh(spots, last_trade, now, **_):
        return {SPOT: "replaced"}

    async def apply_live():
        seen.live += 1
        if isinstance(live, Exception):
            raise live
        return {"mode": "live", "feed_down_seconds": 0.0}

    async def disconnect():
        seen.rebuild.append("disconnect")

    async def connect():
        seen.rebuild.append("connect")

    engine.contract_selector = SimpleNamespace(refresh=refresh, contracts={}, new_symbols=[])
    engine._apply_ladder_refresh_live = apply_live
    engine.disconnect_feed = disconnect
    engine.connect_feed = connect
    engine.events.publish = lambda name, payload: seen.events.append(name)
    return engine, seen


def run_refresh(engine, *, streaming=True):
    async def scenario():
        engine._stream_task = asyncio.create_task(asyncio.sleep(60)) if streaming else None
        try:
            return await engine._refresh_inactive_contracts(datetime(2026, 9, 28, 11, 0, tzinfo=IST))
        finally:
            if engine._stream_task is not None:
                engine._stream_task.cancel()

    return asyncio.run(scenario())


def test_a_changed_ladder_is_applied_live_while_the_feed_runs(tmp_path, monkeypatch):
    monkeypatch.delenv("MACD_CONTRACT_REFRESH_LIVE", raising=False)
    engine, seen = routed_engine(tmp_path)
    engine.history["KEPT"].append(candle("KEPT"))
    assert run_refresh(engine) == {SPOT: "replaced"}
    assert seen.live == 1 and seen.rebuild == []
    assert engine.contract_refresh_status["mode"] == "live"
    assert engine.contract_refresh_status["replaced"] == [SPOT]
    assert "KEPT" in engine.history and SPOT in engine.latest_ticks
    assert "snapshot_required" in seen.events


def test_a_failed_live_refresh_falls_back_to_the_full_rebuild(tmp_path, monkeypatch):
    monkeypatch.delenv("MACD_CONTRACT_REFRESH_LIVE", raising=False)
    engine, seen = routed_engine(tmp_path, live=KeyError("unexpected"))
    run_refresh(engine)
    assert seen.live == 1 and seen.rebuild == ["disconnect", "connect"]
    assert engine.contract_refresh_status["mode"] == "rebuild"
    assert "KeyError" in engine.contract_refresh_status["live_error"]


def test_the_switch_puts_the_refresh_back_on_the_full_rebuild(tmp_path, monkeypatch):
    monkeypatch.setenv("MACD_CONTRACT_REFRESH_LIVE", "false")
    assert live_contract_refresh_enabled() is False
    engine, seen = routed_engine(tmp_path)
    run_refresh(engine)
    assert seen.live == 0 and seen.rebuild == ["disconnect", "connect"]
    monkeypatch.setenv("MACD_CONTRACT_REFRESH_LIVE", "true")
    assert live_contract_refresh_enabled() is True


def test_without_a_running_stream_the_refresh_rebuilds(tmp_path, monkeypatch):
    monkeypatch.delenv("MACD_CONTRACT_REFRESH_LIVE", raising=False)
    engine, seen = routed_engine(tmp_path)
    run_refresh(engine, streaming=False)
    assert seen.live == 0 and seen.rebuild == ["disconnect", "connect"]


# ---- engine: shutdown ----

def started_engine(tmp_path):
    engine = make_engine(tmp_path)

    async def idle_connection():
        await asyncio.Event().wait()

    engine.connect_feed = idle_connection
    return engine, os.path.join(str(tmp_path), f".clean_shutdown.{engine.settings.engine_role}")


def test_stop_reaches_the_marker_when_a_loop_had_already_died(tmp_path):
    engine, marker = started_engine(tmp_path)

    async def scenario():
        await asyncio.wait_for(engine.start(), timeout=5)

        async def died():
            raise RuntimeError("loop died an hour ago")

        if engine._lag_task is not None:
            engine._lag_task.cancel()
        engine._lag_task = asyncio.create_task(died())
        await asyncio.sleep(0)
        await engine.stop()

    asyncio.run(scenario())
    assert engine.status == "stopped" and engine.shutdown_report["failures"] == []
    assert os.path.exists(marker)


def test_stop_closes_everything_else_but_withholds_the_marker_after_a_failed_step(tmp_path):
    engine, marker = started_engine(tmp_path)
    closed = []

    async def scenario():
        await asyncio.wait_for(engine.start(), timeout=5)

        async def broken():
            raise OSError("disk full")

        blast_stop = engine.blast.stop

        async def blast():
            closed.append("blast")
            await blast_stop()

        engine.tick_store.stop = broken
        engine.blast.stop = blast
        await engine.stop()

    asyncio.run(scenario())
    assert engine.status == "stopped" and closed == ["blast"]    # the steps after the failure still ran
    assert any("tick store" in failure and "disk full" in failure for failure in engine.shutdown_report["failures"])
    assert not os.path.exists(marker)
