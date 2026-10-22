"""Rules added after 22 Oct 2026: profit-taking in code, no new risk before 09:45, one re-entry per side at grade A,
slippage-aware sizing, and a readable, retried model error."""
import asyncio
import json
from datetime import time

import httpx

from macd_trader import agentic_brain as brain
from macd_trader import agentic_lane as lane_module
from test_agentic import CE, NOW, PE, FakeClient, act, build, candidate_id, decision, generous_lots  # noqa: F401


def held(lane, symbol, lots, stop=None):
    lane.client = FakeClient(decision(act("open", candidate_id(lane, symbol), lots=lots, stop=stop, reason="test entry")))
    asyncio.run(lane.think("test", None, NOW))
    assert lane.portfolio.positions[symbol].lots == lots
    return lane.portfolio.positions[symbol]


def test_half_is_booked_at_30_percent_and_the_rest_moves_to_breakeven(tmp_path, monkeypatch):
    monkeypatch.setattr(lane_module, "INTRADAY_FLATTEN_AT", time(23, 59))
    lane = build(tmp_path)
    held(lane, PE, 4, stop=70)
    asyncio.run(lane.execution.on_tick(PE, 131.0))
    position = lane.portfolio.positions[PE]
    assert position.lots == 2 and lane.meta[PE]["booked"] == 1 and lane.meta[PE]["stop_premium"] == 100.0
    assert "booked half (2 lots)" in lane.actions()[0]["reason"]
    asyncio.run(lane.execution.on_tick(PE, 150.0))       # +50% best: the trail rises to 88% of it
    assert lane.meta[PE]["stop_premium"] == 132.0
    asyncio.run(lane.execution.on_tick(PE, 131.0))
    assert PE not in lane.held_symbols()                  # stopped on the trail, well in profit


def test_a_one_lot_winner_is_taken_whole_and_a_smaller_one_trails(tmp_path, monkeypatch):
    monkeypatch.setattr(lane_module, "INTRADAY_FLATTEN_AT", time(23, 59))
    lane = build(tmp_path)
    held(lane, PE, 1, stop=70)
    asyncio.run(lane.execution.on_tick(PE, 122.0))      # +22%: no sale yet, the stop trails to 107.36
    assert lane.held_symbols() and lane.meta[PE]["stop_premium"] == 107.36
    asyncio.run(lane.execution.on_tick(PE, 130.5))
    assert PE not in lane.held_symbols() and lane.actions()[0]["reason"] == "guard: PROFIT_TAKE_ONE_LOT"


def test_the_index_first_target_books_before_30_percent(tmp_path, monkeypatch):
    monkeypatch.setattr(lane_module, "INTRADAY_FLATTEN_AT", time(23, 59))
    lane = build(tmp_path)
    held(lane, PE, 2, stop=70)
    lane.matrix = {"NIFTY": {"inputs": {"ib_high": 23100.0, "ib_low": 23000.0}}}
    lane.engine.latest_ticks["NSE:NIFTY50-INDEX"].ltp = 22940.0          # 0.6 IB below the low
    asyncio.run(lane.execution.on_tick(PE, 108.0))
    assert lane.portfolio.positions[PE].lots == 1 and lane.meta[PE]["booked"] == 1


def test_no_new_position_before_0930_unless_grade_a_or_b_and_triggers_wait(tmp_path):
    early = {"candidates": {"C01": {"symbol": PE, "ltp": 100.0, "lot_size": 75, "expiry": "2099-12-30",
                                    "underlying": "NIFTY", "type": "PE"}},
             "positions": {}, "book": {"cash": 1_000_000, "deployed": 0, "day_pnl": 0}, "clock": "09:20",
             "today": "2026-10-05", "market_open": True, "settle_until": "09:30", "entries_today": {}}
    out = brain.validate({"actions": [{**act("open", "C01", lots=1), "grade": "C"}]}, early, brain.Limits(capital=1_000_000))
    assert "before 09:30 only a grade A/B" in out.rejected[0]["rejected"]
    out = brain.validate({"actions": [{**act("open", "C01", lots=1), "grade": "B"}]}, early, brain.Limits(capital=1_000_000))
    assert out.accepted
    lane = build(tmp_path)
    lane.client = FakeClient(decision({**act("arm", candidate_id(lane, PE), lots=1, stop=60), "trigger_index": "NIFTY",
                                       "trigger_op": "below", "trigger_level": 22950, "valid_until": "14:00", "horizon": "intraday"}))
    asyncio.run(lane.think("test", None, NOW))
    lane.engine.latest_ticks["NSE:NIFTY50-INDEX"].ltp = 22900.0
    assert asyncio.run(lane.check_triggers(NOW.replace(hour=9, minute=25))) == [] and "T01" in lane.armed
    assert asyncio.run(lane.check_triggers(NOW.replace(hour=9, minute=31)))[0]["status"] == "filled"


def test_re_entries_need_grade_a_or_b_and_stop_after_two():
    def ctx(taken):
        return {"candidates": {"C01": {"symbol": CE, "ltp": 20.0, "lot_size": 750, "expiry": "2099-12-30",
                                       "underlying": "NIFTY", "type": "CE"}},
                "positions": {}, "book": {"cash": 1_000_000, "deployed": 0, "day_pnl": 0}, "clock": "11:00",
                "today": "2026-10-05", "market_open": True, "entries_today": {"NIFTY:CE": taken}}
    limits = brain.Limits(capital=1_000_000)
    def run(grade, taken):
        return brain.validate({"actions": [{**act("open", "C01", lots=1), "grade": grade}]}, ctx(taken), limits)
    assert "needs grade A or B" in run("C", 1).rejected[0]["rejected"]
    assert run("B", 1).accepted and run("A", 2).accepted
    assert "no more entries" in run("A", 3).rejected[0]["rejected"]
    assert run("D", 0).accepted                               # a first entry at any grade


def test_the_lane_counts_todays_entries_per_index_and_side(tmp_path):
    lane = build(tmp_path)
    held(lane, PE, 1)
    day = lane.portfolio.positions[PE].opened_at.astimezone(lane_module.IST).date().isoformat()
    assert lane.entries_today(day) == {"NIFTY:PE": 1}


def test_a_gateway_error_is_retried_once_and_reported_by_its_title(monkeypatch):
    monkeypatch.setattr(brain, "RETRY_PAUSE_S", 0)
    calls = []

    def flaky(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(520, text="<!DOCTYPE html><html><head><title>api.x.ai | 520: Web server is returning an unknown error</title></head></html>")
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(decision())}}]})

    out, _ = asyncio.run(brain.GrokClient("k", "grok-4", transport=httpx.MockTransport(flaky)).decide("s", "u"))
    assert out["market_view"] == "Trend intact." and len(calls) == 2
    page = "<html><head><title>api.x.ai | 520: Web server is returning an unknown error</title></head><body>" + "x" * 5000
    client = brain.GrokClient("k", "grok-4", transport=httpx.MockTransport(lambda r: httpx.Response(520, text=page)))
    try:
        asyncio.run(client.decide("s", "u"))
    except brain.BrainError as exc:
        assert str(exc) == "HTTP 520: api.x.ai | 520: Web server is returning an unknown error"
    else:
        raise AssertionError("expected a BrainError")


# ------------------------------------------------------------------------------------------- swing positions

def swing(lane, symbol, lots, stop=None, invalidation=None, setup="multiday_breakout"):
    lane.client = FakeClient(decision({**act("open", candidate_id(lane, symbol), lots=lots, stop=stop, reason="swing idea"),
                                       "horizon": "swing", "setup": setup, "invalidation_level": invalidation}))
    cycle = asyncio.run(lane.think("test", None, NOW))
    assert lane.portfolio.positions[symbol].lots == lots, cycle["rejected"]
    return lane.portfolio.positions[symbol]


def fresh_at_any_time(lane, monkeypatch):
    """Ticks below carry the test session's clock, not the wall clock: accept them as fresh."""
    monkeypatch.setattr(lane.execution, "_fresh_price", lambda symbol: lane.execution.last_prices.get(symbol))


def test_a_swing_position_survives_1515_and_books_at_50_not_30_percent(tmp_path, monkeypatch):
    lane = build(tmp_path)
    fresh_at_any_time(lane, monkeypatch)
    swing(lane, PE, 4, stop=60)
    assert lane.meta[PE]["horizon"] == "swing" and lane.meta[PE]["setup"] == "multiday_breakout"
    asyncio.run(lane.flatten_intraday(NOW.replace(hour=15, minute=16)))
    assert lane.held_symbols()
    asyncio.run(lane.execution.on_tick(PE, 131.0, NOW.replace(hour=11, minute=30)))   # +31%: intraday would book
    assert lane.portfolio.positions[PE].lots == 4
    asyncio.run(lane.execution.on_tick(PE, 151.0, NOW.replace(hour=11, minute=31)))
    assert lane.portfolio.positions[PE].lots == 2 and lane.meta[PE]["booked"] == 1


def test_a_swing_position_is_closed_on_its_fifth_session_and_on_index_invalidation(tmp_path, monkeypatch):
    lane = build(tmp_path)
    fresh_at_any_time(lane, monkeypatch)
    swing(lane, PE, 1, stop=40, invalidation=23100)                  # a put: wrong if NIFTY goes above 23,100
    assert lane.meta[PE]["invalidation_level"] == 23100
    lane.engine.latest_ticks["NSE:NIFTY50-INDEX"].ltp = 23080.0
    asyncio.run(lane.check_invalidations(NOW))
    assert lane.held_symbols()
    lane.engine.latest_ticks["NSE:NIFTY50-INDEX"].ltp = 23120.0
    asyncio.run(lane.check_invalidations(NOW))
    assert not lane.held_symbols() and "INDEX_INVALIDATION" in lane.actions()[0]["reason"]

    swing(lane, CE, 1, stop=10)
    lane.meta[CE]["opened_at"] = "2026-09-28T10:00:00+05:30"         # Mon 28 Sep: 5 sessions by Mon 5 Oct (2 Oct holiday)
    assert lane_module.sessions_held(lane.meta[CE]["opened_at"], NOW) == 5
    asyncio.run(lane.execution.on_tick(CE, 20.5, NOW.replace(hour=15, minute=10)))
    assert CE in lane.held_symbols()
    asyncio.run(lane.execution.on_tick(CE, 20.5, NOW.replace(hour=15, minute=16)))
    assert CE not in lane.held_symbols() and lane.actions()[0]["reason"] == "guard: SWING_HOLD_LIMIT_5_SESSIONS"


def test_the_setup_scoreboard_counts_closed_trades_per_setup_and_horizon(tmp_path, monkeypatch):
    monkeypatch.setattr(lane_module, "INTRADAY_FLATTEN_AT", time(23, 59))
    lane = build(tmp_path)
    swing(lane, PE, 2, stop=60, setup="trend_pullback")
    lane.client = FakeClient(decision(act("close", position_id="P01", reason="done")))
    lane.execution.set_quote(PE, 120.0)
    asyncio.run(lane.think("test", None, NOW))
    board = lane.setup_scoreboard()
    assert board == [{"setup": "trend_pullback", "horizon": "swing", "trades": 1, "wins": 1,
                      "net_pnl_rs": board[0]["net_pnl_rs"], "avg_return_pct": board[0]["avg_return_pct"]}]
    assert board[0]["net_pnl_rs"] > 0 and '"setup_scoreboard"' in lane.build_context(NOW)[0]
