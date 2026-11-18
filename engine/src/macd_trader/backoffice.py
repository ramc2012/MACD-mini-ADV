"""Back office: every lane's history, day-wise, monthly and yearly (owner, 18 Nov 2026).

Lane pages now show only the lane day (market_calendar.lane_day_start); this is where the old book went. Nothing is
copied: each report is computed from the lanes' own books on request (the same read-only adapters as scoreboard.py, so
a lane's realised total here equals the scoreboard's to the rupee) and cached for a minute. The books are never
written or trimmed.

Per lane:
  trades   one row per exit slice (a staged exit is several): symbol, side, quantity, entry/exit time and price,
           P&L, exit reason, IST exit day. "Trades" in the summaries count positions once fully flat.
  daily    per IST day: realised P&L of the day's exits, equity at the day's last mark, the day's change in equity with
           capital top-ups netted out (so open marks count), closed trades, wins, gross win and loss.
  periods  months and years rolled up from the days: P&L, return on the period's opening equity, best and worst
           day, the deepest fall from a peak inside the period.

The Upstox live lane is REAL money: it is reported in its own section ("kind": "real") and never added into the
paper totals.
"""
from __future__ import annotations

import json
import re
import threading
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import scoreboard as sb

IST = ZoneInfo("Asia/Kolkata")
CACHE_SECONDS = 60
SPREAD_CACHE_SECONDS = 600


def ist_day(value) -> str | None:
    moment = sb._at(value)
    return moment.astimezone(IST).date().isoformat() if moment else None


def _num(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _kind_of(symbol: str) -> str:
    text = str(symbol or "")
    for suffix in ("CE", "PE", "FUT"):
        if text.endswith(suffix):
            return suffix
    return "EQ"


# ------------------------------------------------------------------ one lane's ledger

def repository_trades(repository, lane: str | None = None) -> list[dict]:
    """Exit slices with their contract details, from a TradeRepository's closed positions (same rows as scoreboard)."""
    out = []
    for r in repository.closed_positions(lane=lane, limit=1_000_000):
        out.append({"position_id": r.get("position_id"), "symbol": r.get("symbol"), "side": r.get("side") or "LONG",
                    "quantity": r.get("quantity"), "lots": r.get("lots"), "entry_time": r.get("entry_time"),
                    "entry_price": r.get("entry_price"), "exit_time": r.get("exit_time"), "exit_price": r.get("exit_price"),
                    "pnl": _num(r.get("realized_pnl")), "fees": _num(r.get("fees")), "exit_reason": r.get("exit_reason"),
                    "partial": bool(r.get("partial")), "return_pct": r.get("return_pct")})
    return out


def repository_curve(repository) -> tuple[list[tuple[str, float]], float | None, list[tuple[str, float]]]:
    """Equity marks, the starting capital and every capital change, read off the marks themselves.

    Each mark stores equity, realised and unrealised P&L, so equity - realised - unrealised is the capital base at
    that moment. Top-ups were not always logged (MACD 50 L -> 1 Cr on 12 Oct 2026, MCX 10 L -> 25 L on 17 Nov 2026
    were settings edits), so a step in that base is the change, and it is netted out of the day's P&L.
    """
    rows = repository.equity_rows(10_000_000)
    curve = [(r["timestamp"], _num(r["equity"])) for r in rows]
    start, base, changes = None, None, []
    for r in rows:
        level = round(_num(r["equity"]) - _num(r.get("realized_pnl")) - _num(r.get("unrealized_pnl")))
        if base is None:
            start = base = level
        elif abs(level - base) > 1:
            changes.append((r["timestamp"], float(level - base)))
            base = level
    return curve, (float(start) if start is not None else None), changes


def daily(trades: list[dict], curve: list[tuple[str, float]], capital: float,
          capital_changes: list[tuple[str, float]] = ()) -> list[dict]:
    """Per IST day. Equity change nets out capital added that day; days with exits but no mark use realised P&L."""
    days: dict[str, dict] = defaultdict(lambda: {"realized": 0.0, "trades": 0, "wins": 0, "gross_win": 0.0,
                                                 "gross_loss": 0.0, "slices": 0, "fees": 0.0})
    positions: dict[str, dict] = {}
    for row in sorted(trades, key=lambda r: str(r.get("exit_time") or "")):
        day = ist_day(row.get("exit_time"))
        if not day:
            continue
        d = days[day]
        d["realized"] += row["pnl"]
        d["fees"] += _num(row.get("fees"))
        d["slices"] += 1
        p = positions.setdefault(str(row.get("position_id")), {"pnl": 0.0})
        p["pnl"] += row["pnl"]
        if not row.get("partial"):
            d["trades"] += 1
            d["wins"] += p["pnl"] > 0
            if p["pnl"] > 0:
                d["gross_win"] += p["pnl"]
            else:
                d["gross_loss"] += p["pnl"]
            positions.pop(str(row.get("position_id")), None)
    marks: dict[str, float] = {}
    for at, equity in curve:
        day = ist_day(at)
        if day:
            marks[day] = equity                       # the curve is in time order: the day's last mark wins
    added: dict[str, float] = defaultdict(float)
    for at, delta in capital_changes:
        day = ist_day(at)
        if day:
            added[day] += delta
    out, previous = [], capital
    for day in sorted(set(days) | set(marks)):
        d = days.get(day) or {"realized": 0.0, "trades": 0, "wins": 0, "gross_win": 0.0, "gross_loss": 0.0,
                              "slices": 0, "fees": 0.0}
        equity = marks.get(day)
        if equity is None:                             # no mark that day (a book without a curve): realised only
            equity = previous + d["realized"] + added.get(day, 0.0)
        change = equity - previous - added.get(day, 0.0)
        if not d["slices"] and abs(change) < 1 and not added.get(day):
            previous = equity
            continue                                   # a weekend or idle day's marks: nothing happened
        out.append({"day": day, "pnl": round(change, 2), "realized": round(d["realized"], 2),
                    "unrealized_change": round(change - d["realized"], 2), "equity": round(equity, 2),
                    "capital_added": round(added.get(day, 0.0), 2), "trades": d["trades"], "wins": d["wins"],
                    "gross_win": round(d["gross_win"], 2), "gross_loss": round(d["gross_loss"], 2),
                    "exits": d["slices"], "fees": round(d["fees"], 2)})
        previous = equity
    return out


_UNDERLYING = re.compile(r"^(?:[A-Z]+:)?([A-Z&-]+?)(?=\d|-EQ$|-INDEX$|$)")


def underlying_of(symbol) -> str:
    match = _UNDERLYING.match(str(symbol or "").upper())
    return match.group(1) if match else str(symbol or "")


def splits(trades: list[dict]) -> dict:
    """P&L of the trades split by underlying, CE/PE/FUT, exit reason and hold time (positions counted once flat)."""
    out: dict[str, dict[str, dict]] = {"underlying": {}, "kind": {}, "exit_reason": {}, "hold": {}}
    positions: dict[str, float] = defaultdict(float)
    for row in sorted(trades, key=lambda r: str(r.get("exit_time") or "")):
        key = str(row.get("position_id"))
        positions[key] += row["pnl"]
        keys = {"underlying": underlying_of(row.get("symbol")), "kind": _kind_of(row.get("symbol")),
                "exit_reason": str(row.get("exit_reason") or "unknown").lower(),
                "hold": sb.hold_bucket(row.get("entry_time"), row.get("exit_time"))}
        flat = not row.get("partial")
        for name, value in keys.items():
            cell = out[name].setdefault(value, {"key": value, "net": 0.0, "trades": 0, "wins": 0, "gross_win": 0.0,
                                                "gross_loss": 0.0})
            cell["net"] += row["pnl"]
            if flat:
                pnl = positions[key]
                cell["trades"] += 1
                cell["wins"] += pnl > 0
                cell["gross_win" if pnl > 0 else "gross_loss"] += pnl
        if flat:
            positions.pop(key, None)
    result = {}
    for name, cells in out.items():
        rows = []
        for c in cells.values():
            rows.append({"key": c["key"], "net": round(c["net"], 2), "trades": c["trades"],
                         "win_rate": round(c["wins"] / c["trades"] * 100, 1) if c["trades"] else None,
                         "profit_factor": round(c["gross_win"] / -c["gross_loss"], 2) if c["gross_loss"] < 0 else None})
        result[name] = sorted(rows, key=lambda r: r["net"], reverse=True)
    return result


def roll_up(days: list[dict], capital: float, width: int) -> list[dict]:
    """Months (width 7: YYYY-MM) or years (width 4) from the day rows."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for d in days:
        groups[d["day"][:width]].append(d)
    out, opening = [], capital
    for period in sorted(groups):
        rows = groups[period]
        pnl = sum(r["pnl"] for r in rows)
        win, loss = sum(r["gross_win"] for r in rows), sum(r["gross_loss"] for r in rows)
        trades, wins = sum(r["trades"] for r in rows), sum(r["wins"] for r in rows)
        added = sum(r["capital_added"] for r in rows)
        peak, deepest = opening + added, 0.0
        for r in rows:
            peak = max(peak, r["equity"])
            deepest = min(deepest, r["equity"] - peak)
        base = opening + added
        out.append({"period": period, "pnl": round(pnl, 2), "realized": round(sum(r["realized"] for r in rows), 2),
                    "return_pct": round(pnl / base * 100, 2) if base else None,
                    "opening_equity": round(opening, 2), "closing_equity": rows[-1]["equity"],
                    "capital_added": round(added, 2), "trades": trades, "wins": wins,
                    "win_rate": round(wins / trades * 100, 1) if trades else None,
                    "profit_factor": round(win / -loss, 2) if loss < 0 else None,
                    "best_day": max(rows, key=lambda r: r["pnl"])["day"], "best_day_pnl": max(r["pnl"] for r in rows),
                    "worst_day": min(rows, key=lambda r: r["pnl"])["day"], "worst_day_pnl": min(r["pnl"] for r in rows),
                    "max_drawdown": round(deepest, 2), "sessions": len(rows)})
        opening = rows[-1]["equity"]
    return out


def lane_report(book: dict) -> dict:
    days = daily(book["trades"], book["curve"], book["capital"], book.get("capital_changes") or [])
    closed = defaultdict(float)
    finished = set()
    for row in book["trades"]:
        closed[str(row.get("position_id"))] += row["pnl"]
        if not row.get("partial"):
            finished.add(str(row.get("position_id")))
    pnls = [closed[p] for p in finished]
    return {"lane": book["lane"], "label": book["label"], "kind": book.get("kind", "paper"),
            "capital": book.get("capital_now", book["capital"]), "starting_capital": book["capital"],
            "capital_added": round(sum(delta for _, delta in book.get("capital_changes") or []), 2),
            "since": min((d["day"] for d in days), default=None), "note": book.get("note"),
            "realized": round(sum(r["pnl"] for r in book["trades"]), 2),
            "equity": days[-1]["equity"] if days else book["capital"],
            "closed": sb.stats(pnls), "days": days,
            "months": roll_up(days, book["capital"], 7), "years": roll_up(days, book["capital"], 4)}


# ------------------------------------------------------------------ every lane

def books(engine) -> list[dict]:
    """Each lane's trades, equity curve and capital, read-only. One unreadable book never blanks the rest."""
    settings = engine.settings
    out: list[dict] = []

    def add(lane: str, label: str, fn) -> None:
        try:
            out.append({"lane": lane, "label": label, **fn()})
        except Exception as exc:  # noqa: BLE001
            out.append({"lane": lane, "label": label, "error": f"{type(exc).__name__}: {exc}"[:200],
                        "trades": [], "curve": [], "capital": 0.0})

    def repo_book(repository, capital, lane=None, **extra):
        curve, start, changes = repository_curve(repository)
        if start is None:                       # no marks yet: the configured capital and the logged changes
            start, changes = float(capital), repository.capital_changes()
        return {"trades": repository_trades(repository, lane), "curve": curve, "capital": start,
                "capital_now": float(capital), "capital_changes": changes, **extra}

    add("macd", "MACD lane", lambda: repo_book(engine.repository, engine.portfolio.initial_capital))
    for lane in engine.smart_lanes.lanes:
        add(lane.name, "Smart " + lane.name.split("_", 1)[-1].upper(),
            lambda lane=lane: repo_book(lane.repository, lane.capital, lane.name))
    spot = getattr(engine, "spot_lane", None)
    if spot is not None:
        add("spot_macd", "Spot MACD", lambda: repo_book(spot.repository, spot.capital, spot.name,
                                                        note="stock futures long + short"))
    trend = getattr(engine, "trend_lane", None)
    if trend is not None:
        add("trend_index", "Trend index", lambda: repo_book(trend.repository, trend.capital, trend.name,
                                                            note="index options on trend states"))
    add("blast", "Blast", lambda: repo_book(engine.blast.repository, engine.blast.portfolio.initial_capital))
    agentic = getattr(engine, "agentic", None)
    if agentic is not None:
        add("agentic", "Agentic (Grok)", lambda: repo_book(agentic.repository, agentic.portfolio.initial_capital))

    def auction():
        book = sb.mp_book(settings.mp_database_path, settings.mp_initial_capital)
        trades = [{**s, "symbol": str(s.get("position_id") or "").split("|")[0], "side": "LONG", "pnl": _num(s["pnl"])}
                  for s in book["slices"]]
        return {"trades": trades, "curve": book["curve"], "capital": float(settings.mp_initial_capital)}
    add("mp", "Auction / MP", auction)
    shadow_dir = Path(settings.runtime_settings_path).parent / "paper_lanes"
    for name, label in (("vanguard", "Vanguard"), ("cbe", "CBE")):
        def shadow(name=name):
            book = sb.shadow_book(shadow_dir / f"{name}.sqlite3")
            if book is None:
                raise FileNotFoundError("no paper book")
            # shadow_lanes paper schema: a position shares its id with the order that opened it (which has the symbol)
            symbols = {r["id"]: r for r in sb.sqlite_rows(
                shadow_dir / f"{name}.sqlite3",
                "SELECT p.id, o.symbol, p.quantity, p.entry, p.exit FROM positions p LEFT JOIN orders o ON o.id = p.id")}
            trades = []
            for s in book["slices"]:
                row = symbols.get(s["position_id"])
                trades.append({**s, "pnl": _num(s["pnl"]), "side": "LONG",
                               "symbol": row["symbol"] if row else None, "quantity": row["quantity"] if row else None,
                               "entry_price": row["entry"] if row else None, "exit_price": row["exit"] if row else None})
            return {"trades": trades, "curve": book["curve"], "capital": book["capital"]}
        add(name, label, shadow)
    mcx = getattr(engine, "mcx", None)
    if mcx is not None:
        add("mcx", "MCX (buying)", lambda: repo_book(mcx.repository, mcx.portfolio.initial_capital))

        def selling():
            trades = []
            for p in mcx.shorts.store.load():
                if p.status != "CLOSED":
                    continue
                trades.append({"position_id": p.short_id, "symbol": p.symbol, "side": "SHORT", "quantity": p.quantity,
                               "lots": p.lots, "entry_time": p.entry_at, "entry_price": p.entry_price,
                               "exit_time": p.exit_at, "exit_price": p.exit_price, "pnl": _num(p.pnl),
                               "exit_reason": p.exit_reason, "partial": False})
            return {"trades": trades, "curve": [], "capital": float(settings.mcx_short_capital),
                    "note": "option selling: equity from realised P&L (no stored marks)"}
        add("mcx_short", "MCX (selling)", selling)

    def upstox():
        path = Path(settings.runtime_settings_path).parent / "upstox_live" / "state.json"
        state = json.loads(path.read_text()) if path.exists() else {}
        trades = []
        for i, c in enumerate(state.get("closed") or []):
            trades.append({"position_id": f"{c.get('symbol')}|{c.get('opened_at') or i}", "symbol": c.get("symbol"),
                           "side": "LONG", "quantity": c.get("quantity"), "entry_time": c.get("opened_at"),
                           "entry_price": c.get("average_price"), "exit_time": c.get("closed_at"),
                           "exit_price": c.get("exit_price"), "pnl": _num(c.get("realized")),
                           "exit_reason": c.get("exit_reason") or "CLOSED", "partial": False})
        return {"trades": trades, "curve": [], "capital": 0.0, "kind": "real",
                "note": "REAL money (Upstox); before charges; equity is realised P&L only"}
    add("upstox_live", "Upstox live (REAL)", upstox)
    for book in out:
        for row in book["trades"]:
            row["day"] = ist_day(row.get("exit_time"))
            row["type"] = _kind_of(row.get("symbol"))
    return out


class BackOffice:
    """Cached reports over every lane's book (rebuilt at most once a minute, in a worker thread)."""

    def __init__(self, engine):
        self.engine = engine
        self._lock = threading.Lock()
        self._at = 0.0
        self._books: list[dict] | None = None
        self._reports: dict[str, dict] = {}
        self._spread: dict[tuple, tuple[float, dict]] = {}

    def _load(self, refresh: bool = False) -> None:
        with self._lock:
            if refresh or self._books is None or time.monotonic() - self._at > CACHE_SECONDS:
                self._books = books(self.engine)
                self._reports = {b["lane"]: lane_report(b) if not b.get("error") else
                                 {"lane": b["lane"], "label": b["label"], "error": b["error"]} for b in self._books}
                self._at = time.monotonic()

    def summary(self, refresh: bool = False) -> dict:
        """Every lane: totals and its months and years; days are fetched per lane (they are the long part)."""
        self._load(refresh)
        lanes = []
        for report in self._reports.values():
            lanes.append({k: v for k, v in report.items() if k != "days"} | {"sessions": len(report.get("days") or [])})
        return {"as_of": datetime.now(IST).isoformat(timespec="seconds"), "lanes": lanes,
                "cache_seconds": CACHE_SECONDS}

    def lane(self, lane: str, start: str | None = None, end: str | None = None) -> dict:
        self._load()
        report = self._reports.get(lane)
        if report is None:
            raise KeyError(lane)
        days = [d for d in report.get("days") or [] if (not start or d["day"] >= start) and (not end or d["day"] <= end)]
        book = next((b for b in self._books or [] if b["lane"] == lane), None)
        trades = [t for t in (book or {}).get("trades") or []
                  if (not start or (t.get("day") or "") >= start) and (not end or (t.get("day") or "") <= end)]
        return {**report, "days": days, "splits": splits(trades)}

    def calendar(self, period: str = "day", start: str | None = None, end: str | None = None) -> dict:
        """Lane x period matrix of P&L (paper lanes, then the real lane separately)."""
        self._load()
        width = {"day": 10, "month": 7, "year": 4}[period]
        cells: dict[str, dict[str, float]] = {}
        for lane, report in self._reports.items():
            rows = report.get("days") or []
            per: dict[str, float] = defaultdict(float)
            for d in rows:
                if (start and d["day"] < start) or (end and d["day"] > end):
                    continue
                per[d["day"][:width]] += d["pnl"]
            cells[lane] = {k: round(v, 2) for k, v in per.items()}
        periods = sorted({p for row in cells.values() for p in row}, reverse=True)
        return {"period": period, "periods": periods,
                "lanes": [{"lane": lane, "label": self._reports[lane].get("label"),
                           "kind": self._reports[lane].get("kind", "paper"), "cells": cells[lane]} for lane in cells]}

    def trades(self, lane: str | None = None, start: str | None = None, end: str | None = None,
               symbol: str | None = None, page: int = 1, size: int = 100) -> dict:
        self._load()
        rows = []
        for book in self._books or []:
            if lane and book["lane"] != lane:
                continue
            for row in book["trades"]:
                day = row.get("day") or ""
                if (start and day < start) or (end and day > end):
                    continue
                if symbol and symbol.upper() not in str(row.get("symbol") or "").upper():
                    continue
                rows.append({"lane": book["lane"], **row})
        rows.sort(key=lambda r: str(r.get("exit_time") or ""), reverse=True)
        total = len(rows)
        page_rows = rows[(page - 1) * size: page * size]
        return {"total": total, "page": page, "size": size, "rows": page_rows,
                "net": round(sum(r["pnl"] for r in rows), 2)}

    def orders(self, lane: str, start: str | None = None, end: str | None = None, page: int = 1, size: int = 200) -> dict:
        """One lane's order log for a date range (paged in SQL; the MACD log alone is 50,000+ rows)."""
        repository = self._repository(lane)
        if repository is None:
            raise KeyError(lane)
        lo = datetime.fromisoformat(f"{start}T00:00:00+05:30").astimezone(ZoneInfo("UTC")).isoformat() if start else ""
        hi = datetime.fromisoformat(f"{end}T23:59:59+05:30").astimezone(ZoneInfo("UTC")).isoformat() if end else "9999"
        with repository._lock:
            total = repository._connection.execute("SELECT COUNT(*) FROM orders WHERE created_at >= ? AND created_at <= ?",
                                                   (lo, hi)).fetchone()[0]
            rows = repository._connection.execute(
                "SELECT payload FROM orders WHERE created_at >= ? AND created_at <= ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (lo, hi, size, (page - 1) * size)).fetchall()
        return {"lane": lane, "total": int(total), "page": page, "size": size,
                "rows": [json.loads(r["payload"]) for r in rows]}

    def spread(self, start: str | None = None, end: str | None = None) -> dict:
        """The spread-aware second mark per lane (spread_mark.py), cached ten minutes per range."""
        from . import spread_mark
        self._load()
        key = (start, end)
        hit = self._spread.get(key)
        if hit and time.monotonic() - hit[0] < SPREAD_CACHE_SECONDS:
            return hit[1]
        value = spread_mark.report(self._books or [], self.engine.settings.tick_database_path, start, end,
                                   self.engine.settings.research_database_path)
        self._spread = {k: v for k, v in self._spread.items() if time.monotonic() - v[0] < SPREAD_CACHE_SECONDS}
        self._spread[key] = (time.monotonic(), value)
        return value

    def _repository(self, lane: str):
        e = self.engine
        if lane == "macd":
            return e.repository
        for smart in e.smart_lanes.lanes:
            if smart.name == lane:
                return smart.repository
        spot = getattr(e, "spot_lane", None)
        if spot is not None and lane == "spot_macd":
            return spot.repository
        trend = getattr(e, "trend_lane", None)
        if trend is not None and lane == "trend_index":
            return trend.repository
        if lane == "blast":
            return e.blast.repository
        if lane == "agentic" and getattr(e, "agentic", None) is not None:
            return e.agentic.repository
        if lane == "mcx" and getattr(e, "mcx", None) is not None:
            return e.mcx.repository
        return None
