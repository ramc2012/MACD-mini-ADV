"""Back office (backoffice.py): day / month / year roll-ups from a lane's own book, capital top-ups netted out."""
from datetime import datetime, timezone

from macd_trader import backoffice as bo
from macd_trader.market_calendar import lane_day_start
from macd_trader.repository import TradeRepository
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def utc(text: str) -> str:
    return datetime.fromisoformat(text).astimezone(timezone.utc).isoformat()


def trade(pid, exit_ist, pnl, partial=False):
    return {"position_id": pid, "symbol": "NSE:NIFTY26NOV25000CE", "exit_time": utc(exit_ist), "pnl": pnl,
            "partial": partial, "fees": 0.0}


def test_days_net_capital_top_ups_and_count_open_marks():
    trades = [trade("a", "2026-11-16T10:00:00+05:30", 500.0, partial=True),
              trade("a", "2026-11-16T14:00:00+05:30", 500.0),
              trade("b", "2026-11-17T11:00:00+05:30", -300.0)]
    curve = [(utc("2026-11-16T15:30:00+05:30"), 101_200.0),    # +1,000 realised, +200 open marks
             (utc("2026-11-17T15:30:00+05:30"), 150_600.0)]    # +50,000 capital, -300 realised, -300 marks
    days = bo.daily(trades, curve, 100_000.0, [(utc("2026-11-17T09:00:00+05:30"), 50_000.0)])
    assert [(d["day"], d["pnl"], d["realized"], d["trades"], d["wins"]) for d in days] == [
        ("2026-11-16", 1200.0, 1000.0, 1, 1), ("2026-11-17", -600.0, -300.0, 1, 0)]
    month = bo.roll_up(days, 100_000.0, 7)[0]
    assert month["pnl"] == 600.0 and month["capital_added"] == 50_000.0 and month["trades"] == 2
    assert month["profit_factor"] == round(1000 / 300, 2) and month["best_day"] == "2026-11-16"


def test_idle_days_are_left_out_and_a_book_without_marks_uses_realised():
    days = bo.daily([trade("a", "2026-11-17T11:00:00+05:30", 250.0)],
                    [(utc("2026-11-15T12:00:00+05:30"), 100_000.0)], 100_000.0)
    assert [(d["day"], d["pnl"], d["equity"]) for d in days] == [("2026-11-17", 250.0, 100_250.0)]


def test_capital_changes_are_read_off_the_marks(tmp_path):
    repo = TradeRepository(str(tmp_path / "book.sqlite3"))
    for at, equity, realised in (("2026-11-16T05:00:00+00:00", 1_000_000.0, 0.0),
                                 ("2026-11-16T10:00:00+00:00", 1_010_000.0, 10_000.0),
                                 ("2026-11-17T05:00:00+00:00", 2_510_000.0, 10_000.0)):   # topped up to 25 L
        repo._connection.execute("INSERT INTO equity_points(timestamp, equity, cash, realized_pnl, unrealized_pnl) "
                                 "VALUES (?, ?, ?, ?, 0)", (at, equity, equity, realised))
    curve, start, changes = bo.repository_curve(repo)
    assert start == 1_000_000.0 and changes == [("2026-11-17T05:00:00+00:00", 1_500_000.0)] and len(curve) == 3


def test_the_lane_day_rolls_at_08_00_ist_and_skips_weekends():
    assert lane_day_start(datetime(2026, 11, 18, 7, 59, tzinfo=IST)).isoformat() == "2026-11-17T08:00:00+05:30"
    assert lane_day_start(datetime(2026, 11, 18, 8, 0, tzinfo=IST)).isoformat() == "2026-11-18T08:00:00+05:30"
    assert lane_day_start(datetime(2026, 11, 21, 12, 0, tzinfo=IST)).isoformat() == "2026-11-20T08:00:00+05:30"  # Sat
    assert lane_day_start(datetime(2026, 11, 23, 7, 0, tzinfo=IST)).isoformat() == "2026-11-20T08:00:00+05:30"   # Mon early


def test_repository_rows_since_keeps_only_the_lane_day(tmp_path):
    repo = TradeRepository(str(tmp_path / "book.sqlite3"))
    for oid, at in (("old", "2026-11-17T05:00:00+00:00"), ("new", "2026-11-18T04:00:00+00:00")):
        repo._connection.execute("INSERT INTO orders(order_id, created_at, payload) VALUES (?, ?, ?)",
                                 (oid, at, f'{{"order_id": "{oid}", "status": "FILLED", "created_at": "{at}"}}'))
    since = lane_day_start(datetime(2026, 11, 18, 10, 0, tzinfo=IST)).astimezone(timezone.utc).isoformat()
    assert [r["order_id"] for r in repo.rows("orders", 10, since)] == ["new"]
    assert [r["order_id"] for r in repo.rows("orders", 10)] == ["new", "old"]
    assert [r["order_id"] for r in repo.orders_collapsed(10, since)] == ["new"]


def test_splits_count_positions_once_flat_and_sum_every_slice():
    from macd_trader.backoffice import splits, underlying_of

    rows = [
        {"position_id": 1, "symbol": "NSE:NIFTY25NOV26000CE", "pnl": 100.0, "partial": True, "exit_reason": "TARGET",
         "entry_time": "2026-11-18T04:00:00+00:00", "exit_time": "2026-11-18T04:30:00+00:00"},
        {"position_id": 1, "symbol": "NSE:NIFTY25NOV26000CE", "pnl": -40.0, "partial": False, "exit_reason": "STOP",
         "entry_time": "2026-11-18T04:00:00+00:00", "exit_time": "2026-11-18T05:30:00+00:00"},
        {"position_id": 2, "symbol": "NSE:RELIANCE25NOV1500PE", "pnl": -50.0, "partial": False, "exit_reason": "STOP",
         "entry_time": "2026-11-16T04:00:00+00:00", "exit_time": "2026-11-18T05:30:00+00:00"},
    ]
    out = splits(rows)
    nifty = next(r for r in out["underlying"] if r["key"] == "NIFTY")
    assert nifty == {"key": "NIFTY", "net": 60.0, "trades": 1, "win_rate": 100.0, "profit_factor": None}
    kinds = {r["key"]: r for r in out["kind"]}
    assert kinds["PE"]["trades"] == 1 and kinds["PE"]["win_rate"] == 0.0
    assert sum(r["net"] for r in out["exit_reason"]) == 10.0
    assert {r["key"] for r in out["hold"]} == {"<1h", "1h-1d", ">2d"}
    assert underlying_of("NSE:BAJAJ-AUTO25NOV9000PE") == "BAJAJ-AUTO"
    assert underlying_of("NSE:M&M25NOVFUT") == "M&M"


def test_market_views_serve_the_cached_answer_at_once_and_refresh_behind():
    import asyncio
    import time as clock
    from types import SimpleNamespace

    from macd_trader.engine import TradingEngine

    calls = []

    async def compute():
        calls.append(1)
        await asyncio.sleep(0.05)
        return {"n": len(calls)}, True

    async def scenario():
        host = SimpleNamespace()
        swr = TradingEngine._stale_while_revalidate.__get__(host)
        first = await swr("view", "k", 30, compute)                  # nothing cached: waits
        assert first == {"n": 1}
        cache = host.__dict__["_view_cache_swr"]
        cache["k"] = (clock.monotonic() - 40, first)                   # stale, not too old
        started = clock.monotonic()
        again = await swr("view", "k", 30, compute)
        assert again == {"n": 1} and clock.monotonic() - started < 0.04   # served at once
        await asyncio.sleep(0.1)                                       # the refresh ran behind
        assert cache["k"][1] == {"n": 2}
        cache["k"] = (clock.monotonic() - 9999, cache["k"][1])         # far too old: waits for a fresh one
        assert await swr("view", "k", 30, compute) == {"n": 3}

    asyncio.run(scenario())
