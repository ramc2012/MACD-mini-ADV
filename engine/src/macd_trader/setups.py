"""The Part 7 setup catalogue as a validation journal, not a signal generator.

Twelve textbook setups, each restated as three questions a stored session can
answer: was the CONTEXT present, did the TRIGGER occur, and did the OUTCOME the
setup promises actually follow. Every session gets a row per setup whether or
not anything fired — the sessions where the context was absent are the
denominator, and the ones where the trigger fired and the outcome did not are
the whole point.

This is deliberately end-of-session and record-only. Several setups need
tick-level confirmation (stacked imbalances, speed of tape, depth reload) that
the stored measurements cannot supply; those rows say ``measurable=0`` with the
reason rather than pretending. What is measurable here is what the blueprint's
"Measure" column asks for: completion rates, break-side distributions, gap
statistics, by regime and by expiry flag. That is the evidence a setup has to
produce before it is allowed near a position.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS setup_journal (
  symbol TEXT NOT NULL,
  day TEXT NOT NULL,
  setup TEXT NOT NULL,
  regime_id TEXT,
  expiry_day INTEGER NOT NULL DEFAULT 0,
  day_type TEXT,
  measurable INTEGER NOT NULL DEFAULT 1,
  context INTEGER,
  triggered INTEGER,
  outcome INTEGER,
  direction TEXT,
  detail TEXT,
  evaluated_at TEXT NOT NULL,
  PRIMARY KEY (symbol, day, setup)
);
CREATE INDEX IF NOT EXISTS idx_setup_journal_setup ON setup_journal(setup, regime_id);
"""


@dataclass(frozen=True)
class Setup:
    setup_id: str
    name: str
    kind: str
    trade_on: tuple[str, ...]
    avoid_on: tuple[str, ...]
    needs_tick_features: bool = False
    claimed_edge: str = ""


# 7.3, the day-type matrix, encoded on each setup. Day types here are the
# volume-profile estimate stored on the session (trend / normal_variation /
# balanced), which is coarser than Dalton's seven; the mapping is stated.
SETUPS: tuple[Setup, ...] = (
    Setup("S1", "80% rule value-area fill", "rotation",
          ("normal_variation_day", "balanced_day"), ("trend_day",),
          claimed_edge="80% (Dalton); ~60% on ES"),
    Setup("S2", "IB breakout with acceptance", "continuation",
          ("trend_day", "normal_variation_day"), ("balanced_day",),
          claimed_edge="two-thirds of first IB breaks are in C; 50%/100% extension on 38%/19% of ES days"),
    Setup("S3", "Look-above-and-fail", "reversal",
          ("normal_variation_day", "balanced_day"), (),
          claimed_edge="open-rejection extremes hold about half the time"),
    Setup("S4", "POC / HVN retest with absorption", "continuation",
          ("trend_day", "normal_variation_day"), (), needs_tick_features=True),
    Setup("S5", "Responsive fade at VAH/VAL", "rotation",
          ("balanced_day",), ("trend_day",),
          claimed_edge="responsive activity dominates on balanced days"),
    Setup("S6", "Poor high / poor low repair", "structure",
          ("normal_variation_day",), (), needs_tick_features=True),
    Setup("S7", "Single-print / tail fill", "magnet",
          ("normal_variation_day", "trend_day"), (), needs_tick_features=True),
    Setup("S8", "Open-drive continuation", "trend",
          ("trend_day",), ("balanced_day",),
          claimed_edge="open-drive leaves the most reliable extreme of the day"),
    Setup("S9", "LVN break-and-go vs HVN magnet", "composite",
          ("trend_day", "normal_variation_day"), (), needs_tick_features=True),
    Setup("S10", "Delta / OFI divergence at an extreme", "reversal",
          ("balanced_day", "normal_variation_day"), ("trend_day",), needs_tick_features=True),
    Setup("S11", "Gap and spike rules", "next-day open",
          ("trend_day", "normal_variation_day", "balanced_day"), (),
          claimed_edge="Dalton's gap rules; gaps that fill tend to fill early"),
    Setup("S12", "Expiry-day rules", "regime",
          ("balanced_day", "normal_variation_day"), ("trend_day",),
          claimed_edge="expiry days are a different regime"),
)
BY_ID = {setup.setup_id: setup for setup in SETUPS}


def eligible(day_type: str | None, expiry_day: bool) -> list[str]:
    """The 7.3 shortlist for a day-type estimate. Expiry narrows it to S3/S5/S12."""
    if expiry_day:
        return ["S3", "S5", "S12"]
    if not day_type or day_type == "unknown":
        return []
    return [setup.setup_id for setup in SETUPS
            if day_type in setup.trade_on and day_type not in setup.avoid_on
            and setup.setup_id != "S12"]


def _flag(value) -> int | None:
    return None if value is None else int(bool(value))


def evaluate(measurement: dict, session: dict) -> list[dict]:
    """One journal row per setup for a closed session.

    ``measurement`` is the session_base_rates row, ``session`` the
    session_profiles row. Each rule below names what it is approximating with
    session-level data; the approximations are the honest ceiling of an
    end-of-day journal and are why the ``detail`` column exists.
    """
    day_type = session.get("day_type")
    expiry = bool(measurement.get("expiry_day"))
    close, open_ = session.get("close"), session.get("open")
    ib_high, ib_low = session.get("ib_high"), session.get("ib_low")
    outside = measurement.get("opened_outside_prior_value")
    ext = measurement.get("extension_ratio")
    gap = measurement.get("gap")
    break_side = measurement.get("break_side")
    rows: list[dict] = []

    def add(setup_id: str, context, triggered, outcome, direction=None, detail="",
            measurable=True):
        rows.append({
            "symbol": measurement["symbol"], "day": measurement["day"], "setup": setup_id,
            "regime_id": measurement.get("regime_id"), "expiry_day": int(expiry),
            "day_type": day_type, "measurable": int(measurable),
            "context": _flag(context), "triggered": _flag(triggered),
            "outcome": _flag(outcome), "direction": direction, "detail": detail,
        })

    # S1: opened outside prior value; trigger = accepted back inside two
    # brackets; outcome = traversed to the far edge.
    add("S1", outside, measurement.get("rule80_triggered") if outside else None,
        measurement.get("rule80_completed") if measurement.get("rule80_triggered") else None,
        detail="context=opened outside prior VA; trigger=two brackets inside; outcome=far edge reached")

    # S2: first break in C; trigger = the break; outcome = 50% extension held
    # (the blueprint's first target). Volume/stack confirmation is not stored.
    first = measurement.get("first_break_bracket")
    add("S2", first == "C" if first is not None or ib_high is not None else None,
        first == "C" if first is not None else None,
        (ext or 0) >= 0.5 if first == "C" else None,
        direction=("bullish" if break_side == "up" else "bearish" if break_side == "down" else None),
        detail="context/trigger=IB broke in bracket C; outcome=extension >= 50% IB; stack confirmation unmeasured")

    # S3: probed beyond the prior range; trigger = came back through the edge
    # (gap filled); outcome = reached prior value again.
    add("S3", gap, measurement.get("gap_filled") if gap else None,
        measurement.get("returned_to_prior_value") if measurement.get("gap_filled") else None,
        detail="context=probe beyond prior range (gap); trigger=edge reclaimed; outcome=prior value reached")

    for setup_id in ("S4", "S6", "S7", "S9", "S10"):
        add(setup_id, None, None, None, measurable=False,
            detail="needs stacked imbalances / speed of tape / depth; not in the stored session")

    # S5: balanced day; trigger = both IB edges probed (rotation); outcome =
    # close back inside the IB, the fade having held.
    balanced = day_type == "balanced_day"
    inside_ib = (close is not None and ib_high is not None and ib_low is not None
                 and ib_low <= close <= ib_high)
    add("S5", balanced, break_side == "both" if balanced else None,
        inside_ib if (balanced and break_side == "both") else None,
        detail="context=balanced day; trigger=both IB edges probed; outcome=close back inside IB")

    # S8: trend-day estimate; trigger = one-sided break with >= 100% extension;
    # outcome = close beyond the IB in the break direction (no fade).
    trend = day_type == "trend_day"
    one_sided = break_side in ("up", "down")
    fired = trend and one_sided and (ext or 0) >= 1.0
    held = None
    if fired and close is not None:
        held = close > ib_high if break_side == "up" else close < ib_low
    add("S8", trend, fired if trend else None, held,
        direction=("bullish" if break_side == "up" else "bearish" if break_side == "down" else None),
        detail="context=trend-day estimate; trigger=one-sided break >= 100% IB; outcome=close beyond IB that side")

    # S11: true gap; trigger = not filled by bracket C (early fills are the
    # ones that fill); outcome = gap still open at the close.
    fill_bracket = measurement.get("gap_fill_bracket")
    late = gap and (not measurement.get("gap_filled") or (fill_bracket or "Z") > "C")
    add("S11", gap, late if gap else None,
        (not measurement.get("gap_filled")) if late else None,
        direction=("bullish" if (open_ or 0) > (session.get("vah") or 0) else "bearish") if gap else None,
        detail="context=open beyond prior range; trigger=no fill by bracket C; outcome=gap held to the close")

    # S12: expiry is context, nothing else is measurable without the chain.
    add("S12", expiry, None, None, detail="context=expiry session; divergence gauge needs the option chain")
    order = {setup.setup_id: index for index, setup in enumerate(SETUPS)}
    return sorted(rows, key=lambda row: order[row["setup"]])


def save(connection: sqlite3.Connection, rows: list[dict]) -> int:
    if not rows:
        return 0
    connection.executescript(SCHEMA)
    stamp = datetime.now(UTC).isoformat()
    with connection:
        connection.executemany(
            """INSERT OR REPLACE INTO setup_journal
               (symbol, day, setup, regime_id, expiry_day, day_type, measurable, context,
                triggered, outcome, direction, detail, evaluated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [(r["symbol"], r["day"], r["setup"], r["regime_id"], r["expiry_day"],
              r["day_type"], r["measurable"], r["context"], r["triggered"], r["outcome"],
              r["direction"], r["detail"], stamp) for r in rows],
        )
    return len(rows)


def journal_summary(connection: sqlite3.Connection, symbol: str,
                    regime_id: str | None = None) -> list[dict]:
    """Per setup: how often the context appeared, the trigger fired, the
    outcome followed. Rates carry their denominators."""
    where = "symbol = ?"
    params: list = [symbol]
    if regime_id:
        where += " AND regime_id = ?"
        params.append(regime_id)
    try:
        rows = connection.execute(f"""
            SELECT setup, MAX(measurable) AS measurable,
                   SUM(context = 1) AS context_n, SUM(context IS NOT NULL) AS context_d,
                   SUM(triggered = 1) AS trig_n, SUM(triggered IS NOT NULL) AS trig_d,
                   SUM(outcome = 1) AS out_n, SUM(outcome IS NOT NULL) AS out_d,
                   COUNT(*) AS sessions
            FROM setup_journal WHERE {where} GROUP BY setup""", params).fetchall()
    except sqlite3.OperationalError:
        return []
    out = []
    for row in rows:
        setup = BY_ID.get(row[0])
        out.append({
            "setup": row[0], "name": setup.name if setup else row[0],
            "kind": setup.kind if setup else "", "measurable": bool(row[1]),
            "claimed_edge": setup.claimed_edge if setup else "",
            "sessions": row[8],
            "context": [row[2] or 0, row[3] or 0],
            "triggered": [row[4] or 0, row[5] or 0],
            "outcome": [row[6] or 0, row[7] or 0],
        })
    order = {setup.setup_id: index for index, setup in enumerate(SETUPS)}
    return sorted(out, key=lambda item: order.get(item["setup"], 99))


def catalogue() -> list[dict]:
    return [{"setup": s.setup_id, "name": s.name, "kind": s.kind, "trade_on": list(s.trade_on),
             "avoid_on": list(s.avoid_on), "needs_tick_features": s.needs_tick_features,
             "claimed_edge": s.claimed_edge} for s in SETUPS]


def journal_rows(connection: sqlite3.Connection, symbol: str, limit: int = 200) -> list[dict]:
    try:
        cursor = connection.execute(
            """SELECT * FROM setup_journal WHERE symbol = ? AND (triggered = 1 OR context = 1)
               ORDER BY day DESC, setup LIMIT ?""", (symbol, limit))
        names = [d[0] for d in cursor.description]
        return [dict(zip(names, row)) for row in cursor.fetchall()]
    except sqlite3.OperationalError:
        return []


def dumps(rows: list[dict]) -> str:
    return json.dumps(rows, default=str)
