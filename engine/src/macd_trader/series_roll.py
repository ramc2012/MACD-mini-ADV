"""Series roll for stock F&O (owner, 18 Nov 2026: R1 "yes", then "close all standing positions as of now and do
complete rollover").

Physical settlement: from E-4 the clearing corporation levies a delivery margin on in-the-money long options and on
every future and short option (10/25/45/70% of it on E-4..E-1), and brokers block it at the start of the day. The
lanes' last day in a stock series is now E-5 (rollover.STOCK_EXIT_SESSIONS). A position still held in a series at
or past its last day is ROLLED: the outgoing leg is closed and the same exposure is opened in the next series.

* Options (MACD, Blast, Smart): closed on the quote side (a long sells at the bid), and re-opened on the same side
  (CE stays CE, PE stays PE) at the next series' ATM strike, with the same rupees the exit raised (whole lots,
  at least one lot when cash covers it).
* Stock futures (Spot MACD): the short is bought back at the ask / the long sold at the bid, and the same number
  of lots is opened in the lane's chosen next-series future.

Prices: the tick store's last minute of the session (close +/- half its average quoted spread) when the contract
streamed, else the stored option chain's latest two-sided quote (stock chains are swept every 15 minutes, so after
the close that is about 15:26 IST). Both legs are tagged: the exit reason is SERIES_ROLL and the entry order's
signal id is ``ROLL:<outgoing symbol>``, so the back office can price the roll separately.

Paper books only. Nothing here can reach a broker: fills go through each book's own ExecutionManager paper path
(administered_fill). Index options are cash settled and never rolled here.
"""
from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import rollover

IST = ZoneInfo("Asia/Kolkata")
ROLL_EXIT = "SERIES_ROLL"
SESSION_TAIL_SECONDS = 1800          # a tick-store minute counts as the closing quote within the last 30 min


@dataclass(slots=True)
class Quote:
    symbol: str
    ltp: float
    bid: float
    ask: float
    timestamp: datetime
    source: str

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2


def _two_sided(bid, ask) -> bool:
    return bool(bid) and bool(ask) and math.isfinite(bid) and math.isfinite(ask) and 0 < bid <= ask


def tick_quote(ticks_path: str, symbol: str) -> Quote | None:
    """The symbol's last stored minute: close and half its average quoted spread either side."""
    try:
        with sqlite3.connect(f"file:{ticks_path}?mode=ro", uri=True, timeout=10) as db:
            row = db.execute(
                """SELECT f.minute_ts, f.close, f.avg_spread FROM tick_minute_flow f
                   JOIN tick_symbols s ON s.id = f.symbol_id WHERE s.symbol = ?
                   ORDER BY f.minute_ts DESC LIMIT 1""", (symbol,)).fetchone()
    except sqlite3.Error:
        return None
    if not row or not row[1] or row[2] is None or row[2] <= 0:
        return None
    at, close, spread = int(row[0]), float(row[1]), float(row[2])
    if spread >= close:
        return None
    return Quote(symbol, close, round(close - spread / 2, 4), round(close + spread / 2, 4),
                 datetime.fromtimestamp(at, UTC), "tick_store")


def chain_quote(research_path: str, symbol: str) -> Quote | None:
    """The stored chain's latest two-sided quote for an option contract."""
    from .option_agent import parse_option
    parsed = parse_option(symbol)
    if not parsed:
        return None
    try:
        with sqlite3.connect(f"file:{research_path}?mode=ro", uri=True, timeout=10) as db:
            row = db.execute(
                """SELECT ts, ltp, bid, ask FROM chain_snapshots WHERE underlying = ? AND symbol = ?
                   AND bid > 0 AND ask >= bid ORDER BY ts DESC LIMIT 1""", (parsed["root"], symbol)).fetchone()
    except sqlite3.Error:
        return None
    if not row:
        return None
    at, ltp, bid, ask = int(row[0]), row[1], float(row[2]), float(row[3])
    return Quote(symbol, float(ltp) if ltp and ltp > 0 else (bid + ask) / 2, bid, ask,
                 datetime.fromtimestamp(at, UTC), "chain")


def live_quote(engine, symbol: str, max_age_seconds: float = 60.0) -> Quote | None:
    """In session: the engine's latest two-sided tick for the symbol, if it is under a minute old."""
    tick = (getattr(engine, "latest_ticks", None) or {}).get(symbol)
    if tick is None or not _two_sided(getattr(tick, "bid", None), getattr(tick, "ask", None)):
        return None
    stamp = getattr(tick, "timestamp", None) or datetime.now(UTC)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    if (datetime.now(UTC) - stamp).total_seconds() > max_age_seconds:
        return None
    return Quote(symbol, float(tick.ltp), float(tick.bid), float(tick.ask), stamp, "live")


def closing_quote(symbol: str, ticks_path: str, research_path: str, engine=None) -> Quote | None:
    """A live tick in session; else the better stored quote: the tick store's minute when it is the later one,
    else the chain's."""
    live = live_quote(engine, symbol) if engine is not None else None
    if live is not None:
        return live
    tick = tick_quote(ticks_path, symbol)
    chain = chain_quote(research_path, symbol) if symbol.endswith(("CE", "PE")) else None
    if tick and chain:
        return tick if tick.timestamp >= chain.timestamp else chain
    return tick or chain


def chain_row_today(research_path: str, root: str, expiry: str, today: date) -> tuple[float | None, float | None, datetime] | None:
    """(futures price, spot, at) stamped on the root's latest stored chain of ``expiry`` -- only if swept ``today``."""
    try:
        with sqlite3.connect(f"file:{research_path}?mode=ro", uri=True, timeout=10) as db:
            row = db.execute("""SELECT ts, fp, spot FROM chain_snapshots WHERE underlying = ? AND expiry = ?
                                ORDER BY ts DESC LIMIT 1""", (root, expiry)).fetchone()
    except sqlite3.Error:
        return None
    if not row:
        return None
    at = datetime.fromtimestamp(int(row[0]), UTC)
    if at.astimezone(IST).date() != today:
        return None
    fp = float(row[1]) if row[1] and row[1] > 0 else None
    spot = float(row[2]) if row[2] and row[2] > 0 else None
    return fp, spot, at


def _around(symbol: str, price: float, half_pct: float, at: datetime, source: str) -> Quote:
    h = price * half_pct / 100
    return Quote(symbol, round(price, 4), round(price - h, 4), round(price + h, 4), at, source)


def refreshed_exit_quote(settings, symbol: str, expiry: str | None, quote: Quote | None, today: date) -> Quote | None:
    """A held leg whose last stored quote is from an earlier session (an illiquid deep-ITM strike outside the stored
    chain's window, or a future dropped from the day's feed) is priced from today's stored chain instead: an ITM
    option at its intrinsic value against today's spot, a future at the chain's futures price -- either side of it by
    the default paper half spread. An OTM option keeps its last quote (nothing fresher is stored)."""
    if quote is not None and (quote.source == "live" or quote.timestamp.astimezone(IST).date() >= today):
        return quote
    from .option_agent import parse_option
    research = settings.research_database_path
    if rollover.is_stock_option(symbol):
        parsed = parse_option(symbol) or {}
        row = chain_row_today(research, parsed.get("root", ""), expiry or "", today) if expiry else None
        if row is None or row[1] is None:
            return quote
        strike, spot = float(parsed.get("strike") or 0), row[1]
        intrinsic = spot - strike if parsed.get("side") == 1 else strike - spot
        if strike <= 0 or intrinsic <= 0:
            return quote
        return _around(symbol, intrinsic, settings.paper_fallback_half_spread_pct, row[2], "intrinsic_today")
    root = rollover.futures_root(symbol)
    if root is None or not expiry:
        return quote
    row = chain_row_today(research, root[1], expiry, today)
    if row is None or row[0] is None:
        return quote
    return _around(symbol, row[0], settings.paper_fallback_half_spread_other_pct, row[2], "chain_futures_price")


def next_future(engine, symbol: str, expiry: str | None, today: date):
    """The next listed series of a stock future, from the stored chains: (symbol, expiry, lot size, quote)."""
    from types import SimpleNamespace
    from . import strategy_builder
    root = rollover.futures_root(symbol)
    if root is None or not expiry:
        return None
    research = engine.settings.research_database_path
    try:
        first = strategy_builder.chain(research, root[1])
    except KeyError:
        return None
    later = [e for e in first.get("expiries", []) if e > expiry]
    if not later:
        return None
    nxt = later[0]
    row = chain_row_today(research, root[1], nxt, today)
    if row is None or row[0] is None:
        return None
    when = date.fromisoformat(nxt)
    new = f"{root[0]}:{root[1]}{when:%y}{when.strftime('%b').upper()}FUT"
    quote = _around(new, row[0], engine.settings.paper_fallback_half_spread_other_pct, row[2], "chain_futures_price")
    return SimpleNamespace(symbol=new, expiry=nxt, lot_size=None, quote=quote, root=root[1])


def series_closed(symbol: str, expiry: str | None, today: date) -> bool:
    """A stock option or stock future at or past its series' last day (E-5)."""
    if rollover.STOCK_EXIT_SESSIONS <= 0 or not expiry:
        return False
    if rollover.is_stock_option(symbol):
        return rollover.stock_entry_closed(symbol, expiry, today)
    root = rollover.futures_root(symbol)
    if root is None:
        return False
    from .universe import INDEX_SPOTS
    if root[1] in INDEX_SPOTS:
        return False
    last = rollover.stock_last_day(expiry)
    return last is not None and today >= last


def next_series_atm(research_path: str, root: str, option_type: str, after: str) -> dict | None:
    """The next listed series' ATM contract of ``option_type`` with a two-sided stored quote (nearest strike to
    the stored spot first)."""
    from . import strategy_builder
    try:
        first = strategy_builder.chain(research_path, root)
    except KeyError:
        return None
    later = [e for e in first.get("expiries", []) if e > after]
    if not later:
        return None
    expiry = later[0]
    chain = strategy_builder.chain(research_path, root, expiry=expiry)
    spot = chain.get("stored_spot") or chain.get("spot")
    if not spot:
        return None
    leg_key = "ce" if option_type == "CE" else "pe"
    for row in sorted(chain["rows"], key=lambda r: abs(r["strike"] - spot)):
        leg = row.get(leg_key) or {}
        if leg.get("symbol") and _two_sided(leg.get("bid"), leg.get("ask")):
            return {"symbol": leg["symbol"], "expiry": expiry, "strike": row["strike"], "option_type": option_type,
                    "spot": spot, "chain": chain,
                    "quote": Quote(leg["symbol"], float(leg.get("ltp") or (leg["bid"] + leg["ask"]) / 2),
                                   float(leg["bid"]), float(leg["ask"]),
                                   datetime.fromisoformat(chain["as_of"]).astimezone(UTC) if chain.get("as_of")
                                   else datetime.now(UTC), "chain")}
    return None


# --------------------------------------------------------------------------- engine side


def _books(engine) -> list[tuple[str, object, object | None]]:
    """(lane, execution, lane object) for every engine paper book that can hold stock F&O."""
    from .engine import _smart_lanes
    out = [("macd", engine.execution, None)]
    blast = getattr(engine, "blast", None)
    if blast is not None:
        out.append(("blast", blast.execution, blast))
    for lane in _smart_lanes(engine):
        if getattr(lane, "name", "") == "agentic":
            continue                                   # index options only
        out.append((lane.name, lane.execution, lane))
    return out


def _expiry(engine, lane_obj, execution, symbol: str) -> str | None:
    from .option_agent import parse_option
    meta = (getattr(lane_obj, "meta", None) or {}).get(symbol) or {}
    expiry = meta.get("expiry") or execution.contract_expiries.get(symbol)
    if not expiry and lane_obj is None and hasattr(engine, "_expiry_of"):
        expiry = engine._expiry_of(symbol)
    if not expiry:
        parsed = parse_option(symbol)
        expiry = parsed["expiry"] if parsed else None
    if not expiry and rollover.is_futures_symbol(symbol):
        from .expiry_exposure import contract_row
        row = contract_row("", symbol, 1, 0, None, None, date.today())
        expiry = row["expiry"] if row else None
    return str(expiry)[:10] if expiry else None


async def plan(engine, now: datetime | None = None) -> list[dict]:
    """What the roll would do now, per held stock F&O position at or past its series' last day."""
    from .option_agent import parse_option
    now = (now or datetime.now(IST)).astimezone(IST)
    today = now.date()
    research, ticks = engine.settings.research_database_path, engine.settings.tick_database_path
    rows: list[dict] = []
    for lane, execution, lane_obj in _books(engine):
        for symbol, position in list(execution.portfolio.positions.items()):
            if position.quantity == 0:
                continue
            expiry = _expiry(engine, lane_obj, execution, symbol)
            if not series_closed(symbol, expiry, today):
                continue
            long = position.quantity > 0
            row = {"lane": lane, "symbol": symbol, "expiry": expiry, "side": "LONG" if long else "SHORT",
                   "quantity": int(position.quantity), "lots": int(position.lots), "lot_size": int(position.lot_size),
                   "average_price": round(float(position.average_price), 4), "status": "PLANNED", "error": None}
            exit_quote = refreshed_exit_quote(engine.settings, symbol, expiry,
                                              closing_quote(symbol, ticks, research, engine), today)
            if exit_quote is None:
                row.update(status="NO_EXIT_QUOTE", error="no stored closing quote")
                rows.append(row)
                continue
            exit_price = exit_quote.bid if long else exit_quote.ask     # the quote side (S1)
            row["exit"] = {"price": exit_price, "bid": exit_quote.bid, "ask": exit_quote.ask, "ltp": exit_quote.ltp,
                           "source": exit_quote.source, "at": exit_quote.timestamp.astimezone(IST).isoformat(timespec="minutes"),
                           "stale": exit_quote.timestamp.astimezone(IST).date() < today}
            row["_exit_quote"] = exit_quote          # every leg is closed, rolled or not (owner: close all, roll all)
            if rollover.is_stock_option(symbol):
                parsed = parse_option(symbol) or {}
                option_type = "CE" if parsed.get("side") == 1 else "PE"
                target = next_series_atm(research, parsed.get("root", ""), option_type, expiry or "")
                if target is None:
                    row.update(status="NO_NEXT_SERIES", error="no next-series ATM quote stored")
                    rows.append(row)
                    continue
                lot = await engine._builder_lot(parsed["root"], target["chain"]) if hasattr(engine, "_builder_lot") else None
                lot = int(lot or position.lot_size)
                quote: Quote = live_quote(engine, target["symbol"]) or target["quote"]
                proceeds = abs(position.quantity) * exit_price
                per_lot = quote.ask * lot
                lots = int(proceeds // per_lot) if per_lot > 0 else 0
                row["entry"] = {"symbol": target["symbol"], "expiry": target["expiry"], "strike": target["strike"],
                                "option_type": option_type, "spot": target["spot"], "lot_size": lot,
                                "lots": max(1, lots), "price": quote.ask, "bid": quote.bid, "ask": quote.ask,
                                "ltp": quote.ltp, "source": quote.source, "rupees_out": round(proceeds, 2),
                                "rupees_in": round(max(1, lots) * per_lot, 2),
                                "at": quote.timestamp.astimezone(IST).isoformat(timespec="minutes")}
                row["_quote"] = quote
            else:
                stock = lane_obj.underlying_of(symbol) if hasattr(lane_obj, "underlying_of") else None
                leg = (getattr(lane_obj, "futures", {}) or {}).get(stock) if stock else None
                quote = None
                if leg is None or leg.symbol == symbol or leg.expiry <= (expiry or ""):
                    leg = next_future(engine, symbol, expiry, today)   # not in the lane's universe today
                    if leg is None:
                        row.update(status="NO_NEXT_SERIES", error="no later futures series with a stored price today")
                        rows.append(row)
                        continue
                    quote = leg.quote
                    try:
                        from . import strategy_builder
                        chain = strategy_builder.chain(research, leg.root, expiry=leg.expiry)
                        lot = await engine._builder_lot(leg.root, chain) if hasattr(engine, "_builder_lot") else None
                    except KeyError:
                        lot = None
                    leg.lot_size = int(lot or position.lot_size)
                quote = live_quote(engine, leg.symbol) or tick_quote(ticks, leg.symbol) or quote
                if quote is None:
                    row.update(status="NO_ENTRY_QUOTE", error=f"no stored quote for {leg.symbol}")
                    rows.append(row)
                    continue
                row["entry"] = {"symbol": leg.symbol, "expiry": leg.expiry, "strike": None,
                                "option_type": "LONG" if long else "SHORT", "spot": None, "lot_size": leg.lot_size,
                                "lots": max(1, position.lots), "price": quote.ask if long else quote.bid,
                                "bid": quote.bid, "ask": quote.ask, "ltp": quote.ltp, "source": quote.source,
                                "at": quote.timestamp.astimezone(IST).isoformat(timespec="minutes")}
                row["_quote"] = quote
            rows.append(row)
    return rows


def _lane_meta(lane_obj, old: str, new: str, entry: dict, position, now: datetime) -> None:
    """Smart / Spot lanes keep per-position metadata (source decides the exit rules): carry it to the new leg."""
    if lane_obj is None or not hasattr(lane_obj, "_db"):
        return
    base = dict((getattr(lane_obj, "meta", {}) or {}).get(old) or {})
    meta = {"position_id": position.position_id, "symbol": new, "source": base.get("source") or "MACD",
            "opened_at": now.isoformat(), "expiry": entry["expiry"], "strike": entry.get("strike"),
            "option_type": entry["option_type"], "spot_symbol": base.get("spot_symbol"), "score": base.get("score"),
            "dispersion": base.get("dispersion"), "journal_id": base.get("journal_id")}
    lane_obj.meta[new] = meta
    with lane_obj._db_lock, lane_obj._db:
        lane_obj._db.execute(
            """INSERT OR REPLACE INTO smart_positions (position_id, symbol, source, opened_at, expiry, strike,
               option_type, spot_symbol, score, dispersion, journal_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            tuple(meta[k] for k in ("position_id", "symbol", "source", "opened_at", "expiry", "strike",
                                    "option_type", "spot_symbol", "score", "dispersion", "journal_id")))


async def execute(engine, now: datetime | None = None, journal_dir: str | None = None) -> dict:
    """Run the roll: every lane's outgoing legs are closed; the next-series legs open only with
    ``series_roll_reopen`` (off since 18 Nov 2026: the lanes enter the next series on their own signals)."""
    from .option_agent import parse_option
    now = (now or datetime.now(IST)).astimezone(IST)
    rows = await plan(engine, now)
    books = {lane: (execution, lane_obj) for lane, execution, lane_obj in _books(engine)}
    for row in rows:
        if "_exit_quote" not in row:
            continue                                   # no quote to close it on: left as it is, reported
        execution, _lane_obj = books[row["lane"]]
        long = row["side"] == "LONG"
        try:
            order = await execution.administered_fill(row["symbol"], "SELL" if long else "BUY", abs(row["quantity"]),
                                                      max(1, row["lot_size"]), row["_exit_quote"], ROLL_EXIT)
            row["exit"]["filled"] = order.fill_price
            if row["status"] == "PLANNED":
                row["status"] = "EXITED"
            else:
                row.update(status="EXITED_ONLY", error=f"closed; not rolled: {row['error']}")
        except Exception as exc:  # noqa: BLE001 -- one refused leg must not stop the others
            row.update(status="EXIT_FAILED", error=f"{type(exc).__name__}: {exc}"[:200])
    reopen = bool(getattr(engine.settings, "series_roll_reopen", False))
    for row in rows:
        if row["status"] == "EXITED" and not reopen:
            row["status"] = "CLOSED"                   # owner: the next series is taken on the lane's own signal
            continue
        if row["status"] != "EXITED":
            continue
        execution, lane_obj = books[row["lane"]]
        entry, long = row["entry"], row["side"] == "LONG"
        side = "BUY" if long else "SELL"
        lots = entry["lots"]
        while lots >= 1:
            try:
                order = await execution.administered_fill(entry["symbol"], side, lots * entry["lot_size"],
                                                          entry["lot_size"], row["_quote"], ROLL_EXIT,
                                                          expiry=entry["expiry"], signal_id=f"ROLL:{row['symbol']}")
            except ValueError as exc:
                if "cash" in str(exc).lower() or "margin" in str(exc).lower():
                    lots -= 1                            # fewer lots if the book cannot fund them all
                    continue
                row.update(status="ENTRY_FAILED", error=f"{type(exc).__name__}: {exc}"[:200])
                break
            except Exception as exc:  # noqa: BLE001
                row.update(status="ENTRY_FAILED", error=f"{type(exc).__name__}: {exc}"[:200])
                break
            entry.update(filled=order.fill_price, lots=lots)
            row["status"] = "ROLLED"
            position = execution.portfolio.positions.get(entry["symbol"])
            if position is not None:
                _lane_meta(lane_obj, row["symbol"], entry["symbol"], entry, position, now)
                contracts = getattr(lane_obj, "_contracts", None)
                if isinstance(contracts, dict):
                    parsed = parse_option(entry["symbol"]) or {}
                    contracts.setdefault(entry["symbol"], {"expiry": entry["expiry"], "strike": entry.get("strike"),
                                                           "option_type": entry["option_type"],
                                                           "spot_symbol": parsed.get("spot")})
            break
        else:
            row.update(status="EXITED_ONLY", error="cash does not cover one lot of the next series")
    for row in rows:
        row.pop("_quote", None)
        row.pop("_exit_quote", None)
    summary = {"at": now.isoformat(timespec="seconds"), "last_day_sessions": rollover.STOCK_EXIT_SESSIONS,
               "counts": {}, "rows": rows}
    for row in rows:
        summary["counts"][row["status"]] = summary["counts"].get(row["status"], 0) + 1
    if journal_dir:
        path = Path(journal_dir) / f"series_roll_{now.strftime('%Y%m%d_%H%M%S')}.json"
        path.write_text(json.dumps(summary, indent=1, default=str))
        summary["journal"] = str(path)
    return summary


def view(rows: list[dict]) -> list[dict]:
    """Plan rows without the private quote objects."""
    return [{k: v for k, v in row.items() if not k.startswith("_")} for row in rows]


