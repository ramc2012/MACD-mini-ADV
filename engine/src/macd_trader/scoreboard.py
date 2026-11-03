"""One scoreboard for every paper book (review 3 Nov 2026: eight lanes reported in eight formats).

Each book is reduced to the same pieces: exit slices (one row per exit, a staged exit being several), the
equity curve, and the book as it stands now. ``score_book`` turns those into the same numbers for every lane:
- equity, realised, unrealised and return;
- maximum drawdown over the whole stored curve;
- closed-trade PF, win rate and average win / loss (a trade runs from its first entry until fully flat);
- weekly realised P&L by IST exit week, and the net with the best week left out;
- exit slices by reason and hold time;
- whether the book can still take a new entry.
Read-only: the in-process books through their repositories, the desk (MP) and shadow (Vanguard, CBE) books
through read-only SQLite connections.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
HOLD_BUCKETS = ((1.0, "<1h"), (24.0, "1h-1d"), (48.0, "1-2d"), (float("inf"), ">2d"))


def _at(value) -> datetime | None:
    if not value:
        return None
    try:
        moment = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=IST)


def hold_bucket(entry, exit_) -> str:
    start, end = _at(entry), _at(exit_)
    if start is None or end is None:
        return "?"
    hours = (end - start).total_seconds() / 3600
    return next(label for limit, label in HOLD_BUCKETS if hours < limit)


def week_of(moment) -> str | None:
    stamp = _at(moment)
    if stamp is None:
        return None
    day = stamp.astimezone(IST).date()
    return (day - timedelta(days=day.weekday())).isoformat()


def stats(pnls: list[float]) -> dict:
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_loss = -sum(losses)
    return {"trades": len(pnls), "wins": len(wins), "win_rate": len(wins) / len(pnls) if pnls else None,
            "net": sum(pnls), "profit_factor": sum(wins) / gross_loss if gross_loss > 0 else None,
            "avg_win": sum(wins) / len(wins) if wins else None,
            "avg_loss": sum(losses) / len(losses) if losses else None}


def max_drawdown(curve, capital: float) -> tuple[float, float]:
    """(rupees, fraction of the peak) over a chronological iterable of equity values.

    The peak starts at the first stored point, not today's capital: the MACD book ran Rs 50 L until its capital
    was raised to Rs 1 Cr on 12 Oct 2026, and measuring from Rs 1 Cr read that as a 50% drawdown.
    """
    peak, worst, worst_pct = None, 0.0, 0.0
    for equity in curve:
        if equity is None:
            continue
        peak = equity if peak is None else max(peak, equity)
        if peak - equity > worst:
            worst, worst_pct = peak - equity, (peak - equity) / peak if peak > 0 else 0.0
    return worst, worst_pct


def score_book(name: str, label: str, *, slices: list[dict], curve, capital: float, now: dict,
               ticket: float | None = None, slots: int | None = None, note: str | None = None) -> dict:
    """slices: {position_id, entry_time, exit_time, pnl, exit_reason, partial}; now: equity/cash/realized/unrealized/open."""
    trades: dict[str, dict] = {}
    for row in sorted(slices, key=lambda r: str(r.get("exit_time") or "")):
        trade = trades.setdefault(str(row.get("position_id")), {"pnl": 0.0, "closed": False, "exit_time": None})
        trade["pnl"] += float(row.get("pnl") or 0.0)
        trade["closed"] = not row.get("partial")
        trade["exit_time"] = row.get("exit_time")
    closed = [t for t in trades.values() if t["closed"]]
    weekly: dict[str, float] = {}
    by_reason: dict[tuple[str, str], list[float]] = {}
    for row in slices:
        week = week_of(row.get("exit_time"))
        if week:
            weekly[week] = weekly.get(week, 0.0) + float(row.get("pnl") or 0.0)
        key = (str(row.get("exit_reason") or "?"), hold_bucket(row.get("entry_time"), row.get("exit_time")))
        by_reason.setdefault(key, []).append(float(row.get("pnl") or 0.0))
    realised = sum(weekly.values())
    best = max(weekly.items(), key=lambda item: item[1]) if weekly else None
    dd, dd_pct = max_drawdown(curve, capital)
    equity, cash = float(now.get("equity") or 0.0), float(now.get("cash") or 0.0)
    held = int(now.get("open") or 0)
    full = None
    if slots:
        full = held >= slots
    if ticket:
        full = bool(full) or cash < ticket
    return {
        "lane": name, "label": label, "capital": capital, "equity": equity, "cash": cash,
        "return_pct": (equity / capital - 1) * 100 if capital else None,
        "realized": float(now.get("realized") if now.get("realized") is not None else realised),
        "unrealized": float(now.get("unrealized") or 0.0), "open_positions": held,
        "max_drawdown": dd, "max_drawdown_pct": dd_pct * 100,
        "closed": stats([t["pnl"] for t in closed]),
        "weekly": [{"week": week, "net": round(net, 2)} for week, net in sorted(weekly.items())],
        "best_week": {"week": best[0], "net": round(best[1], 2)} if best else None,
        "net_without_best_week": round(realised - best[1], 2) if best else None,
        "exits": [{"reason": reason, "hold": hold, **stats(pnls)} for (reason, hold), pnls in sorted(by_reason.items())],
        "book_full": full, "ticket": ticket, "slots": slots, "note": note,
        "since": min((str(r.get("entry_time")) for r in slices if r.get("entry_time")), default=None),
    }


# ------------------------------------------------------------------ adapters

def repository_slices(repository, lane: str | None = None) -> list[dict]:
    rows = repository.closed_positions(lane=lane, limit=1_000_000)
    return [{"position_id": r.get("position_id"), "entry_time": r.get("entry_time"), "exit_time": r.get("exit_time"),
             "pnl": r.get("realized_pnl"), "exit_reason": r.get("exit_reason"), "partial": r.get("partial")} for r in rows]


def sqlite_rows(path: str | Path, sql: str) -> list[sqlite3.Row]:
    if not Path(path).exists():
        return []
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        return connection.execute(sql).fetchall()
    finally:
        connection.close()


def equity_curve(path: str | Path) -> list[float]:
    return [float(r["equity"]) for r in sqlite_rows(path, "SELECT equity FROM equity_points ORDER BY timestamp")]


def fifo_slices(trades: list[dict], reasons: dict | None = None) -> list[dict]:
    """Exit slices from a fill log, first in first out per symbol, fees on both legs.

    For a book whose closed-position records are not complete (the desk keeps mp_closed_positions for the last
    sessions only). ``reasons`` maps (symbol, exit second in UTC) to the recorded exit reason.
    """
    lots: dict[str, list[list]] = {}
    opened: dict[str, str] = {}
    out = []
    for row in sorted(trades, key=lambda r: str(r.get("timestamp"))):
        symbol, quantity, price = row["symbol"], int(row.get("quantity") or 0), float(row.get("price") or 0.0)
        fee = float(row.get("fees") or 0.0)
        book = lots.setdefault(symbol, [])
        if row.get("side") == "BUY":
            if not book:
                opened[symbol] = str(row.get("timestamp"))
            book.append([quantity, price, fee / quantity if quantity else 0.0])
            continue
        pnl, left = -fee, quantity
        while left > 0 and book:
            lot = book[0]
            take = min(left, lot[0])
            pnl += (price - lot[1]) * take - lot[2] * take
            lot[0] -= take
            left -= take
            if lot[0] == 0:
                book.pop(0)
        stamp = _at(row.get("timestamp"))
        key = (symbol, stamp.astimezone(IST).replace(microsecond=0).isoformat() if stamp else None)
        out.append({"position_id": f"{symbol}|{opened.get(symbol)}", "entry_time": opened.get(symbol),
                    "exit_time": row.get("timestamp"), "pnl": pnl, "exit_reason": (reasons or {}).get(key),
                    "partial": bool(book)})
    return out


def mp_book(path: str | Path, capital: float) -> dict:
    """The auction desk's book, rebuilt from its fill log (mp_closed_positions holds only recent sessions)."""
    reasons = {}
    for row in sqlite_rows(path, "SELECT payload FROM mp_closed_positions"):
        item = json.loads(row["payload"])
        stamp = _at(item.get("exit_time"))
        if stamp is not None:
            reasons[(item.get("symbol"), stamp.astimezone(IST).replace(microsecond=0).isoformat())] = item.get("exit_reason")
    trades = [json.loads(r["payload"]) for r in sqlite_rows(path, "SELECT payload FROM trades")]
    slices = fifo_slices(trades, reasons)
    last = sqlite_rows(path, "SELECT equity, cash, realized_pnl, unrealized_pnl FROM equity_points ORDER BY timestamp DESC LIMIT 1")
    now = dict(last[0]) if last else {"equity": capital, "cash": capital}
    return {"slices": slices, "curve": equity_curve(path),
            "now": {"equity": now.get("equity"), "cash": now.get("cash"), "realized": now.get("realized_pnl"),
                    "unrealized": now.get("unrealized_pnl"), "open": 0}}


def shadow_book(path: str | Path) -> dict | None:
    """A Vanguard / CBE book (shadow_lanes paper schema: paise, IST stamps)."""
    account = sqlite_rows(path, "SELECT initial_paise, cash_paise, policy FROM account LIMIT 1")
    if not account:
        return None
    capital = account[0]["initial_paise"] / 100
    slices = [{"position_id": r["id"], "entry_time": r["opened_at"], "exit_time": r["closed_at"],
               "pnl": (r["pnl_paise"] or 0) / 100, "exit_reason": r["exit_reason"], "partial": False}
              for r in sqlite_rows(path, "SELECT id, opened_at, closed_at, exit_reason, pnl_paise FROM positions "
                                         "WHERE closed_at IS NOT NULL")]
    open_rows = sqlite_rows(path, "SELECT quantity, entry, last_bid FROM positions WHERE closed_at IS NULL")
    curve = [r["equity_paise"] / 100 for r in sqlite_rows(path, "SELECT equity_paise FROM equity ORDER BY at")]
    cash = account[0]["cash_paise"] / 100
    marked = sum((r["last_bid"] or r["entry"] or 0) * (r["quantity"] or 0) for r in open_rows)
    cost = sum((r["entry"] or 0) * (r["quantity"] or 0) for r in open_rows)
    try:
        policy = json.loads(account[0]["policy"] or "{}")
    except ValueError:
        policy = {}
    return {"capital": capital, "slices": slices, "curve": curve, "slots": policy.get("max_positions"),
            "now": {"equity": cash + marked, "cash": cash, "realized": sum(s["pnl"] for s in slices),
                    "unrealized": marked - cost, "open": len(open_rows)}}


def portfolio_now(portfolio) -> dict:
    snap = portfolio.snapshot()
    return {"equity": snap["equity"], "cash": snap["cash"], "realized": snap["realized_pnl"],
            "unrealized": snap["unrealized_pnl"], "open": sum(1 for p in portfolio.positions.values() if p.quantity > 0)}


def build(engine) -> dict:
    """Every book, scored alike. Runs in a worker thread: it reads whole histories."""
    settings = engine.settings
    data = Path(settings.database_path).parent
    lanes = []

    def add(fn):
        try:
            lanes.append(fn())
        except Exception as exc:  # noqa: BLE001 -- one unreadable book must not blank the board
            lanes.append({"lane": getattr(fn, "__name__", "?"), "error": f"{type(exc).__name__}: {exc}"[:200]})

    def macd():
        return score_book("macd", "MACD lane", slices=repository_slices(engine.repository),
                          curve=equity_curve(settings.database_path), capital=engine.portfolio.initial_capital,
                          now=portfolio_now(engine.portfolio))
    add(macd)
    from .smart_lane import lane_slots
    slots = lane_slots(getattr(settings, "smart_lane_slots_csv", ""))
    for lane in engine.smart_lanes.lanes:
        add(lambda lane=lane: score_book(
            lane.name, "Smart " + lane.name.split("_", 1)[-1].upper(),
            slices=repository_slices(lane.repository, lane.name), curve=equity_curve(lane.database_path),
            capital=lane.capital, now=portfolio_now(lane.portfolio), slots=slots.get(lane.capital)))
    spot = getattr(engine, "spot_lane", None)
    if spot is not None:
        add(lambda: score_book("spot_macd", "Spot MACD", slices=repository_slices(spot.repository, spot.name),
                               curve=equity_curve(spot.database_path), capital=spot.capital,
                               now=portfolio_now(spot.portfolio), ticket=settings.spot_lane_ticket,
                               note="not proven: long-only spot MACD tracked the market in research"))

    def blast():
        return score_book("blast", "Blast", slices=repository_slices(engine.blast.repository),
                          curve=equity_curve(settings.blast_database_path),
                          capital=engine.blast.portfolio.initial_capital, now=portfolio_now(engine.blast.portfolio),
                          ticket=settings.blast_target_notional, note="reset 29 Oct 2026 (HMA×KAMA)")
    add(blast)
    agentic = getattr(engine, "agentic", None)
    if agentic is not None:
        add(lambda: score_book("agentic", "Agentic (Grok)", slices=repository_slices(agentic.repository),
                               curve=equity_curve(Path(data) / "agentic_trader.sqlite3"),
                               capital=agentic.portfolio.initial_capital, now=portfolio_now(agentic.portfolio)))

    def auction():
        book = mp_book(settings.mp_database_path, settings.mp_initial_capital)
        return score_book("mp", "Auction / MP", capital=settings.mp_initial_capital, **book,
                          note=None if settings.mp_auto_trade else "shadow since 3 Nov 2026: signals logged, no paper entries")
    add(auction)
    shadow_dir = Path(settings.runtime_settings_path).parent / "paper_lanes"
    for name, label in (("vanguard", "Vanguard"), ("cbe", "CBE")):
        def shadow(name=name, label=label):
            book = shadow_book(shadow_dir / f"{name}.sqlite3")
            if book is None:
                return {"lane": name, "label": label, "error": "no paper book"}
            return score_book(name, label, slices=book["slices"], curve=book["curve"], capital=book["capital"],
                              now=book["now"], slots=book["slots"], note="shadow process book")
        add(shadow)
    return {"as_of": datetime.now(IST).isoformat(timespec="seconds"), "lanes": lanes,
            "hold_buckets": [label for _, label in HOLD_BUCKETS]}
