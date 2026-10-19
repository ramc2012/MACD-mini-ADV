"""Equity curves for the option agent's books: rupees, not averages.

An average return hides the path -- a month can average +26% on the back of
two trades while the account spent three weeks in drawdown. Each book here is
traded as a paper account would trade it:

* **Top-10 calls / puts:** every emission's ten picks bought at the 14:45
  price with ``NOTIONAL`` rupees each (fractional quantity, so a ₹1.5 lakh
  lot does not swamp a ₹5,000 one), held to the third session's close.
* **Index trades, plan exit:** one lot, exited at the first minute the INDEX
  touches the stop or target (the option's close in that minute; both in one
  minute counts as the stop), else at the third session's close.
* **Index trades, held:** the same trades held to the third session's close,
  to show what the plan adds or costs.
* **Sized books:** the same picks sized by each contract's own volatility (a
  one-sigma day moves a position about RISK_PER_PICK rupees; an index trade
  loses about RISK_PER_INDEX_TRADE at its stop) and scaled down to a capital
  cap, so a quiet contract is not undersized and a wild one does not dominate.

Benchmarks carry the same capital: the whole day's candidate set of that side
(what picking at random would have done), and for indices a coin flip -- half
a lot of the ATM call and half a lot of the ATM put, held.

Positions are marked at every session close while open; costs (brokerage,
statutory charges, slippage) are paid at exit. A contract whose later bars are
gone (Fyers serves no history once it expires) exits at its last known price;
those exits are counted.
"""
from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

from . import greeks, market_calendar
from . import option_agent as oa
from .universe import INDEX_SPOTS

NOTIONAL = 10_000.0
BROKERAGE_ROUND_TRIP = 40.0          # ₹20 an order, two orders
CHARGES_BUY = 0.0005                 # exchange, GST, stamp on the buy value
CHARGES_SELL = 0.0015                # STT 0.1% plus exchange/GST on the sell value
SLIPPAGE = 0.005                     # half a percent of premium, each side
RATE = 0.065


def cost(entry_value: float, exit_value: float) -> float:
    return (BROKERAGE_ROUND_TRIP + entry_value * (CHARGES_BUY + SLIPPAGE)
            + exit_value * (CHARGES_SELL + SLIPPAGE))


def _sessions_after(day: str, count: int = 3) -> list[str]:
    out, cursor = [], date.fromisoformat(day)
    while len(out) < count:
        cursor = market_calendar.next_trading_day(cursor)
        out.append(cursor.isoformat())
    return out


def _close_on(history: sqlite3.Connection, symbol: str, day: str, after: int | None = None) -> float | None:
    start, end = oa.session_bounds(None, day)
    row = history.execute(
        """SELECT close FROM historical_candles WHERE symbol = ? AND timeframe_seconds = 60
           AND timestamp >= ? AND timestamp < ? ORDER BY timestamp DESC LIMIT 1""",
        (symbol, max(start, after or start), end)).fetchone()
    return row[0] if row else None


def known_lots(live: dict[str, int] | None, snapshot_path: str | None) -> dict[str, int]:
    """Live broker lot sizes, backed by the broker's own on-disk cache.

    Before the morning login (or whenever the Fyers session is down) the live
    map is empty; sizing must not then fall back to a lot of 1 unit, which
    shrank every index trade to a single unit and made the 1-lot book look
    ten times calmer than it was.
    """
    out: dict[str, int] = {}
    if snapshot_path:
        try:
            cached = json.loads((Path(snapshot_path).with_name("fyers_lot_sizes.json")).read_text())
            out.update({symbol: int(size) for symbol, size in (cached.get("lot_sizes") or {}).items() if int(size) > 0})
        except (OSError, ValueError, AttributeError):
            pass
    out.update({symbol: int(size) for symbol, size in (live or {}).items() if int(size) > 0})
    return out


def lot_by_root(lots: dict[str, int]) -> dict[str, int]:
    """Lot size per underlying from any contract of it: expired contracts are no longer in the broker's list."""
    out: dict[str, int] = {}
    for symbol, lot in lots.items():
        parsed = oa.parse_option(symbol)
        if parsed and lot:
            out.setdefault(parsed["root"], int(lot))
    return out


# ---------------------------------------------------------------------------
# Trades -> daily marks
# ---------------------------------------------------------------------------

def _hold_trade(entry_day: str, symbol: str, qty: float, entry: float, returns: list[float | None],
                label: str) -> dict:
    """Held to the third close; marks at each close from the stored labels (r1, r2, r3)."""
    days = _sessions_after(entry_day)
    marks, last = {}, entry
    stale = False
    for day, r in zip(days, returns):
        if r is None:
            stale = True
            continue
        last = entry * (1 + r)
        marks[day] = last
    exit_day = days[-1]
    return {"label": label, "symbol": symbol, "entry_day": entry_day, "qty": qty, "entry": entry, "marks": marks,
            "exit_day": exit_day, "exit": last, "reason": "time exit" if not stale else "last known price",
            "complete": returns[-1] is not None}


def _plan_trade(history: sqlite3.Connection, trade: dict, qty: float) -> dict:
    """One index trade under its plan: minute by minute on the index, exit on the first touch."""
    entry_day, symbol, side = trade["day"], trade["symbol"], trade["side"]
    spot = INDEX_SPOTS[trade["underlying"]]
    cut = oa._as_of_epoch(entry_day)
    days = [entry_day] + _sessions_after(entry_day)
    marks: dict[str, float] = {}
    last = trade["entry_price"]
    for index, day in enumerate(days):
        start, end = oa.session_bounds(None, day)
        lo = cut if index == 0 else start
        option = dict(history.execute(
            """SELECT timestamp, close FROM historical_candles WHERE symbol = ? AND timeframe_seconds = 60
               AND timestamp >= ? AND timestamp < ?""", (symbol, lo, end)).fetchall())
        bars = history.execute(
            """SELECT timestamp, high, low FROM historical_candles WHERE symbol = ? AND timeframe_seconds = 60
               AND timestamp >= ? AND timestamp < ? ORDER BY timestamp""", (spot, lo, end)).fetchall()
        for ts, high, low in bars:
            if ts in option:
                last = option[ts]
            if trade.get("stop_spot") is None:
                continue
            hit_stop = low <= trade["stop_spot"] if side > 0 else high >= trade["stop_spot"]
            hit_target = high >= trade["target_spot"] if side > 0 else low <= trade["target_spot"]
            if hit_stop or hit_target:
                price = option.get(ts) or last
                return {"label": "index-plan", "symbol": symbol, "entry_day": entry_day, "qty": qty,
                        "entry": trade["entry_price"], "marks": marks, "exit_day": day, "exit": price,
                        "reason": "stop" if hit_stop else "target", "complete": True}
        if option:
            last = option[max(option)]
        if index:
            marks[day] = last
        if not bars and not option:
            break
    complete = days[-1] in marks
    return {"label": "index-plan", "symbol": symbol, "entry_day": entry_day, "qty": qty, "entry": trade["entry_price"],
            "marks": marks, "exit_day": days[-1] if complete else (max(marks) if marks else entry_day), "exit": last,
            "reason": "time exit" if complete else "last known price", "complete": complete}


def settle(trades: list[dict], as_of: str) -> None:
    """A trade whose third session is after ``as_of`` is still open, whatever its labels say."""
    for t in trades:
        horizon = _sessions_after(t["entry_day"])[-1]
        if horizon > as_of and not (t["complete"] and t["exit_day"] <= as_of):
            t.update({"reason": "open", "exit_day": horizon, "complete": False})


def _pnl(t: dict) -> float:
    return t["qty"] * (t["exit"] - t["entry"]) - cost(t["qty"] * t["entry"], t["qty"] * t["exit"])


def curve(trades: list[dict], as_of: str) -> dict:
    """Daily equity through ``as_of``: realised P&L (net of costs) plus open positions at that close."""
    if not trades:
        return {"points": [], "stats": {"trades": 0}}
    days, cursor = [min(t["entry_day"] for t in trades)], None
    cursor = date.fromisoformat(days[0])
    while days[-1] < as_of:
        cursor = market_calendar.next_trading_day(cursor)
        days.append(cursor.isoformat())
    closed = [t for t in trades if t["reason"] != "open"]
    points, peak, max_dd = [], 0.0, 0.0
    for day in days:
        realised = unrealised = deployed = 0.0
        for t in trades:
            if t["entry_day"] > day:
                continue
            if t["reason"] != "open" and t["exit_day"] <= day:
                realised += _pnl(t)
                continue
            mark = t["entry"]
            for d in sorted(t["marks"]):
                if d <= day:
                    mark = t["marks"][d]
            unrealised += t["qty"] * (mark - t["entry"])
            deployed += t["qty"] * t["entry"]
        equity = realised + unrealised
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
        points.append({"day": day, "equity": round(equity, 2), "realised": round(realised, 2), "deployed": round(deployed, 2)})
    pnl = [_pnl(t) for t in closed]
    wins, losses = [p for p in pnl if p > 0], [p for p in pnl if p <= 0]
    steps = [b["equity"] - a["equity"] for a, b in zip(points, points[1:])]
    mean_step = sum(steps) / len(steps) if steps else 0.0
    sd = math.sqrt(sum((s - mean_step) ** 2 for s in steps) / (len(steps) - 1)) if len(steps) > 2 else 0.0
    max_deployed = max(p["deployed"] for p in points) or 1.0
    return {"points": points, "stats": {
        "trades": len(trades), "closed": len(closed), "open": len(trades) - len(closed),
        "final": points[-1]["equity"], "max_drawdown": round(max_dd, 2),
        "max_deployed": round(max_deployed, 2), "return_on_deployed": round(points[-1]["equity"] / max_deployed, 4),
        "win_rate": round(len(wins) / len(pnl), 4) if pnl else None,
        "profit_factor": round(sum(wins) / -sum(losses), 3) if losses and sum(losses) else None,
        "best": round(max(pnl), 2) if pnl else None, "worst": round(min(pnl), 2) if pnl else None,
        "costs": round(sum(cost(t["qty"] * t["entry"], t["qty"] * t["exit"]) for t in closed), 2),
        "daily_sharpe": round(mean_step / sd * math.sqrt(250), 2) if sd else None,
        "stale_exits": sum(1 for t in trades if t["reason"] == "last known price"),
        "exits": dict(_count(t["reason"] for t in trades)),
    }}


def _count(values) -> dict:
    out: dict[str, int] = defaultdict(int)
    for value in values:
        out[value] += 1
    return out


# ---------------------------------------------------------------------------
# Books
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Sizing: by each contract's own volatility, inside a capital cap
# ---------------------------------------------------------------------------

RISK_PER_PICK = 1_500.0              # ₹ a one-sigma day may move one top-10 position
PICK_MIN, PICK_MAX = 2_000.0, 25_000.0
BOOK_CAP = 300_000.0                 # ₹ of entry value open at once, per top-10 book
RISK_PER_INDEX_TRADE = 4_000.0       # ₹ lost if the index reaches the plan's stop
INDEX_BOOK_CAP = 200_000.0
FALLBACK_VOL = 0.25                  # premium's daily vol when no implied vol can be solved


def daily_premium_vol(entry: float, spot: float | None, strike: float, expiry: str, side: int, day: str) -> float | None:
    """One-day, one-sigma premium move as a fraction of the premium: |delta| x spot x IV / sqrt(365) / premium."""
    if not entry or not spot or entry <= 0:
        return None
    moment = datetime.combine(date.fromisoformat(day), oa.AS_OF, oa.IST)
    years = greeks.years_to_expiry(expiry, moment)
    if not years:
        return None
    iv = greeks.implied_volatility(entry, spot, strike, years, RATE, side > 0)
    if not iv:
        return None
    d = abs(greeks.delta(spot, strike, years, RATE, iv, side > 0))
    return d * spot * iv / math.sqrt(365) / entry or None


def pick_notional(vol: float | None) -> float:
    return max(PICK_MIN, min(PICK_MAX, RISK_PER_PICK / (vol or FALLBACK_VOL)))


def index_lots(entry: float, premium_stop: float | None, lot: int, vol: float | None) -> int:
    """Lots whose loss at the stop is about RISK_PER_INDEX_TRADE (1.5 sigma if the stop has no price)."""
    per_unit = (entry - premium_stop) if premium_stop is not None and premium_stop < entry else entry * 1.5 * (vol or FALLBACK_VOL)
    if per_unit <= 0 or lot <= 0:
        return 0
    return int(RISK_PER_INDEX_TRADE // (per_unit * lot))


def _open_value(trades: list[dict], day: str) -> float:
    """Entry value still open when ``day``'s entries go in (an exit on ``day`` happens at or after them)."""
    return sum(t["qty"] * t["entry"] for t in trades if t["entry_day"] < day <= t["exit_day"])


def _scale_to_cap(amounts: list[float], open_value: float, cap: float) -> float:
    wanted = sum(amounts)
    return 1.0 if wanted <= 0 else max(0.0, min(1.0, (cap - open_value) / wanted))


def size_pick(row: dict, lot: int | None) -> dict:
    """The live suggestion for one top-10 pick."""
    moneyness = row.get("features", {}).get("o_premium_pct")
    spot = row["entry_price"] / moneyness if moneyness else None
    vol = daily_premium_vol(row["entry_price"], spot, row["strike"], row["expiry"], row["side"], row["day"])
    notional = pick_notional(vol)
    lot_value = row["entry_price"] * lot if lot else None
    return {"vol_daily": round(vol, 4) if vol else None, "notional": round(notional),
            "lot_size": lot, "lot_value": round(lot_value) if lot_value else None,
            "lots": round(notional / lot_value, 2) if lot_value else None}


def books(history: sqlite3.Connection, since: str, lots: dict[str, int] | None = None,
          notional: float = NOTIONAL) -> dict:
    """Every book's curve and its benchmark, from the latest run of each day since ``since``.

    Flat books put ``notional`` in every pick; sized books size each pick by
    its own volatility and scale a day's entries down to the capital cap.
    """
    oa.ensure_schema(history)
    from . import index_trades
    index_trades.ensure_schema(history)
    runs = history.execute(
        """SELECT r.run_id, r.day FROM agent_runs r
           JOIN (SELECT day, MAX(run_id) AS run_id FROM agent_runs WHERE day >= ? GROUP BY day) last ON last.run_id = r.run_id
           ORDER BY r.day""", (since,)).fetchall()
    per_root = lot_by_root(lots or {})
    out: dict[str, list[dict]] = defaultdict(list)
    sizing = {"capped_days": defaultdict(int), "skipped_index": 0, "fallback_vol": 0, "no_lot": 0}
    for run_id, day in runs:
        for side, name in ((1, "ce"), (-1, "pe")):
            picks = history.execute(
                """SELECT p.symbol, c.entry_price, c.r1, c.r2, c.r3, c.strike, c.expiry, c.features FROM agent_picks p
                   JOIN agent_candidates c ON c.day = p.day AND c.symbol = p.symbol
                   WHERE p.run_id = ? AND p.side = ?""", (run_id, side)).fetchall()
            wanted = []
            for symbol, entry, r1, r2, r3, strike, expiry, features in picks:
                out[name].append(_hold_trade(day, symbol, notional / entry, entry, [r1, r2, r3], name))
                pct = json.loads(features or "{}").get("o_premium_pct")
                vol = daily_premium_vol(entry, entry / pct if pct else None, strike, expiry, side, day)
                sizing["fallback_vol"] += vol is None
                wanted.append((symbol, entry, [r1, r2, r3], pick_notional(vol)))
            scale = _scale_to_cap([w[3] for w in wanted], _open_value(out[f"{name}_sized"], day), BOOK_CAP)
            if scale < 1:
                sizing["capped_days"][name] += 1
            for symbol, entry, labels, amount in wanted:
                if amount * scale > 0:
                    out[f"{name}_sized"].append(_hold_trade(day, symbol, amount * scale / entry, entry, labels, f"{name}_sized"))
            mean = history.execute(
                """SELECT AVG(r1), AVG(r2), AVG(r3), COUNT(*), COUNT(r3) FROM agent_candidates
                   WHERE day = ? AND side = ? AND r1 IS NOT NULL""", (day, side)).fetchone()
            if mean and mean[3]:
                path = [mean[0], mean[1], mean[2] if mean[4] else None]
                # The whole side with the same money: ten flat picks' worth, and the sized book's actual outlay.
                out[f"{name}_all"].append(_hold_trade(day, f"all {name.upper()}", oa.TOP_N * notional, 1.0, path, f"{name}_all"))
                outlay = sum(w[3] for w in wanted) * scale
                if outlay:
                    out[f"{name}_all_sized"].append(_hold_trade(day, f"all {name.upper()}", outlay, 1.0, path, f"{name}_all_sized"))
        index_wanted = []
        for trade in index_trades.view(history, run_id):
            if not trade.get("symbol"):
                continue
            trade = {**trade, "day": day}
            lot = trade.get("lot") or per_root.get(trade["underlying"])
            if not lot:
                sizing["no_lot"] += 1          # never a pretend lot of one unit
                continue
            out["index_plan"].append(_plan_trade(history, trade, lot))
            labels = history.execute("SELECT r1, r2, r3 FROM agent_candidates WHERE day = ? AND symbol = ?",
                                     (day, trade["symbol"])).fetchone() or (None, None, None)
            out["index_hold"].append(_hold_trade(day, trade["symbol"], lot, trade["entry_price"], list(labels), "index_hold"))
            coin = _coin_flip_trade(history, day, trade["underlying"], trade["expiry"], lot)
            if coin:
                out["index_coin"].append(coin)
            vol = daily_premium_vol(trade["entry_price"], trade.get("spot"), trade["strike"], trade["expiry"],
                                    trade["side"], day)
            index_wanted.append((trade, lot, index_lots(trade["entry_price"], trade.get("premium_stop"), lot, vol)))
        scale = _scale_to_cap([n * lot * t["entry_price"] for t, lot, n in index_wanted],
                              _open_value(out["index_sized"], day), INDEX_BOOK_CAP)
        if scale < 1:
            sizing["capped_days"]["index"] += 1
        for trade, lot, n in index_wanted:
            n = int(n * scale)
            if n < 1:
                sizing["skipped_index"] += 1
                continue
            out["index_sized"].append({**_plan_trade(history, trade, n * lot), "label": "index_sized"})
    as_of = runs[-1][1] if runs else since
    for trades in out.values():
        settle(trades, as_of)
    return {"since": since, "as_of": as_of, "notional": notional,
            "books": {name: curve(trades, as_of) for name, trades in out.items()},
            "trades": {name: [_public(t) for t in trades] for name, trades in out.items() if "_all" not in name},
            "sizing": {"risk_per_pick": RISK_PER_PICK, "pick_min": PICK_MIN, "pick_max": PICK_MAX, "book_cap": BOOK_CAP,
                       "risk_per_index_trade": RISK_PER_INDEX_TRADE, "index_book_cap": INDEX_BOOK_CAP,
                       "capped_days": dict(sizing["capped_days"]), "skipped_index": sizing["skipped_index"],
                       "fallback_vol": sizing["fallback_vol"], "no_lot": sizing["no_lot"]},
            "costs": {"brokerage_round_trip": BROKERAGE_ROUND_TRIP, "charges_buy": CHARGES_BUY,
                      "charges_sell": CHARGES_SELL, "slippage_each_side": SLIPPAGE}}


def _coin_flip_trade(history: sqlite3.Connection, day: str, root: str, expiry: str, lot: int) -> dict | None:
    """Half a lot each of the ATM call and put of the same expiry, held: a side picked at random."""
    best: dict[int, tuple] = {}
    for side, symbol, entry, r1, r2, r3, moneyness in history.execute(
            """SELECT side, symbol, entry_price, r1, r2, r3, json_extract(features, '$.o_moneyness') FROM agent_candidates
               WHERE day = ? AND underlying = ? AND expiry = ?""", (day, root, expiry)):
        if side not in best or abs(moneyness or 0) < abs(best[side][-1] or 0):
            best[side] = (symbol, entry, r1, r2, r3, moneyness)
    if len(best) < 2:
        return None
    ce, pe = best[1], best[-1]
    value = lot * (ce[1] + pe[1]) / 2
    paths = [None if a is None or b is None else (ce[1] * a + pe[1] * b) / (ce[1] + pe[1])
             for a, b in zip(ce[2:5], pe[2:5])]
    return _hold_trade(day, f"{ce[0]} + {pe[0]}", value, 1.0, paths, "index_coin")


def _public(trade: dict) -> dict:
    pnl = trade["qty"] * (trade["exit"] - trade["entry"])
    return {"entry_day": trade["entry_day"], "symbol": trade["symbol"], "entry": round(trade["entry"], 2),
            "exit_day": trade["exit_day"], "exit": round(trade["exit"], 2), "qty": round(trade["qty"], 2),
            "pnl": round(pnl - cost(trade["qty"] * trade["entry"], trade["qty"] * trade["exit"]), 2),
            "reason": trade["reason"], "complete": trade["complete"]}
