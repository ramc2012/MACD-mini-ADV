"""Agentic trading lane: a paper options book run by a language model (Grok) that can open, add, reduce, close
and re-level positions at any time, writing its reasoning into a journal.

Cycle (every ``agentic_cycle_minutes`` in the session, sooner on a trigger):
  1. build the context: indices, VIX, breadth, sectors, the index scorecard, the 14:47 option-agent picks, the
     open book with each position's thesis, and a short list of tradable contracts with live prices
  2. ask the brain (agentic_brain): Grok, or the rules-only fallback when no key / the call fails
  3. validate every action in code (agentic_brain.validate) and execute the accepted ones in the paper book
  4. store the cycle (market view, decision, accepted, rejected, fills) and each action's commentary
Triggers for an early cycle: a position moving 25% since its last review, NIFTY moving 0.4% since the last
cycle, a stop or target being hit, or the operator's "think now" (with an optional note).

Guards that do not wait for the model, on every tick: the position's stop and target premiums, a hard floor at
-55%, and out by 15:10 on a contract's expiry day. Paper only: the lane holds an order-only broker wrapper and
ExecutionManager never calls the broker in paper mode.

The lane exposes the same surface as a smart lane (portfolio, execution, held_symbols, set_tradable_contracts,
set_contract_context, health, ...) so the engine's existing wiring -- tick fan-out, subscriptions, expiry
settlement, closed-position sweeps -- carries it unchanged (engine._smart_lanes includes it).
"""
from __future__ import annotations

import asyncio
import json
import math
import sqlite3
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from threading import Lock
from uuid import uuid4
from zoneinfo import ZoneInfo

from . import agentic_brain as brain
from . import agentic_features as feat
from . import market_calendar
from .blast_engine import LaneEvents, OrderOnlyBroker
from .config import RUNTIME_DIR
from .execution import ExecutionManager
from .models import Trade
from .portfolio import Portfolio
from .repository import TradeRepository

IST = ZoneInfo("Asia/Kolkata")
LANE_NAME = "agentic"
SESSION_OPEN, SESSION_CLOSE = time(9, 15), time(15, 30)
FIRST_CYCLE_AT = time(9, 20)
LAST_CYCLE_AT = time(15, 20)
EXPIRY_FLATTEN_AT = time(15, 10)
HARD_FLOOR = 0.55
POSITION_TRIGGER = 0.25
INDEX_TRIGGER = 0.004
MIN_TRIGGER_GAP_SECONDS = 300          # event cycles (level breaks, fills, triggers) at most every 5 minutes
INTRADAY_FLATTEN_AT = time(15, 15)
LEVEL_INDICES = ("NIFTY", "BANKNIFTY")
MAX_ENTRY_DRIFT = 0.08             # skip an entry whose premium ran this far while the model was thinking
UNIVERSES = ("index", "all")          # index: NIFTY / BANKNIFTY / SENSEX / MIDCPNIFTY options only
CYCLE_CHOICES = (15, 30, 60, 120)
DEFAULT_UNIVERSE = "index"
MAX_CYCLES_PER_DAY = 60
EQUITY_EVERY_SECONDS = 30.0
NIFTY = "NSE:NIFTY50-INDEX"
INDEX_SPOTS = {"NIFTY": "NSE:NIFTY50-INDEX", "BANKNIFTY": "NSE:NIFTYBANK-INDEX", "SENSEX": "BSE:SENSEX-INDEX",
               "MIDCPNIFTY": "NSE:MIDCPNIFTY-INDEX"}
SECTOR_NAMES = {"NSE:NIFTYIT-INDEX": "IT", "NSE:NIFTYAUTO-INDEX": "Auto", "NSE:NIFTYPHARMA-INDEX": "Pharma",
                "NSE:NIFTYFMCG-INDEX": "FMCG", "NSE:NIFTYMETAL-INDEX": "Metal", "NSE:NIFTYENERGY-INDEX": "Energy",
                "NSE:NIFTYREALTY-INDEX": "Realty", "NSE:FINNIFTY-INDEX": "Fin", "NSE:NIFTYNXT50-INDEX": "Next50",
                "NSE:NIFTYPSUBANK-INDEX": "PSU Bank", "NSE:NIFTYPVTBANK-INDEX": "Pvt Bank", "NSE:NIFTYMEDIA-INDEX": "Media",
                "NSE:NIFTYINFRA-INDEX": "Infra"}
STOCK_SHORTLIST = 24

SCHEMA = """
CREATE TABLE IF NOT EXISTS agentic_cycles (
  cycle_id TEXT PRIMARY KEY, at TEXT NOT NULL, day TEXT NOT NULL, trigger TEXT, mode TEXT, model TEXT,
  market_view TEXT, regime TEXT, confidence REAL, notes TEXT, operator_note TEXT,
  decision TEXT, accepted TEXT, rejected TEXT, executed TEXT, digest TEXT,
  latency_ms INTEGER, prompt_tokens INTEGER, completion_tokens INTEGER, error TEXT);
CREATE INDEX IF NOT EXISTS agentic_cycles_day ON agentic_cycles(day, at);
CREATE TABLE IF NOT EXISTS agentic_actions (
  action_id TEXT PRIMARY KEY, cycle_id TEXT, at TEXT NOT NULL, action TEXT, symbol TEXT, lots INTEGER,
  price REAL, status TEXT, reason TEXT, note TEXT, order_id TEXT);
CREATE INDEX IF NOT EXISTS agentic_actions_at ON agentic_actions(at);
CREATE TABLE IF NOT EXISTS agentic_triggers (
  trigger_id TEXT NOT NULL, day TEXT NOT NULL, created_at TEXT, spec TEXT, status TEXT, closed_at TEXT, outcome TEXT,
  PRIMARY KEY (day, trigger_id));
CREATE TABLE IF NOT EXISTS agentic_positions (
  symbol TEXT PRIMARY KEY, position_id TEXT, opened_at TEXT, thesis TEXT, stop_premium REAL, target_premium REAL,
  review_price REAL, booked INTEGER DEFAULT 0, underlying TEXT, expiry TEXT, last_comment TEXT, updated_at TEXT);
"""


def _now() -> datetime:
    return datetime.now(IST)


def in_session(now: datetime) -> bool:
    local = now.astimezone(IST)
    return market_calendar.is_trading_day(local.date()) and SESSION_OPEN <= local.time() < SESSION_CLOSE


def _r(value, digits=2):
    return None if value is None or not isinstance(value, (int, float)) or not math.isfinite(value) else round(value, digits)


def _underlying_of(spot_symbol: str) -> str:
    for root, spot in INDEX_SPOTS.items():
        if spot == spot_symbol:
            return root
    return spot_symbol.split(":", 1)[-1].removesuffix("-EQ").removesuffix("-INDEX")


# ------------------------------------------------------------------------------------------ daily statistics

def daily_stats(database_path: str, symbols: list[str]) -> dict[str, dict]:
    """Per symbol from the daily_closes table (sector_wheel.py): 5/20/60-session returns, 20/50-day averages,
    60-day relative strength vs NIFTY and 20-day realised volatility. Missing data -> missing keys."""
    out: dict[str, dict] = {}
    try:
        with sqlite3.connect(f"file:{database_path}?mode=ro", uri=True, timeout=30) as db:
            rows = db.execute(f"""SELECT symbol, day, close FROM daily_closes WHERE symbol IN ({','.join('?' * len(symbols))})
                                  AND day >= ? ORDER BY symbol, day""",
                              (*symbols, (date.today() - timedelta(days=160)).isoformat())).fetchall()
    except sqlite3.Error:
        return out
    series: dict[str, list[tuple[str, float]]] = {}
    for symbol, day, close in rows:
        if close:
            series.setdefault(symbol, []).append((day, float(close)))
    bench = dict(series.get(NIFTY, []))
    for symbol, s in series.items():
        closes = [c for _, c in s]
        if len(closes) < 6:
            continue
        stat = {"as_of": s[-1][0], "close": closes[-1]}
        for n in (5, 20, 60):
            if len(closes) > n:
                stat[f"ret{n}"] = (closes[-1] / closes[-1 - n] - 1) * 100
        for n in (20, 50):
            if len(closes) >= n:
                stat[f"ma{n}"] = sum(closes[-n:]) / n
        if len(closes) > 21:
            rets = [math.log(closes[i] / closes[i - 1]) for i in range(len(closes) - 20, len(closes))]
            mean = sum(rets) / len(rets)
            stat["vol20"] = math.sqrt(sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)) * math.sqrt(252) * 100
        if len(s) > 60 and bench:
            then_day, now_day = s[-61][0], s[-1][0]
            if then_day in bench and now_day in bench:
                stat["rs60"] = ((closes[-1] / s[-61][1]) / (bench[now_day] / bench[then_day]) - 1) * 100
        out[symbol] = stat
    return out


def intraday_ohl(database_path: str, symbols: list[str], day: date) -> dict[str, dict]:
    """Today's open / high / low per symbol from the stored minute bars."""
    lo = int(datetime.combine(day, SESSION_OPEN, IST).timestamp())
    hi = int(datetime.combine(day, SESSION_CLOSE, IST).timestamp())
    out = {}
    try:
        with sqlite3.connect(f"file:{database_path}?mode=ro", uri=True, timeout=30) as db:
            for symbol in symbols:
                row = db.execute("""SELECT MIN(timestamp), MAX(high), MIN(low) FROM historical_candles
                                    WHERE symbol=? AND timeframe_seconds=60 AND timestamp>=? AND timestamp<?""",
                                 (symbol, lo, hi)).fetchone()
                if row and row[0]:
                    first = db.execute("""SELECT open FROM historical_candles WHERE symbol=? AND timeframe_seconds=60
                                          AND timestamp=?""", (symbol, row[0])).fetchone()
                    out[symbol] = {"open": first[0] if first else None, "high": row[1], "low": row[2]}
    except sqlite3.Error:
        pass
    return out


def agent_signals(database_path: str, day: str) -> dict:
    """Latest index-scorecard decisions and the option agent's top picks (today's, else the latest run)."""
    out = {"scorecard": [], "agent_picks": {"CE": [], "PE": []}, "scorecard_day": None, "picks_day": None}
    try:
        with sqlite3.connect(f"file:{database_path}?mode=ro", uri=True, timeout=30) as db:
            run = db.execute("SELECT MAX(run_id) FROM agent_index_trades").fetchone()
            if run and run[0] is not None:
                for underlying, d, side, conviction, reason, drivers in db.execute(
                        "SELECT underlying, day, side, conviction, reason, drivers FROM agent_index_trades WHERE run_id=?", (run[0],)):
                    out["scorecard_day"] = d
                    out["scorecard"].append({"index": underlying, "call": {1: "CE", -1: "PE"}.get(side, "no trade"),
                                             "net": conviction, "why": reason or drivers})
            run = db.execute("SELECT MAX(run_id), MAX(day) FROM agent_picks").fetchone()
            if run and run[0] is not None:
                out["picks_day"] = run[1]
                for side, rank, underlying, symbol, score in db.execute(
                        "SELECT side, rank, underlying, symbol, score FROM agent_picks WHERE run_id=? AND rank<=5 ORDER BY side, rank", (run[0],)):
                    out["agent_picks"]["CE" if side == 1 else "PE"].append(underlying or symbol)
    except sqlite3.Error:
        pass
    return out


# ------------------------------------------------------------------------------------------ execution

class AgenticExecutionManager(ExecutionManager):
    """Paper fills from the shared manager; per-position stop/target from the agent, plus hard guards."""

    def __init__(self, *args, lane: "AgenticLane", **kwargs):
        super().__init__(*args, **kwargs)
        self.lane = lane

    async def on_tick(self, symbol: str, price: float, timestamp: datetime | None = None) -> None:
        self.set_quote(symbol, price, timestamp)
        try:
            self._fresh_price(symbol)
        except RuntimeError:
            return
        if self.portfolio.mark(symbol, price):
            self.events.publish("portfolio", self.portfolio_snapshot())
        position = self.portfolio.positions.get(symbol)
        if not position or position.quantity <= 0:
            return
        meta = self.lane.meta.get(symbol, {})
        now = (timestamp or datetime.now(UTC)).astimezone(IST)
        reason = None
        expiry = meta.get("expiry") or self.contract_expiries.get(symbol)
        if expiry and (expiry < now.date().isoformat() or (expiry == now.date().isoformat() and now.time() >= EXPIRY_FLATTEN_AT)):
            reason = "EXPIRY_DAY_FLATTEN"
        elif meta.get("horizon") == "intraday" and now.time() >= INTRADAY_FLATTEN_AT:
            reason = "INTRADAY_FLATTEN"
        elif price <= position.average_price * (1 - HARD_FLOOR):
            reason = f"HARD_FLOOR_{round(HARD_FLOOR * 100)}_PCT"
        elif meta.get("stop_premium") and price <= float(meta["stop_premium"]):
            reason = "AGENT_STOP"
        elif meta.get("target_premium") and price >= float(meta["target_premium"]):
            reason = "AGENT_TARGET"
        if reason:
            self.arm_exit(symbol, reason)
            try:
                await self.submit(symbol, "SELL", position.quantity)
            except (ValueError, RuntimeError):
                return
            self.lane.note_guard_exit(symbol, reason, price)
            return
        review = meta.get("review_price")
        if review and abs(price / float(review) - 1) >= POSITION_TRIGGER:
            self.lane.request_cycle(f"{symbol} moved {((price / float(review)) - 1) * 100:+.0f}% since last review")

    async def on_closed_bar(self, symbol: str, macd: float) -> bool:
        return False

    async def on_signal(self, signal) -> None:
        return None


# ------------------------------------------------------------------------------------------ the lane

class AgenticLane:
    def __init__(self, settings, broker, events, database_path: str, engine=None):
        self.name = LANE_NAME
        self.settings = settings
        self.capital = float(settings.agentic_capital)
        self.database_path = database_path
        self.engine = engine
        Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        self.repository = TradeRepository(database_path)
        self._db = sqlite3.connect(database_path, check_same_thread=False, timeout=30)
        self._db.row_factory = sqlite3.Row
        self._db_lock = Lock()
        with self._db_lock, self._db:
            self._db.executescript(SCHEMA)
            try:
                self._db.execute("ALTER TABLE agentic_positions ADD COLUMN horizon TEXT")
            except sqlite3.OperationalError:
                pass                                          # already there
        self.portfolio = Portfolio(self.capital, lane=self.name)
        self.events = LaneEvents(events, prefix=f"{self.name}_")
        self.broker = OrderOnlyBroker(broker)
        self.execution = AgenticExecutionManager(
            self.execution_settings(settings), self.broker, self.portfolio, self.repository, self.events, lane=self)
        self.meta: dict[str, dict] = {}
        self._contracts: dict[str, dict] = {}
        self.runtime_dir = str(RUNTIME_DIR)
        self.last_test: dict | None = None
        self.client = self._make_client()
        self.paused = False
        self.telegram = None          # agentic_telegram.TelegramBridge, attached by the engine
        self.error: str | None = None
        self.last_cycle: dict | None = None
        self.next_cycle_at: datetime | None = None
        self._triggers: list[str] = []
        self.armed: dict[str, dict] = {}                      # today's conditional entries, id -> spec
        self._levels: dict = {"day": None}
        self._level_fired: set[tuple[str, str]] = set()
        self._operator_notes: list[str] = []
        self._last_trigger_at: datetime | None = None
        self._last_nifty: float | None = None
        self._cycle_lock = asyncio.Lock()
        self._cycles_today: tuple[str, int] = ("", 0)
        self._stats_cache: tuple[str, dict] = ("", {})
        self._atr_cache: tuple[str, dict] = ("", {})
        self._index_lots: dict[str, int] = {}
        self._index_ctx: dict[str, dict] = {}
        self._task: asyncio.Task | None = None
        self._last_equity = 0.0
        self._last_equity_value = float("nan")
        self._last_equity_write = -1e9
        self._restore()

    # ---------------------------------------------------------------- settings / restore

    def execution_settings(self, settings):
        return settings.model_copy(update={
            "initial_capital": self.capital, "target_position_notional": 0.0, "max_target_entry_lots": 1,
            "max_trade_lots": 3 * int(settings.agentic_max_lots_per_order), "max_positions": 0,
            "min_cash_reserve": 0.0, "auto_trade": False, "macd_invalidation_exit": False,
        })

    def apply_settings(self, settings) -> None:
        self.settings = settings
        self.execution.settings = self.execution_settings(settings)
        self.client = self._make_client()

    # ---------------------------------------------------------------- brain connection (the panel)

    def _make_client(self) -> brain.GrokClient:
        """Key, model, endpoint and reasoning effort saved from the panel; settings are the defaults."""
        config = brain.load_config(self.runtime_dir)
        self._config = config
        key, _ = brain.resolve_key(self.runtime_dir)
        base = config.get("api_base") or self.settings.agentic_api_base
        try:
            base = brain.check_base_url(base)
        except ValueError:
            base = brain.DEFAULT_BASE_URL
        return brain.GrokClient(key, config.get("model") or self.settings.agentic_model, base,
                                reasoning_effort=config.get("reasoning_effort") or None)

    @property
    def universe(self) -> str:
        value = getattr(self, "_config", {}).get("universe")
        return value if value in UNIVERSES else DEFAULT_UNIVERSE

    @property
    def cycle_minutes(self) -> int:
        value = getattr(self, "_config", {}).get("cycle_minutes")
        return int(value) if value in CYCLE_CHOICES else int(self.settings.agentic_cycle_minutes)

    def brain_status(self) -> dict:
        _, source = brain.resolve_key(self.runtime_dir)
        return {"configured": self.client.configured, "key_source": source, "key_hint": self.client.key_hint,
                "model": self.client.model, "api_base": self.client.base_url,
                "reasoning_effort": self.client.reasoning_effort or "", "efforts": list(brain.EFFORTS),
                "default_model": self.settings.agentic_model, "last_test": self.last_test,
                "universe": self.universe, "universes": list(UNIVERSES),
                "cycle_minutes": self.cycle_minutes, "cycle_choices": list(CYCLE_CHOICES)}

    def update_brain(self, *, api_key: str | None = None, clear_key: bool = False, model: str | None = None,
                     api_base: str | None = None, reasoning_effort: str | None = None,
                     universe: str | None = None, cycle_minutes: int | None = None) -> dict:
        store = brain.brain_store(self.runtime_dir)
        config = store.load()
        if clear_key:
            config.pop("api_key", None)
        elif api_key is not None and api_key.strip():
            config["api_key"] = api_key.strip()
        if model is not None:
            if model.strip():
                config["model"] = model.strip()
            else:
                config.pop("model", None)
        if api_base is not None:
            if api_base.strip():
                config["api_base"] = brain.check_base_url(api_base)
            else:
                config.pop("api_base", None)
        if reasoning_effort is not None:
            if reasoning_effort in brain.EFFORTS:
                config["reasoning_effort"] = reasoning_effort
            else:
                config.pop("reasoning_effort", None)
        if universe is not None:
            if universe not in UNIVERSES:
                raise ValueError(f"universe must be one of {', '.join(UNIVERSES)}")
            config["universe"] = universe
        if cycle_minutes is not None:
            if cycle_minutes not in CYCLE_CHOICES:
                raise ValueError(f"cycle_minutes must be one of {', '.join(map(str, CYCLE_CHOICES))}")
            config["cycle_minutes"] = cycle_minutes
            if self.next_cycle_at is not None:
                self.next_cycle_at = min(self.next_cycle_at, _now() + timedelta(minutes=cycle_minutes))
        store.save(config)
        self.client = self._make_client()
        self.last_test = None
        return self.brain_status()

    async def test_brain(self) -> dict:
        self.last_test = await self.client.test()
        return self.brain_status()

    def limits(self) -> brain.Limits:
        s = self.settings
        return brain.Limits(capital=self.capital, max_positions=s.agentic_max_positions,
                            max_position_pct=s.agentic_max_position_pct, max_deployed_pct=s.agentic_max_deployed_pct,
                            daily_loss_stop_pct=s.agentic_daily_loss_stop_pct, max_lots_per_order=s.agentic_max_lots_per_order)

    def _restore(self) -> None:
        rows = sorted(self.repository.rows("trades", 1_000_000), key=lambda row: row["timestamp"])
        for row in rows:
            try:
                self.portfolio.apply_trade(Trade(
                    order_id=row["order_id"], symbol=row["symbol"], side=row["side"], quantity=int(row["quantity"]),
                    price=float(row["price"]), lots=int(row.get("lots", 1)), lot_size=int(row.get("lot_size", 1)),
                    fees=float(row.get("fees", 0.0)), timestamp=datetime.fromisoformat(row["timestamp"]), trade_id=row["trade_id"]))
            except (KeyError, ValueError, TypeError):
                continue
        with self._db_lock:
            stored = self._db.execute("SELECT * FROM agentic_positions").fetchall()
        held = {s for s, p in self.portfolio.positions.items() if p.quantity > 0}
        for row in stored:
            if row["symbol"] in held:
                self.meta[row["symbol"]] = dict(row)
        self.execution.set_contract_expiries({s: m.get("expiry") for s, m in self.meta.items() if m.get("expiry")})
        with self._db_lock:
            for row in self._db.execute("SELECT spec FROM agentic_triggers WHERE day = ? AND status = 'armed'",
                                        (_now().date().isoformat(),)):
                spec = json.loads(row[0])
                self.armed[spec["id"]] = spec
        excursions = self.repository.load_position_excursions()
        for position in self.portfolio.positions.values():
            row = excursions.get(position.position_id)
            if row:
                position.max_return_pct = max(position.max_return_pct, float(row["max_return_pct"]))
                position.min_return_pct = min(position.min_return_pct, float(row["min_return_pct"]))

    # ---------------------------------------------------------------- engine wiring (smart-lane surface)

    def set_tradable_contracts(self, lot_sizes: dict[str, int]) -> None:
        held = {s: p.lot_size for s, p in self.portfolio.positions.items() if p.quantity > 0 and p.lot_size > 1}
        self._base_lots = dict(lot_sizes)
        self.execution.set_tradable_contracts({**held, **lot_sizes, **self._index_lots})

    def set_contract_context(self, contracts: dict[str, dict]) -> None:
        held = {s: row for s, row in self._contracts.items() if s in self.portfolio.positions}
        self._contracts = {**held, **contracts, **self._index_ctx}
        self.execution.set_contract_expiries({s: row.get("expiry") for s, row in self._contracts.items()})

    def set_index_universe(self, contracts: dict[str, dict]) -> None:
        """Every selected index contract -- ATM, ITM, OTM, this and the next expiry -- is tradable here.

        The engine trades only the ATM legs elsewhere and keeps the rest on the feed for analysis; this lane
        may roll to the next expiry or choose a strike, so it takes them all (paper fills, live ticks)."""
        self._index_ctx = {s: row for s, row in contracts.items() if int(row.get("lot_size") or 0) > 0}
        self._index_lots = {s: int(row["lot_size"]) for s, row in self._index_ctx.items()}
        self.set_tradable_contracts(getattr(self, "_base_lots", {}))
        self._contracts.update(self._index_ctx)
        self.execution.set_contract_expiries({s: row.get("expiry") for s, row in self._index_ctx.items()})

    def set_resolvers(self, **_) -> None:
        return None

    def on_macd_signal(self, *_, **__) -> None:
        return None

    def on_blast_candidate(self, *_, **__) -> None:
        return None

    def held_symbols(self) -> list[str]:
        return [s for s, p in self.portfolio.positions.items() if p.quantity > 0]

    def sweep_closed_positions(self) -> bool:
        return self.execution.sweep_closed_positions()

    def flush_journal(self) -> int:
        return 0

    # ---------------------------------------------------------------- triggers

    def request_cycle(self, why: str, note: str | None = None) -> None:
        if why not in self._triggers:
            self._triggers.append(why)
        if note:
            self._operator_notes.append(note.strip()[:1000])

    def note_operator_close(self, symbol: str) -> None:
        self._log_action(None, "close", symbol, None, self._price(symbol), "filled", "operator closed the position", None, None)
        if self.telegram is not None:
            self.telegram.on_exit(symbol, "OPERATOR_CLOSE", self._price(symbol))
        self._drop_meta(symbol)
        self.request_cycle(f"operator closed {symbol}")

    def note_guard_exit(self, symbol: str, reason: str, price: float) -> None:
        if self.telegram is not None:
            self.telegram.on_exit(symbol, reason, price)
        self._log_action(None, "close", symbol, None, price, "filled", f"guard: {reason}", None, None)
        self._drop_meta(symbol)
        self.request_cycle(f"{symbol} closed by {reason}")

    # ---------------------------------------------------------------- context

    def _tick(self, symbol: str):
        return getattr(self.engine, "latest_ticks", {}).get(symbol) if self.engine is not None else None

    def _price(self, symbol: str) -> float | None:
        price = self.execution.last_prices.get(symbol)
        if price:
            return float(price)
        tick = self._tick(symbol)
        return float(tick.ltp) if tick is not None and tick.ltp else None

    def _stats(self, symbols: list[str]) -> dict[str, dict]:
        day = _now().date().isoformat()
        if self._stats_cache[0] != day or not self._stats_cache[1]:
            self._stats_cache = (day, daily_stats(self.settings.research_database_path, symbols))
        return self._stats_cache[1]

    def build_context(self, now: datetime | None = None) -> tuple[str, dict]:
        """(prompt text, validation context). Runs in a worker thread: it reads SQLite."""
        now = (now or _now()).astimezone(IST)
        today = now.date().isoformat()
        stocks = sorted({row["spot_symbol"] for row in self._contracts.values() if str(row.get("spot_symbol", "")).endswith("-EQ")})
        sectors = [s for s in SECTOR_NAMES if self._tick(s) is not None]
        stats = self._stats([NIFTY, *INDEX_SPOTS.values(), *sectors, *stocks])
        ohl = intraday_ohl(self.settings.research_database_path, list(INDEX_SPOTS.values()), now.date())
        features: dict[str, dict] = {}
        try:
            with sqlite3.connect(f"file:{self.settings.research_database_path}?mode=ro", uri=True, timeout=30) as fdb:
                def safe(fn, *args):
                    try:
                        return fn(*args)
                    except sqlite3.Error:      # a missing table (no chain capture for this index) costs one field
                        return None

                if self._atr_cache[0] != today:
                    self._atr_cache = (today, {spot: safe(feat.daily_atr, fdb, spot, now.date()) for spot in INDEX_SPOTS.values()})
                for root, spot in INDEX_SPOTS.items():
                    features[root] = {"session": safe(feat.session_shape, fdb, root, spot, now, self._price(spot)),
                                      "positioning": safe(feat.positioning, fdb, root, now)}
        except sqlite3.Error as exc:
            features = {"_error": {"session": {"error": str(exc)[:120]}}}

        def change(symbol):
            tick = self._tick(symbol)
            return _r(getattr(tick, "change_pct", None)) if tick is not None else None

        indices = []
        for root, spot in INDEX_SPOTS.items():
            st, price, day = stats.get(spot, {}), self._price(spot), ohl.get(spot, {})
            rng = None
            if price and day.get("high") and day.get("low") and day["high"] > day["low"]:
                rng = round((price - day["low"]) / (day["high"] - day["low"]) * 100)
            indices.append({"index": root, "ltp": _r(price), "today_pct": change(spot),
                            "from_open_pct": _r((price / day["open"] - 1) * 100) if price and day.get("open") else None,
                            "pos_in_day_range_pct": rng, "ret5d": _r(st.get("ret5")), "ret20d": _r(st.get("ret20")),
                            "vs_ma20_pct": _r((price / st["ma20"] - 1) * 100) if price and st.get("ma20") else None,
                            "vs_ma50_pct": _r((price / st["ma50"] - 1) * 100) if price and st.get("ma50") else None,
                            "realised_vol20_pct": _r(st.get("vol20"), 1),
                            "daily_atr": self._atr_cache[1].get(spot) or None,
                            "session": features.get(root, {}).get("session") or None,
                            **({"options_positioning": features[root]["positioning"]}
                               if features.get(root, {}).get("positioning") else {})})
        vix_symbol = getattr(self.settings, "vix_symbol", "NSE:INDIAVIX-INDEX")
        stock_moves = {s: change(s) for s in stocks}
        moved = [v for v in stock_moves.values() if v is not None]
        breadth = {"stocks": len(moved), "up": sum(v > 0 for v in moved),
                   "median_pct": _r(sorted(moved)[len(moved) // 2]) if moved else None,
                   "above_ma50_pct": _r(100 * sum(1 for s in stocks if stats.get(s, {}).get("ma50") and (self._price(s) or 0) > stats[s]["ma50"])
                                        / max(1, sum(1 for s in stocks if stats.get(s, {}).get("ma50"))), 0)}
        sector_rows = sorted(({"sector": SECTOR_NAMES[s], "today_pct": change(s),
                               "rel20d": _r((stats.get(s, {}).get("ret20") or 0) - (stats.get(NIFTY, {}).get("ret20") or 0))}
                              for s in sectors), key=lambda r: -(r["today_pct"] or -99))
        signals = agent_signals(self.settings.research_database_path, today)

        # candidates: index contracts in full; stocks shortlisted (RS leaders among the volatile, today's movers,
        # the option agent's picks, anything already held)
        held = set(self.held_symbols())
        held_underlyings = {self._contracts.get(s, {}).get("spot_symbol") for s in held}
        rs = sorted((s for s in stocks if "rs60" in stats.get(s, {}) and "vol20" in stats.get(s, {})),
                    key=lambda s: -stats[s]["rs60"])
        vols = sorted(stats[s]["vol20"] for s in rs) if rs else []
        vol_cut = vols[int(len(vols) * 0.6)] if vols else 0
        leaders = [s for s in rs if stats[s]["vol20"] >= vol_cut][:8]
        laggards = [s for s in reversed(rs) if stats[s]["vol20"] >= vol_cut][:4]
        movers = sorted((s for s in stocks if stock_moves.get(s) is not None), key=lambda s: -stock_moves[s])
        picks = {f"NSE:{u}-EQ" for side in signals["agent_picks"].values() for u in side}
        shortlist = [] if self.universe == "index" else list(dict.fromkeys([*held_underlyings - {None}, *leaders, *movers[:4], *movers[-4:], *laggards,
                                        *[s for s in stocks if s in picks]]))[:STOCK_SHORTLIST]
        wanted_spots = set(INDEX_SPOTS.values()) | set(shortlist)
        by_spot: dict[tuple[str, str], list[tuple[str, dict]]] = {}
        for symbol, row in self._contracts.items():
            if row.get("spot_symbol") in wanted_spots and self.execution.lot_sizes.get(symbol, 0) > 0:
                by_spot.setdefault((row["spot_symbol"], row.get("option_type", "")), []).append((symbol, row))
        candidates, cand_rows = {}, []
        for (spot, side), rows in sorted(by_spot.items()):
            spot_price = self._price(spot)
            keep = sorted(rows, key=lambda item: (str(item[1].get("expiry") or ""), float(item[1].get("strike") or 0))) \
                if spot in INDEX_SPOTS.values() else sorted(
                rows, key=lambda item: abs(float(item[1].get("strike") or 0) - (spot_price or 0)))[:1]
            for symbol, row in keep:
                ltp = self._price(symbol)
                if not ltp:
                    continue
                cid = f"C{len(candidates) + 1:02d}"
                lot = int(self.execution.lot_sizes.get(symbol, 0))
                expiry = str(row.get("expiry") or "")[:10]
                dte = market_calendar_sessions(now.date(), expiry)
                tick = self._tick(symbol)
                spread = None
                if tick is not None and getattr(tick, "bid", None) and getattr(tick, "ask", None) and tick.ask >= tick.bid > 0:
                    spread = _r((tick.ask - tick.bid) / ((tick.ask + tick.bid) / 2) * 100)
                st = stats.get(spot, {})
                candidates[cid] = {"symbol": symbol, "ltp": ltp, "lot_size": lot, "expiry": expiry}
                is_index = spot in INDEX_SPOTS.values()
                risk = feat.contract_risk(ltp, spot_price or 0.0, float(row.get("strike") or 0), expiry, side == "CE", lot, now)
                cand_rows.append({"id": cid, "underlying": _underlying_of(spot), "type": side, "strike": row.get("strike"),
                                  "moneyness": str(row.get("moneyness") or "ATM").replace("NEXT-", "next-expiry "),
                                  "expiry": expiry, "sessions_to_expiry": dte, "premium": _r(ltp), "lot": lot,
                                  "lot_value": round(ltp * lot), "spread_pct": spread, **risk,
                                  **({} if is_index else {"spot": _r(spot_price), "spot_today_pct": change(spot),
                                                          "rs60": _r(st.get("rs60"), 1), "vol20": _r(st.get("vol20"), 0)}),
                                  "held": symbol in held})

        # Moneyness from the live index price (the selection's labels are from the morning and go stale on a
        # trending day): the strike nearest spot is ATM per index, expiry and side; later expiries are "next-expiry".
        spot_now = {entry["index"]: entry.get("ltp") for entry in indices}
        groups: dict[tuple, list[dict]] = {}
        for c in cand_rows:
            if c["underlying"] in spot_now and spot_now[c["underlying"]]:
                groups.setdefault((c["underlying"], c["expiry"], c["type"]), []).append(c)
        first_expiry = {}
        for (root, expiry, _side) in groups:
            first_expiry[root] = min(first_expiry.get(root, expiry), expiry)
        for (root, expiry, side), rows in groups.items():
            spot = spot_now[root]
            atm = min(rows, key=lambda c: abs(float(c["strike"] or 0) - spot))
            for c in rows:
                strike = float(c["strike"] or 0)
                near = c is atm and abs(strike / spot - 1) <= 0.005
                label = "ATM" if near else ("ITM" if (strike < spot) == (side == "CE") else "OTM")
                c["moneyness"] = label if expiry == first_expiry[root] else f"next-expiry {label}"
                c["strike_vs_spot_pct"] = _r((strike / spot - 1) * 100)

        # what the option market prices to the nearest expiry, against the index's own daily range and realised vol
        for entry in indices:
            rows = [c for c in cand_rows if c["underlying"] == entry["index"]]
            move = feat.implied_move(rows, entry.get("ltp"))
            if move:
                atr = (entry.get("daily_atr") or {}).get("atr_pct")
                sessions = next((c["sessions_to_expiry"] for c in rows if c["expiry"] == move["expiry"]), None)
                if atr and sessions:
                    move["atr_move_to_expiry_pct"] = _r(atr * max(1, sessions) ** 0.5)
                ivs = [c.get("iv_pct") for c in rows if c["expiry"] == move["expiry"] and c.get("strike") == move["strike"] and c.get("iv_pct")]
                if ivs and entry.get("realised_vol20_pct"):
                    move["atm_iv_pct"] = _r(sum(ivs) / len(ivs), 1)
                    move["iv_to_realised"] = _r(move["atm_iv_pct"] / entry["realised_vol20_pct"])
                entry["implied_move"] = move

        positions, pos_rows = {}, []
        deployed = 0.0
        for symbol, p in self.portfolio.positions.items():
            if p.quantity <= 0:
                continue
            meta = self.meta.get(symbol, {})
            ltp = self._price(symbol) or p.last_price
            value = ltp * p.quantity
            deployed += p.average_price * p.quantity
            ret = (ltp / p.average_price - 1) * 100 if p.average_price else 0.0
            pid = f"P{len(positions) + 1:02d}"
            expiry = meta.get("expiry") or self._contracts.get(symbol, {}).get("expiry")
            positions[pid] = {"symbol": symbol, "lots": p.lots, "lot_size": p.lot_size, "ltp": ltp, "value": value,
                              "return_pct": ret, "expiry": str(expiry or "")[:10], "booked": bool(meta.get("booked"))}
            pos_rows.append({"position_id": pid, "contract": symbol, "lots": p.lots, "avg_premium": _r(p.average_price),
                             "premium_now": _r(ltp), "return_pct": _r(ret, 1), "best_pct": _r(p.max_return_pct, 1),
                             "worst_pct": _r(p.min_return_pct, 1), "pnl_rs": round((ltp - p.average_price) * p.quantity),
                             "opened": str(meta.get("opened_at") or p.opened_at)[:16], "expiry": str(expiry or "")[:10],
                             "stop_premium": meta.get("stop_premium"), "target_premium": meta.get("target_premium"),
                             "booked_partial": bool(meta.get("booked")), "thesis": meta.get("thesis"),
                             "last_comment": meta.get("last_comment")})
        snap = self.portfolio.snapshot()
        day_pnl = self._day_pnl(snap)
        book = {"capital": self.capital, "equity": round(snap["equity"]), "cash": round(self.portfolio.cash),
                "deployed": round(deployed), "day_pnl": round(day_pnl), "open_positions": len(positions)}
        limits = self.limits()
        recent = self.recent_cycles(limit=3)
        prompt = {
            "universe": ("INDEX OPTIONS ONLY: NIFTY, BANKNIFTY, SENSEX, MIDCPNIFTY. Do not propose stock options; "
                         "use breadth and sectors only as context for the index view." if self.universe == "index"
                         else "index and F&O stock options"),
            "cycle": f"you are consulted every {self.cycle_minutes} minutes (sooner only if an open position needs attention); "
                     "plan positions and levels so they can run unattended until the next cycle",
            "time_ist": now.strftime("%Y-%m-%d %H:%M"), "weekday": now.strftime("%A"),
            "market_open": in_session(now),
            "indices": indices,
            "india_vix": {"ltp": _r(self._price(vix_symbol)), "today_pct": change(vix_symbol)},
            "breadth_fno_stocks": breadth,
            "sectors_today": sector_rows,
            "index_scorecard": {"day": signals["scorecard_day"], "rows": signals["scorecard"]},
            **({} if self.universe == "index" else {"option_agent_picks": {"day": signals["picks_day"], **signals["agent_picks"]}}),
            "book": book,
            "limits": {"max_positions": limits.max_positions, "max_position_rs": round(limits.max_position_pct * self.capital),
                       "max_deployed_rs": round(limits.max_deployed_pct * self.capital),
                       "daily_loss_stop_rs": round(limits.daily_loss_stop_pct * self.capital),
                       "max_lots_per_order": limits.max_lots_per_order, "no_new_risk_after": limits.no_new_after},
            "open_positions": pos_rows,
            "armed_triggers": [self._trigger_view(t) for t in self.armed.values()],
            "candidates": cand_rows,
            "your_recent_notes": [{"at": c["at"][11:16], "view": c["market_view"], "note": c["notes"]} for c in recent],
            "your_track_record": feat.track_record(self.repository.closed_positions(lane=self.name, limit=80)),
            "data_quality": self._data_quality(now, stats, features, signals),
        }
        validation = {"candidates": candidates, "positions": positions, "book": book,
                      "clock": now.strftime("%H:%M"), "today": today, "market_open": in_session(now),
                      "triggers": dict(self.armed), "armed_count": len(self.armed),
                      "index_prices": {root: self._price(spot) for root, spot in INDEX_SPOTS.items()}}
        return json.dumps(prompt, separators=(",", ":"), default=str), validation

    def _data_quality(self, now: datetime, stats: dict, features: dict, signals: dict) -> dict:
        """Freshness flags so the model can discount stale inputs."""
        expected = feat.previous_session(now.date()).isoformat()
        as_of = stats.get(NIFTY, {}).get("as_of")
        ages = {}
        for root, spot in INDEX_SPOTS.items():
            tick = self._tick(spot)
            stamp = getattr(tick, "timestamp", None) if tick is not None else None
            age = (now - stamp).total_seconds() if stamp is not None else None
            ages[root] = round(age) if age is not None and age >= 0 else None
        out = {"daily_closes_as_of": as_of, "daily_closes_stale": bool(as_of is None or as_of < expected),
               "index_tick_age_seconds": ages, "scorecard_day": signals.get("scorecard_day"),
               "chain_snapshot_age_min": {root: f["positioning"]["snapshot_age_min"] for root, f in features.items()
                                          if isinstance(f, dict) and f.get("positioning")}}
        if "_error" in features:
            out["feature_error"] = features["_error"]["session"]["error"]
        return out

    def _day_pnl(self, snap: dict) -> float:
        start = self._day_start_equity()
        return snap["equity"] - start if start is not None else 0.0

    def _day_start_equity(self) -> float | None:
        today = _now().date().isoformat()
        rows = self.repository.equity_rows(3000)
        before = [r for r in rows if str(r.get("timestamp") or r.get("at") or "")[:10] < today]
        if before:
            return float(before[-1].get("equity"))
        return self.capital if not rows else float(rows[0].get("equity"))

    # ---------------------------------------------------------------- the cycle

    async def think(self, trigger: str, operator_note: str | None = None, now: datetime | None = None) -> dict:
        async with self._cycle_lock:
            now = (now or _now()).astimezone(IST)
            day = now.date().isoformat()
            count = self._cycles_today[1] + 1 if self._cycles_today[0] == day else 1
            self._cycles_today = (day, count)
            cycle = {"cycle_id": uuid4().hex, "at": now.isoformat(timespec="seconds"), "day": day, "trigger": trigger,
                     "operator_note": operator_note, "error": None}
            prompt, validation = await asyncio.to_thread(self.build_context, now)
            mode = "grok" if self.client.configured else "rules"
            meta = {}
            if mode == "grok":
                user = prompt if not operator_note else (
                    prompt + "\n\nOPERATOR NOTE (from the account owner; weigh it, the limits still apply): " + operator_note)
                try:
                    decision, meta = await self.client.decide(brain.PLAYBOOK, f"Trigger: {trigger}\nState: {user}")
                except brain.BrainError as exc:
                    cycle["error"] = str(exc)
                    mode = "rules"
                    decision = brain.rules_decision(validation, f"model call failed: {str(exc)[:120]}")
            else:
                decision = brain.rules_decision(validation, "no XAI_API_KEY configured")
            checked = brain.validate(decision, validation, self.limits())
            executed = []
            for action in checked.accepted:
                executed.append(await self._execute(cycle["cycle_id"], action, now))
            for symbol in self.held_symbols():
                if symbol in self.meta:
                    self.meta[symbol]["review_price"] = self._price(symbol) or self.meta[symbol].get("review_price")
                    self._save_meta(symbol)
            nifty = self._price(NIFTY)
            if nifty:
                self._last_nifty = nifty
            minutes = decision.get("next_check_minutes") if isinstance(decision.get("next_check_minutes"), int) else None
            minutes = max(self.cycle_minutes, min(self.cycle_minutes * 2, minutes or self.cycle_minutes))
            self.next_cycle_at = now + timedelta(minutes=minutes)
            cycle.update({"mode": mode, "model": meta.get("model") if mode == "grok" else None,
                          "market_view": str(decision.get("market_view") or "")[:2000],
                          "regime": decision.get("regime"), "confidence": brain._num(decision.get("confidence")),
                          "day_bias": decision.get("day_bias"), "blockers": decision.get("blockers") or [],
                          "data_wanted": decision.get("data_wanted"),
                          "notes": str(decision.get("notes_for_next_cycle") or "")[:1500],
                          "decision": decision, "accepted": checked.accepted, "rejected": checked.rejected,
                          "executed": executed, "latency_ms": meta.get("latency_ms"),
                          "prompt_tokens": meta.get("prompt_tokens"), "completion_tokens": meta.get("completion_tokens"),
                          "digest": {"nifty": _r(nifty), "positions": len(self.held_symbols()),
                                     "equity": round(self.portfolio.snapshot()["equity"])}})
            await asyncio.to_thread(self._save_cycle, cycle)
            self.last_cycle = {k: cycle[k] for k in ("cycle_id", "at", "trigger", "mode", "market_view", "regime", "error")}
            self.events.publish("cycle", self.last_cycle)
            if self.telegram is not None:
                snap = self.portfolio.snapshot()
                self.telegram.on_cycle(cycle, {"equity": snap["equity"], "day_pnl": self._day_pnl(snap),
                                               "open_positions": len(self.held_symbols())})
            return cycle

    async def _execute(self, cycle_id: str, action: dict, now: datetime) -> dict:
        kind, symbol = action["action"], action["symbol"]
        reason, note = str(action.get("reason") or "")[:1500], action.get("note")
        result = {"action": kind, "symbol": symbol, "lots": action.get("lots"), "status": "done", "price": None}
        try:
            self._refresh_quote(symbol)
            if kind in ("open", "add"):
                # The model can take a minute to answer: never chase a premium that ran away meanwhile.
                decided, current = brain._num(action.get("ltp")), self._price(symbol)
                if decided and current and current > decided * (1 + MAX_ENTRY_DRIFT):
                    raise ValueError(f"premium moved from {decided:.2f} to {current:.2f} (>{MAX_ENTRY_DRIFT:.0%}) "
                                     "while the model was deciding; entry skipped")
                order = await self.execution.submit(symbol, "BUY", lots=int(action["lots"]))
                result.update(status=order.status.lower(), price=getattr(order, "fill_price", None) or self._price(symbol),
                              order_id=order.order_id)
                meta = self.meta.get(symbol) or {"symbol": symbol, "opened_at": now.isoformat(timespec="seconds"),
                                                  "underlying": _underlying_of(self._contracts.get(symbol, {}).get("spot_symbol", "")),
                                                  "expiry": str(self._contracts.get(symbol, {}).get("expiry") or "")[:10],
                                                  "booked": 0}
                if kind == "open":
                    meta["thesis"] = reason
                    meta["horizon"] = action.get("horizon") or "intraday"
                meta["last_comment"] = reason
                for level in ("stop_premium", "target_premium"):
                    if action.get(level):
                        meta[level] = float(action[level])
                position = self.portfolio.positions.get(symbol)
                meta["position_id"] = position.position_id if position else None
                meta["review_price"] = self._price(symbol)
                self.meta[symbol] = meta
                self._save_meta(symbol)
            elif kind in ("reduce", "close"):
                position = self.portfolio.positions.get(symbol)
                if not position or position.quantity <= 0:
                    raise ValueError("no open position")
                self.execution.arm_exit(symbol, f"AGENT_{kind.upper()}")
                quantity = position.quantity if kind == "close" else int(action["lots"]) * position.lot_size
                order = await self.execution.submit(symbol, "SELL", quantity)
                result.update(status=order.status.lower(), price=getattr(order, "fill_price", None) or self._price(symbol),
                              order_id=order.order_id)
                if kind == "close" or symbol not in self.held_symbols():
                    self._drop_meta(symbol)
                else:
                    self.meta.setdefault(symbol, {"symbol": symbol})
                    self.meta[symbol]["booked"] = 1
                    self.meta[symbol]["last_comment"] = reason
                    self._save_meta(symbol)
            elif kind == "arm":
                spec = self._arm(action, now)
                result.update(status="armed", trigger_id=spec["id"])
                note = f"{spec['id']}: {self._trigger_condition(spec)} until {spec['valid_until']}"
            elif kind == "disarm":
                self._close_trigger(action["trigger_id"], "disarmed", reason, now)
                result.update(status="disarmed")
            else:   # set_levels / hold
                meta = self.meta.setdefault(symbol, {"symbol": symbol})
                for level in ("stop_premium", "target_premium"):
                    if action.get(level) is not None:
                        meta[level] = float(action[level])
                meta["last_comment"] = reason
                self._save_meta(symbol)
        except (ValueError, RuntimeError, PermissionError) as exc:
            result.update(status="failed", error=str(exc)[:200])
        self._log_action(cycle_id, kind, symbol, action.get("lots"), result.get("price"), result["status"], reason, note,
                         result.get("order_id"))
        return result

    def _refresh_quote(self, symbol: str) -> None:
        """Take the engine's latest tick for this contract when it is newer than the book's quote."""
        tick = self._tick(symbol)
        if tick is None or not tick.ltp:
            return
        stamp = self.execution.last_price_at.get(symbol)
        when = getattr(tick, "timestamp", None)
        if stamp is None or (when is not None and when > stamp):
            self.execution.set_quote(symbol, float(tick.ltp), when)

    # ---------------------------------------------------------------- conditional entries

    @staticmethod
    def _trigger_condition(spec: dict) -> str:
        text = f"{spec['trigger_index']} {spec['trigger_op']} {spec['trigger_level']:g}"
        if spec.get("confirm_index"):
            text += f" and {spec['confirm_index']} {spec['confirm_op']} {spec['confirm_level']:g}"
        return text

    def _trigger_view(self, spec: dict) -> dict:
        return {"id": spec["id"], "contract": spec["symbol"], "lots": spec["lots"], "when": self._trigger_condition(spec),
                "valid_until": spec["valid_until"], "stop_premium": spec.get("stop_premium"),
                "target_premium": spec.get("target_premium"), "horizon": spec.get("horizon"), "armed_at": spec["armed_at"],
                "why": spec["reason"][:300]}

    def _arm(self, action: dict, now: datetime) -> dict:
        day = now.date().isoformat()
        with self._db_lock:
            used = self._db.execute("SELECT COUNT(*) FROM agentic_triggers WHERE day = ?", (day,)).fetchone()[0]
        spec = {"id": f"T{used + 1:02d}", "day": day, "symbol": action["symbol"], "lots": int(action["lots"]),
                "stop_premium": action.get("stop_premium"), "target_premium": action.get("target_premium"),
                "horizon": action.get("horizon") or "intraday", "trigger_index": action["trigger_index"],
                "trigger_op": action["trigger_op"], "trigger_level": float(action["trigger_level"]),
                "confirm_index": action.get("confirm_index"), "confirm_op": action.get("confirm_op"),
                "confirm_level": float(action["confirm_level"]) if action.get("confirm_level") is not None else None,
                "valid_until": action["valid_until"], "reason": str(action.get("reason") or ""),
                "armed_at": now.strftime("%H:%M"), "premium_at_arm": action.get("ltp")}
        self.armed[spec["id"]] = spec
        with self._db_lock, self._db:
            self._db.execute("INSERT OR REPLACE INTO agentic_triggers VALUES (?,?,?,?,?,?,?)",
                             (spec["id"], day, now.isoformat(timespec="seconds"), json.dumps(spec), "armed", None, None))
        return spec

    def _close_trigger(self, trigger_id: str, status: str, outcome: str, now: datetime) -> None:
        spec = self.armed.pop(trigger_id, None)
        day = spec["day"] if spec else now.date().isoformat()
        with self._db_lock, self._db:
            self._db.execute("UPDATE agentic_triggers SET status = ?, closed_at = ?, outcome = ? WHERE day = ? AND trigger_id = ?",
                             (status, now.isoformat(timespec="seconds"), outcome[:500], day, trigger_id))

    def _condition_met(self, index: str | None, op: str | None, level: float | None) -> bool:
        price = self._price(INDEX_SPOTS.get(index or "", ""))
        if not price or level is None:
            return False
        return price > level if op == "above" else price < level

    async def check_triggers(self, now: datetime | None = None) -> list[dict]:
        """Fire, expire or keep each armed entry. Runs every few seconds; the model is not consulted to fire."""
        now = (now or _now()).astimezone(IST)
        fired = []
        for spec in list(self.armed.values()):
            if spec["day"] != now.date().isoformat() or now.strftime("%H:%M") > spec["valid_until"]:
                self._close_trigger(spec["id"], "expired", "not triggered before valid_until", now)
                self._log_action(None, "expire", spec["symbol"], spec["lots"], None, "expired",
                                 f"{spec['id']} expired: {self._trigger_condition(spec)} never held", None, None)
                continue
            if not in_session(now) or not self._condition_met(spec["trigger_index"], spec["trigger_op"], spec["trigger_level"]):
                continue
            if spec.get("confirm_index") and not self._condition_met(spec["confirm_index"], spec["confirm_op"], spec["confirm_level"]):
                continue
            fired.append(await self._fire(spec, now))
        return fired

    async def _fire(self, spec: dict, now: datetime) -> dict:
        symbol = spec["symbol"]
        self._refresh_quote(symbol)
        price = self._price(symbol)
        held = self.portfolio.positions.get(symbol)
        positions, deployed = {}, 0.0
        for i, (s, p) in enumerate(((s, p) for s, p in self.portfolio.positions.items() if p.quantity > 0), 1):
            ltp = self._price(s) or p.last_price
            positions[f"P{i:02d}"] = {"symbol": s, "lots": p.lots, "lot_size": p.lot_size, "ltp": ltp, "value": ltp * p.quantity,
                                      "expiry": str(self._contracts.get(s, {}).get("expiry") or "")[:10]}
            deployed += p.average_price * p.quantity
        snap = self.portfolio.snapshot()
        context = {"candidates": {"C": {"symbol": symbol, "ltp": price, "lot_size": self.execution.lot_sizes.get(symbol, 0),
                                        "expiry": str(self._contracts.get(symbol, {}).get("expiry") or "")[:10]}},
                   "positions": positions, "book": {"cash": self.portfolio.cash, "deployed": deployed, "day_pnl": self._day_pnl(snap)},
                   "clock": now.strftime("%H:%M"), "today": now.date().isoformat(), "market_open": in_session(now)}
        pid = next((k for k, v in positions.items() if v["symbol"] == symbol), None)
        action = {"action": "add" if held and held.quantity > 0 else "open", "contract_id": "C", "position_id": pid,
                  "lots": spec["lots"], "stop_premium": spec.get("stop_premium"), "target_premium": spec.get("target_premium"),
                  "horizon": spec.get("horizon"),
                  "reason": f"trigger {spec['id']} fired ({self._trigger_condition(spec)}, armed {spec['armed_at']}): {spec['reason']}"}
        checked = brain.validate({"actions": [action]}, context, self.limits())
        if not checked.accepted:
            why = checked.rejected[0]["rejected"] if checked.rejected else "rejected"
            self._close_trigger(spec["id"], "blocked", why, now)
            self._log_action(None, "trigger", symbol, spec["lots"], price, "blocked", f"{spec['id']} fired but blocked: {why}", None, None)
            self.request_cycle(f"trigger {spec['id']} fired but was blocked: {why}")
            return {"id": spec["id"], "status": "blocked", "why": why}
        accepted = checked.accepted[0]
        accepted.pop("ltp", None)              # the decision price is now: no drift check against the arming price
        result = await self._execute(None, accepted, now)
        self._close_trigger(spec["id"], "fired", f"{result['status']} @ {result.get('price')}", now)
        if self.telegram is not None:
            self.telegram.notify(f"Agentic | trigger {spec['id']} fired: {self._trigger_condition(spec)} -> "
                                 f"{accepted['action'].upper()} {symbol.split(':')[-1]} x{accepted['lots']} @ {result.get('price')} "
                                 f"({result['status']})")
        self.request_cycle(f"trigger {spec['id']} fired: bought {symbol.split(':')[-1]} x{accepted['lots']}")
        return {"id": spec["id"], **result}

    # ---------------------------------------------------------------- intraday exits and level events

    async def flatten_intraday(self, now: datetime | None = None) -> None:
        """Positions opened as intraday are out at 15:15 even if no tick arrives to trigger the guard."""
        now = (now or _now()).astimezone(IST)
        if not in_session(now) or now.time() < INTRADAY_FLATTEN_AT:
            return
        for symbol in list(self.held_symbols()):
            if self.meta.get(symbol, {}).get("horizon") != "intraday":
                continue
            position = self.portfolio.positions[symbol]
            self._refresh_quote(symbol)
            try:
                self.execution.arm_exit(symbol, "INTRADAY_FLATTEN")
                await self.execution.submit(symbol, "SELL", position.quantity)
            except (ValueError, RuntimeError):
                continue
            self.note_guard_exit(symbol, "INTRADAY_FLATTEN", self._price(symbol))

    def _compute_levels(self, now: datetime) -> dict:
        out = {"day": now.date().isoformat(), "first_hour": now.time() >= time(10, 16)}
        try:
            with sqlite3.connect(f"file:{self.settings.research_database_path}?mode=ro", uri=True, timeout=30) as db:
                for root in LEVEL_INDICES:
                    shape = feat.session_shape(db, root, INDEX_SPOTS[root], now, self._price(INDEX_SPOTS[root]))
                    levels = {"prev_high": shape.get("prev_high"), "prev_low": shape.get("prev_low")}
                    if out["first_hour"] and shape.get("first_hour"):
                        levels["first_hour_high"] = shape["first_hour"]["high"]
                        levels["first_hour_low"] = shape["first_hour"]["low"]
                    out[root] = levels
        except sqlite3.Error:
            pass
        return out

    async def refresh_levels(self, now: datetime | None = None) -> None:
        now = (now or _now()).astimezone(IST)
        if not in_session(now):
            return
        stale = self._levels.get("day") != now.date().isoformat() or (
            not self._levels.get("first_hour") and now.time() >= time(10, 16))
        if not stale:
            return
        if self._levels.get("day") != now.date().isoformat():
            self._level_fired = set()
        self._levels = await asyncio.to_thread(self._compute_levels, now)
        # a level already broken when it becomes known is not news
        for root in LEVEL_INDICES:
            for key, level in (self._levels.get(root) or {}).items():
                if level and self._broken(root, key, level):
                    self._level_fired.add((root, key))

    def _broken(self, root: str, key: str, level: float) -> bool:
        price = self._price(INDEX_SPOTS[root])
        if not price:
            return False
        return price > level if key.endswith("high") else price < level

    def _level_events(self) -> None:
        for root in LEVEL_INDICES:
            for key, level in (self._levels.get(root) or {}).items():
                if level and (root, key) not in self._level_fired and self._broken(root, key, level):
                    self._level_fired.add((root, key))
                    side = "above" if key.endswith("high") else "below"
                    self.request_cycle(f"{root} broke {side} its {key.replace('_', ' ')} {level:g}")

    # ---------------------------------------------------------------- persistence

    def _save_meta(self, symbol: str) -> None:
        meta = self.meta.get(symbol)
        if not meta:
            return
        with self._db_lock, self._db:
            self._db.execute("""INSERT OR REPLACE INTO agentic_positions (symbol, position_id, opened_at, thesis, stop_premium,
                                target_premium, review_price, booked, underlying, expiry, last_comment, updated_at, horizon)
                                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                             (symbol, meta.get("position_id"), meta.get("opened_at"), meta.get("thesis"), meta.get("stop_premium"),
                              meta.get("target_premium"), meta.get("review_price"), int(meta.get("booked") or 0),
                              meta.get("underlying"), meta.get("expiry"), meta.get("last_comment"), datetime.now(UTC).isoformat(),
                              meta.get("horizon")))

    def _drop_meta(self, symbol: str) -> None:
        self.meta.pop(symbol, None)
        with self._db_lock, self._db:
            self._db.execute("DELETE FROM agentic_positions WHERE symbol = ?", (symbol,))

    def _log_action(self, cycle_id, action, symbol, lots, price, status, reason, note, order_id) -> None:
        with self._db_lock, self._db:
            self._db.execute("INSERT INTO agentic_actions VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                             (uuid4().hex, cycle_id, _now().isoformat(timespec="seconds"), action, symbol, lots,
                              price, status, reason, note, order_id))

    def _save_cycle(self, c: dict) -> None:
        dumps = lambda v: json.dumps(v, default=str)  # noqa: E731
        with self._db_lock, self._db:
            self._db.execute("INSERT OR REPLACE INTO agentic_cycles VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                             (c["cycle_id"], c["at"], c["day"], c["trigger"], c["mode"], c["model"], c["market_view"],
                              c["regime"], c["confidence"], c["notes"], c["operator_note"], dumps(c["decision"]),
                              dumps(c["accepted"]), dumps(c["rejected"]), dumps(c["executed"]), dumps(c["digest"]),
                              c["latency_ms"], c["prompt_tokens"], c["completion_tokens"], c["error"]))

    # ---------------------------------------------------------------- loop

    def can_cycle(self, now: datetime) -> bool:
        if self.paused or not in_session(now) or not (FIRST_CYCLE_AT <= now.time() <= LAST_CYCLE_AT):
            return False
        day = now.date().isoformat()
        return not (self._cycles_today[0] == day and self._cycles_today[1] >= MAX_CYCLES_PER_DAY)

    def _due(self, now: datetime) -> str | None:
        if not self.can_cycle(now):
            return None
        day = now.date().isoformat()
        if self.next_cycle_at is None or self.next_cycle_at.date() != now.date():
            return "session start" if self.last_cycle is None or self.last_cycle["at"][:10] != day else "scheduled"
        why = self.event_reason(now)
        if why:
            return why
        if now >= self.next_cycle_at:
            return "scheduled"
        return None

    def event_reason(self, now: datetime) -> str | None:
        """A level break, a fired trigger, a stop/target, or a big move while holding: worth a cycle now."""
        self._level_events()
        nifty = self._price(NIFTY)
        if self.held_symbols() and nifty and self._last_nifty and abs(nifty / self._last_nifty - 1) >= INDEX_TRIGGER:
            self.request_cycle(f"NIFTY moved {(nifty / self._last_nifty - 1) * 100:+.2f}% since the last cycle")
        if self._triggers and (self._last_trigger_at is None or (now - self._last_trigger_at).total_seconds() >= MIN_TRIGGER_GAP_SECONDS):
            self._last_trigger_at = now
            why = "; ".join(self._triggers)
            self._triggers = []
            return why
        return None

    async def run(self) -> None:
        while True:
            await asyncio.sleep(5.0)
            try:
                now = _now()
                await self.refresh_levels(now)
                await self.check_triggers(now)
                await self.flatten_intraday(now)
                why = self._due(now)
                if why:
                    note = " | ".join(self._operator_notes) or None
                    self._operator_notes = []
                    await self.think(why, note, now)
                loop_now = asyncio.get_running_loop().time()
                if loop_now - self._last_equity >= EQUITY_EVERY_SECONDS:
                    snapshot = self.portfolio.snapshot()
                    if abs(snapshot["equity"] - self._last_equity_value) >= 1.0 or loop_now - self._last_equity_write >= 900:
                        self._last_equity_write = loop_now
                        self._last_equity_value = snapshot["equity"]
                        await asyncio.to_thread(self.repository.save_equity_point, snapshot)
                    self._last_equity = loop_now
                    await asyncio.to_thread(self.repository.save_position_excursions, list(self.portfolio.positions.values()))
                self.error = None
            except Exception as exc:  # noqa: BLE001 -- the lane must never take the feed down
                self.error = f"{type(exc).__name__}: {exc}"[:300]

    def start(self) -> None:
        if self._task is None and self.settings.agentic_enabled:
            self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None

    # ---------------------------------------------------------------- views

    def recent_cycles(self, day: str | None = None, limit: int = 50) -> list[dict]:
        query = "SELECT * FROM agentic_cycles" + (" WHERE day = ?" if day else "") + " ORDER BY at DESC LIMIT ?"
        with self._db_lock:
            rows = self._db.execute(query, (day, limit) if day else (limit,)).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            for key in ("decision", "accepted", "rejected", "executed", "digest"):
                try:
                    item[key] = json.loads(item[key]) if item.get(key) else None
                except ValueError:
                    pass
            out.append(item)
        return out

    def actions(self, limit: int = 300) -> list[dict]:
        with self._db_lock:
            return [dict(r) for r in self._db.execute("SELECT * FROM agentic_actions ORDER BY at DESC LIMIT ?", (limit,))]

    def journal_days(self, limit: int = 30) -> list[str]:
        with self._db_lock:
            return [r[0] for r in self._db.execute("SELECT DISTINCT day FROM agentic_cycles ORDER BY day DESC LIMIT ?", (limit,))]

    def equity_curve(self, limit: int = 3000) -> list[dict]:
        return [{"at": r.get("timestamp") or r.get("at"), "equity": r.get("equity")} for r in self.repository.equity_rows(limit)]

    def snapshot(self) -> dict:
        snap = self.execution.portfolio_snapshot()
        for row in snap["positions"]:
            meta = self.meta.get(row["symbol"], {})
            for key in ("thesis", "last_comment", "stop_premium", "target_premium", "expiry", "booked", "opened_at", "horizon"):
                row[key] = meta.get(key)
        return {"lane": self.name, "capital": self.capital, "portfolio": snap, "health": self.health(),
                "triggers": [self._trigger_view(t) for t in self.armed.values()],
                "closed": self.execution.visible_closed_positions()[-100:]}

    def health(self) -> dict:
        return {"lane": self.name, "enabled": bool(self.settings.agentic_enabled), "paused": self.paused,
                "brain": "grok" if self.client.configured else "rules-only (no API key yet)",
                "model": self.client.model, "cycle_minutes": self.cycle_minutes, "universe": self.universe,
                "next_cycle_at": self.next_cycle_at.isoformat(timespec="seconds") if self.next_cycle_at else None,
                "cycles_today": self._cycles_today[1] if self._cycles_today[0] == _now().date().isoformat() else 0,
                "pending_triggers": list(self._triggers), "last_cycle": self.last_cycle, "error": self.error,
                "open_positions": len(self.held_symbols()),
                "limits": self.limits().__dict__}


def market_calendar_sessions(today: date, expiry: str) -> int | None:
    """Trading sessions from today to the expiry day, inclusive of the expiry, exclusive of today."""
    try:
        end = date.fromisoformat(expiry[:10])
    except ValueError:
        return None
    count, day = 0, today
    while day < end:
        day += timedelta(days=1)
        if market_calendar.is_trading_day(day):
            count += 1
    return count
