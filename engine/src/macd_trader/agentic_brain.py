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

import json
import math
import os
import time as clock
from dataclasses import dataclass, field
from pathlib import Path

import httpx

DEFAULT_BASE_URL = "https://api.x.ai/v1"
ACTIONS = ("open", "add", "reduce", "close", "set_levels", "hold")
REGIMES = ("bullish", "bearish", "neutral", "volatile")

PLAYBOOK = """You are the portfolio manager of a PAPER options book on Indian markets (NSE/BSE index and F&O stock
options). You only BUY options (calls or puts); you never write options. You think like an experienced discretionary
options trader who respects evidence and risk. Every cycle you receive the market state, the open book and a list of
tradable contracts with live prices. You decide what to do now and explain why, briefly and concretely.

How to think each cycle:
1. Read the regime: index trend (vs 20/50-day averages), today's move and where it sits in the day's range, breadth,
   sector leadership, India VIX, time of day, days to expiry. Say what changed since your last note.
2. Review every open position against its thesis: is the reason for holding still true? Hold, add, reduce, move the
   stop/target, or close. Do not let a winner turn into a loser; do not average down a losing option.
3. Only then consider new positions, and only where there is a real edge. Doing nothing is a valid decision.
4. Size so that being wrong is survivable: risk on any one idea should be a small part of capital.

What the backtests on this market found (Oct 2026 studies, 2019-2026 daily data, one year of real option premiums):
- Stock calls on 60-day relative-strength leaders were the most consistent stock-option edge, mainly when NIFTY is
  above its 50-day average; below it they did poorly. "The move already happened" did NOT hurt: strength persists
  for 2-4 weeks on daily bars.
- Buying stock PUTS lost money under every selection rule tested (weak stocks bounce, put premium decays). Express a
  bearish view through index puts, not stock puts, unless the evidence in front of you is unusually strong.
- Rotation hooks (RRG Improving->Leading, Weakening->Lagging) did not predict the next big movers. Do not rely on them.
- Volatile names predict the SIZE of a move, not its direction; their options already charge for it.
- Month-start sector leaders out-ran laggards for about 13 sessions, then faded by month end.
- Next-day index direction is hard to call. The index scorecard (needs net +2 or -2) is the best-tested next-day
  signal; when it says no trade, have a better reason than a feeling before taking an overnight index position.
- Exits decide results more than entries: past positions often gave back 30-70 points from their peak. Book part of
  a big winner (for example half at +50% to +80%) and trail the rest; cut a loser at its stop.
- Time decay accelerates in the last two sessions before expiry: prefer the next expiry when fewer than 2 sessions
  remain, and never hold a long option into the last 30 minutes of its expiry day.
- After holidays and big overnight gaps, wait for the first 15-30 minutes to settle before adding risk.

Hard limits (enforced in code; orders that break them are trimmed or rejected): maximum open positions, maximum value
per position, maximum total deployed, a daily loss stop that blocks new risk, whole lots only, and only the
contract_ids listed in this message. Use position_id values exactly as given to manage positions.

Answer ONLY with JSON matching the schema. In "reason" give the specific evidence (numbers) behind each action. Keep
market_view to 2-4 sentences, a trader's read, not a summary of the input."""

DECISION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["market_view", "regime", "confidence", "actions", "next_check_minutes", "notes_for_next_cycle"],
    "properties": {
        "market_view": {"type": "string"},
        "regime": {"type": "string", "enum": list(REGIMES)},
        "confidence": {"type": "number"},
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["action", "contract_id", "position_id", "lots", "stop_premium", "target_premium", "reason"],
                "properties": {
                    "action": {"type": "string", "enum": list(ACTIONS)},
                    "contract_id": {"type": ["string", "null"]},
                    "position_id": {"type": ["string", "null"]},
                    "lots": {"type": ["integer", "null"]},
                    "stop_premium": {"type": ["number", "null"]},
                    "target_premium": {"type": ["number", "null"]},
                    "reason": {"type": "string"},
                },
            },
        },
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


class BrainError(RuntimeError):
    pass


class GrokClient:
    """One structured chat-completions call per cycle. Async, no retries beyond one schema fallback."""

    def __init__(self, key: str, model: str, base_url: str = DEFAULT_BASE_URL, timeout: float = 120.0,
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
                response = await client.post(f"{self.base_url}/chat/completions", json=body)
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
            raise BrainError(f"HTTP {response.status_code}: {response.text[:300]}")
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
    max_positions: int = 8
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
        action = {k: raw.get(k) for k in ("action", "contract_id", "position_id", "lots", "stop_premium", "target_premium", "reason")}
        kind = action["action"]
        if kind not in ACTIONS:
            reject(action, "unknown action")
            continue
        key = f"{kind}:{action.get('contract_id') or action.get('position_id')}"
        if key in seen:
            reject(action, "duplicate action")
            continue
        seen.add(key)
        if kind in ("open", "add", "reduce", "close") and context.get("market_open") is False:
            reject(action, "market closed")
            continue
        stop, target = _num(action.get("stop_premium")), _num(action.get("target_premium"))
        lots = action.get("lots")
        lots = int(lots) if isinstance(lots, (int, float)) and not isinstance(lots, bool) and lots == lots else None

        if kind in ("open", "add"):
            if loss_stop_hit:
                reject(action, f"daily loss stop ({limits.daily_loss_stop_pct:.0%} of capital) reached: no new risk today")
                continue
            if now >= limits.no_new_after:
                reject(action, f"no new risk after {limits.no_new_after}")
                continue
            if kind == "open":
                row = candidates.get(str(action.get("contract_id")))
                if not row:
                    reject(action, "contract_id is not in this cycle's candidate list")
                    continue
                if any(p["symbol"] == row["symbol"] for p in positions.values()):
                    reject(action, "already held: use add with its position_id")
                    continue
                if open_count >= limits.max_positions:
                    reject(action, f"already at {limits.max_positions} open positions")
                    continue
                held_value = 0.0
            else:
                pos = positions.get(str(action.get("position_id")))
                if not pos:
                    reject(action, "position_id not found")
                    continue
                row = {"symbol": pos["symbol"], "ltp": pos["ltp"], "lot_size": pos["lot_size"], "expiry": pos.get("expiry")}
                held_value = float(pos.get("value", 0.0))
            ltp, lot_size = _num(row.get("ltp")), int(row.get("lot_size") or 0)
            if not ltp or ltp <= 0 or lot_size < 1:
                reject(action, "no live price or lot size")
                continue
            if row.get("expiry") == context["today"] and now >= limits.expiry_no_new_after:
                reject(action, "contract expires today: no new risk this late")
                continue
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
            accepted = {**action, "lots": granted, "symbol": row["symbol"], "ltp": ltp, "lot_size": lot_size,
                        "stop_premium": stop if stop and stop < ltp else None,
                        "target_premium": target if target and target > ltp else None, "note": note}
            out.accepted.append(accepted)
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
            "regime": "neutral", "confidence": 0.0, "actions": actions, "next_check_minutes": 15,
            "notes_for_next_cycle": ""}
