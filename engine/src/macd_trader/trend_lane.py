"""Trend-state index lane (owner, 18 Nov 2026: U1, capital Rs 5 L).

The 27 Oct 2026 study (research/trend_episodes_20261027, sim.py policy P1 on 30-minute bars, K = 2) traded as a paper
book of index options. Owner's frame (27 Oct): learn the trend's life cycle and move through trade states -- flat,
initiate, add, reduce, exit -- over hours to days, with re-entry; not minute-bar chasing.

Trend tracker (causal, per index, on 30-minute bars from 09:15 IST built from the stored 1-minute candles):
directional change on closes against the running extreme: an up trend ends when a close is K x ATR(14, Wilder)
below its highest high, and a down trend when a close is K x ATR above its lowest low; that close confirms the new
trend.
P1 states, decided at a bar's close and acted on at once (the study filled at the next bar's open):
- INITIATE: 1 unit at the confirmation (a flip closes the old side first);
- ADD 1 unit (at most 3) while the trend is RUNNING (3-10 bars old), STRONG (MACD(12,26,9) histogram, in ATRs, at
  least 75% of its peak in this trend) and has RESUMED after a shallow pullback (0.25-1 ATR off the extreme, then
  back within 0.25 ATR on a close in the trend's direction);
- REDUCE to 1 unit when OLD (> 10 bars) and FADING (< 50% of the peak);
- EXIT / FLIP at the reversal confirmation. There is no price stop: the trend's own reversal is the exit.
Instrument: the index's option on the trend's side (CE up, PE down), about 2% in the money, in the nearest MONTHLY
series at least 20 calendar days out (study: ATM >= 7 d PF 1.20, >= 20 d PF 1.32, 2% ITM >= 20 d PF 1.36; real
premiums Apr 2025 - Mar 2026, 8/16 months positive, profits from trending months). A unit is
``trend_lane_lots_per_unit`` exchange lots. Index options are cash settled: no rollover margin.
The lane joins only trends confirmed after it was activated (it never buys into an old trend on its first day) and
keeps following a trend it holds. Nothing is skipped on a score; cash is the only gate. Not proven forward.
"""
from __future__ import annotations

import asyncio
import json
import math
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from . import market_calendar
from .execution import ExecutionManager
from .models import Signal
from .smart_lane import SmartLane, TAKEN, NO_CASH, NO_PRICE, ENTRY_REJECTED, WAITING_PRICE, SESSION_CLOSED
from .universe import INDEX_SPOTS

IST = ZoneInfo("Asia/Kolkata")
LANE_NAME = "trend_index"
BAR_MINUTES = 30
SESSION_START = 9 * 60 + 15
SESSION_END = 15 * 60 + 30
HISTORY_DAYS = 70                    # calendar days of 1-minute candles replayed each evaluation (~45 sessions)
EVALUATE_EVERY = 20.0                # seconds between evaluations in session
PICK_REFRESH_SECONDS = 1800          # contract choice re-read every 30 min (or when spot moved > 1%)
RUNNING = (3, 10)
STRONG, FADING = 0.75, 0.5
PULL = (0.25, 1.0)
INITIATE, ADD, REDUCE, FLIP, EXIT = "INITIATE", "ADD", "REDUCE", "FLIP", "EXIT"


# ------------------------------------------------------------------------------------------------- bars


@dataclass(slots=True)
class Bar:
    start: datetime          # IST
    end: datetime            # IST (15:30 for the last bar of a session)
    open: float
    high: float
    low: float
    close: float


def bars_from_minutes(rows, now: datetime | None = None) -> list[Bar]:
    """30-minute session bars (09:15, 09:45, ... 15:15) from (epoch_seconds, open, high, low, close) 1-minute rows.
    A bar counts only once it has closed (``now`` past its end)."""
    buckets: dict[tuple[date, int], list] = {}
    for ts, o, h, l, c in rows:
        t = datetime.fromtimestamp(int(ts), IST)
        minute = t.hour * 60 + t.minute
        if minute < SESSION_START or minute >= SESSION_END:
            continue
        slot = (minute - SESSION_START) // BAR_MINUTES
        key = (t.date(), slot)
        b = buckets.get(key)
        if b is None:
            buckets[key] = [float(o), float(h), float(l), float(c)]
        else:
            b[1] = max(b[1], float(h))
            b[2] = min(b[2], float(l))
            b[3] = float(c)
    out = []
    for (day, slot), (o, h, l, c) in sorted(buckets.items()):
        start = datetime.combine(day, time(9, 15), IST) + timedelta(minutes=BAR_MINUTES * slot)
        end = min(start + timedelta(minutes=BAR_MINUTES), datetime.combine(day, time(15, 30), IST))
        if now is not None and end > now:
            continue
        out.append(Bar(start, end, o, h, l, c))
    return out


def _ema(values: list[float], n: int) -> list[float]:
    alpha, out = 2 / (n + 1), []
    for i, v in enumerate(values):
        out.append(v if i == 0 else out[-1] + alpha * (v - out[-1]))
    return out


def _wilder(values: list[float], n: int) -> list[float]:
    out = [math.nan] * len(values)
    if len(values) < n:
        return out
    out[n - 1] = sum(values[:n]) / n
    for i in range(n, len(values)):
        out[i] = out[i - 1] + (values[i] - out[i - 1]) / n
    return out


def indicators(bars: list[Bar]) -> tuple[list[float], list[float]]:
    """ATR(14, Wilder) and the MACD(12,26,9) histogram, as study.indicators."""
    closes = [b.close for b in bars]
    tr = []
    for i, b in enumerate(bars):
        prev = bars[i - 1].close if i else b.close
        tr.append(max(b.high - b.low, abs(b.high - prev), abs(b.low - prev)))
    macd = [a - b for a, b in zip(_ema(closes, 12), _ema(closes, 26))]
    hist = [m - s for m, s in zip(macd, _ema(macd, 9))]
    return _wilder(tr, 14), hist


# ------------------------------------------------------------------------------------------- the policy


@dataclass
class TrendState:
    direction: int = 0                  # +1 up, -1 down, 0 none yet
    units: int = 0                      # P1's units after the last closed bar's decision
    confirmed_at: datetime | None = None  # the confirming bar's end (IST)
    age: int = 0                        # bars since confirmation
    fade: float = 0.0                   # histogram now / its peak in this trend
    pullback: float = 0.0               # ATRs off the extreme
    last_action: str | None = None      # the last closed bar's decision
    last_bar: datetime | None = None    # the last closed bar's end
    actions: list[tuple[str, str, int]] = field(default_factory=list)   # (bar end ISO, action, direction), recent

    def view(self) -> dict:
        return {"direction": {1: "UP", -1: "DOWN"}.get(self.direction, "NONE"), "units": self.units,
                "confirmed_at": self.confirmed_at.isoformat() if self.confirmed_at else None, "age_bars": self.age,
                "fade": round(self.fade, 3), "pullback_atr": round(self.pullback, 3), "last_action": self.last_action,
                "last_bar": self.last_bar.isoformat() if self.last_bar else None, "recent": self.actions[-8:]}


def run_policy(bars: list[Bar], k: float = 2.0, max_units: int = 3) -> TrendState:
    """P1 over closed bars, bar by bar (sim.simulate's tracker and rules); the returned units are what P1 holds once
    the last bar's decision is filled."""
    n = len(bars)
    state = TrendState()
    if n == 0:
        return state
    atr, hist = indicators(bars)
    h = [b.high for b in bars]
    lo = [b.low for b in bars]
    c = [b.close for b in bars]
    units = 0
    d = 0
    ext = conf = None
    peak, pulled = 0.0, False
    pending: tuple[str, int] | None = None
    start = next((i for i in range(n) if math.isfinite(atr[i]) and atr[i] > 0), n)
    lo_i = hi_i = start
    for i in range(start, n):
        if pending is not None:                         # fill the previous close's decision at this bar
            kind, pd_ = pending
            if kind == FLIP:
                units = 1 if pd_ else 0
            elif kind == ADD and units < max_units:
                units += 1
            elif kind == REDUCE:
                units = min(units, 1)
            elif kind == EXIT:
                units = 0
            pending = None
        a = atr[i]
        if not math.isfinite(a) or a <= 0:
            continue
        reversed_now = False
        new_ext = False
        if d == 0:
            hi_i = i if h[i] > h[hi_i] else hi_i
            lo_i = i if lo[i] < lo[lo_i] else lo_i
            if c[i] >= lo[lo_i] + k * a:
                d, ext, conf, reversed_now = 1, h[i], i, True
            elif c[i] <= h[hi_i] - k * a:
                d, ext, conf, reversed_now = -1, lo[i], i, True
        elif d == 1:
            new_ext = h[i] > ext
            ext = max(ext, h[i])
            if c[i] <= ext - k * a:
                d, ext, conf, reversed_now = -1, lo[i], i, True
        else:
            new_ext = lo[i] < ext
            ext = min(ext, lo[i])
            if c[i] >= ext + k * a:
                d, ext, conf, reversed_now = 1, h[i], i, True
        action = None
        if reversed_now:
            peak, pulled = 0.0, False
            pending = action = (FLIP, d)
        elif d != 0:
            hs = d * hist[i] / a
            peak = max(peak, hs)
            fade = hs / peak if peak > 0 else 0.0
            age = i - conf
            pullback = d * (ext - c[i]) / a
            running, strong = RUNNING[0] <= age <= RUNNING[1], fade >= STRONG
            old, fading = age > RUNNING[1], fade < FADING
            if PULL[0] <= pullback <= PULL[1]:
                pulled = True
            resumed = pulled and pullback < PULL[0] and d * (c[i] - c[i - 1]) > 0
            if units:
                if old and fading and units > 1:
                    pending = action = (REDUCE, d)
                elif running and strong and resumed and units < max_units:
                    pending, pulled = (ADD, d), False
                    action = pending
            state.fade, state.pullback, state.age = fade, pullback, age
        if action is not None:
            state.actions.append((bars[i].end.isoformat(), action[0], action[1]))
            del state.actions[:-50]
        state.last_action = action[0] if action else None
    if pending is not None:                             # the last close's decision, acted on now
        kind, pd_ = pending
        if kind == FLIP:
            units = 1 if pd_ else 0
        elif kind == ADD and units < max_units:
            units += 1
        elif kind == REDUCE:
            units = min(units, 1)
    state.direction, state.units = d, units
    state.confirmed_at = bars[conf].end if conf is not None else None
    state.age = (n - 1 - conf) if conf is not None else 0
    state.last_bar = bars[-1].end
    return state


# ------------------------------------------------------------------------------------- contract choice


def monthly_expiries(expiries: list[str]) -> list[str]:
    """The last listed expiry of each month (the monthly series)."""
    by_month: dict[str, str] = {}
    for e in sorted(expiries):
        by_month[e[:7]] = e
    return sorted(by_month.values())


def choose_expiry(expiries: list[str], today: date, min_days: int) -> str | None:
    """The nearest monthly series at least ``min_days`` calendar days out."""
    for e in monthly_expiries(expiries):
        if (date.fromisoformat(e) - today).days >= min_days:
            return e
    return None


def choose_strike(entries, option_type: str, spot: float, itm_pct: float):
    """The listed strike nearest spot x (1 - itm) for a call, spot x (1 + itm) for a put."""
    target = spot * (1 - itm_pct) if option_type == "CE" else spot * (1 + itm_pct)
    legs = [e for e in entries if e.option_type == option_type and e.symbol]
    return min(legs, key=lambda e: abs(e.strike - target)) if legs else None


@dataclass(slots=True)
class Pick:
    index: str
    option_type: str
    symbol: str
    strike: float
    expiry: str
    lot_size: int
    spot: float
    at: datetime


# ------------------------------------------------------------------------------------------ execution


class TrendExecutionManager(ExecutionManager):
    """Paper fills from the shared manager; no price exits -- the lane's state machine closes positions."""

    def __init__(self, *args, lane: "TrendLane", **kwargs):
        super().__init__(*args, **kwargs)
        self.lane = lane

    def exit_rules(self, _source: str = "trend") -> dict:
        return {"hard": 0.99, "act": 1e9, "trail": 0.99, "target": None, "staged": False, "hold": 0,
                "invalidate": False}

    async def on_tick(self, symbol: str, price: float, timestamp: datetime | None = None) -> None:
        self.set_quote(symbol, price, timestamp)
        if symbol not in self.portfolio.positions:
            return
        if self.portfolio.mark(symbol, price):
            self.events.publish("portfolio", self.portfolio_snapshot())

    async def on_closed_bar(self, symbol: str, macd: float) -> bool:
        return False

    async def on_signal(self, signal: Signal) -> None:
        return None


class TrendLane(SmartLane):
    """One paper book of index options following P1 trend states per index."""

    kind = "trend"
    execution_class = TrendExecutionManager

    def __init__(self, settings, broker, events, database_path: str, name: str = LANE_NAME):
        self.indices = [i.strip().upper() for i in str(settings.trend_lane_indices_csv or "").split(",")
                        if i.strip().upper() in INDEX_SPOTS]
        self.states: dict[str, TrendState] = {}
        self.picks: dict[tuple[str, str], Pick] = {}
        self.pick_errors: dict[str, str] = {}
        self.subscribe = None                      # engine hook: async callable(list[str]) for a live subscription
        self._attempted: dict[tuple[str, str, str], int] = {}
        self._last_eval = 0.0
        self.last_evaluated_at: str | None = None
        super().__init__(name, settings.trend_lane_capital, settings, broker, events, database_path)
        with self._db_lock, self._db:
            self._db.execute("CREATE TABLE IF NOT EXISTS trend_kv (key TEXT PRIMARY KEY, value TEXT)")
            row = self._db.execute("SELECT value FROM trend_kv WHERE key = 'activated_at'").fetchone()
            if row is None:
                self.activated_at = datetime.now(IST)
                self._db.execute("INSERT INTO trend_kv VALUES ('activated_at', ?)", (self.activated_at.isoformat(),))
            else:
                self.activated_at = datetime.fromisoformat(row["value"])
        self._task: asyncio.Task | None = None

    @property
    def market(self):
        """The engine's broker for chains and lot sizes (follows reconfigure's rebind; orders stay paper)."""
        return self.broker._broker

    def execution_settings(self, settings):
        return settings.model_copy(update={
            "execution_mode": "paper", "allow_live_orders": False, "initial_capital": self.capital,
            "hard_stop_pct": 0.99, "trailing_activation_pct": 1e9, "trailing_stop_pct": 0.99,
            "target_position_notional": 1.0, "max_target_entry_lots": 1_000, "max_trade_lots": 1_000,
            "max_positions": 0, "min_cash_reserve": 0.0, "auto_trade": True, "macd_invalidation_exit": False,
            "pyramid_adds": False,
        })

    # ------------------------------------------------------------------ wiring (engine fan-out: not this lane's)

    def set_tradable_contracts(self, lot_sizes: dict[str, int]) -> None:
        held = {s: p.lot_size for s, p in self.portfolio.positions.items() if p.quantity > 0 and p.lot_size > 0}
        picks = {p.symbol: p.lot_size for p in self.picks.values()}
        self.execution.set_tradable_contracts({**held, **picks})

    def set_contract_context(self, contracts: dict[str, dict]) -> None:
        return None

    def on_macd_signal(self, signal, option_type, features, now=None) -> None:
        return None

    def on_blast_candidate(self, row, now=None) -> None:
        return None

    def cutoff(self) -> float:
        return 0.0

    def feed_symbols(self) -> list[str]:
        return list(dict.fromkeys([*(p.symbol for p in self.picks.values()), *self.held_symbols()]))

    # ------------------------------------------------------------------ data

    def _bars(self, index: str, now: datetime) -> list[Bar]:
        spot = INDEX_SPOTS[index]
        since = int((now - timedelta(days=HISTORY_DAYS)).timestamp())
        path = self.settings.research_database_path
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10) as db:
            rows = db.execute("""SELECT timestamp, open, high, low, close FROM historical_candles
                                 WHERE symbol = ? AND timeframe_seconds = 60 AND timestamp >= ? ORDER BY timestamp""",
                              (spot, since)).fetchall()
        # a bar is closed once its last minute has been written (end + 60 s)
        return bars_from_minutes(rows, now - timedelta(seconds=60))

    async def _pick(self, index: str, option_type: str, now: datetime) -> Pick | None:
        key = (index, option_type)
        spot_symbol = INDEX_SPOTS[index]
        held = self.picks.get(key)
        spot_now = self.execution.last_prices.get(spot_symbol)
        if held and (now - held.at).total_seconds() < PICK_REFRESH_SECONDS and held.at.date() == now.date() and (
                not spot_now or abs(spot_now / held.spot - 1) < 0.01):
            return held
        try:
            front = await self.market.option_chain(spot_symbol)
            expiries = {e.date: e for e in (front.expiries or [])}
            chosen = choose_expiry(list(expiries), now.date(), int(self.settings.trend_lane_min_days))
            if chosen is None:
                self.pick_errors[index] = "no monthly series far enough out"
                return held
            chain = front if front.expiry[:10] == chosen else await self.market.option_chain(
                spot_symbol, expiries[chosen].token)
            spot = float(chain.spot_price or front.spot_price or spot_now or 0)
            leg = choose_strike(chain.entries, option_type, spot, float(self.settings.trend_lane_itm_pct))
            if leg is None or spot <= 0:
                self.pick_errors[index] = f"no {option_type} strikes in the {chosen} chain"
                return held
            lots = await self.market.lot_sizes([leg.symbol])
            lot = int(lots.get(leg.symbol) or 0)
            if lot < 1:
                self.pick_errors[index] = f"no lot size for {leg.symbol}"
                return held
        except Exception as exc:  # noqa: BLE001 -- keep the last pick rather than trade nothing
            self.pick_errors[index] = f"{type(exc).__name__}: {exc}"[:200]
            return held
        pick = Pick(index, option_type, leg.symbol, float(leg.strike), chosen, lot, spot, now)
        self.picks[key] = pick
        self.pick_errors.pop(index, None)
        self.execution.lot_sizes[pick.symbol] = lot
        self.execution.tradable_symbols.add(pick.symbol)
        self.execution.set_contract_expiries({pick.symbol: chosen})
        if self.subscribe is not None:
            try:
                await self.subscribe([pick.symbol])
            except Exception:  # noqa: BLE001 -- the next reconfigure subscribes it
                pass
        return pick

    # ------------------------------------------------------------------ the loop

    def _held(self, index: str) -> list[tuple[str, object]]:
        """This index's open legs, oldest first."""
        out = []
        for symbol, position in self.portfolio.positions.items():
            meta = self.meta.get(symbol) or {}
            if position.quantity > 0 and meta.get("spot_symbol") == INDEX_SPOTS[index]:
                out.append((symbol, position))
        return sorted(out, key=lambda kv: str(getattr(kv[1], "opened_at", "")))

    async def step(self, now: datetime | None = None) -> int:
        now = (now or datetime.now(UTC)).astimezone(IST)
        loop_now = asyncio.get_running_loop().time()
        if loop_now - self._last_eval < EVALUATE_EVERY:
            return 0
        self._last_eval = loop_now
        if not market_calendar.is_trading_day(now.date()):
            return 0
        minute = now.hour * 60 + now.minute
        if not (SESSION_START <= minute < SESSION_END):
            return 0
        acted = 0
        for index in self.indices:
            try:
                acted += await self._evaluate(index, now)
            except Exception as exc:  # noqa: BLE001 -- one index must not stop the others
                self.error = f"{index}: {type(exc).__name__}: {exc}"[:300]
        self.last_evaluated_at = now.isoformat(timespec="seconds")
        return acted

    async def _evaluate(self, index: str, now: datetime) -> int:
        bars = await asyncio.to_thread(self._bars, index, now)
        state = run_policy(bars, float(self.settings.trend_lane_k), int(self.settings.trend_lane_max_units))
        self.states[index] = state
        if state.last_bar is None:
            return 0
        bar_key = state.last_bar.isoformat()
        wanted = "CE" if state.direction > 0 else ("PE" if state.direction < 0 else None)
        lots_per_unit = max(1, int(self.settings.trend_lane_lots_per_unit))
        held = self._held(index)
        acted = 0
        # 1. The wrong side (a flip) or no trend: out.
        for symbol, position in held:
            if (self.meta.get(symbol) or {}).get("option_type") != wanted or state.units == 0:
                acted += await self._sell(index, symbol, position.quantity, FLIP if wanted else EXIT, state, now)
        held = [(s, p) for s, p in self._held(index) if (self.meta.get(s) or {}).get("option_type") == wanted]
        units = sum(p.lots for _, p in held) // lots_per_unit
        # 2. Reduce: newest legs first.
        while units > state.units and held:
            symbol, position = held.pop()
            take = min(position.lots, (units - state.units) * lots_per_unit)
            acted += await self._sell(index, symbol, take * position.lot_size, REDUCE, state, now)
            units -= max(1, take // lots_per_unit)
        # 3. Initiate / add, only into a trend confirmed after activation or one already held.
        if wanted and units < state.units and (units > 0 or (state.confirmed_at and state.confirmed_at >= self.activated_at)):
            kind = INITIATE if units == 0 else ADD
            marker = (index, bar_key, kind)
            if self._attempted.get(marker, 0) < 30:          # ~10 min of retries for a fresh quote, then next bar
                self._attempted[marker] = self._attempted.get(marker, 0) + 1
                acted += await self._buy(index, wanted, (state.units - units) * lots_per_unit, kind, state, now, marker)
        if len(self._attempted) > 500:
            for key in list(self._attempted)[:-200]:
                del self._attempted[key]
        return acted

    def _signal(self, symbol: str, side: str, kind: str, price: float) -> Signal:
        return Signal(symbol, side, f"TREND_{kind}", float(price), 0.0, 0.0, 0.0)

    async def _sell(self, index: str, symbol: str, quantity: int, kind: str, state: TrendState, now: datetime) -> int:
        position = self.portfolio.positions.get(symbol)
        if position is None or quantity <= 0:
            return 0
        self.execution.arm_exit(symbol, f"TREND_{kind}")
        try:
            await self.execution.submit(symbol, "SELL", min(quantity, position.quantity))
        except (ValueError, RuntimeError, PermissionError) as exc:
            self.execution._armed_exits.discard(symbol)
            self._note(index, symbol, kind, NO_PRICE if "price" in str(exc).lower() else ENTRY_REJECTED, now, state,
                       {"error": str(exc)[:200], "side": "SELL"})
            return 0
        self._note(index, symbol, kind, TAKEN, now, state, {"side": "SELL", "quantity": quantity})
        return 1

    async def _buy(self, index: str, option_type: str, lots: int, kind: str, state: TrendState, now: datetime,
                   marker) -> int:
        pick = await self._pick(index, option_type, now)
        if pick is None:
            self._note(index, None, kind, ENTRY_REJECTED, now, state, {"error": self.pick_errors.get(index)})
            return 0
        try:
            price = self.execution._fresh_price(pick.symbol)
        except RuntimeError:
            if self._attempted.get(marker) == 1:
                self._note(index, pick.symbol, kind, WAITING_PRICE, now, state, {"reason": "waiting for a fresh quote"})
            return 0
        per_lot = self.execution.estimate_fill(pick.symbol, "BUY", price) * pick.lot_size
        affordable = int(self.portfolio.cash // per_lot) if per_lot > 0 else 0
        take = min(lots, affordable)
        if take < 1:
            self._attempted[marker] = 10_000                    # once per bar: cash will not appear by itself
            self._note(index, pick.symbol, kind, NO_CASH, now, state,
                       {"one_lot": round(per_lot, 2), "cash": round(self.portfolio.cash, 2)})
            return 0
        try:
            order = await self.execution.submit(pick.symbol, "BUY", lots=take,
                                                signal=self._signal(pick.symbol, "BUY", kind, price))
        except (ValueError, RuntimeError, PermissionError) as exc:
            self._note(index, pick.symbol, kind, ENTRY_REJECTED, now, state, {"error": str(exc)[:200]})
            return 0
        self._attempted[marker] = 10_000
        position = self.portfolio.positions.get(pick.symbol)
        journal = self._note(index, pick.symbol, kind, TAKEN, now, state,
                             {"side": "BUY", "lots": take, "price": order.fill_price, "strike": pick.strike,
                              "expiry": pick.expiry})
        if position is not None and pick.symbol not in self.meta:
            meta = {"position_id": position.position_id, "symbol": pick.symbol, "source": "trend",
                    "opened_at": now.isoformat(), "expiry": pick.expiry, "strike": pick.strike,
                    "option_type": option_type, "spot_symbol": INDEX_SPOTS[index], "score": None,
                    "dispersion": None, "journal_id": journal}
            self.meta[pick.symbol] = meta
            with self._db_lock, self._db:
                self._db.execute(
                    """INSERT OR REPLACE INTO smart_positions (position_id, symbol, source, opened_at, expiry, strike,
                       option_type, spot_symbol, score, dispersion, journal_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    tuple(meta[k] for k in ("position_id", "symbol", "source", "opened_at", "expiry", "strike",
                                            "option_type", "spot_symbol", "score", "dispersion", "journal_id")))
        return 1

    def _note(self, index: str, symbol: str | None, kind: str, decision: str, now: datetime, state: TrendState,
              detail: dict) -> str:
        from uuid import uuid4
        row_id = uuid4().hex
        info = {**detail, "index": index, "action": kind, "trend": state.view()}
        self._journal_buffer.append((
            row_id, now.isoformat(), now.date().isoformat(), "trend", symbol or INDEX_SPOTS[index],
            "CE" if state.direction > 0 else "PE", state.last_bar.isoformat() if state.last_bar else None,
            None, detail.get("price"), decision, None, None, None, None, detail.get("lots"), None,
            json.dumps(info, default=str)))
        self.counts[decision] = self.counts.get(decision, 0) + 1
        self.recent.append({"at": now.isoformat(), "symbol": symbol or index, "source": "trend",
                            "decision": f"{kind} {decision}", "score": None})
        del self.recent[:-40]
        self.events.publish("decision", self.recent[-1])
        return row_id

    # ------------------------------------------------------------------ lifecycle

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        await asyncio.to_thread(self.flush_journal)

    # ------------------------------------------------------------------ views

    def snapshot(self) -> dict:
        out = super().snapshot()
        for row in out["portfolio"]["positions"]:
            meta = self.meta.get(row["symbol"], {})
            row["index"] = next((i for i, s in INDEX_SPOTS.items() if s == meta.get("spot_symbol")), None)
            row["option_type"] = meta.get("option_type")
            row["strike"] = meta.get("strike")
        return out

    def health(self) -> dict:
        snap = self.portfolio.snapshot()
        held_units = {}
        for index in self.indices:
            held_units[index] = sum(p.lots for _, p in self._held(index)) // max(1, int(self.settings.trend_lane_lots_per_unit))
        return {"lane": self.name, "kind": self.kind, "capital": self.capital, "counts": dict(self.counts),
                "recent": self.recent[-12:], "error": self.error, "last_evaluated_at": self.last_evaluated_at,
                "activated_at": self.activated_at.isoformat(), "open_positions": len(self.held_symbols()),
                "cash": snap["cash"], "equity": snap["equity"],
                "indices": {index: {**(self.states[index].view() if index in self.states else {}),
                                    "held_units": held_units.get(index, 0),
                                    "pick_error": self.pick_errors.get(index)} for index in self.indices},
                "picks": [{"index": p.index, "option_type": p.option_type, "symbol": p.symbol, "strike": p.strike,
                           "expiry": p.expiry, "lot_size": p.lot_size, "spot": p.spot,
                           "at": p.at.isoformat(timespec="seconds")} for p in self.picks.values()],
                "rules": {
                    "bars": "30-minute index bars from 09:15 IST (stored 1-minute candles)",
                    "trend": f"directional change, K = {self.settings.trend_lane_k:g} x ATR(14)",
                    "states": "initiate 1 unit at confirmation; add (max "
                              f"{int(self.settings.trend_lane_max_units)}) when 3-10 bars old, MACD histogram >= 75% of "
                              "its trend peak and resuming after a 0.25-1 ATR pullback; reduce to 1 when > 10 bars and "
                              "< 50%; flip at the reversal; no price stop",
                    "instrument": f"CE up / PE down, about {self.settings.trend_lane_itm_pct * 100:g}% ITM, nearest "
                                  f"monthly series >= {int(self.settings.trend_lane_min_days)} days out",
                    "unit_lots": int(self.settings.trend_lane_lots_per_unit),
                    "joins": "only trends confirmed after activation; cash is the only gate",
                },
                "validation": "research/trend_episodes_20261027: index options 2% ITM >= 20 d PF 1.36 (Apr 2025 - "
                              "Mar 2026, 8/16 months positive); not proven forward"}
