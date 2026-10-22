"""The agentic lane's brain: a Grok (xAI) model reads the market and the book every cycle and answers with
actions -- open, add, reduce, close, set levels, hold -- each with its reasoning.

Three layers, so a language model never has the last word on risk:
  PLAYBOOK      what the research of Oct 2026 found, written as a trader's rules of thumb (the system prompt)
  GrokClient    one chat-completions call per cycle, JSON-schema output (api.x.ai, OpenAI-compatible)
  validate()    every proposed action is checked against the live candidate list and hard limits in code;
                sizes are trimmed to fit, anything unknown or unsafe is rejected with the reason recorded
When no API key is set or the call fails, rules_decision() manages the open positions on its own (stops,
profit booking, expiry) and opens nothing, and the journal says so.

The API key, model, endpoint and reasoning effort are saved from the Agentic lane's connection panel into
runtime/agentic_brain.json (owner-only file; credentials.json is rewritten whole by the Fyers login and would lose
it); the XAI_API_KEY environment variable is the fallback. The key is never logged or returned by any route (only
its last four characters), and it is only ever sent to an https host on x.ai.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import time as clock
from dataclasses import dataclass, field
from pathlib import Path

import httpx

DEFAULT_BASE_URL = "https://api.x.ai/v1"
ACTIONS = ("open", "add", "reduce", "close", "set_levels", "hold", "arm", "disarm")
REGIMES = ("bullish", "bearish", "neutral", "volatile")
BIASES = ("bullish", "bearish", "neutral")
HORIZONS = ("intraday", "swing")
SETUPS = {
    "ib_breakout": "first-hour range broken with the open type agreeing (intraday plan)",
    "opening_drive": "trade the drive's direction once the open has settled",
    "trend_pullback": "with the 20/50-day trend after a pullback to the 20-day average or prior support",
    "multiday_breakout": "break of the 5-10 session high/low or the weekly value area, held for days",
    "failed_breakout_reversal": "a break of a key level (prior day/week high or low) that fails back inside: trade the other way",
    "gap_follow_through": "a close near the day's extreme held overnight for the gap",
    "mean_reversion": "fade an extended multi-day move (far from the 20-day average) back toward value",
    "scorecard_overnight": "the tested next-day index scorecard call",
    "expiry_positioning": "expiry-week plays around open-interest walls and max pain",
    "other": "anything else (explain it in the reason)",
}
SWING_MIN_SESSIONS_TO_EXPIRY = 3
SWING_MAX_SESSIONS = 5
TRIGGER_OPS = ("above", "below")
INDEX_ROOTS = ("NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY")
BLOCKERS = ("none", "data_missing_or_stale", "no_signal", "price_location", "risk_reward", "time_decay", "trend_conflict",
            "limits", "event_risk")
MAX_ARMED = 4
INTRADAY_FLATTEN = "15:15"

PLAYBOOK = """You are the portfolio manager of a PAPER options book on Indian markets (NSE/BSE index and F&O stock
options). You only BUY options (calls or puts); you never write options. You think like an experienced discretionary
options trader who respects evidence and risk, and who is paid to take good trades, not to watch. Every cycle you
receive the market state, the open book, your armed triggers and a list of tradable contracts with live prices.

How to think each cycle:
1. Separate the multi-week TREND (vs 20/50-day averages) from TODAY'S TRADABLE BIAS (gap, first-hour range, VWAP,
   hourly closes, breadth, the leading index). A down-trending market can have a strong up day; trade the day you
   are in. "day_bias" is today's bias for the next few hours; "regime" is the overall read.
2. Review every open position against its thesis: hold, add, reduce, re-level or close. Do not let a winner turn
   into a loser; never average down a losing option.
3. Decide new risk. If the setup is ready now, OPEN. If it needs a level to break first, ARM a trigger: the code
   watches the index every few seconds between your cycles and fires the entry the moment the condition holds
   (index above/below a level, optionally a second index confirming, valid until a time). Prefer arming a precise
   trigger over saying "I would buy if...". If a trigger you wrote earlier has fired or its level has broken by now,
   act on it or state concretely what changed. Disarm triggers whose idea is no longer valid.
4. Size so that being wrong is survivable; define stop and target premiums for every entry.
5. Being flat is a decision with a cost. When the read is clear and a contract's risk/reward fits, take it with
   defined risk instead of waiting for a perfect entry.

Horizon of every entry -- trade BOTH, the operator wants directional moves that span days, not only scalps:
- "intraday": the code closes it at 15:15 the same day. Use for today's structure (opening drive, first-hour break).
- "swing": a directional position held 1-5 sessions (the code closes it at 15:15 on the 5th session). Use the
  multi-day structure: 20/50-day trend, 10-session high/low, the scorecard, where yesterday closed. Rules: a contract
  with at least 3 sessions to expiry (prefer the next expiry), a stop premium wide enough for overnight noise, and an
  "invalidation_level": the INDEX level that proves the idea wrong (below the index for a call, above for a put) --
  the code exits when the index crosses it, so a swing is judged on the index, not on premium noise. The code books
  half a swing winner at +50% and trails the rest from +40% at 75% of the best premium.

Setups -- every open/arm names one in "setup" so the scoreboard learns which ideas work here:
""" + "\n".join(f"- {k}: {v}" for k, v in SETUPS.items()) + """

LEARNING PHASE: the operator wants varied, liberal trades to learn and firm up setups. Try different setups on both
horizons when the evidence is reasonable, at the fixed lot sizes of the decision matrix; you do not need a perfect
setup, you need a defined idea, a stop and an invalidation. Read setup_scoreboard: lean toward setups that are
working, keep testing the ones with few trades, and drop ones that keep failing.

What the backtests on this market found (Oct 2026 studies, 2019-2026 daily data, one year of real option premiums):
- Next-day index direction is hard to call; the scorecard above is the tested exception. It is an OVERNIGHT filter,
  not a reason to skip a clear intraday trend day.
- Stock calls on 60-day relative-strength leaders were the most consistent STOCK-option edge, mainly when NIFTY is
  above its 50-day average. That 50-day finding is about stock calls; it is not a ban on index calls on an up day.
- Buying stock PUTS lost money under every selection rule tested. Express bearish views through index puts.
- Rotation hooks (RRG) did not predict the next big movers. Volatile names predict the SIZE of a move, not its
  direction; their options already charge for it.
- Exits decide results more than entries: past positions gave back 30-70 points from their peak. Book part of a
  big winner (half at +50% to +80%) and trail the rest; cut a loser at its stop.
- Time decay accelerates in the last two sessions before expiry: prefer the next expiry when fewer than 2 sessions
  remain, never hold into the last 30 minutes of expiry day. Same-day expiry contracts are for intraday only.
- After holidays and big gaps, let the first 15-30 minutes settle before adding risk.

Reading the inputs:
- indices[].session: hourly closes, opening gap, first-hour range (price above/inside/below it), yesterday's
  high/low/close, futures VWAP distance. indices[].daily_atr vs implied_move: options are expensive when the implied
  move (or iv_to_realised) is well above the usual range. options_positioning: put/call OI, biggest OI strikes
  (support/resistance), today's OI change.
- candidates: moneyness, iv_pct, delta, theta_rs_per_lot_day, breakeven, move_needed_pct. Prefer contracts whose
  needed move is realistic against daily_atr and the time left.
- armed_triggers: your pending conditional entries. your_track_record: your recent closed trades.
- data_quality: freshness flags. Missing VIX or a stale option chain lowers confidence; it is rarely a reason by
  itself to skip a trade whose price structure is clear.

Decision matrix (computed by the code each cycle; it GOVERNS size): decision_matrix grades every index x side
(CE / PE) A-D twice: "intraday" (open type from 09:45, first-hour break and width from 10:15, 20-day trend, prior
day's size, option richness) and "swing" (scorecard, 20/50-day trend, 10-session breakout, pullback to the 20-day
average, where yesterday closed, prior day's size, today's timing). Each factor is listed with its points. The
grade of the entry's horizon fixes the most lots it may carry: A 4, B 3, C 2, D 1 (an add counts what is held).
An armed intraday break is graded as if it had broken. Name the grade and the setup in each open/add/arm reason.

Report honestly what is holding you back: "blockers" lists the main reasons you are not taking (more) risk now, by
category; "data_wanted" names the one missing input that would most change this decision, or "none".

Hard limits (enforced in code; orders that break them are trimmed or rejected): maximum open positions, maximum value
per position, maximum total deployed, a daily loss stop that blocks new risk, no new risk after 15:10, at most
""" + str(MAX_ARMED) + """ armed triggers, whole lots only, and only the contract_ids listed in this message.
Also enforced (lessons of 22 Oct 2026): no new position before 09:45 unless grade A (arm triggers instead; they
fill from 09:45); at most one re-entry per index and side per day, and only at grade A. Intraday winners: half is
booked at +30% or when the index reaches half its first-hour range beyond the edge (one lot: all of it), the rest
moves to breakeven, and from +20% the stop trails at 88% of the best premium. Swing winners: see Horizon above. Use
position_id and trigger ids exactly as given.

Action fields: open/add need contract_id (open) or position_id (add), lots, stop_premium, target_premium, horizon,
setup, and for swing an invalidation_level (set_levels may move it).
arm needs contract_id, lots, stop/target, horizon, trigger_index (NIFTY/BANKNIFTY/SENSEX/MIDCPNIFTY), trigger_op
(above/below), trigger_level, valid_until (HH:MM IST) and optionally confirm_index/confirm_op/confirm_level.
disarm needs contract_id = the trigger id (T01...). Unused fields are null.

Answer ONLY with JSON matching the schema. In "reason" give the specific evidence (numbers). Keep market_view to 2-4
sentences, a trader's read."""

_NULLABLE_STR = {"type": ["string", "null"]}
_NULLABLE_NUM = {"type": ["number", "null"]}
DECISION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["market_view", "regime", "day_bias", "confidence", "actions", "blockers", "data_wanted",
                 "next_check_minutes", "notes_for_next_cycle"],
    "properties": {
        "market_view": {"type": "string"},
        "regime": {"type": "string", "enum": list(REGIMES)},
        "day_bias": {"type": "string", "enum": list(BIASES)},
        "confidence": {"type": "number"},
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["action", "contract_id", "position_id", "lots", "stop_premium", "target_premium", "horizon",
                             "setup", "invalidation_level",
                             "trigger_index", "trigger_op", "trigger_level", "confirm_index", "confirm_op", "confirm_level",
                             "valid_until", "reason"],
                "properties": {
                    "action": {"type": "string", "enum": list(ACTIONS)},
                    "contract_id": _NULLABLE_STR, "position_id": _NULLABLE_STR,
                    "lots": {"type": ["integer", "null"]},
                    "stop_premium": _NULLABLE_NUM, "target_premium": _NULLABLE_NUM,
                    "horizon": _NULLABLE_STR,
                    "setup": {"type": ["string", "null"], "enum": [*SETUPS, None]},
                    "invalidation_level": _NULLABLE_NUM,
                    "trigger_index": _NULLABLE_STR, "trigger_op": _NULLABLE_STR, "trigger_level": _NULLABLE_NUM,
                    "confirm_index": _NULLABLE_STR, "confirm_op": _NULLABLE_STR, "confirm_level": _NULLABLE_NUM,
                    "valid_until": _NULLABLE_STR,
                    "reason": {"type": "string"},
                },
            },
        },
        "blockers": {
            "type": "array",
            "items": {"type": "object", "additionalProperties": False, "required": ["category", "detail"],
                      "properties": {"category": {"type": "string", "enum": list(BLOCKERS)}, "detail": {"type": "string"}}},
        },
        "data_wanted": {"type": "string"},
        "next_check_minutes": {"type": "integer"},
        "notes_for_next_cycle": {"type": "string"},
    },
}


BRAIN_FILE = "agentic_brain.json"          # beside credentials.json; that file is rewritten whole by the Fyers login
EFFORTS = ("low", "medium", "high", "xhigh")
ALLOWED_HOST_SUFFIX = "x.ai"                # the key is only ever sent to xAI


def brain_store(runtime_dir: str):
    from .settings_store import RuntimeSettingsStore      # 0600, atomic replace
    return RuntimeSettingsStore(str(Path(runtime_dir) / BRAIN_FILE))


def load_config(runtime_dir: str | None) -> dict:
    return brain_store(runtime_dir).load() if runtime_dir else {}


def resolve_key(runtime_dir: str | None) -> tuple[str, str]:
    """(key, source): the key saved from the panel wins, then the XAI_API_KEY environment variable."""
    key = str(load_config(runtime_dir).get("api_key") or "").strip()
    if key:
        return key, "panel"
    key = os.environ.get("XAI_API_KEY", "").strip()
    return (key, "environment") if key else ("", "none")


def api_key(runtime_dir: str | None = None) -> str:
    return resolve_key(runtime_dir)[0]


def check_base_url(url: str) -> str:
    """Only https endpoints on xAI's own domain may receive the key."""
    from urllib.parse import urlparse
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (host == ALLOWED_HOST_SUFFIX or host.endswith("." + ALLOWED_HOST_SUFFIX)):
        raise ValueError("the API base must be an https URL on x.ai (for example https://api.x.ai/v1)")
    return url.strip().rstrip("/")


RETRY_PAUSE_S = 5.0


def _error_text(response: httpx.Response) -> str:
    """A short, readable reason: the API's JSON error message, or the page title of a gateway error page."""
    text = response.text or ""
    try:
        err = response.json().get("error")
        message = err.get("message") if isinstance(err, dict) else err
        if message:
            return str(message)[:200]
    except (ValueError, AttributeError):
        pass
    import re
    title = re.search(r"<title>(.*?)</title>", text, re.I | re.S)
    if title:
        return re.sub(r"\s+", " ", title.group(1)).strip()[:200]
    return re.sub(r"\s+", " ", text).strip()[:200] or "(empty body)"


class BrainError(RuntimeError):
    pass


class GrokClient:
    """One structured chat-completions call per cycle. Async, no retries beyond one schema fallback."""

    def __init__(self, key: str, model: str, base_url: str = DEFAULT_BASE_URL, timeout: float = 240.0,
                 transport: httpx.AsyncBaseTransport | None = None, reasoning_effort: str | None = None):
        self._key = key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._transport = transport
        self.reasoning_effort = reasoning_effort if reasoning_effort in EFFORTS else None
        self.json_schema_supported = True

    @property
    def key_hint(self) -> str | None:
        return f"…{self._key[-4:]}" if len(self._key) >= 8 else None

    async def test(self) -> dict:
        """List the account's models, then make one tiny completion with the configured model."""
        result = {"ok": False, "models": [], "model": self.model, "model_listed": None, "reply": None, "latency_ms": None,
                  "error": None, "at": None}
        from datetime import UTC, datetime
        result["at"] = datetime.now(UTC).isoformat(timespec="seconds")
        if not self._key:
            result["error"] = "no API key saved"
            return result
        try:
            models = await self.models()
            result["models"] = sorted(m for m in models if m)
            result["model_listed"] = self.model in models
        except httpx.HTTPStatusError as exc:
            result["error"] = f"listing models failed: HTTP {exc.response.status_code}: {exc.response.text[:200]}"
            return result
        except httpx.HTTPError as exc:
            result["error"] = f"cannot reach {self.base_url}: {type(exc).__name__}"
            return result
        body = {"model": self.model, "max_completion_tokens": 400,
                "messages": [{"role": "user", "content": "Connection test. Reply with the single word OK."}]}
        if self.reasoning_effort:
            body["reasoning_effort"] = self.reasoning_effort
        started = clock.monotonic()
        try:
            async with self._client() as client:
                response = await client.post(f"{self.base_url}/chat/completions", json=body)
        except httpx.HTTPError as exc:
            result["error"] = f"completion failed: {type(exc).__name__}"
            return result
        result["latency_ms"] = round((clock.monotonic() - started) * 1000)
        if response.status_code != 200:
            result["error"] = f"completion failed: HTTP {response.status_code}: {response.text[:240]}"
            return result
        try:
            result["reply"] = str(response.json()["choices"][0]["message"]["content"])[:80]
        except (KeyError, IndexError, TypeError, ValueError):
            result["reply"] = "(unreadable reply)"
        result["ok"] = True
        return result

    @property
    def configured(self) -> bool:
        return bool(self._key)

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self.timeout, transport=self._transport,
                                 headers={"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"})

    async def models(self) -> list[str]:
        async with self._client() as client:
            response = await client.get(f"{self.base_url}/models")
            response.raise_for_status()
            return [row.get("id") for row in response.json().get("data", []) if row.get("id")]

    async def _post_with_retry(self, client: httpx.AsyncClient, body: dict) -> httpx.Response:
        """One retry after a short pause on a gateway error (5xx) or a dropped connection; never on 4xx."""
        for attempt in (1, 2):
            try:
                response = await client.post(f"{self.base_url}/chat/completions", json=body)
            except (httpx.TransportError,):
                if attempt == 2:
                    raise
            else:
                if response.status_code < 500 or attempt == 2:
                    return response
            await asyncio.sleep(RETRY_PAUSE_S)
        raise AssertionError("unreachable")

    async def decide(self, system: str, user: str, max_tokens: int = 2500) -> tuple[dict, dict]:
        """Returns (decision, meta). Raises BrainError on transport or parse failure."""
        body = {"model": self.model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "max_completion_tokens": max_tokens}
        if self.reasoning_effort:
            body["reasoning_effort"] = self.reasoning_effort
        if self.json_schema_supported:
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": "trading_decision", "strict": True, "schema": DECISION_SCHEMA}}
        else:
            body["response_format"] = {"type": "json_object"}
        started = clock.monotonic()
        try:
            async with self._client() as client:
                response = await self._post_with_retry(client, body)
                if response.status_code == 400 and self.json_schema_supported and "response_format" in response.text:
                    # Older models: fall back to plain JSON mode once and remember it.
                    self.json_schema_supported = False
                    body["response_format"] = {"type": "json_object"}
                    body["messages"][0]["content"] = system + "\n\nJSON schema:\n" + json.dumps(DECISION_SCHEMA)
                    response = await client.post(f"{self.base_url}/chat/completions", json=body)
        except httpx.HTTPError as exc:
            raise BrainError(f"{type(exc).__name__}: {exc}"[:300]) from exc
        latency = round((clock.monotonic() - started) * 1000)
        if response.status_code != 200:
            # The body can echo the request but never the key (it travels only in the header).
            raise BrainError(f"HTTP {response.status_code}: {_error_text(response)}")
        payload = response.json()
        try:
            content = payload["choices"][0]["message"]["content"]
            decision = json.loads(content)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise BrainError(f"unparseable model output: {type(exc).__name__}") from exc
        usage = payload.get("usage") or {}
        return decision, {"latency_ms": latency, "model": payload.get("model", self.model),
                          "prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": usage.get("completion_tokens")}


# ---------------------------------------------------------------------------------------------- validation

@dataclass
class Limits:
    capital: float
    max_positions: int = 10
    max_position_pct: float = 0.15
    max_deployed_pct: float = 0.60
    daily_loss_stop_pct: float = 0.03
    max_lots_per_order: int = 20
    no_new_after: str = "15:10"          # HH:MM IST
    expiry_no_new_after: str = "13:30"   # on a contract's expiry day


@dataclass
class Validated:
    accepted: list[dict] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)


def _num(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def validate(decision: dict, context: dict, limits: Limits) -> Validated:
    """Check each proposed action against the candidates, the book and the hard limits.

    ``context`` carries ``candidates`` {contract_id: {symbol, ltp, lot_size, expiry, ...}}, ``positions``
    {position_id: {symbol, lots, lot_size, ltp, value}}, ``book`` {cash, deployed, day_pnl}, ``clock`` "HH:MM"
    and ``today`` "YYYY-MM-DD".
    """
    out = Validated()
    candidates, positions, book = context["candidates"], context["positions"], context["book"]
    now = context["clock"]
    deployed = float(book.get("deployed", 0.0))
    cash = float(book.get("cash", 0.0))
    open_count = len(positions)
    loss_stop_hit = float(book.get("day_pnl", 0.0)) <= -limits.daily_loss_stop_pct * limits.capital
    seen: set[str] = set()
    actions = decision.get("actions") if isinstance(decision.get("actions"), list) else []

    def reject(action: dict, why: str) -> None:
        out.rejected.append({**action, "rejected": why})

    for raw in actions:
        if not isinstance(raw, dict):
            continue
        action = {k: raw.get(k) for k in ("action", "contract_id", "position_id", "lots", "stop_premium", "target_premium",
                                          "horizon", "setup", "invalidation_level", "trigger_index", "trigger_op",
                                          "trigger_level", "confirm_index", "confirm_op", "confirm_level", "valid_until",
                                          "reason")}
        if action.get("horizon") == "overnight":          # the older name for a multi-day hold
            action["horizon"] = "swing"
        if action.get("setup") not in SETUPS:
            action["setup"] = "other"
        if context.get("matrix") is None:     # a trigger fill: the grade was set by the code when it was armed
            action["grade"] = raw.get("grade")
        if action.get("horizon") not in HORIZONS:
            action["horizon"] = "intraday"
        kind = action["action"]
        if kind not in ACTIONS:
            reject(action, "unknown action")
            continue
        key = f"{kind}:{action.get('contract_id') or action.get('position_id')}"
        if kind == "arm":     # several triggers on one contract at different levels are different orders
            key += f":{action.get('trigger_index')}:{action.get('trigger_op')}:{action.get('trigger_level')}"
        if key in seen:
            reject(action, "duplicate action")
            continue
        seen.add(key)
        if kind == "disarm":
            if str(action.get("contract_id")) in context.get("triggers", {}):
                out.accepted.append({**action, "symbol": context["triggers"][str(action["contract_id"])]["symbol"],
                                     "trigger_id": str(action["contract_id"]), "note": None})
            else:
                reject(action, "no armed trigger with that id")
            continue
        if kind == "arm":
            problem = _check_trigger(action, context, limits)
            if problem:
                reject(action, problem)
                continue
        if kind in ("open", "add", "reduce", "close", "arm") and context.get("market_open") is False:
            reject(action, "market closed")
            continue
        stop, target = _num(action.get("stop_premium")), _num(action.get("target_premium"))
        lots = action.get("lots")
        lots = int(lots) if isinstance(lots, (int, float)) and not isinstance(lots, bool) and lots == lots else None

        if kind in ("open", "add", "arm"):
            if loss_stop_hit:
                reject(action, f"daily loss stop ({limits.daily_loss_stop_pct:.0%} of capital) reached: no new risk today")
                continue
            if now >= limits.no_new_after:
                reject(action, f"no new risk after {limits.no_new_after}")
                continue
            if kind in ("open", "arm"):
                row = candidates.get(str(action.get("contract_id")))
                if not row:
                    reject(action, "contract_id is not in this cycle's candidate list")
                    continue
                if kind == "open" and any(p["symbol"] == row["symbol"] for p in positions.values()):
                    reject(action, "already held: use add with its position_id")
                    continue
                if kind == "open" and open_count >= limits.max_positions:
                    reject(action, f"already at {limits.max_positions} open positions")
                    continue
                held_value = 0.0
            else:
                pos = positions.get(str(action.get("position_id")))
                if not pos:
                    reject(action, "position_id not found")
                    continue
                if pos.get("horizon") in HORIZONS:          # an add belongs to the position's own horizon
                    action["horizon"] = pos["horizon"]
                row = {"symbol": pos["symbol"], "ltp": pos["ltp"], "lot_size": pos["lot_size"], "expiry": pos.get("expiry"),
                       "underlying": pos.get("underlying"), "type": pos.get("type")}
                held_value = float(pos.get("value", 0.0))
            ltp, lot_size = _num(row.get("ltp")), int(row.get("lot_size") or 0)
            if not ltp or ltp <= 0 or lot_size < 1:
                reject(action, "no live price or lot size")
                continue
            if row.get("expiry") == context["today"] and now >= limits.expiry_no_new_after:
                reject(action, "contract expires today: no new risk this late")
                continue
            if kind in ("open", "arm") and action["horizon"] == "swing":
                left = row.get("sessions_to_expiry")
                if left is not None and left < SWING_MIN_SESSIONS_TO_EXPIRY:
                    reject(action, f"a swing trade needs at least {SWING_MIN_SESSIONS_TO_EXPIRY} sessions to expiry "
                                   f"(this has {left}): use the next expiry")
                    continue
            action["invalidation_level"] = _invalidation(action, row, context)
            per_lot = ltp * lot_size
            room = min(limits.max_position_pct * limits.capital - held_value,
                       limits.max_deployed_pct * limits.capital - deployed,
                       cash * 0.98)
            fit = int(room // per_lot) if room > 0 else 0
            wanted = max(1, lots or 1)
            granted = min(wanted, fit, limits.max_lots_per_order)
            if granted < 1:
                reject(action, f"no room: one lot is Rs {per_lot:,.0f}, room Rs {max(room, 0):,.0f}")
                continue
            note = f"trimmed from {wanted} to {granted} lots to fit limits" if granted < wanted else None
            grade = None
            if context.get("matrix") is not None:
                sized = _matrix_size(kind, action, row, context, limits, ltp, stop if stop and stop < ltp else None, lot_size,
                                     pos if kind == "add" else None)
                if isinstance(sized, str):
                    reject(action, sized)
                    continue
                budget_lots, grade, why = sized
                if budget_lots < granted:
                    granted = budget_lots
                    note = f"trimmed from {wanted} to {granted} lots: {why}"
                else:
                    note = f"{note}; {why}" if note else why
            grade = grade or action.get("grade")     # set above only on the matrix-free trigger-fill path
            gate = _entry_gate(kind, row, context, grade, now)
            if gate:
                reject(action, gate)
                continue
            accepted = {**action, "lots": granted, "symbol": row["symbol"], "ltp": ltp, "lot_size": lot_size,
                        "stop_premium": stop if stop and stop < ltp else None,
                        "target_premium": target if target and target > ltp else None, "note": note, "grade": grade}
            out.accepted.append(accepted)
            if kind == "arm":           # reserved when it fires, not now
                continue
            deployed += granted * per_lot
            cash -= granted * per_lot
            if kind == "open":
                open_count += 1
            continue

        pos = positions.get(str(action.get("position_id")))
        if not pos:
            reject(action, "position_id not found")
            continue
        ltp = _num(pos.get("ltp")) or 0.0
        if kind == "reduce":
            held = int(pos["lots"])
            take = max(1, lots or 1)
            if take >= held:
                out.accepted.append({**action, "action": "close", "symbol": pos["symbol"], "lots": held,
                                     "note": f"reduce of {take} lots covers the whole {held}-lot position: closed"})
            else:
                out.accepted.append({**action, "symbol": pos["symbol"], "lots": take, "note": None})
            continue
        if kind == "close":
            out.accepted.append({**action, "symbol": pos["symbol"], "lots": int(pos["lots"]), "note": None})
            continue
        # set_levels / hold: levels only make sense on the right side of the current price
        out.accepted.append({**action, "symbol": pos["symbol"], "lots": None,
                             "stop_premium": stop if stop is not None and (not ltp or stop < ltp) else None,
                             "target_premium": target if target is not None and (not ltp or target > ltp) else None,
                             "note": None})
    return out


def _entry_gate(kind: str, row: dict, context: dict, grade: str | None, now: str) -> str | None:
    """22 Oct 2026 lessons, enforced: no new position before the open has settled unless the setup is grade A,
    and at most one re-entry per index and side per day, only at grade A."""
    if kind == "open" and context.get("settle_until") and now < context["settle_until"] and grade != "A":
        return f"before {context['settle_until']} only a grade A setup may open (this is {grade or 'ungraded'}); arm a trigger instead"
    entries = context.get("entries_today")
    if entries is None or kind not in ("open", "arm"):
        return None
    key = f"{row.get('underlying')}:{row.get('type')}"
    taken = int(entries.get(key, 0))
    if taken >= 2:
        return f"{key.replace(':', ' ')} already entered and re-entered today: no third entry"
    if taken == 1 and grade != "A":
        return f"re-entering {key.replace(':', ' ')} after a closed trade today needs grade A (this is {grade or 'ungraded'})"
    return None


def _invalidation(action: dict, row: dict, context: dict) -> float | None:
    """An index level that ends the idea: below the index for a call, above it for a put; otherwise dropped."""
    level = _num(action.get("invalidation_level"))
    price = (context.get("index_prices") or {}).get(row.get("underlying"))
    if level is None or not price:
        return level if level is not None and not price else None
    if (row.get("type") == "CE" and level < price) or (row.get("type") == "PE" and level > price):
        return level
    return None


def _matrix_size(kind: str, action: dict, row: dict, context: dict, limits: Limits, ltp: float, stop: float | None,
                 lot_size: int, pos: dict | None) -> tuple[int, str, str] | str:
    """Lots the decision matrix allows: a fixed number per grade (learning phase), on the horizon's own grade."""
    from . import agentic_matrix as mx
    root, side = row.get("underlying"), row.get("type")
    if side not in mx.SIDES:
        return "decision matrix: unknown option side"
    cell = (context["matrix"].get(root) or {}).get(side)
    if cell is None:     # no inputs for this index (no history yet): the smallest graded size
        cell = {"grade": "C", "score": None}
    swing = action.get("horizon") == "swing"
    if swing and cell.get("swing"):
        cell = cell["swing"]
    elif kind == "arm" and action.get("trigger_op") == ("above" if side == "CE" else "below") and cell.get("if_break"):
        cell = cell["if_break"]
    grade = cell["grade"]
    cap = mx.LOTS_BY_GRADE[grade]
    held = int(pos.get("lots") or 0) if pos is not None else 0
    lots = cap - held
    why = (f"{'swing' if swing else 'intraday'} grade {grade} (score {cell.get('score')}): up to {cap} lots"
           + (f", {held} held" if held else "") + f"; Rs {mx.risk_per_lot(ltp, stop, lot_size):,.0f} at risk per lot")
    if lots < 1:
        return f"decision matrix: {why} -- no room to add"
    return lots, grade, why


def _check_trigger(action: dict, context: dict, limits: Limits) -> str | None:
    """A trigger must watch a known index, not be already satisfied, and expire inside today's risk window."""
    prices = context.get("index_prices", {})
    if context.get("armed_count", 0) >= MAX_ARMED:
        return f"already {MAX_ARMED} armed triggers: disarm one first"
    for prefix, required in (("trigger", True), ("confirm", False)):
        index, op, level = action.get(f"{prefix}_index"), action.get(f"{prefix}_op"), _num(action.get(f"{prefix}_level"))
        if not required and index is None and op is None and level is None:
            continue
        if index not in INDEX_ROOTS or op not in TRIGGER_OPS or level is None:
            return f"{prefix} needs an index ({'/'.join(INDEX_ROOTS)}), op (above/below) and a level"
        now_price = prices.get(index)
        if not now_price:
            return f"no live price for {index}"
        if required and ((op == "above" and now_price > level) or (op == "below" and now_price < level)):
            return f"{index} is already {op} {level:g} ({now_price:g}): use open, not arm"
    until = str(action.get("valid_until") or "")
    if len(until) != 5 or until[2] != ":" or not until.replace(":", "").isdigit():
        return "valid_until must be HH:MM"
    if until <= context["clock"] or until > limits.no_new_after:
        return f"valid_until must be after now and no later than {limits.no_new_after}"
    return None


# ---------------------------------------------------------------------------------------------- fallback

def rules_decision(context: dict, reason: str) -> dict:
    """Manage what is open without a model: stops, half off a big winner, nothing new. Commentary says why."""
    actions = []
    for position_id, pos in context["positions"].items():
        ret = pos.get("return_pct") or 0.0
        expiry_today = pos.get("expiry") == context["today"]
        if expiry_today and context["clock"] >= "14:45":
            actions.append({"action": "close", "position_id": position_id, "contract_id": None, "lots": None,
                            "stop_premium": None, "target_premium": None,
                            "reason": "expiry day after 14:45: out before the last-half-hour decay"})
        elif ret <= -45.0:
            actions.append({"action": "close", "position_id": position_id, "contract_id": None, "lots": None,
                            "stop_premium": None, "target_premium": None, "reason": f"down {ret:.0f}%: cut at the rules' stop"})
        elif ret >= 60.0 and int(pos["lots"]) >= 2 and not pos.get("booked"):
            actions.append({"action": "reduce", "position_id": position_id, "contract_id": None,
                            "lots": int(pos["lots"]) // 2, "stop_premium": None, "target_premium": None,
                            "reason": f"up {ret:.0f}%: book half, let the rest run"})
    return {"market_view": f"Rules-only cycle ({reason}). Managing open positions; no new positions without the model.",
            "regime": "neutral", "day_bias": "neutral", "confidence": 0.0, "actions": actions,
            "blockers": [{"category": "limits", "detail": reason}], "data_wanted": "none",
            "next_check_minutes": 15, "notes_for_next_cycle": ""}
