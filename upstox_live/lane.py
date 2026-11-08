"""Upstox live lane: follows the MACD lane's signals with real Upstox orders inside a fixed fund (default Rs 25,000).

What it does, every few seconds:
  * reads new MACD signals from the engine (through the gateway) and the MACD paper lane's open positions;
  * maps each signal's Fyers symbol to its Upstox instrument key and lot size through the platform hub;
  * ENTRY: the first new BUY signal whose one-lot cost fits the lane's free funds (lane fund minus what is deployed,
    plus booked P&L, and never more than Upstox's available margin) gets a 1-lot LIMIT BUY (product D);
  * EXIT: when the MACD paper lane closes that contract (or never took it within 10 minutes), a LIMIT SELL of the
    whole held quantity at a small discount to the last price, re-priced up to three times;
  * reads the Upstox order book, trades, positions and funds, and serves the lane at /api/upstox-live/snapshot.

Real orders go out ONLY while the arm file exists and contains "yes" (runtime/upstox_live/ARMED). Without it the lane
runs dry: it journals exactly what it would have ordered. The owner arms and disarms it; nothing here does.
The Upstox access token is read from a file and never logged, printed or served.
"""
from __future__ import annotations

import json
import re
import math
import os
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

IST = timezone(timedelta(hours=5, minutes=30))
ENGINE_URL = os.environ.get("ENGINE_URL", "http://gateway:8100").rstrip("/")
ENGINE_TOKEN = os.environ.get("MACD_API_TOKEN", "")
HUB_URL = os.environ.get("HUB_URL", "http://host.docker.internal:8400").rstrip("/")
TOKEN_FILE = os.environ.get("UPSTOX_TOKEN_FILE", "/secrets/upstox.token")
STATE_DIR = os.environ.get("STATE_DIR", "/app/runtime/upstox_live")
CAPITAL = float(os.environ.get("LANE_CAPITAL", "25000"))
PORT = int(os.environ.get("PORT", "8440"))
LOOP_SECONDS = float(os.environ.get("LOOP_SECONDS", "5"))
READ_SECONDS = float(os.environ.get("READ_SECONDS", "15"))
MAX_SIGNAL_AGE = float(os.environ.get("MAX_SIGNAL_AGE_SECONDS", "120"))
ENTRY_BUFFER = float(os.environ.get("ENTRY_BUFFER_PCT", "2")) / 100
EXIT_BUFFERS = [0.03, 0.06, 0.10]
ENTRY_TIMEOUT = float(os.environ.get("ENTRY_TIMEOUT_SECONDS", "90"))
EXIT_TIMEOUT = float(os.environ.get("EXIT_TIMEOUT_SECONDS", "60"))
MACD_ADOPT_SECONDS = 600
MAX_ORDERS_PER_DAY = int(os.environ.get("MAX_ORDERS_PER_DAY", "30"))
TAG = "MACDLIVE"
ORDER_HOST = os.environ.get("UPSTOX_ORDER_HOST", "https://api-hft.upstox.com")
ORDER_TOKEN_FILE = os.environ.get("UPSTOX_ORDER_TOKEN_FILE", "/secrets/upstox-trade.token")
READ_HOST = os.environ.get("UPSTOX_READ_HOST", "https://api.upstox.com")
ARM_FILE = os.path.join(STATE_DIR, "ARMED")
STATE_FILE = os.path.join(STATE_DIR, "state.json")
JOURNAL_FILE = os.path.join(STATE_DIR, "journal.jsonl")

lock = threading.RLock()
OPTION_RE = re.compile(r"\d(CE|PE)$")


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(d: datetime | None = None) -> str:
    return (d or now()).astimezone(IST).isoformat(timespec="seconds")


def parse_time(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def armed() -> bool:
    try:
        with open(ARM_FILE) as f:
            return f.read().strip().lower() == "yes"
    except OSError:
        return False


def tick_round(price: float, tick: float, up: bool) -> float:
    tick = tick or 0.05
    steps = price / tick
    steps = math.ceil(steps - 1e-9) if up else math.floor(steps + 1e-9)
    return round(max(tick, steps * tick), 2)


# ---------------------------------------------------------------- state

def blank_state() -> dict:
    return {"created_at": iso(), "seen_signals": [], "positions": {}, "orders": {}, "fills": {}, "decisions": [],
            "realized": 0.0, "orders_today": {"day": "", "count": 0}, "started_at": iso()}


def load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            s = json.load(f)
        for k, v in blank_state().items():
            s.setdefault(k, v)
        return s
    except (OSError, ValueError):
        return blank_state()


def save_state(s: dict) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f, indent=1, default=str)
    os.replace(tmp, STATE_FILE)


def journal(kind: str, **detail) -> dict:
    row = {"at": iso(), "kind": kind, **detail}
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(JOURNAL_FILE, "a") as f:
        f.write(json.dumps(row, default=str) + "\n")
    state["decisions"] = (state["decisions"] + [row])[-300:]
    return row


state = load_state()
cache: dict = {"funds": None, "orders": [], "trades": [], "positions": [], "read_at": None, "read_error": None,
               "engine_error": None, "macd_open": [], "macd_checked_at": None}


# ---------------------------------------------------------------- HTTP

def http(method: str, url: str, headers: dict, body: dict | None = None, timeout: float = 15) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Accept": "application/json", "User-Agent": "macd-mini-upstox-lane/1.0", **headers,
                                                                         **({"Content-Type": "application/json"} if data else {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        text = e.read()[:600].decode("utf-8", "replace")
        raise RuntimeError(f"{method} {urllib.parse.urlparse(url).path} -> {e.code}: {text}") from None


def engine(path: str) -> object:
    return http("GET", ENGINE_URL + path, {"X-Macd-Token": ENGINE_TOKEN})


def upstox_token(order: bool = False) -> str:
    """Reads use the platform's token; ORDER calls need the daily OAuth access token (the analytics token is read-only)."""
    path = ORDER_TOKEN_FILE if order and os.path.exists(ORDER_TOKEN_FILE) else TOKEN_FILE
    with open(path) as f:
        return f.read().strip()


def upstox(method: str, host: str, path: str, body: dict | None = None, query: dict | None = None) -> dict:
    url = host + path + (("?" + urllib.parse.urlencode(query)) if query else "")
    return http(method, url, {"Authorization": "Bearer " + upstox_token(order=host == ORDER_HOST)}, body)


def resolve(symbol: str) -> dict | None:
    try:
        return http("GET", HUB_URL + "/v1/resolve?" + urllib.parse.urlencode({"symbol": symbol}), {})
    except RuntimeError:
        return None


# ---------------------------------------------------------------- reads

def read_upstox() -> None:
    out = {}
    try:
        out["funds"] = upstox("GET", READ_HOST, "/v2/user/get-funds-and-margin", query={"segment": "SEC"}).get("data")
        out["orders"] = upstox("GET", READ_HOST, "/v2/order/retrieve-all").get("data") or []
        out["trades"] = upstox("GET", READ_HOST, "/v2/order/trades/get-trades-for-day").get("data") or []
        out["positions"] = upstox("GET", READ_HOST, "/v2/portfolio/short-term-positions").get("data") or []
        with lock:
            cache.update(out, read_at=iso(), read_error=None)
            record_fills()
    except Exception as e:  # keep the last good read on screen
        with lock:
            cache["read_error"] = str(e)[:400]


def record_fills() -> None:
    """Lane fills = Upstox trades on orders this lane placed (dedupe by trade id); positions follow from them."""
    ours = set(state["orders"])
    for t in cache["trades"]:
        oid, tid = str(t.get("order_id")), str(t.get("trade_id") or t.get("exchange_order_id", "")) + "|" + str(t.get("order_id"))
        if oid not in ours or tid in state["fills"]:
            continue
        o = state["orders"][oid]
        qty = int(t.get("quantity") or 0)
        price = float(t.get("average_price") or t.get("price") or 0)
        side = (t.get("transaction_type") or o["side"]).upper()
        state["fills"][tid] = {"order_id": oid, "symbol": o["symbol"], "instrument": o["instrument"], "side": side,
                               "quantity": qty, "price": price, "at": t.get("exchange_timestamp") or t.get("order_timestamp") or iso()}
        p = state["positions"].setdefault(o["symbol"], {"symbol": o["symbol"], "instrument": o["instrument"], "quantity": 0,
                                                       "average_price": 0.0, "lot_size": o.get("lot_size"), "tick": o.get("tick"),
                                                       "opened_at": None, "macd_seen": False, "realized": 0.0, "bought": 0})
        if side == "BUY":
            p["bought"] = p.get("bought", 0) + qty
            total = p["average_price"] * p["quantity"] + price * qty
            p["quantity"] += qty
            p["average_price"] = total / p["quantity"] if p["quantity"] else 0.0
            p["opened_at"] = p["opened_at"] or state["fills"][tid]["at"]
        else:
            pnl = (price - p["average_price"]) * min(qty, p["quantity"])
            p["realized"] += pnl
            state["realized"] += pnl
            p["quantity"] = max(0, p["quantity"] - qty)
            if p["quantity"] == 0:
                journal("CLOSED", symbol=o["symbol"], realized=round(p["realized"], 2))
                state.setdefault("closed", []).append({**p, "quantity": p.get("bought") or qty, "closed_at": state["fills"][tid]["at"],
                                                       "exit_price": price})
                del state["positions"][o["symbol"]]
        journal("FILL", symbol=o["symbol"], side=side, quantity=qty, price=price, order_id=oid)
    # order status from the book
    book = {str(o.get("order_id")): o for o in cache["orders"]}
    for oid, o in state["orders"].items():
        b = book.get(oid)
        if b:
            o["status"] = b.get("status")
            o["filled"] = int(b.get("filled_quantity") or 0)
            o["message"] = b.get("status_message") or b.get("status_message_raw")


# ---------------------------------------------------------------- decisions

def deployed() -> float:
    held = sum(p["average_price"] * p["quantity"] for p in state["positions"].values())
    pending = sum(o["price"] * (o["quantity"] - o.get("filled", 0)) for o in state["orders"].values()
                  if o["side"] == "BUY" and o.get("open", True) and not o.get("dry"))
    return held + pending


def free_funds() -> float:
    lane = CAPITAL + state["realized"] - deployed()
    margin = None
    f = cache.get("funds") or {}
    eq = f.get("equity") if isinstance(f, dict) else None
    if eq and eq.get("available_margin") is not None:
        margin = float(eq["available_margin"])
    return min(lane, margin) if margin is not None else lane


def count_order() -> bool:
    day = now().astimezone(IST).date().isoformat()
    if state["orders_today"]["day"] != day:
        state["orders_today"] = {"day": day, "count": 0}
    if state["orders_today"]["count"] >= MAX_ORDERS_PER_DAY:
        return False
    state["orders_today"]["count"] += 1
    return True


def place(symbol: str, inst: dict, side: str, qty: int, price: float, reason: str, force_live: bool = False) -> None:
    live = armed() or force_live
    body = {"quantity": qty, "product": "D", "validity": "DAY", "price": price, "tag": TAG,
            "instrument_token": inst["upstox"], "order_type": "LIMIT", "transaction_type": side,
            "disclosed_quantity": 0, "trigger_price": 0, "is_amo": False, "slice": False}
    if not live:
        oid = f"DRY-{int(time.time() * 1000)}"
        state["orders"][oid] = {"order_id": oid, "symbol": symbol, "instrument": inst["upstox"], "side": side, "quantity": qty,
                                "price": price, "placed_at": iso(), "reason": reason, "dry": True, "open": False,
                                "status": "dry run (not sent)", "lot_size": inst.get("lot_size"), "tick": inst.get("tick_size")}
        journal("DRY_RUN_ORDER", symbol=symbol, side=side, quantity=qty, price=price, reason=reason)
        return
    if not count_order():
        journal("ORDER_LIMIT", symbol=symbol, side=side, reason=f"{MAX_ORDERS_PER_DAY} orders already sent today")
        return
    try:
        r = upstox("POST", ORDER_HOST, "/v3/order/place", body)
        ids = (r.get("data") or {}).get("order_ids") or [(r.get("data") or {}).get("order_id")]
        for oid in filter(None, ids):
            state["orders"][str(oid)] = {"order_id": str(oid), "symbol": symbol, "instrument": inst["upstox"], "side": side,
                                         "quantity": qty, "price": price, "placed_at": iso(), "reason": reason, "open": True,
                                         "status": "sent", "lot_size": inst.get("lot_size"), "tick": inst.get("tick_size"),
                                         "attempt": 0}
        journal("ORDER_SENT", symbol=symbol, side=side, quantity=qty, price=price, order_ids=ids, reason=reason)
    except Exception as e:
        journal("ORDER_ERROR", symbol=symbol, side=side, quantity=qty, price=price, error=str(e)[:400])


def cancel(o: dict, why: str) -> bool:
    if o.get("dry"):
        o["open"] = False
        return True
    try:
        upstox("DELETE", ORDER_HOST, "/v3/order/cancel", query={"order_id": o["order_id"]})
        o["open"] = False
        journal("ORDER_CANCELLED", symbol=o["symbol"], order_id=o["order_id"], reason=why)
        return True
    except Exception as e:
        journal("CANCEL_ERROR", symbol=o["symbol"], order_id=o["order_id"], error=str(e)[:300])
        return False


def probe_order_api() -> None:
    """Can this token reach the ORDER API? Cancels an order id that cannot exist (nothing is placed or changed)."""
    try:
        mtime = os.path.getmtime(ORDER_TOKEN_FILE if os.path.exists(ORDER_TOKEN_FILE) else TOKEN_FILE)
    except OSError:
        mtime = 0
    last = cache.get("order_api") or {}
    if last.get("token_mtime") == mtime and time.time() - last.get("checked", 0) < 900:
        return
    result = {"token_mtime": mtime, "checked": time.time(), "checked_at": iso()}
    try:
        upstox("DELETE", ORDER_HOST, "/v3/order/cancel", query={"order_id": "000000000000000"})
        result.update(ok=True, detail="order API reachable")
    except Exception as e:
        text = str(e)
        if "UDAPI100067" in text:
            result.update(ok=False, detail="the Upstox token is READ-ONLY: orders are refused (log in for a trading token)")
        elif "UDAPI1221" in text:
            result.update(ok=False, detail="this IP is not registered as the app's static IP (UDAPI1221)")
        elif " 401" in text or "-> 401" in text:
            result.update(ok=False, detail="token not accepted by the order API: " + text[-200:])
        else:   # e.g. order not found: the request got through to the order system
            result.update(ok=True, detail="order API reachable (test cancel of a non-existent order id was answered)")
    cache["order_api"] = result


def ltp(symbol: str) -> float | None:
    for p in cache["positions"]:
        if p.get("instrument_token") == state["positions"].get(symbol, {}).get("instrument"):
            v = p.get("last_price")
            return float(v) if v else None
    return None


def step() -> None:
    try:
        signals = engine("/api/signals?limit=50")
        port = engine("/api/portfolio")
        cache["engine_error"] = None
    except Exception as e:
        cache["engine_error"] = str(e)[:300]
        return
    macd_open = {p["symbol"] for p in port.get("positions", [])}
    cache["macd_open"], cache["macd_checked_at"] = sorted(macd_open), iso()
    seen = set(state["seen_signals"])
    started = parse_time(state["started_at"]) or now()

    # Orders still open past their time: cancel; exits get re-priced below.
    book_open = {"open", "trigger pending", "put order req received", "validation pending", "open pending", "modify pending"}
    for o in list(state["orders"].values()):
        if not o.get("open"):
            continue
        status = (o.get("status") or "").lower()
        if status in ("complete", "rejected", "cancelled"):
            o["open"] = False
            if status == "rejected":
                journal("ORDER_REJECTED", symbol=o["symbol"], order_id=o["order_id"], message=o.get("message"))
            continue
        age = (now() - (parse_time(o["placed_at"]) or now())).total_seconds()
        limit = ENTRY_TIMEOUT if o["side"] == "BUY" else EXIT_TIMEOUT
        if age > limit and (status in book_open or status in ("sent", "")):
            cancel(o, f"unfilled after {int(limit)} s")

    # Exits: follow the MACD lane out.
    for sym, p in list(state["positions"].items()):
        if p["quantity"] <= 0:
            continue
        if sym in macd_open:
            p["macd_seen"] = True
            continue
        opened = parse_time(p.get("opened_at")) or now()
        if not p["macd_seen"] and (now() - opened).total_seconds() < MACD_ADOPT_SECONDS:
            continue
        if any(o["symbol"] == sym and o["side"] == "SELL" and o.get("open") for o in state["orders"].values()):
            continue
        attempts = sum(1 for o in state["orders"].values() if o["symbol"] == sym and o["side"] == "SELL" and not o.get("dry"))
        last = ltp(sym)
        if last is None:
            journal("EXIT_WAIT", symbol=sym, reason="no last price from Upstox yet")
            continue
        buffer = EXIT_BUFFERS[min(attempts, len(EXIT_BUFFERS) - 1)]
        why = "MACD lane closed it" if p["macd_seen"] else "MACD lane did not take this signal within 10 min"
        place(sym, {"upstox": p["instrument"], "lot_size": p.get("lot_size"), "tick_size": p.get("tick")}, "SELL",
              int(p["quantity"]), tick_round(last * (1 - buffer), p.get("tick") or 0.05, up=False), why)

    # Entries: new BUY signals in time order; the first that fits the fund is taken.
    fresh = [s for s in signals if s.get("signal_id") and s["signal_id"] not in seen]
    for s in sorted(fresh, key=lambda s: s.get("timestamp", "")):
        seen.add(s["signal_id"])
        at = parse_time(s.get("timestamp"))
        if not at or at < started or (now() - at).total_seconds() > MAX_SIGNAL_AGE:
            continue   # never act on signals from before the lane started or that are stale
        sym = s["symbol"]
        if s.get("side") != "BUY":
            journal("SKIP", symbol=sym, reason=f"{s.get('side')} signal; the lane only buys options to open")
            continue
        if not OPTION_RE.search(sym):
            journal("SKIP", symbol=sym, reason="not an option contract; the lane buys index/stock options only")
            continue
        if sym in state["positions"] or any(o["symbol"] == sym and o.get("open") for o in state["orders"].values()):
            journal("SKIP", symbol=sym, reason="already held or ordering")
            continue
        inst = resolve(sym)
        if not inst or not inst.get("upstox"):
            journal("SKIP", symbol=sym, reason="no Upstox instrument for this symbol in the hub's master")
            continue
        lot, price = int(inst.get("lot_size") or 1), float(s.get("price") or 0)
        limit = tick_round(price * (1 + ENTRY_BUFFER), float(inst.get("tick_size") or 0.05), up=True)
        cost, free = limit * lot, free_funds()
        if price <= 0 or cost > free:
            journal("SKIP_FUNDS", symbol=sym, price=price, lot_size=lot, cost=round(cost, 2), free=round(free, 2))
            continue
        place(sym, inst, "BUY", lot, limit, f"{s.get('kind')} at {price}")
    state["seen_signals"] = list(seen)[-2000:]


def loop() -> None:
    last_read = 0.0
    if not state["seen_signals"]:
        try:   # first start: everything already published is history, not a new signal
            state["seen_signals"] = [s["signal_id"] for s in engine("/api/signals?limit=200") if s.get("signal_id")]
        except Exception:
            pass
    while True:
        try:
            if time.time() - last_read >= READ_SECONDS:
                read_upstox()
                probe_order_api()
                last_read = time.time()
            with lock:
                step()
                save_state(state)
        except Exception:
            traceback.print_exc()
        time.sleep(LOOP_SECONDS)


# ---------------------------------------------------------------- REST

# ---------------------------------------------------------------- owner controls (Settings page)

def set_armed(on: bool, who: str) -> dict:
    os.makedirs(STATE_DIR, exist_ok=True)
    if on:
        tmp = ARM_FILE + ".tmp"
        with open(tmp, "w") as f:
            f.write("yes\n")
        os.replace(tmp, ARM_FILE)
        journal("ARMED", by=who)
        return {"armed": True}
    try:
        os.remove(ARM_FILE)
    except FileNotFoundError:
        pass
    cancelled = [o["order_id"] for o in list(state["orders"].values())
                 if o["side"] == "BUY" and o.get("open") and not o.get("dry") and cancel(o, "kill switch")]
    journal("KILLED", by=who, cancelled_entries=cancelled)
    return {"armed": False, "cancelled_entries": cancelled}


def exit_all(who: str) -> dict:
    """Sell every lane position now (LIMIT 3% under the last price), armed or not: the owner pressed it."""
    sent, waiting = [], []
    for sym, p in list(state["positions"].items()):
        if p["quantity"] <= 0 or any(o["symbol"] == sym and o["side"] == "SELL" and o.get("open") for o in state["orders"].values()):
            continue
        last = ltp(sym)
        if last is None:
            waiting.append(sym)
            continue
        place(sym, {"upstox": p["instrument"], "lot_size": p.get("lot_size"), "tick_size": p.get("tick")}, "SELL",
              int(p["quantity"]), tick_round(last * (1 - EXIT_BUFFERS[0]), p.get("tick") or 0.05, up=False),
              "owner: exit all", force_live=True)
        p["macd_seen"] = True
        sent.append(sym)
    journal("EXIT_ALL", by=who, sent=sent, no_price=waiting)
    return {"sent": sent, "no_price": waiting}


def snapshot() -> dict:
    with lock:
        lane_instruments = {o["instrument"] for o in state["orders"].values()} | {p["instrument"] for p in state["positions"].values()}
        positions = []
        for p in state["positions"].values():
            last = ltp(p["symbol"])
            positions.append({**p, "last_price": last,
                              "unrealized_pnl": round((last - p["average_price"]) * p["quantity"], 2) if last else None,
                              "return_pct": round((last / p["average_price"] - 1) * 100, 2) if last and p["average_price"] else None})
        unreal = sum(p["unrealized_pnl"] or 0 for p in positions)
        f = cache.get("funds") or {}
        return {
            "lane": "upstox_live", "label": "Upstox live", "armed": armed(), "mode": "LIVE" if armed() else "DRY RUN",
            "capital": CAPITAL, "deployed": round(deployed(), 2), "free": round(free_funds(), 2),
            "realized": round(state["realized"], 2), "unrealized": round(unreal, 2),
            "equity": round(CAPITAL + state["realized"] + unreal, 2),
            "upstox_funds": (f.get("equity") if isinstance(f, dict) else None),
            "positions": positions, "closed": state.get("closed", [])[-100:],
            "orders": sorted(state["orders"].values(), key=lambda o: o["placed_at"], reverse=True)[:200],
            "fills": sorted(state["fills"].values(), key=lambda f: str(f["at"]), reverse=True)[:200],
            "decisions": list(reversed(state["decisions"][-200:])),
            "account": {"positions": cache["positions"], "orders": cache["orders"], "trades": cache["trades"],
                        "lane_instruments": sorted(lane_instruments)},
            "macd_open": cache["macd_open"], "macd_checked_at": cache["macd_checked_at"],
            "read_at": cache["read_at"], "read_error": cache["read_error"], "engine_error": cache["engine_error"],
            "order_api": {k: v for k, v in (cache.get("order_api") or {}).items() if k in ("ok", "detail", "checked_at")} or None,
            "orders_today": state["orders_today"], "max_orders_per_day": MAX_ORDERS_PER_DAY, "started_at": state["started_at"],
            "rules": {"entry": f"First new MACD BUY signal whose 1-lot LIMIT cost (signal price +{ENTRY_BUFFER*100:.0f}%) fits the free fund; cancelled if unfilled in {int(ENTRY_TIMEOUT)} s",
                      "exit": "LIMIT SELL of the whole quantity when the MACD paper lane closes the contract (or never takes it within 10 min); 3% under the last price, then 6%, then 10%",
                      "fund": f"Rs {CAPITAL:,.0f} plus booked P&L, never more than Upstox's available margin",
                      "orders": f"product D (carry forward), DAY validity, tag {TAG}, at most {MAX_ORDERS_PER_DAY} orders a day",
                      "arming": "Real orders only while runtime/upstox_live/ARMED contains 'yes'; otherwise a dry run"},
            "checked_at": iso(),
        }


LOOPBACK = {"localhost", "127.0.0.1", "::1", "[::1]"}
ALLOWED_ORIGINS = {o.strip().rstrip("/") for o in os.environ.get("MACD_ALLOWED_ORIGINS_CSV", "").split(",") if o.strip()}


def allowed(headers) -> bool:
    """Browser calls from this Mac's own pages (localhost origin) or a client holding the MACD API token."""
    if ENGINE_TOKEN and headers.get("X-Macd-Token") == ENGINE_TOKEN:
        return True
    origin = (headers.get("Origin") or "").rstrip("/")
    if not origin:
        return False
    if origin in ALLOWED_ORIGINS:          # the engine's own list: the Tailscale address and machine names
        return True
    return (urllib.parse.urlparse(origin).hostname or "") in LOOPBACK


class Handler(BaseHTTPRequestHandler):
    def reply(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        path = self.path.split("?")[0].removeprefix("/api/upstox-live")
        if path not in ("/arm", "/kill", "/exit-all"):
            return self.reply(404, {"detail": "Not Found"})
        if not allowed(self.headers) or "application/json" not in (self.headers.get("Content-Type") or ""):
            return self.reply(403, {"detail": "Only this Mac's own MACD page (or the API token) can change the lane"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except ValueError:
            return self.reply(400, {"detail": "Body must be JSON"})
        who = self.headers.get("Origin") or "api token"
        with lock:
            if path == "/arm":
                if body.get("confirm") != "ARM":
                    return self.reply(400, {"detail": 'Type ARM to confirm real orders'})
                api = cache.get("order_api") or {}
                if not api.get("ok"):
                    return self.reply(409, {"detail": "Not armed: the Upstox order API is blocked: " + str(api.get("detail") or "not checked yet")})
                result = set_armed(True, who)
            elif path == "/kill":
                result = set_armed(False, who)
            else:
                if body.get("confirm") != "EXIT":
                    return self.reply(400, {"detail": "Confirm the exit"})
                result = exit_all(who)
            save_state(state)
        return self.reply(200, {**result, "snapshot": snapshot()})

    def do_GET(self):
        if self.path.split("?")[0] in ("/api/upstox-live/snapshot", "/snapshot"):
            body = json.dumps(snapshot(), default=str).encode()
            self.send_response(200)
        elif self.path == "/health":
            body = b'{"ok": true}'
            self.send_response(200)
        else:
            body = b'{"detail": "Not Found"}'
            self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    os.makedirs(STATE_DIR, exist_ok=True)
    print(f"upstox-live: fund Rs {CAPITAL:,.0f}, {'ARMED (real orders)' if armed() else 'dry run'}, port {PORT}", flush=True)
    threading.Thread(target=loop, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
