"""Grok picks and manages the smart lanes' trades (owner, 18 Nov 2026).

The MACD lane's BUY signals still pass the smart lane's structural checks (held, ATM-only/one per stock, session,
tradable, fresh quote, MACD still above zero five minutes on). What used to be sized and bought at once is queued and
sent to Grok in batches; Grok takes the few it judges the best probable winners and skips the rest (skipping all is
allowed -- the owner's exception to "no score cut-offs" for these two books). Each take is a fixed ticket: Rs 1 L in
the Rs 50 L book, a third of equity in the Rs 1 L index-only book. Grok also manages exits: it sets a stop and a
target premium at entry and reviews open positions every 30 minutes (hold / close / halve / move the levels). The code
enforces those levels on ticks, a safety stop, and the expiry-day and stock-series exits; it never adds to a position.

Every call -- prompt, reply, latency, tokens and xAI's own cost -- is kept in smart_grok_calls in the lane's database.
A failed call skips the batch (GROK_UNAVAILABLE): no Grok, no entry. Paper only. Not proven.
"""
from __future__ import annotations

import asyncio
import json
import math
from datetime import UTC, datetime, time
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

from . import agentic_brain as brain
from .universe import INDEX_SPOTS

IST = ZoneInfo("Asia/Kolkata")
GROK_SOURCE = "grok"
GROK_SKIP = "GROK_SKIP"
GROK_UNAVAILABLE = "GROK_UNAVAILABLE"
GROK_PRICE_MOVED = "GROK_PRICE_MOVED"
GROK_STALE = "GROK_STALE"
EXIT_REVIEW_FROM = time(9, 46)        # 30-minute bars close at :15/:45 -- a minute later, every 30 minutes
EXIT_REVIEW_UNTIL = time(15, 20)
QUEUE_MAX_AGE_S = 900.0               # a queued signal Grok has not seen within 15 minutes is history
CALLS_SCHEMA = """
CREATE TABLE IF NOT EXISTS smart_grok_calls (
  id TEXT PRIMARY KEY, at TEXT NOT NULL, day TEXT NOT NULL, kind TEXT NOT NULL, items INTEGER, taken INTEGER,
  latency_ms INTEGER, model TEXT, prompt_tokens INTEGER, completion_tokens INTEGER, reasoning_tokens INTEGER,
  cost_ticks INTEGER, error TEXT, request TEXT, response TEXT);
CREATE INDEX IF NOT EXISTS smart_grok_calls_day ON smart_grok_calls(day);
CREATE TABLE IF NOT EXISTS smart_grok_levels (
  position_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, stop REAL, target REAL, conviction TEXT, reason TEXT,
  updated_at TEXT NOT NULL);
"""

_NUM = {"type": ["number", "null"]}
ENTRY_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["market_read", "picks"],
    "properties": {
        "market_read": {"type": "string"},
        "picks": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["id", "action", "conviction", "reason", "stop_premium", "target_premium"],
            "properties": {
                "id": {"type": "string"}, "action": {"type": "string", "enum": ["take", "skip"]},
                "conviction": {"type": "string", "enum": ["high", "medium", "low"]}, "reason": {"type": "string"},
                "stop_premium": _NUM, "target_premium": _NUM}}}}}
EXIT_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["market_read", "positions"],
    "properties": {
        "market_read": {"type": "string"},
        "positions": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["symbol", "action", "reason", "stop_premium", "target_premium"],
            "properties": {
                "symbol": {"type": "string"}, "action": {"type": "string", "enum": ["hold", "close", "reduce_half"]},
                "reason": {"type": "string"}, "stop_premium": _NUM, "target_premium": _NUM}}}}}

HOUSE_NOTES = """House notes from this app's own paper history (short samples -- not proven):
- MACD-lane index option signals paid best on the call side (index CE profit factor ~4.9, index PE ~1.9);
  stock PE signals were weak; stock CE was middling.
- Payoffs are fat-tailed: a few large winners carry the book. Judge by side selection and payoff asymmetry
  (how far it can run vs how far it can fall), not by how often it wins. Tight stops lost money in past studies.
- A cross that fires late in a move already stretched far from the day's open/VWAP, or against the index's
  direction, tends to fade. Signals in the same direction as the broad market's breadth did better.
- Signal-time features: u_* are the underlying's returns signed in the option's favour (positive = moving the
  option's way); o_* are the option premium's own; crowd_* count how many option contracts crossed on the same /
  other side in this bar; dip_* describe the MACD dip before the cross; research_rank is an old model's 0-1 rank
  (weak separation, context only); whale = unusually heavy recent volume in that underlying (no direction).
- Near-expiry options decay fast; stock series must be closed by the code five sessions before expiry."""


def entry_system(book: str, ticket: str, hard_pct: float, slots: int) -> str:
    return f"""You choose trades for a PAPER options book on Indian markets (NSE/BSE). Book: {book}.
Every candidate is a long option (buy CE or buy PE only) whose 30-minute premium MACD just crossed above zero and was
still above zero five minutes later. Your job is to pick only the cream: the few candidates most likely to make money,
and skip the rest. Skipping every candidate is fine when none stands out -- there will be more signals.
Each take is a fixed {ticket} in whole lots; the book holds at most {slots} positions and never adds to one. The code
buys at the ask and sells at the bid.
For each take give stop_premium (exit when the premium trades at or below it) and target_premium (exit at or above
it); null leaves that side open. Whatever you set, the code also exits {hard_pct:.0f}% below the entry price, on expiry
day at 15:10 IST, and before a stock series' last five sessions. You review the open positions every 30 minutes and
can close, halve or move the levels then, so do not set a stop just to have one.
{HOUSE_NOTES}
Reply with exactly one pick per candidate id, a reason of at most 25 words each, and a one-line market_read."""


def exit_system(book: str, hard_pct: float) -> str:
    return f"""You manage the open positions of a PAPER options book on Indian markets (NSE/BSE). Book: {book}.
Every position is a long option bought on a 30-minute premium MACD zero cross and chosen by you as a probable winner.
For each position choose hold, close, or reduce_half (sell half the lots; only when lots >= 2), and give the
stop_premium and target_premium to keep (null = no level on that side). A stop at or above the current premium is
ignored -- use close instead. The code always exits {hard_pct:.0f}% below the entry price, on expiry day at 15:10 IST
and before a stock series' last five sessions; the book never adds to a position.
{HOUSE_NOTES}
Reply with exactly one entry per position symbol, a reason of at most 25 words each, and a one-line market_read."""


def lane_values(text: str) -> dict[float, float]:
    """'100000:0.33,5000000:100000' -> {100000.0: 0.33, 5000000.0: 100000.0}; malformed pairs are ignored."""
    out: dict[float, float] = {}
    for pair in str(text or "").split(","):
        capital, _, value = pair.partition(":")
        try:
            if float(value) > 0:
                out[float(capital)] = float(value)
        except ValueError:
            continue
    return out


def is_index_option(symbol: str) -> bool:
    body = symbol.split(":", 1)[-1]
    for root in sorted(INDEX_SPOTS, key=len, reverse=True):
        if body.startswith(root) and body[len(root):len(root) + 2].isdigit():
            return True
    return False


def underlying_root(symbol: str) -> str:
    body = symbol.split(":", 1)[-1]
    for i, ch in enumerate(body):
        if ch.isdigit():
            return body[:i]
    return body


def _r(value, digits=4):
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return round(value, digits) if math.isfinite(value) else None


def _pct(value, digits=2):
    value = _r(value, 8)
    return None if value is None else round(100 * value, digits)


def make_client(settings, runtime_dir: str | None):
    """The agentic panel's key, model and endpoint; the smart lanes' own effort and timeout."""
    config = brain.load_config(runtime_dir)
    key, _ = brain.resolve_key(runtime_dir)
    if not key:
        return None
    try:
        base = brain.check_base_url(config.get("api_base") or settings.agentic_api_base)
    except ValueError:
        base = brain.DEFAULT_BASE_URL
    return brain.GrokClient(key, config.get("model") or settings.agentic_model, base,
                            timeout=float(settings.smart_grok_timeout_seconds),
                            reasoning_effort=settings.smart_grok_effort)


class GrokDesk:
    """One lane's queue of Grok-reviewed entries, its exit reviews, and the call journal."""

    def __init__(self, lane, runtime_dir: str | None = None):
        self.lane = lane
        self.runtime_dir = runtime_dir
        self.client_factory = lambda: make_client(lane.settings, self.runtime_dir)
        self.queue: list[tuple] = []           # (Pending, price when queued, queued at)
        self._entry_task: asyncio.Task | None = None
        self._entry_batch: list[tuple] = []
        self._exit_task: asyncio.Task | None = None
        self._exit_slot: str | None = None
        self.last_entry: dict | None = None
        self.last_exit: dict | None = None
        self.error: str | None = None
        with lane._db_lock, lane._db:
            lane._db.executescript(CALLS_SCHEMA)

    # ---------------------------------------------------------------- settings

    @property
    def capital(self) -> float:
        return self.lane.capital

    @property
    def ticket_value(self) -> float:
        return lane_values(self.lane.settings.smart_grok_tickets_csv).get(self.capital, 100_000.0)

    def ticket(self, equity: float) -> float:
        value = self.ticket_value
        return value * equity if value <= 1 else value

    def ticket_text(self) -> str:
        value = self.ticket_value
        return f"{value * 100:.0f}% of book equity per trade" if value <= 1 else f"Rs {value:,.0f} ticket per trade"

    @property
    def slots(self) -> int:
        return int(lane_values(self.lane.settings.smart_grok_slots_csv).get(self.capital, 0)) or 50

    @property
    def index_only(self) -> bool:
        names = {n.strip() for n in str(self.lane.settings.smart_grok_index_only_csv).split(",") if n.strip()}
        return self.lane.name in names

    @property
    def hard_pct(self) -> float:
        return float(self.lane.settings.smart_grok_hard_stop_pct)

    def book_label(self) -> str:
        # No fixed list: every index the MACD lane signals (BANKEX and NIFTYNXT50 too) is in scope. Naming five
        # made Grok skip a BANKEX signal as "not allowed" in the 18 Nov readiness replay.
        scope = "index options only; every candidate shown is an index option" if self.index_only \
            else "index and F&O stock options"
        return f"{self.lane.name} (Rs {self.capital:,.0f} paper capital; {scope})"

    # ------------------------------------------------------------------- loop

    def enqueue(self, item, price: float, now: datetime) -> None:
        item.detail["grok_queued_price"] = price
        item.detail["grok_queued_at"] = now.isoformat()
        self.queue.append((item, price, now))

    async def tick(self, now: datetime) -> None:
        """Collect finished calls, then start the next entry batch or the half-hourly exit review."""
        if self._entry_task is not None and self._entry_task.done():
            task, batch, self._entry_task, self._entry_batch = self._entry_task, self._entry_batch, None, []
            await self._apply_entries(task, batch, datetime.now(UTC) if now is None else now)
        if self._exit_task is not None and self._exit_task.done():
            task, self._exit_task = self._exit_task, None
            await self._apply_exits(task, now)
        settings = self.lane.settings
        fresh = []
        for row in self.queue:
            item, _price, queued = row
            if (now - queued).total_seconds() > QUEUE_MAX_AGE_S:
                self.lane._record(item, GROK_STALE, now, detail={"reason": "queued longer than 15 minutes"})
            else:
                fresh.append(row)
        self.queue = fresh
        if self._entry_task is None and self.queue:
            oldest = min(q for _, _, q in self.queue)
            if ((now - oldest).total_seconds() >= settings.smart_grok_batch_seconds
                    or len(self.queue) >= settings.smart_grok_max_batch):
                batch, self.queue = self.queue[:settings.smart_grok_max_batch], self.queue[settings.smart_grok_max_batch:]
                self._entry_batch = batch
                self._entry_task = asyncio.create_task(self._entry_call(batch, now))
        if self._exit_task is None:
            slot = self.exit_slot(now)
            if slot and slot != self._exit_slot:
                self._exit_slot = slot
                held = self.grok_positions()
                if held:
                    self._exit_task = asyncio.create_task(self._exit_call(held, now))

    @staticmethod
    def exit_slot(now: datetime) -> str | None:
        local = now.astimezone(IST)
        if local.weekday() >= 5 or not (EXIT_REVIEW_FROM <= local.time() <= EXIT_REVIEW_UNTIL):
            return None
        minutes = (local.hour * 60 + local.minute) - (9 * 60 + 16)
        return f"{local.date().isoformat()}#{minutes // 30}"

    def grok_positions(self) -> list[str]:
        lane = self.lane
        return [s for s in lane.held_symbols() if (lane.meta.get(s) or {}).get("source") == GROK_SOURCE]

    # ---------------------------------------------------------------- prompts

    def frame(self, now: datetime) -> dict:
        lane = self.lane
        try:
            market = lane._market_frame(now) or {}
        except Exception as exc:  # noqa: BLE001 -- context only
            market = {"error": type(exc).__name__}
        snap = lane.portfolio.snapshot()
        positions = []
        for symbol in lane.held_symbols():
            p = lane.portfolio.positions[symbol]
            positions.append({"symbol": symbol, "lots": p.lots, "avg": _r(p.average_price, 2),
                              "ltp": _r(p.last_price, 2),
                              "return_pct": _pct(p.last_price / p.average_price - 1) if p.average_price else None})
        return {"time_ist": now.astimezone(IST).strftime("%Y-%m-%d %H:%M"), "market": market,
                "book": {"cash": round(snap["cash"]), "equity": round(snap["equity"]),
                         "realized_today": None, "open": positions, "slots": self.slots},
                "track_record": self.track_record()}

    def track_record(self) -> dict:
        """This book's own Grok-chosen closed trades, by index/stock and side."""
        out: dict[str, dict] = {}
        try:
            trades = [t for t in self.lane.trades(5000) if t.get("closed") and t.get("source") == GROK_SOURCE]
        except Exception:  # noqa: BLE001
            return out
        for t in trades:
            key = ("index" if is_index_option(t["symbol"]) else "stock") + "_" + t["symbol"][-2:]
            row = out.setdefault(key, {"trades": 0, "wins": 0, "net": 0.0})
            row["trades"] += 1
            row["wins"] += 1 if t["pnl"] > 0 else 0
            row["net"] += t["pnl"]
        for row in out.values():
            row["net"] = round(row["net"])
        return out

    def candidate(self, cid: str, item, price: float, now: datetime) -> dict:
        lane = self.lane
        symbol = item.symbol
        f = item.features or {}
        contract = lane._contracts.get(symbol) or {}
        lot = lane.execution.lot_sizes.get(symbol) or 0
        allocation = item.detail.get("allocation") or {}
        whale = item.detail.get("whale") or {}
        return {
            "id": cid, "symbol": symbol, "underlying": underlying_root(symbol), "index": is_index_option(symbol),
            "side": item.option_type, "strike": contract.get("strike") or f.get("strike"),
            "expiry": contract.get("expiry") or f.get("expiry"), "dte": _r(f.get("dte"), 1),
            "premium": _r(price, 2), "signal_premium": _r(item.signal.price, 2),
            "signal_age_min": round((now - item.created).total_seconds() / 60, 1),
            "lot_size": lot, "one_lot_rs": round(price * lot) if lot else None,
            "moneyness_pct": _pct(f.get("moneyness")), "premium_pct_spot": _pct(f.get("premium_pct_spot"), 3),
            "macd_pct": _pct(f.get("macd_preview_pct"), 3), "macd_slope_pct": _pct(f.get("macd_slope_pct"), 3),
            "dip_bars": f.get("dip_bars"), "dip_depth_pct": _pct(f.get("dip_depth_pct")),
            "o_ret_open_pct": _pct(f.get("o_ret_open")), "o_ret_prev_pct": _pct(f.get("o_ret_prev")),
            "o_range_pos": _r(f.get("o_range_pos"), 2), "o_vol_ratio": _r(f.get("o_vol_ratio"), 2),
            "u_ret_open_pct": _pct(f.get("u_ret_open")), "u_ret_prev_pct": _pct(f.get("u_ret_prev")),
            "u_vwap_pct": _pct(f.get("u_vwap")), "nifty_ret_prev_pct": _pct(f.get("nifty_ret_prev")),
            "crowd_same_side": f.get("crowd_same_side"), "crowd_other_side": f.get("crowd_other_side"),
            "minutes_since_open": _r(f.get("minutes_since_open"), 0),
            "research_rank": _r((allocation.get("components") or {}).get("research_rank"), 2),
            "whale_multiple": _r(whale.get("multiple"), 1) if whale else None,
        }

    def entry_prompt(self, batch: list[tuple], now: datetime) -> tuple[str, dict]:
        ids = {}
        rows = []
        for i, (item, price, _q) in enumerate(batch, 1):
            cid = f"c{i}"
            ids[cid] = item
            rows.append(self.candidate(cid, item, price, now))
        payload = {**self.frame(now), "ticket": self.ticket_text(), "candidates": rows}
        return json.dumps(payload, separators=(",", ":"), default=str), ids

    def position_row(self, symbol: str, now: datetime) -> dict:
        lane = self.lane
        p = lane.portfolio.positions[symbol]
        meta = lane.meta.get(symbol) or {}
        levels = lane.grok_levels.get(symbol) or {}
        try:
            live = (lane._entry_context(SimpleNamespace(symbol=symbol), now) or {}).get("live") or {}
        except Exception:  # noqa: BLE001
            live = {}
        try:
            opened = datetime.fromisoformat(str(meta.get("opened_at") or p.opened_at))
            held_min = round((now - opened).total_seconds() / 60)
        except (TypeError, ValueError):
            held_min = None
        return {"symbol": symbol, "underlying": underlying_root(symbol), "side": symbol[-2:],
                "expiry": meta.get("expiry"), "lots": p.lots, "quantity": p.quantity,
                "entry": _r(p.average_price, 2), "ltp": _r(p.last_price, 2),
                "return_pct": _pct(p.last_price / p.average_price - 1) if p.average_price else None,
                "best_pct": _r(p.max_return_pct, 2), "worst_pct": _r(p.min_return_pct, 2),
                "held_minutes": held_min, "stop": levels.get("stop"), "target": levels.get("target"),
                "entry_reason": levels.get("reason"), "macd_pct_now": _pct(live.get("macd_pct"), 3),
                "u_ret_open_pct": _pct(live.get("u_ret_open")), "u_vwap_pct": _pct(live.get("u_vwap")),
                "safety_stop": _r(p.average_price * (1 - self.hard_pct), 2)}

    # ------------------------------------------------------------------ calls

    async def _call(self, kind: str, system: str, user: str, schema: dict, items: int) -> tuple[dict | None, dict]:
        now = datetime.now(UTC)
        meta: dict = {}
        decision = None
        error = None
        client = self.client_factory()
        if client is None:
            error = "no xAI key saved (agentic lane panel)"
        else:
            try:
                decision, meta = await client.decide(system, user, max_tokens=self.lane.settings.smart_grok_max_tokens,
                                                     schema=schema, schema_name=f"smart_{kind}")
            except brain.BrainError as exc:
                error = str(exc)[:300]
            except Exception as exc:  # noqa: BLE001
                error = f"{type(exc).__name__}: {exc}"[:300]
        meta = {**meta, "error": error}
        self._journal(kind, now, items, meta, user, decision)
        if error:
            self.error = f"{kind}: {error}"
        return decision, meta

    def _journal(self, kind: str, now: datetime, items: int, meta: dict, request: str, decision: dict | None) -> None:
        lane = self.lane
        taken = None
        if decision and kind == "entry":
            taken = sum(1 for p in decision.get("picks") or [] if p.get("action") == "take")
        row = (uuid4().hex, now.isoformat(), now.astimezone(IST).date().isoformat(), kind, items, taken,
               meta.get("latency_ms"), meta.get("model"), meta.get("prompt_tokens"), meta.get("completion_tokens"),
               meta.get("reasoning_tokens"), meta.get("cost_ticks"), meta.get("error"), request,
               json.dumps(decision, default=str) if decision is not None else None)
        with lane._db_lock, lane._db:
            lane._db.execute("INSERT INTO smart_grok_calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row)

    async def _entry_call(self, batch: list[tuple], now: datetime):
        user, ids = self.entry_prompt(batch, now)
        system = entry_system(self.book_label(), self.ticket_text(), self.hard_pct * 100, self.slots)
        decision, meta = await self._call("entry", system, user, ENTRY_SCHEMA, len(batch))
        return decision, meta, ids

    async def _exit_call(self, symbols: list[str], now: datetime):
        rows = [self.position_row(s, now) for s in symbols if s in self.lane.portfolio.positions]
        payload = {**self.frame(now), "positions": rows}
        user = json.dumps(payload, separators=(",", ":"), default=str)
        decision, meta = await self._call("exit", exit_system(self.book_label(), self.hard_pct * 100), user,
                                          EXIT_SCHEMA, len(rows))
        return decision, meta

    # ----------------------------------------------------------------- results

    async def _apply_entries(self, task: asyncio.Task, batch: list[tuple], now: datetime) -> None:
        lane = self.lane
        try:
            decision, meta, ids = task.result()
        except Exception as exc:  # noqa: BLE001
            decision, meta, ids = None, {"error": f"{type(exc).__name__}: {exc}"[:300]}, {}
        if not decision:
            for item, _p, _q in batch:
                lane._record(item, GROK_UNAVAILABLE, now, detail={"error": meta.get("error")})
            return
        picks = {str(p.get("id")): p for p in decision.get("picks") or [] if isinstance(p, dict)}
        self.last_entry = {"at": now.isoformat(), "market_read": decision.get("market_read"),
                           "candidates": len(batch), "taken": sum(1 for p in picks.values() if p.get("action") == "take"),
                           "latency_ms": meta.get("latency_ms"), "cost_ticks": meta.get("cost_ticks")}
        by_item = {id(item): cid for cid, item in ids.items()}
        takes = []
        for item, price, _q in batch:
            pick = picks.get(by_item.get(id(item), ""))
            grok = {"conviction": (pick or {}).get("conviction"), "reason": (pick or {}).get("reason"),
                    "stop": (pick or {}).get("stop_premium"), "target": (pick or {}).get("target_premium"),
                    "market_read": decision.get("market_read")}
            item.detail["grok"] = grok
            if not pick or pick.get("action") != "take":
                lane._record(item, GROK_SKIP, now, price=price,
                             detail={"reason": "no pick returned" if not pick else pick.get("reason")})
                continue
            takes.append((item, price, pick))
        order = {"high": 0, "medium": 1, "low": 2}
        for item, price, pick in sorted(takes, key=lambda t: order.get(t[2].get("conviction"), 3)):
            try:
                await lane._grok_enter(item, price, pick, now)
            except Exception as exc:  # noqa: BLE001 -- one bad entry must not stop the batch
                lane.error = f"{item.symbol}: {type(exc).__name__}: {exc}"[:300]
                lane._record(item, "ENTRY_REJECTED", now, detail={"error": str(exc)[:200]})

    async def _apply_exits(self, task: asyncio.Task, now: datetime) -> None:
        lane = self.lane
        try:
            decision, meta = task.result()
        except Exception as exc:  # noqa: BLE001
            decision, meta = None, {"error": f"{type(exc).__name__}: {exc}"[:300]}
        if not decision:
            return                                    # levels already set stay in force
        actions = []
        for row in decision.get("positions") or []:
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol") or "")
            position = lane.portfolio.positions.get(symbol)
            if not position or position.quantity <= 0 or (lane.meta.get(symbol) or {}).get("source") != GROK_SOURCE:
                continue
            action = row.get("action")
            note = {"symbol": symbol, "action": action, "reason": row.get("reason")}
            if action == "close":
                lane.execution.arm_exit(symbol, "GROK_EXIT")
                await lane.execution.submit(symbol, "SELL", position.quantity)
            elif action == "reduce_half" and position.lots >= 2:
                lane.execution.arm_exit(symbol, "GROK_REDUCE")
                await lane.execution.submit(symbol, "SELL", lots=position.lots // 2)
            if symbol in lane.portfolio.positions and lane.portfolio.positions[symbol].quantity > 0:
                note.update(lane.set_grok_levels(symbol, row.get("stop_premium"), row.get("target_premium"),
                                                 reason=row.get("reason"), now=now, keep_reason=True))
            actions.append(note)
        self.last_exit = {"at": now.isoformat(), "market_read": decision.get("market_read"), "actions": actions,
                          "latency_ms": meta.get("latency_ms"), "cost_ticks": meta.get("cost_ticks")}

    # ------------------------------------------------------------------- views

    def calls(self, *, day: str | None = None, limit: int = 50) -> list[dict]:
        lane = self.lane
        where, params = ("WHERE day = ?", (day,)) if day else ("", ())
        with lane._db_lock:
            rows = lane._db.execute(f"SELECT * FROM smart_grok_calls {where} ORDER BY at DESC LIMIT ?",
                                    (*params, limit)).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            for key in ("request", "response"):
                try:
                    item[key] = json.loads(item[key]) if item[key] else None
                except ValueError:
                    pass
            out.append(item)
        return out

    def usage_today(self, now: datetime | None = None) -> dict:
        lane = self.lane
        day = (now or datetime.now(UTC)).astimezone(IST).date().isoformat()
        with lane._db_lock:
            row = lane._db.execute(
                """SELECT COUNT(*), SUM(cost_ticks), SUM(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END),
                          AVG(latency_ms), SUM(taken), SUM(CASE WHEN kind='entry' THEN items ELSE 0 END)
                   FROM smart_grok_calls WHERE day = ?""", (day,)).fetchone()
        return {"day": day, "calls": row[0] or 0, "cost_usd": round((row[1] or 0) / brain.TICKS_PER_USD, 3),
                "errors": row[2] or 0, "avg_latency_ms": round(row[3]) if row[3] else None,
                "taken": row[4] or 0, "candidates": row[5] or 0}

    def health(self) -> dict:
        return {"enabled": True, "ticket": self.ticket_text(), "slots": self.slots, "index_only": self.index_only,
                "effort": self.lane.settings.smart_grok_effort, "queued": len(self.queue),
                "entry_in_flight": self._entry_task is not None, "exit_in_flight": self._exit_task is not None,
                "safety_stop_pct": self.hard_pct, "last_entry": self.last_entry, "last_exit": self.last_exit,
                "today": self.usage_today(), "error": self.error,
                "validation": "Grok's picks are not proven; paper only"}
