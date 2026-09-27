"""A positional auction lane, reading days and weeks instead of minutes.

The intraday desk in ``mp_engine`` trades the session it is inside: initial
balance, opening range, value-area probes, flat by 15:20. That lane is
untouched by this module and keeps its own book.

This one asks a different question once a day, after the close: given where
value has been migrating over the last sessions, where the week's composite
value sits, and which prior points of control were never traded back to, is
there a multi-day position worth holding? Its setups are the classic
longer-horizon auction reads:

* **value_migration** — three consecutive sessions building value in one
  direction. The most reliable positional tell the profile gives, because it is
  the auction repeatedly agreeing on a new price, not a single day's excursion.
* **weekly_breakout** — the session accepts outside the week's composite value
  area, with session flow agreeing. Acceptance, not a touch: the close has to
  hold outside.
* **naked_poc_target** — price is travelling toward an untested prior POC with
  nothing structural in the way. A magnet trade, held until the level is
  reached.
* **failed_breakout** — the session probed outside weekly value and closed back
  inside. The auction rejected the new price, and the reversal runs back across
  the range.

Every evaluation is recorded whether or not it fires, with the full reference
context attached, so which of these actually pay is a measurable question
rather than an opinion. Direction is expressed in the option, exactly as the
intraday desk does it: bullish buys the ATM call, bearish buys the ATM put.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from .profile_history import MarketReference, ensure_tables as ensure_profile_tables

IST = ZoneInfo("Asia/Kolkata")
# Three sessions is the shortest run that distinguishes migration from a single
# outlier day followed by a bounce.
MIGRATION_SESSIONS = 3
# Acceptance outside a composite value area, as a share of that area's width.
# A close one tick past VAH is a touch; a close well clear of it is acceptance.
ACCEPTANCE_FRACTION = 0.10
# A naked POC further away than this (as a share of the weekly range) is not a
# trade, it is a hope.
NAKED_POC_MAX_REACH = 1.5
MIN_SESSIONS_FOR_CONTEXT = 5

SCHEMA = """
CREATE TABLE IF NOT EXISTS positional_evaluations (
  symbol TEXT NOT NULL,
  day TEXT NOT NULL,
  setup TEXT,
  direction TEXT,
  fired INTEGER NOT NULL DEFAULT 0,
  reason TEXT NOT NULL,
  close REAL,
  context TEXT NOT NULL,
  evaluated_at TEXT NOT NULL,
  PRIMARY KEY (symbol, day)
);
CREATE INDEX IF NOT EXISTS idx_positional_day ON positional_evaluations(day);
"""


@dataclass
class PositionalBias:
    setup: str
    direction: str
    reason: str
    target: float | None = None
    stop: float | None = None
    horizon_days: int = 5

    @property
    def option_type(self) -> str:
        return "CE" if self.direction == "bullish" else "PE"


@dataclass
class PositionalContext:
    """Everything the decision saw, flattened for storage and study."""
    symbol: str
    day: str
    close: float | None = None
    session: dict = field(default_factory=dict)
    migration_run: list[str] = field(default_factory=list)
    location: dict = field(default_factory=dict)
    alignment: str = "unknown"
    levels: dict = field(default_factory=dict)
    naked_pocs: list[float] = field(default_factory=list)
    sessions_available: int = 0


def migration_run(history: list[dict]) -> list[str]:
    """The unbroken tail of same-direction value migrations, newest last.

    ``overlapping_higher`` counts toward an up-run: value that keeps shifting up
    while still touching yesterday's is the ordinary way a trend builds. A
    single ``inside`` or ``engulfing`` session breaks the run, because the
    auction stopped agreeing on a direction.
    """
    up = {"higher", "overlapping_higher"}
    down = {"lower", "overlapping_lower"}
    run: list[str] = []
    for row in reversed(history):
        value = row.get("value_migration") or "unknown"
        if not run:
            if value in up or value in down:
                run.append(value)
                continue
            break
        same = (run[-1] in up and value in up) or (run[-1] in down and value in down)
        if not same:
            break
        run.append(value)
    return list(reversed(run))


def evaluate(*, symbol: str, day: str, session: dict, reference: MarketReference,
             history: list[dict]) -> tuple[PositionalBias | None, PositionalContext, str]:
    """(bias | None, recorded context, reason). Called once per session close."""
    close = session.get("close")
    context = PositionalContext(
        symbol=symbol, day=day, close=close, session=_slim(session),
        sessions_available=reference.sessions_available,
        naked_pocs=reference.naked_pocs[:6],
    )
    if close is None:
        return None, context, "session has no close"
    context.location = reference.location(close)
    context.alignment = reference.alignment(close)
    context.levels = reference.levels()
    context.migration_run = migration_run(history)

    # None means unmeasurable, not neutral: an index has no aggressor split at
    # all. The migration read is structural and stands on its own there; the
    # other setups keep requiring flow, and say so when it is absent.
    flow_known = session.get("imbalance") is not None
    imbalance = session.get("imbalance") or 0.0
    if reference.sessions_available < MIN_SESSIONS_FOR_CONTEXT:
        return None, context, f"only {reference.sessions_available} prior sessions stored"
    if not flow_known and len(context.migration_run) < MIGRATION_SESSIONS:
        # Without flow, only the structural migration read is available.
        return None, context, "no aggressor flow on this instrument; no migration run"

    week = reference.week or {}
    weekly_vah, weekly_val = week.get("vah"), week.get("val")
    width = (weekly_vah - weekly_val) if (weekly_vah is not None and weekly_val is not None) else None
    acceptance = width * ACCEPTANCE_FRACTION if width else None

    # 1. Value migration. Checked first: a run of sessions agreeing on
    #    direction outranks any single session's excursion.
    run = context.migration_run
    if len(run) >= MIGRATION_SESSIONS:
        rising = run[-1] in {"higher", "overlapping_higher"}
        agrees = (rising and imbalance > 0) or (not rising and imbalance < 0)
        if agrees or not flow_known:
            flow_note = (f"session imbalance {imbalance:+.3f}" if flow_known
                         else "no aggressor flow on this instrument")
            return PositionalBias(
                "value_migration", "bullish" if rising else "bearish",
                f"{len(run)} sessions migrating {'up' if rising else 'down'} "
                f"({'>'.join(run[-3:])}), {flow_note}",
                stop=session.get("val") if rising else session.get("vah"),
                horizon_days=max(5, len(run)),
            ), context, "value migration"

    # 2. Acceptance outside the week's composite value.
    if acceptance and weekly_vah is not None and weekly_val is not None:
        if close > weekly_vah + acceptance and imbalance > 0:
            return PositionalBias(
                "weekly_breakout", "bullish",
                f"closed {close:.2f} accepted above weekly VAH {weekly_vah:.2f} "
                f"with imbalance {imbalance:+.3f}",
                stop=weekly_vah, target=week.get("high"),
            ), context, "weekly breakout"
        if close < weekly_val - acceptance and imbalance < 0:
            return PositionalBias(
                "weekly_breakout", "bearish",
                f"closed {close:.2f} accepted below weekly VAL {weekly_val:.2f} "
                f"with imbalance {imbalance:+.3f}",
                stop=weekly_val, target=week.get("low"),
            ), context, "weekly breakout"

        # 3. Failed breakout: probed outside the week's value and closed back in.
        high, low = session.get("high"), session.get("low")
        inside = weekly_val <= close <= weekly_vah
        if inside and high is not None and high > weekly_vah + acceptance and imbalance < 0:
            return PositionalBias(
                "failed_breakout", "bearish",
                f"probed {high:.2f} above weekly VAH {weekly_vah:.2f} and closed back "
                f"inside at {close:.2f}, imbalance {imbalance:+.3f}",
                stop=high, target=weekly_val,
            ), context, "failed breakout"
        if inside and low is not None and low < weekly_val - acceptance and imbalance > 0:
            return PositionalBias(
                "failed_breakout", "bullish",
                f"probed {low:.2f} below weekly VAL {weekly_val:.2f} and closed back "
                f"inside at {close:.2f}, imbalance {imbalance:+.3f}",
                stop=low, target=weekly_vah,
            ), context, "failed breakout"

    # 4. An untested prior POC within reach, with flow pointing at it.
    target = nearest_naked_poc(close, reference.naked_pocs, width)
    if target is not None:
        rising = target > close
        if (rising and imbalance > 0) or (not rising and imbalance < 0):
            return PositionalBias(
                "naked_poc_target", "bullish" if rising else "bearish",
                f"untested POC at {target:.2f}, {abs(target - close) / close * 100:.2f}% away, "
                f"imbalance {imbalance:+.3f}",
                target=target, stop=session.get("val") if rising else session.get("vah"),
            ), context, "naked POC in reach"

    return None, context, "no positional setup"


def nearest_naked_poc(close: float, levels: list[float], width: float | None) -> float | None:
    """The closest untested POC inside a sane distance, or None."""
    if not levels or not close:
        return None
    reach = (width or close * 0.02) * NAKED_POC_MAX_REACH
    candidates = [level for level in levels if 0 < abs(level - close) <= reach]
    return min(candidates, key=lambda level: abs(level - close)) if candidates else None


def _slim(session: dict) -> dict:
    keep = ("open", "high", "low", "close", "poc", "vah", "val", "volume",
            "cumulative_delta", "imbalance", "day_type", "value_migration", "source")
    return {key: session.get(key) for key in keep if session.get(key) is not None}


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------

def ensure_tables(connection: sqlite3.Connection) -> None:
    ensure_profile_tables(connection)
    connection.executescript(SCHEMA)


def record(database_path: str, symbol: str, day: str, bias: PositionalBias | None,
           context: PositionalContext, reason: str) -> None:
    """Store every evaluation, fired or not.

    A lane that only records its entries can never answer "what did it pass on,
    and should it have?". The rejections are the control group.
    """
    connection = sqlite3.connect(database_path, timeout=30)
    try:
        ensure_tables(connection)
        with connection:
            connection.execute(
                """INSERT OR REPLACE INTO positional_evaluations
                   (symbol, day, setup, direction, fired, reason, close, context, evaluated_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (symbol, day, bias.setup if bias else None,
                 bias.direction if bias else None, 1 if bias else 0,
                 bias.reason if bias else reason, context.close,
                 json.dumps(asdict(context), default=str), datetime.now(UTC).isoformat()),
            )
    finally:
        connection.close()


def evaluations(database_path: str, day: str | None = None, symbol: str | None = None,
                fired_only: bool = False, limit: int = 500) -> list[dict]:
    connection = sqlite3.connect(database_path, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        ensure_tables(connection)
        where, params = [], []
        if day:
            where.append("day = ?"); params.append(day)
        if symbol:
            where.append("symbol = ?"); params.append(symbol)
        if fired_only:
            where.append("fired = 1")
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        rows = connection.execute(
            f"""SELECT * FROM positional_evaluations {clause}
                ORDER BY day DESC, symbol LIMIT ?""", (*params, limit)).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            try:
                item["context"] = json.loads(item["context"])
            except (TypeError, ValueError):
                item["context"] = {}
            out.append(item)
        return out
    finally:
        connection.close()
