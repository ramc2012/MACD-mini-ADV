"""Whale tracker: tick-level shapes, per-strike OI builds, the futures leg
against the option book, the day-over-day picture, and one composite.

A large participant on NSE is constrained by the freeze quantity and by
position limits, and those constraints are what make it visible: an order that
wants more than one order can carry gets sliced, and slices leave shapes. Layer
A looks for three of them in the recorded tick stream:

* **A1 large print** — a cluster at least ten times the rolling 30-minute
  median and at least five lots. Scored log(quantity / median).
* **A2 freeze-size print** — quantity exactly at the freeze-constrained
  maximum (1,755 for NIFTY at lot 65). Three or more within 60 seconds on the
  same side is a *slicer*.
* **A3 constant child size** — five or more clusters of one identical size
  (not a single lot) with machine-regular spacing (coefficient of variation of
  the inter-arrival times below 0.3).

Layer B reads the option chain the desk snapshots once a minute. Over a
three-minute window every strike's OI change is weighted by a delta solved
from its own premium, so a 20-lot build in a 0.5-delta strike outranks a
200-lot build in a 0.02-delta one, and the sum over the chain is the option
book's net delta. Two absolute notices need no history: window volume at or
above the strike's prior OI, and pairs of same-sized fresh legs that read as
a structure (straddle, strangle, risk reversal, vertical, synthetic future).
Layer C puts the futures leg beside that — classified CVD and book OFI from
the desk's own feed, OI from the quotes REST — and calls a divergence when the
two legs point opposite ways and the option side is five times the size.
Layer D is the same chain read at the close against the previous close.

Layer E scores each of those against the same 15-minute slot of the previous
twenty sessions. Until twenty sessions exist the z-scores are None, the
composite is None and nothing is sent — the absolute notices are shown, the
statistical ones wait for a distribution to exist. Scores decay with a
fifteen-minute half-life. The tracker sees shapes, not names — every event is
context for a setup, never a signal on its own.
"""
from __future__ import annotations

import json
import math
import sqlite3
from bisect import bisect_left, insort
from collections import deque
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .greeks import contract_delta
from .regimes import regime_for, spans_a_break

IST = ZoneInfo("Asia/Kolkata")
LIVE = "live"

LARGE_PRINT_MULTIPLE = 10.0
MIN_LARGE_PRINT_LOTS = 5
MEDIAN_WINDOW_SECONDS = 1800
SLICER_WINDOW_SECONDS = 60
SLICER_MIN_PRINTS = 3
CHILD_MIN_REPEATS = 5
CHILD_MAX_CV = 0.3
HALF_LIFE_SECONDS = 900.0
# A1's rolling median needs thirty minutes of prints before it judges, so a
# live re-detection over the trailing slice has to reach a little past that.
A_LIVE_LOOKBACK_MS = 35 * 60 * 1000

SESSION_OPEN = time(9, 15)
SLOT_MINUTES = 15
WINDOW_SECONDS = 180
# A "window" whose earlier snapshot is more than twice the window old is a
# feed hole, not a three-minute window; the ΔOI across it is not comparable.
MAX_GAP_FACTOR = 2
# ΔOI below a quarter of the window's volume is churn — contracts changing
# hands, not positions being built — and churn must not read as positioning.
CHURN_MIN_OI_SHARE = 0.25
UNUSUAL_MEDIAN_MULTIPLE = 3.0
HISTORY_DAYS = 20
BUCKET_LIMIT = 8
# A premium move smaller than one tick net of the underlying's own move says
# nothing about who was aggressing; the strike keeps its magnitude, not a sign.
PREMIUM_PROXY_MIN = 0.05
# ₹2 cr of delta-notional per leg before a strike can be half a structure;
# legs must be within 2x of each other to be read as one trade.
DN_MIN = 2e7
PAIR_RATIO = 2.0
PCR_JUMP = 0.05
# Layer C: the option leg has to be five times the futures leg AND at least
# ₹5 cr before "options against futures" is worth a notice, and a futures leg
# under ₹1 cr is flat — and flat is not "opposite".
OPTION_TO_FUTURES_RATIO = 5.0
FUT_DN_FLOOR = 1e7
DIVERGENCE_DN_MIN = 5e7
WEIGHTS = {"a": 0.3, "b": 0.3, "c": 0.3, "d": 0.1}
ALERT_THRESHOLD = 2.5

SCHEMA = """
CREATE TABLE IF NOT EXISTS whale_events (
  symbol TEXT NOT NULL,
  day TEXT NOT NULL,
  ts_ms INTEGER NOT NULL,
  layer TEXT NOT NULL,
  kind TEXT NOT NULL,
  side INTEGER NOT NULL DEFAULT 0,
  quantity INTEGER,
  price REAL,
  score REAL NOT NULL,
  evidence TEXT,
  PRIMARY KEY (symbol, ts_ms, kind)
);
CREATE INDEX IF NOT EXISTS idx_whale_day ON whale_events(day, symbol);
CREATE TABLE IF NOT EXISTS chain_snapshots (
  ts INTEGER NOT NULL,
  underlying TEXT NOT NULL,
  expiry TEXT NOT NULL,
  strike REAL NOT NULL,
  option_type TEXT NOT NULL,
  symbol TEXT NOT NULL,
  ltp REAL, volume INTEGER, oi INTEGER, spot REAL,
  bid REAL, ask REAL, prev_oi INTEGER, fp REAL, vix REAL,
  PRIMARY KEY (ts, symbol)
);
CREATE INDEX IF NOT EXISTS idx_chain_underlying_ts ON chain_snapshots(underlying, ts);
-- One row per (day, root) the collector actually stored a chain for. Counting
-- collected days off chain_snapshots means COUNT(DISTINCT date(ts, ...)) over
-- every leg of every minute of every session — a scan no index can serve, on
-- a table that grows by ~77,000 rows a day.
CREATE TABLE IF NOT EXISTS chain_days (
  day TEXT NOT NULL, underlying TEXT NOT NULL, PRIMARY KEY (day, underlying)
);
-- The futures leg, one row per symbol-minute: the live sample of the desk's
-- own classified flow and book OFI, or the nightly rebuild of the same.
CREATE TABLE IF NOT EXISTS whale_flow_minutes (
  symbol TEXT NOT NULL, minute_ts INTEGER NOT NULL, day TEXT NOT NULL,
  ltp REAL, cvd REAL NOT NULL DEFAULT 0,
  buy_volume REAL NOT NULL DEFAULT 0, sell_volume REAL NOT NULL DEFAULT 0,
  total_volume REAL NOT NULL DEFAULT 0, trades INTEGER NOT NULL DEFAULT 0,
  ofi_cum REAL NOT NULL DEFAULT 0, ofi_events INTEGER NOT NULL DEFAULT 0, depth_scale REAL,
  oi INTEGER, source TEXT NOT NULL,
  PRIMARY KEY (symbol, minute_ts)
);
CREATE INDEX IF NOT EXISTS idx_whale_flow_day ON whale_flow_minutes(day, symbol);
CREATE TABLE IF NOT EXISTS whale_strike_windows (
  underlying TEXT NOT NULL, ts INTEGER NOT NULL, day TEXT NOT NULL, slot INTEGER NOT NULL,
  expiry TEXT, strike REAL NOT NULL, option_type TEXT NOT NULL, symbol TEXT,
  oi INTEGER, prior_oi INTEGER, d_oi INTEGER, d_volume INTEGER, ltp REAL, d_ltp REAL,
  iv REAL, delta REAL, delta_source TEXT, flow_sign INTEGER, flow_source TEXT,
  dn REAL, contribution REAL, bucket INTEGER, unusual TEXT,
  PRIMARY KEY (underlying, ts, strike, option_type)
);
CREATE INDEX IF NOT EXISTS idx_whale_strike_slot ON whale_strike_windows(underlying, slot, day);
CREATE TABLE IF NOT EXISTS whale_windows (
  underlying TEXT NOT NULL, ts INTEGER NOT NULL, day TEXT NOT NULL, slot INTEGER NOT NULL,
  window_seconds INTEGER NOT NULL, then_ts INTEGER, spot REAL, fut REAL, expiry TEXT,
  pcr_oi REAL, pcr_oi_then REAL, pcr_jump REAL, pcr_vol_window REAL,
  net_delta_units REAL, net_dn REAL, unusual_count INTEGER,
  fut_symbol TEXT, fut_delta_units REAL, fut_ofi REAL, fut_dn REAL, fut_oi INTEGER,
  fut_d_oi INTEGER, fut_sign INTEGER,
  a_net REAL, divergence_ratio REAL, divergence INTEGER NOT NULL DEFAULT 0,
  structures TEXT, walls TEXT, unusual TEXT,
  score_a REAL, score_b REAL, score_c REAL, score_d REAL,
  z_a REAL, z_b REAL, z_c REAL, z_d REAL, composite REAL, composite_decayed REAL,
  history_days INTEGER, source TEXT NOT NULL,
  PRIMARY KEY (underlying, ts)
);
CREATE INDEX IF NOT EXISTS idx_whale_windows_slot ON whale_windows(underlying, slot, day);
CREATE TABLE IF NOT EXISTS whale_eod (
  day TEXT NOT NULL, underlying TEXT NOT NULL, close_ts INTEGER, spot REAL, fut REAL,
  call_oi INTEGER, put_oi INTEGER, pcr_oi REAL, prior_pcr_oi REAL,
  d_call_oi INTEGER, d_put_oi INTEGER,
  fut_oi INTEGER, fut_pdoi INTEGER, fut_avg_trade REAL, fut_avg_trade_pct REAL,
  top_oi TEXT, top_d_oi TEXT, walls TEXT, score REAL, z REAL, history_days INTEGER,
  fut_symbol TEXT, d_oi_skipped INTEGER,
  PRIMARY KEY (day, underlying)
);
CREATE TABLE IF NOT EXISTS whale_alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, underlying TEXT NOT NULL, ts INTEGER NOT NULL,
  day TEXT NOT NULL, composite REAL NOT NULL, direction INTEGER NOT NULL,
  spot REAL, fut REAL, evidence TEXT NOT NULL,
  next_15 TEXT, next_30 TEXT, next_60 TEXT, sent INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_whale_alerts_day ON whale_alerts(day, underlying);
"""
# CREATE TABLE IF NOT EXISTS never widens a table that already exists, so the
# chain table on the running desk keeps its original shape unless each added
# column is also listed here (the same rule tick_store keeps for its tables).
CHAIN_COLUMNS = (("bid", "REAL"), ("ask", "REAL"), ("prev_oi", "INTEGER"),
                 ("fp", "REAL"), ("vix", "REAL"))
EOD_COLUMNS = (("fut_symbol", "TEXT"), ("d_oi_skipped", "INTEGER"))
# The IST calendar day of a stored epoch second, in SQL.
IST_DAY_SQL = "date(ts, 'unixepoch', '+5 hours', '+30 minutes')"


def ensure_schema(connection: sqlite3.Connection) -> None:
    fresh = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'chain_days'").fetchone() is None
    connection.executescript(SCHEMA)
    for table, columns in (("chain_snapshots", CHAIN_COLUMNS), ("whale_eod", EOD_COLUMNS)):
        existing = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        for name, definition in columns:
            if name not in existing:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
    if fresh:
        # Once, on the desk that already has months of chain: the scan the
        # index cannot serve is paid here rather than on every page load.
        connection.execute(
            f"""INSERT OR IGNORE INTO chain_days (day, underlying)
                SELECT DISTINCT {IST_DAY_SQL}, underlying FROM chain_snapshots""")
    connection.commit()


@dataclass(frozen=True)
class WhaleEvent:
    symbol: str
    ts_ms: int
    kind: str
    side: int
    quantity: int
    price: float
    score: float
    evidence: str
    layer: str = "A"


def underlying_of(symbol: str) -> str:
    body = symbol.split(":", 1)[-1]
    for root in ("BANKNIFTY", "MIDCPNIFTY", "FINNIFTY", "NIFTY", "SENSEX"):
        if body.startswith(root):
            return root
    return body.replace("-EQ", "").replace("-INDEX", "")


def freeze_quantity(symbol: str, day: str) -> int:
    """Units per order at the freeze limit, from the regime in force."""
    regime = regime_for(day)
    root = underlying_of(symbol)
    if root in ("NIFTY", "BANKNIFTY"):
        return regime.freeze_units(root)
    return 0


def lot_size(symbol: str, day: str) -> int:
    root = underlying_of(symbol)
    if root in ("NIFTY", "BANKNIFTY"):
        return regime_for(day).lot_size(root)
    return 1


# ---------------------------------------------------------------------------
# Clock
# ---------------------------------------------------------------------------

def session_span(day: str) -> tuple[int, int]:
    """UTC seconds of the F&O session on an IST date, open to the regime's close.

    The regime end, not a fixed 15:30: since 3 Aug 2026 the derivatives
    session runs to 15:40, and the last ten minutes are where expiry-day
    unwinds and closing-auction hedges land — the window the tracker most
    wants to see.
    """
    opened = datetime.combine(date.fromisoformat(day), SESSION_OPEN, IST)
    hour, minute = (int(part) for part in regime_for(day).session_end.split(":"))
    closed = opened.replace(hour=hour, minute=minute)
    return int(opened.timestamp()), int(closed.timestamp())


def day_of(ts: int) -> str:
    return datetime.fromtimestamp(ts, UTC).astimezone(IST).date().isoformat()


def slot_of(ts: int) -> int:
    """Fifteen-minute slot of the session, 0 at 09:15. Same-slot comparison
    across days is what makes a 09:20 OI build comparable with other 09:20s
    rather than with the whole day."""
    local = datetime.fromtimestamp(ts, UTC).astimezone(IST)
    opened = SESSION_OPEN.hour * 60 + SESSION_OPEN.minute
    return max(0, (local.hour * 60 + local.minute - opened) // SLOT_MINUTES)


def snapshot_stamp() -> int:
    return int(datetime.now(UTC).timestamp())


def _dicts(cursor) -> list[dict]:
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def _one(connection: sqlite3.Connection, sql: str, params: tuple = ()) -> dict | None:
    rows = _dicts(connection.execute(sql, params))
    return rows[0] if rows else None


# ---------------------------------------------------------------------------
# Layer A
# ---------------------------------------------------------------------------

def detect(symbol: str, day: str, prints: list[tuple[int, float, int, int]]) -> list[WhaleEvent]:
    """Layer A over one symbol's session.

    ``prints`` are ``(ts_ms, price, quantity, side)`` for volume-bearing
    updates in time order, side being +1/-1/0 from the classifier. Quantity is
    the traded size of the update, which on a batched feed is a cluster, not a
    single trade — the freeze-size test tolerates that by matching the
    cluster, which is what a slicer's child order looks like after batching.
    """
    freeze = freeze_quantity(symbol, day)
    lot = max(1, lot_size(symbol, day))
    events: list[WhaleEvent] = []
    window: deque = deque()          # (ts_ms, qty) for the rolling median
    # The same quantities kept in sorted order. Re-sorting the whole deque on
    # every print is O(n log n) per print and the live re-detection runs the
    # trailing 35 minutes once a minute on the tick-bound process; an insort
    # and a bisect-delete are a memmove each and give the identical median.
    ordered: list[int] = []
    freeze_hits: deque = deque()     # (ts_ms, side) recent freeze prints
    by_size: dict[int, list[int]] = {}
    slicer_marked: set[int] = set()

    for ts_ms, price, qty, side in prints:
        if qty <= 0:
            continue
        window.append((ts_ms, qty))
        insort(ordered, qty)
        while window and ts_ms - window[0][0] > MEDIAN_WINDOW_SECONDS * 1000:
            _, dropped = window.popleft()
            del ordered[bisect_left(ordered, dropped)]
        # A1 — against the rolling median, once there is a window to judge by.
        if len(ordered) >= 30:
            median = ordered[len(ordered) // 2]
            if median > 0 and qty >= LARGE_PRINT_MULTIPLE * median and qty >= MIN_LARGE_PRINT_LOTS * lot:
                events.append(WhaleEvent(symbol, ts_ms, "large_print", side, qty, price,
                                         round(math.log(qty / median), 3),
                                         f"{qty} vs 30m median {median}"))
        # A2 — exactly the freeze limit.
        if freeze and qty == freeze:
            events.append(WhaleEvent(symbol, ts_ms, "freeze_print", side, qty, price, 1.0,
                                     f"freeze-size {qty} ({qty // lot} lots)"))
            freeze_hits.append((ts_ms, side))
            while freeze_hits and ts_ms - freeze_hits[0][0] > SLICER_WINDOW_SECONDS * 1000:
                freeze_hits.popleft()
            same = [t for t, s in freeze_hits if s == side and side != 0]
            if len(same) >= SLICER_MIN_PRINTS and ts_ms not in slicer_marked:
                slicer_marked.add(ts_ms)
                events.append(WhaleEvent(symbol, ts_ms, "slicer", side, qty, price,
                                         round(1.0 + 0.5 * len(same), 3),
                                         f"{len(same)} freeze-size prints in {SLICER_WINDOW_SECONDS}s"))
        # A3 — the same non-lot size, machine-regular.
        if qty != lot and qty % lot == 0:
            times = by_size.setdefault(qty, [])
            times.append(ts_ms)
            if len(times) >= CHILD_MIN_REPEATS:
                recent = times[-CHILD_MIN_REPEATS:]
                gaps = [b - a for a, b in zip(recent, recent[1:])]
                mean = sum(gaps) / len(gaps)
                if mean > 0:
                    cv = math.sqrt(sum((g - mean) ** 2 for g in gaps) / len(gaps)) / mean
                    if cv < CHILD_MAX_CV:
                        events.append(WhaleEvent(
                            symbol, ts_ms, "constant_child", side, qty, price,
                            round(1.5 - cv, 3),
                            f"{CHILD_MIN_REPEATS}x {qty} every ~{mean / 1000:.1f}s (cv {cv:.2f})"))
                        by_size[qty] = []
    return events


def aggression(events: list[WhaleEvent], at_ms: int) -> dict:
    """Decayed sum of A-layer scores, signed by side, as of ``at_ms``."""
    buy = sell = 0.0
    for event in events:
        age = max(0.0, (at_ms - event.ts_ms) / 1000.0)
        weight = 0.5 ** (age / HALF_LIFE_SECONDS) * event.score
        if event.side > 0:
            buy += weight
        elif event.side < 0:
            sell += weight
    return {"buy": round(buy, 3), "sell": round(sell, 3), "net": round(buy - sell, 3)}


def _updates(ticks: sqlite3.Connection, symbol: str, day: str, since_ms: int | None = None):
    """Every raw update of one session, classified against the prior book,
    with its book-OFI contribution.

    Yields ``(ts_ms, ltp, size, side, ofi)``; ``size`` is 0 for a quote-only
    update, whose OFI still counts. This is the one walk both Layer A and the
    nightly futures-leg rebuild take, so the two can never disagree on a side.
    """
    from .ofi import OFIState
    from .orderflow import classify_update
    start, end = session_span(day)
    rows = ticks.execute(
        """SELECT t.ts_ms, t.ltp, t.cum_volume, t.last_qty, t.bid, t.ask, t.bid_qty, t.ask_qty,
                  t.tbq, t.tsq
           FROM ticks t JOIN tick_symbols s ON s.id = t.symbol_id
           WHERE s.symbol = ? AND t.ts_ms >= ? AND t.ts_ms < ? ORDER BY t.ts_ms""",
        (symbol, max(start * 1000, since_ms or 0), end * 1000)).fetchall()
    ofi = OFIState(symbol=symbol)
    prev_vol = prev_price = prev_book = prev_pending = None
    last_side = 0
    for ts_ms, ltp, cum_volume, last_qty, bid, ask, bid_qty, ask_qty, tbq, tsq in rows:
        contribution = ofi.update(ts_ms / 1000.0, bid, ask, bid_qty, ask_qty)
        size = 0
        if last_qty:
            size = int(last_qty)
        elif cum_volume is not None and prev_vol is not None:
            size = max(0, int(cum_volume) - prev_vol)
        if cum_volume is not None:
            prev_vol = int(cum_volume)
        book, pending = prev_book, prev_pending
        prev_book, prev_pending = (bid, ask), (tbq, tsq)
        if size <= 0:
            yield int(ts_ms), float(ltp), 0, 0, contribution, ofi
            continue
        buy_change = sell_change = None
        if pending and pending[0] is not None and tbq is not None:
            buy_change = tbq - pending[0]
        if pending and pending[1] is not None and tsq is not None:
            sell_change = tsq - pending[1]
        verdict = classify_update(
            price=ltp, traded=size, last_qty=last_qty,
            prior_bid=book[0] if book else None, prior_ask=book[1] if book else None,
            prior_price=prev_price, last_side=last_side,
            buy_pending_change=buy_change, sell_pending_change=sell_change)
        prev_price = ltp
        if verdict.side:
            last_side = verdict.side
        yield int(ts_ms), float(ltp), size, verdict.side, contribution, ofi


def prints_for(ticks: sqlite3.Connection, symbol: str, day: str,
               since_ms: int | None = None) -> list[tuple[int, float, int, int]]:
    """Volume-bearing raw updates for one session, classified against the prior book."""
    return [(ts_ms, ltp, size, side)
            for ts_ms, ltp, size, side, _, _ in _updates(ticks, symbol, day, since_ms) if size > 0]


def detect_recent(ticks: sqlite3.Connection, symbol: str, day: str, now_ms: int) -> list[WhaleEvent]:
    """Layer A over the trailing slice, so aggression exists during the
    session and not only after the night job. The slice starts with no
    carry-forward, so A3's size history differs from the full-day pass; the
    nightly run overwrites with the full-day result."""
    return detect(symbol, day, prints_for(ticks, symbol, day, now_ms - A_LIVE_LOOKBACK_MS))


def save_events(connection: sqlite3.Connection, day: str, events: list[WhaleEvent]) -> int:
    ensure_schema(connection)
    if not events:
        return 0
    with connection:
        connection.executemany(
            """INSERT OR REPLACE INTO whale_events
               (symbol, day, ts_ms, layer, kind, side, quantity, price, score, evidence)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            [(e.symbol, day, e.ts_ms, e.layer, e.kind, e.side, e.quantity, e.price,
              e.score, e.evidence) for e in events])
    return len(events)


def events_for(connection: sqlite3.Connection, day: str, symbol: str | None = None,
               limit: int = 300) -> list[dict]:
    try:
        where, params = "day = ?", [day]
        if symbol:
            where += " AND symbol = ?"
            params.append(symbol)
        cursor = connection.execute(
            f"""SELECT * FROM whale_events WHERE {where} ORDER BY score DESC, ts_ms DESC LIMIT ?""",
            (*params, limit))
        names = [d[0] for d in cursor.description]
        return [dict(zip(names, row)) for row in cursor.fetchall()]
    except sqlite3.OperationalError:
        return []


# ---------------------------------------------------------------------------
# Chain snapshots
# ---------------------------------------------------------------------------

def save_chain(connection: sqlite3.Connection, ts: int, underlying: str, expiry: str,
               spot: float, entries, fp: float = 0.0, vix: float | None = None) -> int:
    """One option-chain snapshot. ``entries`` carry strike/option_type/symbol/ltp/volume/oi;
    bid/ask/prev_oi are taken when present so an older entry shape still stores.

    ``prev_oi`` is stored NULL, not 0, when the broker did not supply one. The
    day-over-day read treats a zero there as a real prior close of nothing and
    books the strike's entire OI as a build; an absent prior close is unknown,
    and unknown has to survive the write to be recognised at the read.
    """
    ensure_schema(connection)
    rows = [(ts, underlying, expiry, float(e.strike), e.option_type, e.symbol,
             float(e.ltp or 0), int(e.volume or 0), int(e.oi or 0), spot,
             float(getattr(e, "bid", 0) or 0), float(getattr(e, "ask", 0) or 0),
             _prev_oi(e), float(fp or 0), vix) for e in entries]
    if not rows:
        return 0
    with connection:
        connection.executemany(
            """INSERT OR REPLACE INTO chain_snapshots
               (ts, underlying, expiry, strike, option_type, symbol, ltp, volume, oi, spot,
                bid, ask, prev_oi, fp, vix)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
        connection.execute("INSERT OR IGNORE INTO chain_days (day, underlying) VALUES (?,?)",
                           (day_of(ts), underlying))
    return len(rows)


def _prev_oi(entry) -> int | None:
    """The broker's prior close for this strike, or None when it gave none.
    Zero is treated as no answer: Fyers fills the field with 0 when it has
    nothing, and a listed strike whose prior OI is genuinely zero contributes
    a build worth nothing next to a whole book mis-read as one."""
    value = getattr(entry, "prev_oi", None)
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    return value or None


CHAIN_RETENTION_DAYS = 90


def condense_chain_snapshots(connection: sqlite3.Connection, before_day: str,
                             keep_days: int = CHAIN_RETENTION_DAYS) -> int:
    """Beyond ``keep_days``, keep only each day's closing snapshot per root.

    Nothing else reads an old intraday chain: the windows are stored in
    whale_strike_windows, alert outcomes are same-day, and Layer D needs the
    prior close and nothing before it. Keeping the whole minute series is
    ~77,000 rows a session for a table that had no retention rule at all.
    """
    ensure_schema(connection)
    cutoff = (date.fromisoformat(before_day) - timedelta(days=keep_days)).isoformat()
    closes = [row[0] for row in connection.execute(
        f"""SELECT MAX(ts) FROM chain_snapshots WHERE {IST_DAY_SQL} < ?
            GROUP BY {IST_DAY_SQL}, underlying""", (cutoff,))]
    if not closes:
        return 0
    with connection:
        cursor = connection.execute(
            f"""DELETE FROM chain_snapshots WHERE {IST_DAY_SQL} < ?
                AND ts NOT IN ({", ".join("?" * len(closes))})""", (cutoff, *closes))
    return cursor.rowcount


def snapshot_times(connection: sqlite3.Connection, underlying: str, day: str) -> list[int]:
    start, end = session_span(day)
    try:
        return [row[0] for row in connection.execute(
            """SELECT DISTINCT ts FROM chain_snapshots WHERE underlying = ? AND ts >= ? AND ts < ?
               ORDER BY ts""", (underlying, start, end))]
    except sqlite3.OperationalError:
        return []


def _snapshot(connection: sqlite3.Connection, underlying: str, ts: int) -> dict[tuple, dict]:
    rows = _dicts(connection.execute(
        "SELECT * FROM chain_snapshots WHERE underlying = ? AND ts = ?", (underlying, ts)))
    return {(row["strike"], row["option_type"]): row for row in rows}


def _underlying_price(rows: dict[tuple, dict]) -> tuple[float, bool]:
    """(price to solve delta against, whether it is the futures price)."""
    sample = next(iter(rows.values()))
    fp = sample.get("fp") or 0
    return (float(fp), True) if fp else (float(sample["spot"] or 0), False)


def chain_window(connection: sqlite3.Connection, underlying: str, seconds: int = 180) -> dict:
    """Layer B4 and a delta-free B1: per-strike ΔOI over the last window, PCR, walls.

    Reported in units, not lots, and without delta weighting — the legacy
    read of the latest chain, kept for the panel's chain block.
    """
    try:
        latest = connection.execute(
            "SELECT MAX(ts) FROM chain_snapshots WHERE underlying = ?", (underlying,)).fetchone()[0]
    except sqlite3.OperationalError:
        return {"underlying": underlying, "strikes": [], "snapshots": 0}
    if latest is None:
        return {"underlying": underlying, "strikes": [], "snapshots": 0}
    earlier = connection.execute(
        "SELECT MAX(ts) FROM chain_snapshots WHERE underlying = ? AND ts <= ?",
        (underlying, latest - seconds)).fetchone()[0]
    now_rows = connection.execute(
        """SELECT strike, option_type, oi, volume, ltp, spot FROM chain_snapshots
           WHERE underlying = ? AND ts = ?""", (underlying, latest)).fetchall()
    then = {}
    if earlier is not None:
        then = {(r[0], r[1]): (r[2], r[3]) for r in connection.execute(
            """SELECT strike, option_type, oi, volume FROM chain_snapshots
               WHERE underlying = ? AND ts = ?""", (underlying, earlier))}
    strikes, call_oi, put_oi, call_vol, put_vol, spot = [], 0, 0, 0, 0, None
    for strike, kind, oi, volume, ltp, s in now_rows:
        spot = s
        prior = then.get((strike, kind))
        d_oi = oi - prior[0] if prior else 0
        d_vol = volume - prior[1] if prior else 0
        strikes.append({"strike": strike, "type": kind, "oi": oi, "d_oi": d_oi,
                        "d_volume": d_vol, "ltp": ltp,
                        "notional": round(d_oi * (spot or 0), 0)})
        if kind == "CE":
            call_oi += oi; call_vol += volume
        else:
            put_oi += oi; put_vol += volume
    strikes.sort(key=lambda r: -abs(r["d_oi"]))
    walls = sorted(now_rows, key=lambda r: -r[2])[:4]
    return {
        "underlying": underlying, "as_of": latest, "window_seconds": seconds,
        "spot": spot, "snapshots": 2 if earlier else 1,
        "pcr_oi": round(put_oi / call_oi, 3) if call_oi else None,
        "pcr_volume": round(put_vol / call_vol, 3) if call_vol else None,
        "strikes": strikes[:12],
        "walls": [{"strike": r[0], "type": r[1], "oi": r[2]} for r in walls],
    }


# ---------------------------------------------------------------------------
# Layer B — per-strike windows
# ---------------------------------------------------------------------------

@dataclass
class StrikeWindow:
    strike: float
    option_type: str
    symbol: str
    expiry: str
    oi: int
    prior_oi: int
    d_oi: int
    d_volume: int
    ltp: float
    d_ltp: float
    iv: float | None
    delta: float
    delta_source: str
    # 'prints' when the desk streams this leg and classified its trades;
    # 'premium' when the sign is inferred from the premium change net of the
    # underlying's move; 'none' when neither said anything.
    flow_sign: int
    flow_source: str
    # |ΔOI| x |Δ| x underlying, rupees — the magnitude that ranks the strike.
    dn: float
    # Δ x |ΔOI| x flow_sign in delta units, signed: bought calls and written
    # puts are long the underlying, written calls and bought puts short it.
    contribution: float
    bucket: int
    unusual: list


def _bracket(connection, underlying: str, at_ts: int, seconds: int) -> tuple[int | None, int | None]:
    latest = connection.execute(
        "SELECT MAX(ts) FROM chain_snapshots WHERE underlying = ? AND ts <= ?",
        (underlying, at_ts)).fetchone()[0]
    if latest is None:
        return None, None
    then = connection.execute(
        "SELECT MAX(ts) FROM chain_snapshots WHERE underlying = ? AND ts <= ?",
        (underlying, latest - seconds)).fetchone()[0]
    if then is None or latest - then > MAX_GAP_FACTOR * seconds or day_of(then) != day_of(latest):
        return latest, None
    return latest, then


def strike_windows(connection: sqlite3.Connection, underlying: str, at_ts: int,
                   seconds: int = WINDOW_SECONDS, *, flow_signs: dict | None = None,
                   medians: dict | None = None, rate: float = 0.065) -> tuple[dict, list[StrikeWindow]]:
    """Every strike's ΔOI over the window ending at the latest snapshot ≤ ``at_ts``.

    ``flow_signs`` maps option symbol -> +1/-1 from classified prints of the
    legs the desk actually streams; every other strike falls back to the
    premium proxy. ``medians`` maps (option_type, bucket) -> the 20-day median
    window volume for this slot, or None while that history does not exist.
    """
    latest, then = _bracket(connection, underlying, at_ts, seconds)
    meta = {"underlying": underlying, "as_of": latest, "then": then, "window_seconds": seconds}
    if latest is None or then is None:
        meta["status"] = "no_snapshot" if latest is None else "no_window"
        return meta, []
    now, before = _snapshot(connection, underlying, latest), _snapshot(connection, underlying, then)
    if not now or not before:
        meta["status"] = "no_window"
        return meta, []
    under_now, forward = _underlying_price(now)
    under_then, _ = _underlying_price(before)
    use_rate = 0.0 if forward else rate
    strikes = sorted({key[0] for key in now})
    step = min((b - a for a, b in zip(strikes, strikes[1:]) if b > a), default=50.0)
    when = datetime.fromtimestamp(latest, UTC)
    legs: list[StrikeWindow] = []
    for key, row in now.items():
        prior = before.get(key)
        if prior is None:
            continue
        d_oi = int(row["oi"] or 0) - int(prior["oi"] or 0)
        d_vol = int(row["volume"] or 0) - int(prior["volume"] or 0)
        d_ltp = float(row["ltp"] or 0) - float(prior["ltp"] or 0)
        d, iv, source = contract_delta(
            premium=float(row["ltp"] or 0), underlying=under_now, strike=float(row["strike"]),
            expiry=row["expiry"], option_type=row["option_type"], rate=use_rate, now=when)
        # Premium change net of the underlying's own move. What is left is
        # the part of the move the option's OWN order flow paid for.
        adjusted = d_ltp - d * (under_now - under_then)
        if flow_signs and row["symbol"] in flow_signs and flow_signs[row["symbol"]]:
            sign, flow_source = (1 if flow_signs[row["symbol"]] > 0 else -1), "prints"
        elif abs(adjusted) >= PREMIUM_PROXY_MIN:
            sign, flow_source = (1 if adjusted > 0 else -1), "premium"
        else:
            sign, flow_source = 0, "none"
        bucket = max(-BUCKET_LIMIT, min(BUCKET_LIMIT, round((float(row["strike"]) - under_now) / step)))
        dn = abs(d_oi) * abs(d) * under_now
        unusual: list[str] = []
        fresh = d_oi > 0 and d_oi >= CHURN_MIN_OI_SHARE * max(d_vol, 1)
        prior_oi = int(prior["oi"] or 0)
        if d_vol > 0 and prior_oi > 0 and d_vol >= prior_oi and fresh:
            unusual.append("volume_ge_oi")
        median = (medians or {}).get((row["option_type"], bucket))
        if median and d_vol >= UNUSUAL_MEDIAN_MULTIPLE * median and fresh:
            unusual.append("volume_3x_median")
        legs.append(StrikeWindow(
            float(row["strike"]), row["option_type"], row["symbol"], row["expiry"],
            int(row["oi"] or 0), prior_oi, d_oi, d_vol, float(row["ltp"] or 0), round(d_ltp, 2),
            iv, d, source, sign, flow_source, dn, d * abs(d_oi) * sign, bucket, unusual))
    legs.sort(key=lambda leg: -leg.dn)
    sample = next(iter(now.values()))
    meta.update({"status": "ok", "spot": sample["spot"], "fut": sample.get("fp") or None,
                 "underlying_price": under_now, "d_underlying": round(under_now - under_then, 2),
                 "step": step, "expiry": sample["expiry"], "vix": sample.get("vix")})
    return meta, legs


def bucket_medians(connection: sqlite3.Connection, underlying: str, slot: int, day: str,
                   days: int = HISTORY_DAYS) -> tuple[dict | None, int]:
    """Median window volume per (type, moneyness bucket) at this slot over the
    prior ``days`` sessions. (None, n) while only n < days sessions exist —
    a median of three days is not the distribution the rule was written for."""
    from .base_rates import median
    seen = [row[0] for row in connection.execute(
        """SELECT DISTINCT day FROM whale_windows WHERE underlying = ? AND day < ?
           ORDER BY day DESC LIMIT ?""", (underlying, day, days))]
    if len(seen) < days:
        return None, len(seen)
    groups: dict[tuple, list] = {}
    for kind, bucket, volume in connection.execute(
            """SELECT option_type, bucket, d_volume FROM whale_strike_windows
               WHERE underlying = ? AND slot = ? AND day >= ? AND day < ?""",
            (underlying, slot, seen[-1], day)):
        groups.setdefault((kind, bucket), []).append(volume)
    return {key: median(values) for key, values in groups.items()}, len(seen)


def structures(legs: list[StrikeWindow], step: float) -> list[dict]:
    """Pairs of same-sized fresh legs that read as one trade.

    A leg counts when it is a build (ΔOI > 0), has a flow sign, and carries
    at least max(DN_MIN, a quarter of the window's largest leg); two count as
    a pair when their delta-notionals are within PAIR_RATIO of each other.
    """
    if not legs:
        return []
    top = max(leg.dn for leg in legs)
    significant = [leg for leg in legs if leg.d_oi > 0 and leg.flow_sign and leg.dn >= max(DN_MIN, 0.25 * top)]
    found = []
    for index, a in enumerate(significant):
        for b in significant[index + 1:]:
            if min(a.dn, b.dn) / max(a.dn, b.dn) < 1 / PAIR_RATIO:
                continue
            if a.option_type != b.option_type:
                ce, pe = (a, b) if a.option_type == "CE" else (b, a)
                if ce.strike == pe.strike:
                    if ce.flow_sign == pe.flow_sign:
                        kind, direction = "straddle", 0
                    else:
                        kind, direction = "synthetic_future", ce.flow_sign
                elif ce.flow_sign == pe.flow_sign:
                    kind, direction = "strangle", 0
                else:
                    # Short call + long put is the bearish collar shape; the
                    # mirror is bullish.
                    kind = "risk_reversal"
                    direction = 1 if (ce.flow_sign > 0 and pe.flow_sign < 0) else -1
            elif abs(abs(a.strike - b.strike) - step) < 1e-6 and a.flow_sign != b.flow_sign:
                low, high = (a, b) if a.strike < b.strike else (b, a)
                kind = "vertical"
                if a.option_type == "CE":
                    direction = 1 if low.flow_sign > 0 else -1
                else:
                    direction = -1 if high.flow_sign > 0 else 1
            else:
                continue
            found.append({"kind": kind, "direction": direction,
                          "legs": [[leg.strike, leg.option_type, leg.flow_sign, leg.d_oi, round(leg.dn)]
                                   for leg in (a, b)],
                          "dn": round(a.dn + b.dn)})
    return sorted(found, key=lambda item: -item["dn"])[:6]


def walls_and_pcr(connection: sqlite3.Connection, underlying: str, latest: int, then: int,
                  day_open_ts: int | None) -> dict:
    """Largest-OI strikes now and where they were at the open, and the OI PCR
    now against the window's start."""
    def tops(ts):
        rows = connection.execute(
            """SELECT strike, option_type, oi FROM chain_snapshots
               WHERE underlying = ? AND ts = ? ORDER BY oi DESC""", (underlying, ts)).fetchall()
        return {"CE": [r for r in rows if r[1] == "CE"][:4], "PE": [r for r in rows if r[1] == "PE"][:4]}

    def pcr(ts):
        calls, puts = connection.execute(
            """SELECT SUM(CASE WHEN option_type = 'CE' THEN oi END),
                      SUM(CASE WHEN option_type = 'PE' THEN oi END)
               FROM chain_snapshots WHERE underlying = ? AND ts = ?""", (underlying, ts)).fetchone()
        # SUM over a side with no rows is NULL, not 0 — a chain response
        # that came back one-sided must read as a PCR of 0, not a crash.
        return round((puts or 0) / calls, 3) if calls else None

    now = tops(latest)
    opened = tops(day_open_ts) if day_open_ts is not None else {"CE": [], "PE": []}
    pcr_now, pcr_then = pcr(latest), pcr(then)
    jump = round(pcr_now - pcr_then, 3) if pcr_now is not None and pcr_then is not None else None
    return {
        "walls": {kind: [{"strike": r[0], "oi": r[2]} for r in rows] for kind, rows in now.items()},
        "migration": {kind: {"from": opened[kind][0][0] if opened[kind] else None,
                             "to": now[kind][0][0] if now[kind] else None} for kind in ("CE", "PE")},
        "pcr_oi": pcr_now, "pcr_oi_then": pcr_then, "pcr_jump": jump,
        "pcr_jumped": jump is not None and abs(jump) >= PCR_JUMP,
    }


def net_option_delta(legs: list[StrikeWindow], underlying_price: float) -> dict:
    units = sum(leg.contribution for leg in legs)
    return {"units": round(units, 1), "dn": round(units * underlying_price),
            "sign": (units > 0) - (units < 0),
            "signed_legs": sum(1 for leg in legs if leg.flow_sign),
            "unsigned_legs": sum(1 for leg in legs if not leg.flow_sign and leg.d_oi)}


def pcr_volume_window(legs: list[StrikeWindow]) -> float | None:
    calls = sum(leg.d_volume for leg in legs if leg.option_type == "CE")
    puts = sum(leg.d_volume for leg in legs if leg.option_type == "PE")
    return round(puts / calls, 3) if calls > 0 else None


# ---------------------------------------------------------------------------
# Layer C — the futures leg
# ---------------------------------------------------------------------------

FLOW_COLUMNS = ("symbol", "minute_ts", "day", "ltp", "cvd", "buy_volume", "sell_volume",
                "total_volume", "trades", "ofi_cum", "ofi_events", "depth_scale", "oi", "source")


class LiveFlow:
    """Per futures symbol: a live OFI state (the desk keeps none elsewhere) and
    the once-a-minute sample of it beside the desk's classified flow."""

    def __init__(self, symbols=()):
        self.ofi: dict = {}
        self.day: str | None = None
        self.watch(symbols)

    def watch(self, symbols) -> None:
        """The symbols sampled from now on — exactly these, not these as well.

        A retired series must drop out: ``sample`` walks every state it holds
        and a contract that rolled off would otherwise get a zero-flow live
        row written every minute for the rest of the process's life, which a
        nightly rebuild of its real day is then forbidden to displace.
        """
        from .ofi import OFIState
        wanted = list(symbols)
        for symbol in wanted:
            if symbol not in self.ofi:
                self.ofi[symbol] = OFIState(symbol=symbol)
        for symbol in [s for s in self.ofi if s not in wanted]:
            del self.ofi[symbol]

    def on_tick(self, tick) -> None:
        state = self.ofi.get(tick.symbol)
        if state is None:
            return
        # Wall clock, not the tick's stamp: a re-broadcast of yesterday's
        # close carries yesterday's date and must not roll the session.
        today = datetime.now(IST).date().isoformat()
        if self.day != today:
            self.day = today
            for each in self.ofi.values():
                each.reset()
        state.update(tick.timestamp.timestamp(), tick.bid, tick.ask, tick.bid_qty, tick.ask_qty)

    def sample(self, connection: sqlite3.Connection, stamp: int, flow_states: dict, quotes: dict) -> int:
        minute_ts = stamp - stamp % 60
        rows = []
        for symbol, ofi in self.ofi.items():
            flow, quote = flow_states.get(symbol), quotes.get(symbol)
            price = (quote.ltp if quote and quote.ltp else None) or (flow.last_price if flow else None)
            rows.append((symbol, minute_ts, day_of(stamp), price,
                         flow.cumulative_delta if flow else 0.0,
                         flow.buy_volume if flow else 0.0, flow.sell_volume if flow else 0.0,
                         flow.total_volume if flow else 0.0, flow.trades if flow else 0,
                         ofi.cumulative, ofi.events, ofi.depth_scale(float(stamp)),
                         quote.open_interest if quote else None, LIVE))
        return save_flow_minutes(connection, rows, LIVE)


# cvd and ofi_cum are signed and may fall; these only ever rise within a day,
# and a fall in one of them is a process that restarted mid-session and began
# counting again from zero — not selling.
MONOTONIC_FLOW_COLUMNS = ("total_volume", "trades", "buy_volume", "sell_volume", "ofi_events")


def _counters_reset(then: dict, latest: dict) -> bool:
    return any((latest.get(name) or 0) < (then.get(name) or 0) for name in MONOTONIC_FLOW_COLUMNS)


def save_flow_minutes(connection: sqlite3.Connection, rows: list[tuple], source: str) -> int:
    """Upsert futures minute rows. A rebuild never displaces a live row."""
    if not rows:
        return 0
    ensure_schema(connection)
    guard = "" if source == LIVE else f" WHERE whale_flow_minutes.source <> '{LIVE}'"
    assignments = ", ".join(f"{name} = excluded.{name}" for name in FLOW_COLUMNS[2:])
    with connection:
        connection.executemany(
            f"""INSERT INTO whale_flow_minutes ({", ".join(FLOW_COLUMNS)})
                VALUES ({", ".join("?" * len(FLOW_COLUMNS))})
                ON CONFLICT(symbol, minute_ts) DO UPDATE SET {assignments}{guard}""", rows)
    return len(rows)


def minute_flow_from_ticks(ticks: sqlite3.Connection, symbol: str, day: str) -> list[tuple]:
    """The futures leg rebuilt from raw ticks, one cumulative row per minute.

    Same walk as Layer A and the same classification the condenser applies, so
    a day rebuilt tonight matches the day sampled live to within the sampling
    moment. OI stays NULL: the socket never carried it.
    """
    rows: list[tuple] = []
    current = None
    cvd = buy = sell = total = 0.0
    trades = ofi_cum = 0.0
    ofi_events = 0
    last_price = None
    state = None
    for ts_ms, ltp, size, side, contribution, state in _updates(ticks, symbol, day):
        minute_ts = (ts_ms // 60_000) * 60
        if current is not None and minute_ts != current:
            rows.append((symbol, current, day, last_price, cvd, buy, sell, total, int(trades),
                         ofi_cum, ofi_events, state.depth_scale(current + 60.0), None, "raw"))
        current = minute_ts
        last_price = ltp
        if contribution is not None:
            ofi_cum += contribution
            ofi_events += 1
        if size > 0:
            trades += 1
            total += size
            if side > 0:
                buy += size
            elif side < 0:
                sell += size
            cvd = buy - sell
    if current is not None:
        rows.append((symbol, current, day, last_price, cvd, buy, sell, total, int(trades),
                     ofi_cum, ofi_events, state.depth_scale(current + 60.0), None, "raw"))
    return rows


def minute_flow_from_condensed(ticks: sqlite3.Connection, symbol: str, day: str) -> list[tuple]:
    """The futures leg from tick_minute_flow, for days the raw tier has already
    been condensed away. Running sums, so the row shape matches the live one."""
    start, end = session_span(day)
    try:
        minutes = ticks.execute(
            """SELECT f.minute_ts, f.close, f.buy_volume, f.sell_volume, f.volume, f.trades, f.ofi
               FROM tick_minute_flow f JOIN tick_symbols s ON s.id = f.symbol_id
               WHERE s.symbol = ? AND f.minute_ts >= ? AND f.minute_ts < ? ORDER BY f.minute_ts""",
            (symbol, start, end)).fetchall()
    except sqlite3.OperationalError:
        return []
    rows: list[tuple] = []
    buy = sell = total = 0.0
    trades = 0
    ofi_cum = 0.0
    for minute_ts, close, buy_volume, sell_volume, volume, count, ofi in minutes:
        buy += buy_volume or 0
        sell += sell_volume or 0
        total += volume or 0
        trades += count or 0
        ofi_cum += ofi or 0
        rows.append((symbol, minute_ts, day, close, buy - sell, buy, sell, total, trades,
                     ofi_cum, 0, None, None, "condensed"))
    return rows


def rebuild_flow_minutes(ticks: sqlite3.Connection, history: sqlite3.Connection,
                         symbol: str, day: str) -> int:
    """The nightly source for the futures leg: the condensed table once the
    day has been rolled up, the raw ticks until then. Live rows survive."""
    rows = (minute_flow_from_condensed(ticks, symbol, day)
            or minute_flow_from_ticks(ticks, symbol, day))
    if not rows:
        return 0
    clear_restarted_minutes(history, symbol, day)
    return save_flow_minutes(history, rows, rows[0][-1])


def restarted_minutes(connection: sqlite3.Connection, symbol: str, day: str) -> list[int]:
    """The live minutes from the first counter reset of the day onwards.

    A live row written after a mid-session restart counts from the restart,
    not from the open, so it cannot be read beside the rows before it. The
    rebuild counts from the open throughout and is the repair — but only if
    the live-wins rule steps aside for the rows it can prove are broken.
    """
    rows = _dicts(connection.execute(
        """SELECT * FROM whale_flow_minutes WHERE symbol = ? AND day = ? ORDER BY minute_ts""",
        (symbol, day)))
    for index in range(1, len(rows)):
        if _counters_reset(rows[index - 1], rows[index]):
            return [row["minute_ts"] for row in rows[index:]]
    return []


def clear_restarted_minutes(connection: sqlite3.Connection, symbol: str, day: str) -> int:
    minutes = restarted_minutes(connection, symbol, day)
    if not minutes:
        return 0
    with connection:
        connection.execute(
            f"""DELETE FROM whale_flow_minutes WHERE symbol = ? AND day = ?
                AND minute_ts IN ({", ".join("?" * len(minutes))})""", (symbol, day, *minutes))
    return len(minutes)


def futures_window(connection: sqlite3.Connection, symbol: str, at_ts: int,
                   seconds: int = WINDOW_SECONDS) -> dict | None:
    latest = _one(connection, """SELECT * FROM whale_flow_minutes WHERE symbol = ? AND minute_ts <= ?
                                 ORDER BY minute_ts DESC LIMIT 1""", (symbol, at_ts))
    then = _one(connection, """SELECT * FROM whale_flow_minutes WHERE symbol = ? AND minute_ts <= ?
                               ORDER BY minute_ts DESC LIMIT 1""", (symbol, at_ts - seconds))
    if not latest or not then or latest["day"] != then["day"] or latest["minute_ts"] == then["minute_ts"]:
        return None
    if _counters_reset(then, latest):
        # The counters restarted between the two rows. Differencing them would
        # fabricate a large sell — negative volume, negative trades — out of a
        # deployment, and the window row it feeds is permanent. No window.
        return None
    delta_units = latest["cvd"] - then["cvd"]
    ofi = latest["ofi_cum"] - then["ofi_cum"]
    price = latest["ltp"] or 0.0
    has_oi = latest["oi"] is not None and then["oi"] is not None
    return {"symbol": symbol, "ltp": price, "delta_units": round(delta_units), "ofi": round(ofi),
            "ofi_normalised": round(ofi / latest["depth_scale"], 3) if latest["depth_scale"] else None,
            "volume": latest["total_volume"] - then["total_volume"],
            "trades": latest["trades"] - then["trades"],
            "oi": latest["oi"], "d_oi": (latest["oi"] - then["oi"]) if has_oi else None,
            "dn": round(delta_units * price)}


def futures_sign(fut: dict, a_net: float) -> int:
    """Majority of three votes — inferred delta, book OFI, Layer A net — and
    the delta leg breaks a tie, because it is the one with a size behind it."""
    votes = [(fut["delta_units"] > 0) - (fut["delta_units"] < 0),
             (fut["ofi"] > 0) - (fut["ofi"] < 0),
             (a_net > 0) - (a_net < 0)]
    total = sum(votes)
    return (total > 0) - (total < 0) or votes[0]


def divergence(option: dict, fut: dict | None, a_net: float) -> dict:
    if fut is None:
        return {"status": "no_futures_window", "divergence": False, "ratio": None}
    sign = futures_sign(fut, a_net)
    if abs(fut["dn"]) < FUT_DN_FLOOR:
        return {"status": "futures_flat", "divergence": False, "ratio": None, "fut_sign": sign}
    ratio = abs(option["dn"]) / abs(fut["dn"])
    flagged = (option["sign"] * sign < 0 and ratio >= OPTION_TO_FUTURES_RATIO
               and abs(option["dn"]) >= DIVERGENCE_DN_MIN)
    return {"status": "ok", "divergence": flagged, "ratio": round(ratio, 2), "fut_sign": sign,
            "option_sign": option["sign"], "fresh_futures": (fut["d_oi"] or 0) > 0}


# ---------------------------------------------------------------------------
# Layer E — scoring, composite, alerts
# ---------------------------------------------------------------------------

SCORE_COLUMNS = {"a": "score_a", "b": "score_b", "c": "score_c", "d": "score_d"}


def zscore(connection: sqlite3.Connection, underlying: str, column: str, slot: int, day: str,
           value: float, days: int = HISTORY_DAYS) -> tuple[float | None, int]:
    """(z, sessions available). None until ``days`` prior sessions carry this
    slot — a z against three days would look like a number and mean nothing."""
    if column not in SCORE_COLUMNS.values():
        raise ValueError(column)
    rows = connection.execute(
        f"""SELECT day, {column} FROM whale_windows
            WHERE underlying = ? AND slot = ? AND day < ? AND {column} IS NOT NULL""",
        (underlying, slot, day)).fetchall()
    seen = sorted({row[0] for row in rows})[-days:]
    if len(seen) < days:
        return None, len(seen)
    sample = [row[1] for row in rows if row[0] >= seen[0]]
    mean = sum(sample) / len(sample)
    variance = sum((x - mean) ** 2 for x in sample) / max(1, len(sample) - 1)
    std = math.sqrt(variance)
    return (round((value - mean) / std, 3) if std > 1e-9 else 0.0), len(seen)


def scores(a_net: float, option: dict, div: dict, eod_score: float | None) -> dict:
    return {"a": abs(a_net), "b": float(abs(option["dn"])),
            "c": float(div["ratio"]) if div.get("divergence") else 0.0,
            "d": float(eod_score) if eod_score is not None else 0.0}


def composite(zs: dict) -> float | None:
    """Weighted z composite. D may be missing (no EOD yet) and its weight is
    then spread over the rest; any of A/B/C missing means no composite."""
    if any(zs.get(key) is None for key in ("a", "b", "c")):
        return None
    weights = dict(WEIGHTS)
    if zs.get("d") is None:
        weights.pop("d")
    total = sum(weights.values())
    return round(sum(weights[key] * zs[key] for key in weights) / total, 3)


def decayed_composite(connection: sqlite3.Connection, underlying: str, day: str,
                      at_ts: int) -> float | None:
    """Half-life-weighted mean of the day's window composites up to ``at_ts``.

    A mean, not a sum: a steady 2.0 must read 2.0 however many windows have
    been evaluated, and a single spike must fade rather than be diluted.
    """
    rows = connection.execute(
        """SELECT ts, composite FROM whale_windows
           WHERE underlying = ? AND day = ? AND ts <= ? AND composite IS NOT NULL""",
        (underlying, day, at_ts)).fetchall()
    if not rows:
        return None
    weights = [(0.5 ** ((at_ts - ts) / HALF_LIFE_SECONDS), value) for ts, value in rows]
    return round(sum(w * v for w, v in weights) / sum(w for w, _ in weights), 3)


def maybe_alert(connection: sqlite3.Connection, underlying: str, ts: int, day: str,
                value: float | None, direction: int, spot, fut, evidence: dict) -> int | None:
    """One alert per underlying per half-life once the decayed composite
    clears the threshold. Returns the row id, or None."""
    if value is None or value < ALERT_THRESHOLD:
        return None
    last = connection.execute(
        "SELECT ts FROM whale_alerts WHERE underlying = ? AND day = ? ORDER BY ts DESC LIMIT 1",
        (underlying, day)).fetchone()
    if last and ts - last[0] < HALF_LIFE_SECONDS:
        return None
    with connection:
        cursor = connection.execute(
            """INSERT INTO whale_alerts (underlying, ts, day, composite, direction, spot, fut, evidence)
               VALUES (?,?,?,?,?,?,?,?)""",
            (underlying, ts, day, value, direction, spot, fut, json.dumps(evidence)))
    return cursor.lastrowid


def mark_alert_sent(database_path: str, alert_id: int) -> None:
    connection = sqlite3.connect(database_path, timeout=30)
    try:
        with connection:
            connection.execute("UPDATE whale_alerts SET sent = 1 WHERE id = ?", (alert_id,))
    finally:
        connection.close()


OUTCOME_HORIZONS = ((15, "next_15"), (30, "next_30"), (60, "next_60"))
# The snapshot that answers a horizon must sit within two collector cadences
# of it. The last snapshot before the horizon would otherwise do — and after
# the close that is the 15:39 chain answering a 16:25 question.
OUTCOME_TOLERANCE_SECONDS = MAX_GAP_FACTOR * 60


def fill_outcomes(connection: sqlite3.Connection, now_ts: int) -> int:
    """Write what the spot did 15/30/60 minutes after each alert, from the
    chain's own spot series. A horizon past the close stays NULL and reads as
    unresolved — the day ended, the question did not get an answer.

    Bounded to ``now_ts``'s own session. An alert raised after ~14:40 can never
    get a 60-minute answer, so its next_60 is NULL for the life of the
    database; without the bound every one of those is re-queried, three chain
    lookups each, on every window evaluation of every session thereafter.
    """
    filled = 0
    for row in _dicts(connection.execute(
            "SELECT id, underlying, ts, direction, spot, next_15, next_30, next_60 "
            "FROM whale_alerts WHERE next_60 IS NULL AND ts >= ?",
            (session_span(day_of(now_ts))[0],))):
        for horizon, column in OUTCOME_HORIZONS:
            target = row["ts"] + horizon * 60
            if now_ts < target:
                break
            if row[column] or not row["spot"]:
                continue
            later = connection.execute(
                """SELECT spot FROM chain_snapshots WHERE underlying = ? AND ts <= ? AND ts >= ?
                   ORDER BY ts DESC LIMIT 1""",
                (row["underlying"], target, max(row["ts"] + 1, target - OUTCOME_TOLERANCE_SECONDS))).fetchone()
            if not later or later[0] is None:
                continue
            move = later[0] - row["spot"]
            record = {"spot": later[0], "move_pts": round(move, 2),
                      "move_pct": round(100 * move / row["spot"], 3),
                      "agreed": bool(row["direction"]) and (move > 0) == (row["direction"] > 0)}
            with connection:
                connection.execute(f"UPDATE whale_alerts SET {column} = ? WHERE id = ?",
                                   (json.dumps(record), row["id"]))
            filled += 1
    return filled


def prior_eod_score(history: sqlite3.Connection, underlying: str, day: str) -> float | None:
    """Layer D's score from the most recent close before ``day``."""
    try:
        row = history.execute(
            "SELECT score FROM whale_eod WHERE underlying = ? AND day < ? ORDER BY day DESC LIMIT 1",
            (underlying, day)).fetchone()
    except sqlite3.OperationalError:
        return None
    return row[0] if row else None


def window_payload(connection: sqlite3.Connection, row: dict) -> dict:
    """A stored window row in the shape evaluate_window returns.

    The page reads a window replayed by the night job and one the live loop
    produced a minute ago through the same fields, so the reshaping lives
    beside the writer rather than in the view.
    """
    underlying, ts, day = row["underlying"], row["ts"], row["day"]
    legs = _dicts(connection.execute(
        """SELECT * FROM whale_strike_windows WHERE underlying = ? AND ts = ? ORDER BY dn DESC""",
        (underlying, ts)))
    for leg in legs:
        leg["unusual"] = json.loads(leg.get("unusual") or "[]")
        for column in ("underlying", "ts", "day", "slot"):
            leg.pop(column, None)
    walls = json.loads(row.get("walls") or "{}")
    units = row.get("net_delta_units") or 0.0
    option = {"units": units, "dn": row.get("net_dn") or 0, "sign": (units > 0) - (units < 0),
              "signed_legs": sum(1 for leg in legs if leg["flow_sign"]),
              "unsigned_legs": sum(1 for leg in legs if not leg["flow_sign"] and leg["d_oi"])}
    fut = None
    if row.get("fut_delta_units") is not None:
        fut = {"symbol": row.get("fut_symbol"), "delta_units": row["fut_delta_units"],
               "ofi": row.get("fut_ofi"), "dn": row.get("fut_dn"), "oi": row.get("fut_oi"),
               "d_oi": row.get("fut_d_oi")}
    ratio = row.get("divergence_ratio")
    div = {"status": "ok" if ratio is not None else ("no_futures_window" if fut is None else "futures_flat"),
           "divergence": bool(row.get("divergence")), "ratio": ratio, "fut_sign": row.get("fut_sign"),
           "option_sign": option["sign"], "fresh_futures": bool(fut and (fut["d_oi"] or 0) > 0)}
    alert = connection.execute(
        "SELECT id FROM whale_alerts WHERE underlying = ? AND ts = ?", (underlying, ts)).fetchone()
    days = row.get("history_days") or 0
    return {
        "underlying": underlying, "as_of": ts, "then": row.get("then_ts"), "day": day,
        "slot": row.get("slot"), "status": "ok", "source": row.get("source"),
        "spot": row.get("spot"), "fut": row.get("fut"), "vix": None, "expiry": row.get("expiry"),
        "lot": lot_size(row.get("fut_symbol") or underlying, day),
        "strikes": legs[:12], "unusual": [leg for leg in legs if leg["unusual"]],
        "structures": json.loads(row.get("structures") or "[]"),
        "walls": walls.get("walls", {}), "migration": walls.get("migration", {}),
        "pcr_oi": row.get("pcr_oi"), "pcr_oi_then": row.get("pcr_oi_then"),
        "pcr_jump": row.get("pcr_jump"), "pcr_jumped": bool(walls.get("pcr_jumped")),
        "pcr_vol_window": row.get("pcr_vol_window"), "net_option_delta": option,
        "futures": fut, "divergence": div, "aggression_net": row.get("a_net") or 0.0,
        "scores": {key: row.get(column) for key, column in SCORE_COLUMNS.items()},
        "z": {key: row.get(f"z_{key}") for key in SCORE_COLUMNS},
        "composite": row.get("composite"), "composite_decayed": row.get("composite_decayed"),
        "history": {"days": days, "required": HISTORY_DAYS,
                    "status": "ok" if days >= HISTORY_DAYS else "insufficient"},
        "alert_id": alert[0] if alert else None,
    }


def alert_payload(row: dict) -> dict:
    """A whale_alerts row with its JSON columns decoded. A horizon the session
    ended before stays None, and the page reads that as unresolved."""
    out = dict(row)
    out["evidence"] = json.loads(row.get("evidence") or "{}")
    for _, column in OUTCOME_HORIZONS:
        out[column] = json.loads(row[column]) if row.get(column) else None
    return out


def _a_net(connection: sqlite3.Connection, day: str, futures_symbol: str, at_ms: int) -> float:
    events = [WhaleEvent(r["symbol"], r["ts_ms"], r["kind"], r["side"], r["quantity"] or 0,
                         r["price"] or 0.0, r["score"], r["evidence"] or "")
              for r in events_for(connection, day, futures_symbol, limit=4000) if r["ts_ms"] <= at_ms]
    return aggression(events, at_ms)["net"]


def evaluate_window(history: sqlite3.Connection, underlying: str, futures_symbol: str, at_ts: int,
                    *, flow_signs: dict | None = None, eod_score: float | None = None,
                    source: str = LIVE) -> dict:
    """One underlying, one window ending at the latest snapshot ≤ ``at_ts``.

    Persists the per-strike rows and the window row, then the decayed
    composite, the alert if one is due, and any alert outcomes that have
    matured. A nightly pass leaves a window the live loop already wrote.
    """
    ensure_schema(history)
    day, slot = day_of(at_ts), slot_of(at_ts)
    medians, hist_days = bucket_medians(history, underlying, slot, day)
    history_block = {"days": hist_days, "required": HISTORY_DAYS,
                     "status": "ok" if hist_days >= HISTORY_DAYS else "insufficient"}
    meta, legs = strike_windows(history, underlying, at_ts, flow_signs=flow_signs, medians=medians)
    if meta.get("status") != "ok":
        return {**meta, "day": day, "slot": slot, "history": history_block}
    latest, then = meta["as_of"], meta["then"]
    if source != LIVE:
        kept = history.execute("SELECT source FROM whale_windows WHERE underlying = ? AND ts = ?",
                               (underlying, latest)).fetchone()
        if kept and kept[0] == LIVE:
            return {"underlying": underlying, "as_of": latest, "then": then, "day": day,
                    "slot": slot, "status": "ok", "source": LIVE, "history": history_block}
    open_ts = history.execute(
        "SELECT MIN(ts) FROM chain_snapshots WHERE underlying = ? AND ts >= ? AND ts < ?",
        (underlying, *session_span(day))).fetchone()[0]
    walls = walls_and_pcr(history, underlying, latest, then, open_ts)
    option = net_option_delta(legs, meta["underlying_price"])
    structs = structures(legs, meta["step"])
    a_net = _a_net(history, day, futures_symbol, latest * 1000)
    fut = futures_window(history, futures_symbol, latest)
    div = divergence(option, fut, a_net)
    raw = scores(a_net, option, div, eod_score)
    zs = {key: zscore(history, underlying, SCORE_COLUMNS[key], slot, day, raw[key])[0]
          for key in ("a", "b", "c")}
    zs["d"] = (zscore(history, underlying, SCORE_COLUMNS["d"], slot, day, raw["d"])[0]
               if eod_score is not None else None)
    comp = composite(zs)
    unusual = [leg for leg in legs if leg.unusual]
    with history:
        history.executemany(
            """INSERT OR REPLACE INTO whale_strike_windows
               (underlying, ts, day, slot, expiry, strike, option_type, symbol, oi, prior_oi, d_oi,
                d_volume, ltp, d_ltp, iv, delta, delta_source, flow_sign, flow_source, dn,
                contribution, bucket, unusual)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [(underlying, latest, day, slot, leg.expiry, leg.strike, leg.option_type, leg.symbol,
              leg.oi, leg.prior_oi, leg.d_oi, leg.d_volume, leg.ltp, leg.d_ltp, leg.iv, leg.delta,
              leg.delta_source, leg.flow_sign, leg.flow_source, leg.dn, leg.contribution, leg.bucket,
              json.dumps(leg.unusual)) for leg in legs])
        history.execute(
            """INSERT OR REPLACE INTO whale_windows
               (underlying, ts, day, slot, window_seconds, then_ts, spot, fut, expiry,
                pcr_oi, pcr_oi_then, pcr_jump, pcr_vol_window, net_delta_units, net_dn, unusual_count,
                fut_symbol, fut_delta_units, fut_ofi, fut_dn, fut_oi, fut_d_oi, fut_sign,
                a_net, divergence_ratio, divergence, structures, walls, unusual,
                score_a, score_b, score_c, score_d, z_a, z_b, z_c, z_d, composite, composite_decayed,
                history_days, source)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (underlying, latest, day, slot, meta["window_seconds"], then, meta["spot"], meta["fut"],
             meta["expiry"], walls["pcr_oi"], walls["pcr_oi_then"], walls["pcr_jump"],
             pcr_volume_window(legs), option["units"], option["dn"], len(unusual),
             futures_symbol, fut["delta_units"] if fut else None, fut["ofi"] if fut else None,
             fut["dn"] if fut else None, fut["oi"] if fut else None, fut["d_oi"] if fut else None,
             div.get("fut_sign"), a_net, div.get("ratio"), int(bool(div.get("divergence"))),
             json.dumps(structs), json.dumps(walls),
             json.dumps([[leg.strike, leg.option_type, leg.unusual] for leg in unusual]),
             raw["a"], raw["b"], raw["c"], raw["d"], zs["a"], zs["b"], zs["c"], zs["d"], comp, None,
             hist_days, source))
    decayed = decayed_composite(history, underlying, day, latest)
    with history:
        history.execute("UPDATE whale_windows SET composite_decayed = ? WHERE underlying = ? AND ts = ?",
                        (decayed, underlying, latest))
    evidence = {"strikes": [asdict(leg) for leg in legs[:6]], "structures": structs,
                "divergence": div, "aggression": a_net, "walls": walls, "net_option_delta": option}
    alert_id = maybe_alert(history, underlying, latest, day, decayed, option["sign"],
                           meta["spot"], meta["fut"], evidence)
    fill_outcomes(history, latest)
    first_day = history.execute("SELECT MIN(day) FROM whale_windows WHERE underlying = ?",
                                (underlying,)).fetchone()[0]
    return {
        "underlying": underlying, "as_of": latest, "then": then, "day": day, "slot": slot,
        "status": "ok", "source": source, "spot": meta["spot"], "fut": meta["fut"], "vix": meta["vix"],
        "expiry": meta["expiry"], "lot": lot_size(futures_symbol, day),
        "strikes": [asdict(leg) for leg in legs[:12]], "unusual": [asdict(leg) for leg in unusual],
        "structures": structs, **walls, "pcr_vol_window": pcr_volume_window(legs),
        "net_option_delta": option, "futures": fut, "divergence": div, "aggression_net": a_net,
        "scores": raw, "z": zs, "composite": comp, "composite_decayed": decayed,
        "history": {**history_block, "regime_breaks": spans_a_break(first_day, day) if first_day else []},
        "alert_id": alert_id,
    }


# ---------------------------------------------------------------------------
# Layer D — the close against the previous close
# ---------------------------------------------------------------------------

def _z_against(sample: list, value: float) -> float | None:
    if len(sample) < HISTORY_DAYS:
        return None
    mean = sum(sample) / len(sample)
    std = math.sqrt(sum((x - mean) ** 2 for x in sample) / max(1, len(sample) - 1))
    return round((value - mean) / std, 3) if std > 1e-9 else 0.0


def _day_totals(history: sqlite3.Connection, symbol: str, day: str) -> tuple[float, int]:
    """(volume, trades) for the day, summed from the per-minute increments of
    the running counters. A row lower than the one before it is a restart, and
    its own value is then the increment since that restart."""
    rows = history.execute(
        """SELECT total_volume, trades FROM whale_flow_minutes WHERE symbol = ? AND day = ?
           ORDER BY minute_ts""", (symbol, day)).fetchall()
    volume = trades = 0.0
    previous_volume = previous_trades = 0.0
    for row_volume, row_trades in rows:
        row_volume, row_trades = float(row_volume or 0), float(row_trades or 0)
        volume += row_volume - previous_volume if row_volume >= previous_volume else row_volume
        trades += row_trades - previous_trades if row_trades >= previous_trades else row_trades
        previous_volume, previous_trades = row_volume, row_trades
    return volume, int(trades)


def eod(history: sqlite3.Connection, day: str, underlying: str, futures_symbol: str) -> dict:
    """The day's last snapshot against the previous close, per strike and in
    total, plus the futures OI and average print size.

    The previous close is the desk's own prior EOD snapshot when it has one;
    on the first day it is Fyers' prev_oi carried on the snapshot, so the
    day-over-day read exists from day one. Scores need twenty EOD rows.
    """
    ensure_schema(history)
    start, end = session_span(day)
    close_ts = history.execute(
        "SELECT MAX(ts) FROM chain_snapshots WHERE underlying = ? AND ts >= ? AND ts < ?",
        (underlying, start, end)).fetchone()[0]
    if close_ts is None:
        return {"day": day, "underlying": underlying, "status": "no_snapshots"}
    prior = _one(history, """SELECT * FROM whale_eod WHERE underlying = ? AND day < ?
                             ORDER BY day DESC LIMIT 1""", (underlying, day))
    now = _snapshot(history, underlying, close_ts)
    before = _snapshot(history, underlying, prior["close_ts"]) if prior and prior["close_ts"] else {}
    call_oi = sum(int(r["oi"] or 0) for k, r in now.items() if k[1] == "CE")
    put_oi = sum(int(r["oi"] or 0) for k, r in now.items() if k[1] == "PE")
    # A strike with no comparable prior close is unknown, not built from zero.
    # The band re-centres as spot moves, so a strike absent from yesterday's
    # snapshot and carrying no prev_oi would otherwise book its whole open
    # interest as a one-day build — millions of units of imaginary positioning
    # feeding the z-scores, the day's score and tomorrow's Layer D.
    per_strike, skipped = [], 0
    for key, row in now.items():
        oi = int(row["oi"] or 0)
        if key in before:
            was = int(before[key]["oi"] or 0)
        else:
            was = row.get("prev_oi")
            was = int(was) if was else None
        if was is None:
            skipped += 1
        per_strike.append((key[0], key[1], oi, None if was is None else oi - was))
    d_call = sum(r[3] for r in per_strike if r[1] == "CE" and r[3] is not None)
    d_put = sum(r[3] for r in per_strike if r[1] == "PE" and r[3] is not None)
    pcr = round(put_oi / call_oi, 3) if call_oi else None
    fut_row = _one(history, """SELECT oi FROM whale_flow_minutes WHERE symbol = ? AND day = ?
                               AND oi IS NOT NULL ORDER BY minute_ts DESC LIMIT 1""", (futures_symbol, day))
    fut_oi = fut_row["oi"] if fut_row else None
    # Only against the same series. On the session after a monthly rollover the
    # prior row's fut_oi is the expiring contract's settled book and this one's
    # is a contract still building — the difference is a large invented unwind.
    same_series = bool(prior) and (prior.get("fut_symbol") or futures_symbol) == futures_symbol
    fut_pdoi = prior["fut_oi"] if same_series else None
    # Summed per-minute increments, not MAX() of the running totals: those
    # counters restart at zero when the process does, and MAX() would then
    # describe only whichever side of the restart happened to be larger.
    volume, trades = _day_totals(history, futures_symbol, day)
    avg_trade = round(volume / trades, 2) if volume and trades else None
    priors = _dicts(history.execute(
        """SELECT d_call_oi, d_put_oi, fut_oi, fut_pdoi, fut_avg_trade, score FROM whale_eod
           WHERE underlying = ? AND day < ? ORDER BY day DESC LIMIT ?""", (underlying, day, HISTORY_DAYS)))
    prior_avgs = [r["fut_avg_trade"] for r in priors if r["fut_avg_trade"] is not None]
    pct = (round(100 * sum(1 for x in prior_avgs if x <= avg_trade) / len(prior_avgs), 1)
           if avg_trade is not None and len(prior_avgs) >= HISTORY_DAYS else None)
    fut_d_oi = (fut_oi - fut_pdoi) if fut_oi is not None and fut_pdoi is not None else None
    zs = [z for z in (
        _z_against([r["d_call_oi"] for r in priors if r["d_call_oi"] is not None], d_call),
        _z_against([r["d_put_oi"] for r in priors if r["d_put_oi"] is not None], d_put),
        _z_against([r["fut_oi"] - r["fut_pdoi"] for r in priors
                    if r["fut_oi"] is not None and r["fut_pdoi"] is not None], fut_d_oi)
        if fut_d_oi is not None else None,
        _z_against(prior_avgs, avg_trade) if avg_trade is not None else None,
    ) if z is not None]
    score = round(sum(abs(z) for z in zs) / len(zs), 3) if zs else None
    z = _z_against([r["score"] for r in priors if r["score"] is not None], score) if score is not None else None
    sample = next(iter(now.values()))
    top_oi = sorted(per_strike, key=lambda r: -r[2])[:10]
    top_d = sorted([r for r in per_strike if r[3] is not None], key=lambda r: -abs(r[3]))[:10]
    walls = {"CE": [r[0] for r in top_oi if r[1] == "CE"][:3], "PE": [r[0] for r in top_oi if r[1] == "PE"][:3]}
    row = {"day": day, "underlying": underlying, "close_ts": close_ts, "spot": sample["spot"],
           "fut": sample.get("fp") or None, "call_oi": call_oi, "put_oi": put_oi, "pcr_oi": pcr,
           "prior_pcr_oi": prior["pcr_oi"] if prior else None, "d_call_oi": d_call, "d_put_oi": d_put,
           "fut_oi": fut_oi, "fut_pdoi": fut_pdoi, "fut_avg_trade": avg_trade, "fut_avg_trade_pct": pct,
           "top_oi": top_oi, "top_d_oi": top_d, "walls": walls, "score": score, "z": z,
           "history_days": len(priors), "fut_symbol": futures_symbol, "d_oi_skipped": skipped}
    with history:
        history.execute(
            """INSERT OR REPLACE INTO whale_eod
               (day, underlying, close_ts, spot, fut, call_oi, put_oi, pcr_oi, prior_pcr_oi,
                d_call_oi, d_put_oi, fut_oi, fut_pdoi, fut_avg_trade, fut_avg_trade_pct,
                top_oi, top_d_oi, walls, score, z, history_days, fut_symbol, d_oi_skipped)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (day, underlying, close_ts, row["spot"], row["fut"], call_oi, put_oi, pcr, row["prior_pcr_oi"],
             d_call, d_put, fut_oi, fut_pdoi, avg_trade, pct, json.dumps(top_oi), json.dumps(top_d),
             json.dumps(walls), score, z, len(priors), futures_symbol, skipped))
    return {**row, "status": "ok"}
