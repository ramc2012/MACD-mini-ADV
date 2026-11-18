"""Spread-aware second mark for the back office (owner, 18 Nov 2026).

Paper fills are optimistic in different ways per lane: the ExecutionManager lanes (MACD, Smart, Spot, Blast, MCX
buying, Agentic) cross the quoted spread only when a two-sided quote rode on the same tick as the price
(execution._paper_price) and otherwise take last price +/- slippage_bps; MCX selling and the Auction desk charge a
fixed bps slippage; the Vanguard/CBE paper books fill at the displayed ask/bid. Rather than trust each lane's basis, every fill is
checked against the contract's own quoted spread at that minute (tick_minute_flow.avg_spread, the mean ask - bid
over the minute's ticks, stored since 8 Sep 2026; a contract the feed never streamed falls back to the stored option
chain's nearest two-sided quote, chain_snapshots in historical.sqlite3):

  charged  = what the recorded fill already paid beyond the minute's last price (buy: fill - close; sell: close - fill)
  extra    = max(0, half spread - max(0, charged)) x quantity, at entry and again at exit

``net_at_quote = net - extra`` is the lane's P&L had every fill crossed half the quoted spread: the second mark.
Fills with no stored minute within five minutes are counted as uncovered and get no haircut, so coverage is
reported beside the figure. Measurement only: no lane reads it and the books are never changed.
"""
from __future__ import annotations

import sqlite3
import statistics
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

from . import scoreboard as sb

IST = ZoneInfo("Asia/Kolkata")
WINDOW_SECONDS = 300


def _epoch(value) -> int | None:
    moment = sb._at(value)
    return int(moment.timestamp()) if moment else None


class SpreadBook:
    """Nearest stored minute (close, avg_spread) for a symbol and moment, from the tick store, read-only."""

    def __init__(self, path: str, chains_path: str | None = None):
        self.db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10, check_same_thread=False)
        self.chains = sqlite3.connect(f"file:{chains_path}?mode=ro", uri=True, timeout=10, check_same_thread=False) \
            if chains_path else None
        self._ids: dict[str, int | None] = {}

    def close(self) -> None:
        self.db.close()
        if self.chains is not None:
            self.chains.close()

    def chain_quote(self, symbol: str, at: int) -> tuple[float, float] | None:
        """Fallback for contracts the tick store never streamed: the stored option chain's nearest snapshot
        (index chains about once a minute, stock chains every 15 minutes), when it carried a two-sided quote."""
        if self.chains is None:
            return None
        from .option_agent import parse_option
        parsed = parse_option(symbol)
        if not parsed:
            return None
        try:
            row = self.chains.execute(
                """SELECT ltp, bid, ask FROM chain_snapshots WHERE underlying = ? AND ts BETWEEN ? AND ? AND symbol = ?
                   AND bid > 0 AND ask >= bid ORDER BY ABS(ts - ?) LIMIT 1""",
                (parsed["root"], at - WINDOW_SECONDS, at + WINDOW_SECONDS, symbol, at)).fetchone()
        except sqlite3.Error:
            return None
        if not row:
            return None
        ltp, bid, ask = row
        close = float(ltp) if ltp and ltp > 0 else (bid + ask) / 2
        spread = float(ask - bid)
        return (close, spread) if spread <= close else None

    def symbol_id(self, symbol: str) -> int | None:
        if symbol not in self._ids:
            row = self.db.execute("SELECT id FROM tick_symbols WHERE symbol = ?", (symbol,)).fetchone()
            self._ids[symbol] = int(row[0]) if row else None
        return self._ids[symbol]

    def quote(self, symbol: str, at: int) -> tuple[float, float] | None:
        sid = self.symbol_id(symbol)
        if sid is None:
            return self.chain_quote(symbol, at)
        minute = at - at % 60
        row = self.db.execute(
            """SELECT close, avg_spread FROM tick_minute_flow WHERE symbol_id = ? AND minute_ts BETWEEN ? AND ?
               AND avg_spread IS NOT NULL AND avg_spread >= 0 AND close > 0
               ORDER BY ABS(minute_ts - ?) LIMIT 1""",
            (sid, minute - WINDOW_SECONDS, minute + WINDOW_SECONDS, minute)).fetchone()
        if not row or row[1] is None or row[1] > row[0]:      # a spread wider than the price is a broken quote
            return self.chain_quote(symbol, at)
        return float(row[0]), float(row[1])


def leg_cost(fill: float, quote: tuple[float, float] | None, buy: bool, quantity: float) -> tuple[float, float, float] | None:
    """(charged, extra, spread % of price) for one fill, or None when no quote."""
    if quote is None or not fill or not quantity:
        return None
    close, spread = quote
    half = spread / 2
    paid = (fill - close) if buy else (close - fill)
    extra = max(0.0, half - max(0.0, paid))
    return paid * quantity, extra * quantity, spread / close * 100 if close else 0.0


def mark_trades(trades: list[dict], book: SpreadBook) -> list[dict]:
    """Per exit slice: entry and exit leg costs (None when not covered)."""
    out = []
    for t in trades:
        symbol, qty = t.get("symbol"), abs(float(t.get("quantity") or 0))
        short = str(t.get("side") or "LONG").upper() in ("SHORT", "SELL")
        entry = exit_ = None
        if symbol and qty:
            at = _epoch(t.get("entry_time"))
            if at:
                entry = leg_cost(float(t.get("entry_price") or 0), book.quote(symbol, at), not short, qty)
            at = _epoch(t.get("exit_time"))
            if at:
                exit_ = leg_cost(float(t.get("exit_price") or 0), book.quote(symbol, at), short, qty)
        out.append({"day": t.get("day") or "", "pnl": float(t.get("pnl") or 0), "entry": entry, "exit": exit_,
                    "partial": bool(t.get("partial")), "position_id": t.get("position_id")})
    return out


def summarize(marked: list[dict]) -> dict:
    """Totals and months for one lane."""
    def block(rows: list[dict]) -> dict:
        legs = [leg for r in rows for leg in (r["entry"], r["exit"])]
        covered = [leg for leg in legs if leg is not None]
        net = sum(r["pnl"] for r in rows)
        extra = sum(leg[1] for leg in covered)
        charged = sum(leg[0] for leg in covered)
        spreads = [r["entry"][2] for r in rows if r["entry"] is not None]
        return {"slices": len(rows), "legs": len(legs), "covered_legs": len(covered),
                "coverage_pct": round(len(covered) / len(legs) * 100, 1) if legs else None,
                "net": round(net, 2), "charged": round(charged, 2), "extra": round(extra, 2),
                "net_at_quote": round(net - extra, 2),
                "median_entry_spread_pct": round(statistics.median(spreads), 3) if spreads else None}
    total = block(marked)
    months: dict[str, list[dict]] = defaultdict(list)
    for r in marked:
        months[r["day"][:7] or "unknown"].append(r)
    return {**total, "months": [{"month": m, **block(rows)} for m, rows in sorted(months.items(), reverse=True)]}


def report(books: list[dict], ticks_path: str, start: str | None = None, end: str | None = None,
           chains_path: str | None = None) -> dict:
    """Every lane's second mark over the exit-day range."""
    book = SpreadBook(ticks_path, chains_path)
    try:
        lanes = []
        for b in books:
            if b.get("error"):
                continue
            trades = [t for t in b.get("trades") or []
                      if (not start or (t.get("day") or "") >= start) and (not end or (t.get("day") or "") <= end)]
            lanes.append({"lane": b["lane"], "label": b["label"], "kind": b.get("kind", "paper"),
                          **summarize(mark_trades(trades, book))})
    finally:
        book.close()
    return {"as_of": datetime.now(IST).isoformat(timespec="seconds"),
            "window_seconds": WINDOW_SECONDS, "source": "tick_minute_flow.avg_spread (since 8 Sep 2026), else the stored option chain's quote",
            "lanes": lanes}
