"""Stored market breadth views (analytics_schedule, owner 18 Nov 2026) and recorded MCX chains (mcx_chains)."""
import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from macd_trader import analytics_schedule, mcx_chains, straddle_views
from macd_trader.brokers import OptionChain, OptionChainEntry
from macd_trader.engine import TradingEngine
from macd_trader.models import Tick
from macd_trader.repository import TradeRepository
from macd_trader.rollover import Expiry

IST = ZoneInfo("Asia/Kolkata")


def test_a_restarted_engine_serves_the_stored_view_at_once_and_refreshes_behind(tmp_path):
    repo = TradeRepository(str(tmp_path / "book.sqlite3"))
    calls = []

    async def compute():
        calls.append(1)
        await asyncio.sleep(0.05)
        return {"rows": [len(calls)]}, True

    async def scenario():
        first = SimpleNamespace(repository=repo, lane_errors={})
        swr = TradingEngine._stale_while_revalidate.__get__(first)
        answer = await swr("day_highs", "live", 30, compute)          # never computed: waits once
        assert answer["rows"] == [1] and answer["computed_at"]
        stored = repo.load_analytics("day_highs", "live")
        assert stored and stored[1]["rows"] == [1]
        # A new process (empty memory): the stored answer at once, no wait, even though it is past its 30 s.
        repo.save_analytics("day_highs", "live", "2026-11-18T04:00:00+00:00", {"rows": ["old"]})
        restarted = SimpleNamespace(repository=repo, lane_errors={})
        swr2 = TradingEngine._stale_while_revalidate.__get__(restarted)
        started = asyncio.get_running_loop().time()
        assert (await swr2("day_highs", "live", 30, compute))["rows"] == ["old"]
        assert asyncio.get_running_loop().time() - started < 0.04
        await asyncio.sleep(0.1)                                        # refreshed behind and stored
        assert repo.load_analytics("day_highs", "live")[1]["rows"] == [2]
        # Tuple keys store under a joined key.
        await swr2("sector_coil", ("YTD", "M", "own"), 60, compute)
        assert repo.load_analytics("sector_coil", "YTD:M:own")[1]["rows"] == [3]

    asyncio.run(scenario())


def test_the_schedule_runs_after_each_15_minute_bar_of_the_nse_session():
    at = lambda h, m, s=0, d=19: datetime(2026, 11, d, h, m, s, tzinfo=IST)
    assert analytics_schedule.slot_of(at(9, 30, 10)) is None              # bar just closed: let it settle
    assert analytics_schedule.slot_of(at(9, 30, 50)) == at(9, 30)
    assert analytics_schedule.slot_of(at(9, 44, 0)) == at(9, 30)
    assert analytics_schedule.slot_of(at(9, 20, 0)) is None               # before the first bar closes
    assert analytics_schedule.slot_of(at(15, 45, 50)) == at(15, 45)       # once after F&O ends at 15:40
    assert analytics_schedule.slot_of(at(16, 0, 50)) is None
    assert analytics_schedule.slot_of(at(10, 0, 50, d=22)) is None        # Sunday
    assert analytics_schedule.next_slot(at(15, 50, d=20)).startswith("2026-11-23T09:30:45")   # Fri -> Mon


def test_refresh_all_forces_every_default_view_and_keeps_going_after_a_failure():
    seen = []

    class Host:
        async def day_highs(self, force=False): seen.append(("day_highs", force))
        async def atm_heatmap(self, force=False): raise RuntimeError("no ATM selection yet")
        async def sector_wheel(self, force=False): seen.append(("wheel", force))
        async def sector_coil(self, b, s, m, force=False): seen.append(("coil", b, s, m, force))
        async def trend_rotation(self, k, f, force=False): seen.append(("trend", k, f, force))
        async def iv_scanner(self, force=False): seen.append(("iv", force))

    async def rrg(key, t, w, n): seen.append(("rrg", key))

    host = Host()
    report = asyncio.run(analytics_schedule.refresh_all(host, rrg, "test"))
    assert report["views"]["atm_heatmap"]["error"].startswith("RuntimeError")
    assert ("day_highs", True) in seen and ("iv", True) in seen and ("rrg", "86400:14:15") in seen
    assert sum(1 for row in seen if row[0] == "trend") == 9 and all(row[-1] for row in seen if row[0] != "rrg")
    assert report["runs"] == 1 and report["views"]["day_highs"]["error"] is None


def _chain(spot, expiry="2026-12-16", oi_shift=0):
    entries = []
    for strike in range(7300, 8101, 50):
        ce = max(5.0, spot - strike + 120)
        pe = max(5.0, strike - spot + 120)
        entries += [OptionChainEntry(strike, "CE", f"MCX:CRUDEOIL26DEC{strike}CE", ce, 100, 1000 + strike - 7300 + oi_shift),
                    OptionChainEntry(strike, "PE", f"MCX:CRUDEOIL26DEC{strike}PE", pe, 100, 2000 + oi_shift)]
    return OptionChain(expiry=expiry, spot_price=spot, entries=entries,
                       expiries=[Expiry("2026-11-17", "1"), Expiry("2026-12-16", "2")], fp=spot)


def test_recorded_mcx_chains_build_the_same_straddle_and_oi_view(tmp_path):
    path = str(tmp_path / "mcx_chains.sqlite3")
    base = int(datetime(2026, 11, 18, 10, 0, tzinfo=IST).timestamp())
    for i, spot in enumerate((7700.0, 7710.0, 7760.0)):
        assert mcx_chains.save(path, base + 120 * i, "CRUDEOIL", "2026-12-16", spot, _chain(spot).entries, spot) == 34
    out = straddle_views.series(path, "CRUDEOIL", span="day", today=datetime(2026, 11, 18).date())
    assert out["expiry"] == "2026-12-16" and len(out["points"]) == 3
    assert out["points"][0]["strike"] == 7700 and out["points"][-1]["strike"] == 7750
    assert out["points"][0]["ce_oi_band"] is not None and out["points"][0]["band"] == [7550, 7850]   # opening ATM +-3
    assert straddle_views.instruments(path)["instruments"][0]["underlying"] == "CRUDEOIL"


def test_the_recorder_asks_for_the_lane_option_expiry_and_prices_from_the_live_future(tmp_path):
    calls = []

    async def option_chain(symbol, expiry_token=None, strikecount=12, priority="high"):
        calls.append((symbol, expiry_token, strikecount))
        return _chain(7700.0, expiry="2026-11-17") if expiry_token is None else _chain(7700.0)

    contract = SimpleNamespace(root="CRUDEOIL", option_type="CE", retained=False, futures_symbol="MCX:CRUDEOIL26DECFUT",
                               expiry="2026-12-16")
    held_old = SimpleNamespace(root="CRUDEOIL", option_type="PE", retained=True, futures_symbol="MCX:CRUDEOIL26NOVFUT",
                               expiry="2026-11-17")
    now = datetime.now(UTC)
    lane = SimpleNamespace(contracts={"a": contract, "b": held_old}, roots=["CRUDEOIL"],
                           broker=SimpleNamespace(option_chain=option_chain),
                           latest={"MCX:CRUDEOIL26DECFUT": Tick("MCX:CRUDEOIL26DECFUT", 7712.0, timestamp=now)})
    recorder = mcx_chains.McxChainRecorder(lane, str(tmp_path / "mcx_chains.sqlite3"))
    assert recorder.series() == [("CRUDEOIL", "MCX:CRUDEOIL26DECFUT", "2026-12-16")]   # a retained holding is not a series

    async def scenario():
        mcx_chains.CHAIN_PACING_SECONDS = 0
        recorder.schedule(now)
        await recorder._task
        recorder.schedule(now)                                       # inside two minutes: no second call
        assert recorder._task.done()

    asyncio.run(scenario())
    assert calls == [("MCX:CRUDEOIL26DECFUT", None, 8), ("MCX:CRUDEOIL26DECFUT", "2", 8)]
    assert recorder.report["snapshots"] == 1 and not recorder.report["failed"]
    import sqlite3
    spots = sqlite3.connect(tmp_path / "mcx_chains.sqlite3").execute("SELECT DISTINCT spot, expiry FROM chain_snapshots").fetchall()
    assert spots == [(7712.0, "2026-12-16")]
    assert mcx_chains.chain_path(":memory:") is None
