"""Trend-state index lane (owner, 18 Nov 2026: U1, Rs 5 L): P1 states on 30-minute index bars."""
from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from macd_trader import trend_lane
from macd_trader.config import Settings
from macd_trader.events import EventHub
from macd_trader.trend_lane import Bar, TrendLane, bars_from_minutes, choose_expiry, choose_strike, run_policy

IST = ZoneInfo("Asia/Kolkata")


def _bars(closes, start=datetime(2026, 10, 1, 9, 15, tzinfo=IST), spread=4.0):
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        t = start + timedelta(minutes=30 * i)
        out.append(Bar(t, t + timedelta(minutes=30), prev, max(prev, c) + spread, min(prev, c) - spread, c))
        prev = c
    return out


def test_minute_rows_make_closed_session_bars_from_0915():
    day = datetime(2026, 11, 18, 9, 15, tzinfo=IST)
    rows = [(int((day + timedelta(minutes=m)).timestamp()), 100 + m, 101 + m, 99 + m, 100.5 + m) for m in range(75)]
    bars = bars_from_minutes(rows, now=day + timedelta(minutes=61))
    assert [b.start.strftime("%H:%M") for b in bars] == ["09:15", "09:45"]          # 10:15 bar still open
    assert bars[0].open == 100 and bars[0].close == 129.5 and bars[0].high == 130 and bars[0].low == 99


def test_p1_initiates_on_the_confirmation_and_flips_on_the_reversal():
    flat = [1000 + (3 if i % 2 else -3) for i in range(30)]
    up = [flat[-1] + 12 * i for i in range(1, 25)]
    state = run_policy(_bars(flat + up))
    assert state.direction == 1 and state.units >= 1
    assert state.actions[0][1] == trend_lane.FLIP and state.actions[0][2] == 1
    down = [up[-1] - 25 * i for i in range(1, 12)]
    state = run_policy(_bars(flat + up + down))
    assert state.direction == -1 and state.units == 1


def test_contract_choice_monthly_at_least_20_days_and_2pct_itm():
    expiries = ["2026-11-24", "2026-12-01", "2026-12-29", "2027-01-05", "2027-01-27"]
    assert choose_expiry(expiries, date(2026, 11, 18), 20) == "2026-12-29"
    assert choose_expiry(expiries, date(2026, 12, 15), 20) == "2027-01-27"
    legs = [SimpleNamespace(strike=k, option_type=r, symbol=f"X{k}{r}") for k in range(25000, 27001, 100)
            for r in ("CE", "PE")]
    assert choose_strike(legs, "CE", 26000, 0.02).strike == 25500          # 26000 x 0.98 = 25480
    assert choose_strike(legs, "PE", 26000, 0.02).strike == 26500          # 26000 x 1.02 = 26520


class _Market:
    def __init__(self):
        self.subscribed = []

    async def option_chain(self, symbol, expiry_token=None):
        expiries = [SimpleNamespace(date="2026-11-24", token="a"), SimpleNamespace(date="2026-12-29", token="b")]
        mon = "DEC" if expiry_token == "b" else "NOV"
        entries = [SimpleNamespace(strike=k, option_type=r, symbol=f"NSE:NIFTY26{mon}{k}{r}")
                   for k in range(900, 1601, 20) for r in ("CE", "PE")]
        return SimpleNamespace(expiry="2026-12-29" if mon == "DEC" else "2026-11-24", spot_price=self.spot,
                               entries=entries, expiries=expiries)

    async def lot_sizes(self, symbols):
        return {s: 65 for s in symbols}


def _minutes(path, closes, start_day=date(2026, 9, 1)):
    db = sqlite3.connect(path)
    db.execute("""CREATE TABLE historical_candles (symbol TEXT, timeframe_seconds INTEGER, timestamp INTEGER, open REAL,
                  high REAL, low REAL, close REAL, volume REAL)""")
    day, i, rows = start_day, 0, []
    while i < len(closes):
        if day.weekday() < 5:
            for slot in range(13):                                    # one 30-minute bar = 30 one-minute rows
                if i >= len(closes):
                    break
                t0 = datetime(day.year, day.month, day.day, 9, 15, tzinfo=IST) + timedelta(minutes=30 * slot)
                prev = closes[i - 1] if i else closes[0]
                for m in range(30 if slot < 12 else 15):
                    c = prev + (closes[i] - prev) * (m + 1) / (30 if slot < 12 else 15)
                    rows.append(("NSE:NIFTY50-INDEX", 60, int((t0 + timedelta(minutes=m)).timestamp()), c, c + 2, c - 2,
                                 c, 0))
                i += 1
        day += timedelta(days=1)
    db.executemany("INSERT INTO historical_candles VALUES (?,?,?,?,?,?,?,?)", rows)
    db.commit()
    db.close()
    return datetime.fromtimestamp(rows[-1][2] + 120, IST)


def test_the_lane_buys_the_trends_itm_call_after_activation_and_flips_to_the_put(tmp_path):
    flat = [1200 + (3 if i % 2 else -3) for i in range(300)]
    up = [flat[-1] + 10 * i for i in range(1, 9)]
    settings = Settings(execution_mode="paper", database_path=str(tmp_path / "m.sqlite3"),
                        research_database_path=str(tmp_path / "h.sqlite3"), trend_lane_indices_csv="NIFTY")
    now = _minutes(settings.research_database_path, flat + up)
    market = _Market()
    market.spot = up[-1]
    lane = TrendLane(settings, market, EventHub(), str(tmp_path / "trend.sqlite3"))
    lane.activated_at = now - timedelta(days=3)
    state = run_policy(lane._bars("NIFTY", now))
    assert state.direction == 1 and state.units == 1 and state.confirmed_at >= lane.activated_at
    strike = min(range(900, 1601, 20), key=lambda k: abs(k - up[-1] * 0.98))
    ce = f"NSE:NIFTY26NOV{strike}CE"                                 # 2 Oct: November is >= 20 days out
    lane.execution.set_quote(ce, 50.0, datetime.now(UTC))

    async def go(t):
        lane._last_eval = -1e9
        return await lane._evaluate("NIFTY", t)

    assert asyncio.run(go(now)) == 1
    position = lane.portfolio.positions[ce]
    assert position.quantity == 65 and lane.meta[ce]["option_type"] == "CE"
    assert lane.execution.estimate_fill(ce, "BUY", 50.0) == pytest.approx(position.average_price)
    assert asyncio.run(go(now)) == 0                                  # same bar: nothing more to do

    # Before activation: an old trend is not bought.
    lane2 = TrendLane(settings, market, EventHub(), str(tmp_path / "trend2.sqlite3"))
    lane2.activated_at = now
    lane2.execution.set_quote(ce, 50.0, datetime.now(UTC))
    lane2._last_eval = -1e9
    assert asyncio.run(lane2._evaluate("NIFTY", now)) == 0 and not lane2.portfolio.positions
