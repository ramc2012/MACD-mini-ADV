"""M7 -- Risk & Sizing. Veto authority over every ticket M6 wants to emit.

Defaults are the spec's own (Section 3, M7):
  - risk per trade 0.75% of capital AT STOP; hard cap 1.0%
  - premium paid is capped separately at 1.5% of capital
  - sizing: 0.25x fractional Kelly from a rolling 60-trade edge estimate;
    fixed-fractional until 60 closed trades exist AND the measured edge
    clears its own confidence interval (see kelly_risk_pct)
  - max concurrent 3; max per sector20 = 2; portfolio heat <= 2.5%
  - daily loss stop -2.0% -> STAND-DOWN, no new tickets until next session
  - weekly loss stop -4.0% -> stand-down + mandatory review flag
  - event guard: no fresh tickets 1 session before a symbol's results date

TWO NUMBERS, NOT ONE -- and why this changed on 2026-08-27
----------------------------------------------------------------------
This module used to size on the FULL PREMIUM and then store that figure in
`sizing_risk_rupees`, on the argument that an option can gap through its
stop to zero. The argument is sound; using it as the sizing basis was not,
because every downstream limit is denominated in that same column:

    premium <= 0.75% of capital, stop at -15% of premium
      => an actual stop-out costs 0.1125% of capital
      => the -2.0% daily stand-down needs ~18 stop-outs in one session
      => with MAX_CONCURRENT_POSITIONS = 3, it can never fire.

The weekly stop, the 2.5% heat cap and the 1.0% hard cap were all loose by
the same ~6.7x factor, and MAX_PREMIUM_PER_TRADE_PCT was unreachable dead
code (0.75% of capital as premium is always below a 1.5% premium cap). A
limit that is measured and then cannot bind is worse than no limit, because
the number sitting in the journal implies it was being respected.

So the two quantities are now separate and BOTH bind:

  risk_rupees     = (entry - stop) * lots * lot_size
                    Capital at risk if the stop fills as intended. This is
                    what portfolio heat, the daily stand-down and the weekly
                    stop are denominated in. 3 x 0.75% = 2.25% <= the 2.5%
                    heat cap, which is the arithmetic the spec's own numbers
                    were chosen for.
  premium_rupees  = entry * lots * lot_size
                    The most a gap-to-zero can take. Capped at
                    MAX_PREMIUM_PER_TRADE_PCT independently. With a 15% stop
                    this cap is the one that usually binds (0.75% at stop
                    implies 5% of capital in premium, well over 1.5%), so
                    the gap risk the old code was worried about is still
                    controlled -- by the cap built for it, rather than by
                    disabling every other limit.

Pre-006 tickets stored premium in `sizing_risk_rupees`; migration 006 labels
them `sizing_risk_basis = 'full_premium'` rather than rewriting them.

Scope decision (stated once, honestly): every position sized here is an
INTRADAY thesis with an EOD time stop. The spec's "positional, T+3 unless
target-1 hit" variant is a real, separate instrument class the spec
describes but this build does not yet distinguish -- M6 does not yet emit a
positional-vs-intraday flag, so there is nothing for this module to key a
T+3 rule on. Documented here rather than silently defaulted.

This module NEVER places an order and NEVER calls a broker API -- it only
computes a position size and a yes/no gate from data already in Postgres
(open `fills`/`outcomes` rows, `paper_capital_daily`, `sector_taxonomy`,
`results_calendar`). It is deliberately pure/query-only so M6 can call
`risk_check()` synchronously while building a ticket.
"""
from __future__ import annotations

import os
import math
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import psycopg2
import psycopg2.extras

DEFAULT_DSN = ""

RISK_PER_TRADE_PCT = 0.75          # of capital, at stop
RISK_PER_TRADE_HARD_CAP_PCT = 1.00
MAX_PREMIUM_PER_TRADE_PCT = 1.50   # of capital, as premium paid (gap-to-zero cap)
KELLY_FRACTION = 0.25
KELLY_MIN_TRADES = 60
# A 60-trade win rate carries a standard error of roughly 6 percentage
# points, so a point estimate of p is not evidence of an edge. Kelly is only
# allowed to size UP when the measured edge clears this many standard errors;
# otherwise the fixed-fractional default stands. It may always size DOWN.
KELLY_MIN_T_STAT = 1.0
MAX_CONCURRENT_POSITIONS = 3
MAX_POSITIONS_PER_SECTOR20 = 2
MAX_PORTFOLIO_HEAT_PCT = 2.5
DAILY_LOSS_STOP_PCT = -2.0
WEEKLY_LOSS_STOP_PCT = -4.0
DEFAULT_CAPITAL = 1_000_000.0       # configurable; see main()'s --capital


@dataclass
class RiskState:
    """Everything risk_check() needs, fetched once per M6 run (not once per
    candidate) so a 20-candidate session issues a handful of queries, not
    hundreds."""
    capital: float
    daily_pnl_pct: float
    weekly_pnl_pct: float
    open_positions: list[dict]           # [{ticket_id, symbol, sector20, risk_rupees}, ...]
    kelly_edge: dict | None              # {p, b, n} from the last 60 CLOSED trades, or None if < 60
    stand_down: bool = field(init=False)
    weekly_review_flag: bool = field(init=False)
    portfolio_heat_pct: float = field(init=False)

    def __post_init__(self) -> None:
        self.stand_down = self.daily_pnl_pct <= DAILY_LOSS_STOP_PCT
        self.weekly_review_flag = self.weekly_pnl_pct <= WEEKLY_LOSS_STOP_PCT
        heat_rupees = sum(p["risk_rupees"] for p in self.open_positions)
        self.portfolio_heat_pct = 100.0 * heat_rupees / self.capital if self.capital else 0.0


def load_risk_state(connection, as_of: datetime, capital: float) -> RiskState:
    """Risk state AS OF `as_of` -- every query is bounded by it.

    The bound is not cosmetic. M8's backtest replays historical bars through
    this exact function, so an unbounded query answers "what is the book doing
    NOW" for a decision being made in July. An adversarial review confirmed
    this on 2026-08-27: the open-positions and Kelly-sample queries had no time
    predicate whatsoever, so every historical bar in a replay was sized against
    the present-day book and the present-day edge estimate -- lookahead that
    silently invalidates the backtest it feeds. The daily/weekly P&L queries
    were bounded to the right DAY but not to the right INSTANT, so a bar at
    10:00 could see a loss that only closed at 14:30 and stand itself down for
    a drawdown that had not happened yet.
    """
    today = as_of.date()
    week_start = today - timedelta(days=today.weekday())

    with connection.cursor() as cursor:
        cursor.execute(
            """SELECT COALESCE(SUM(o.pnl_rupees), 0)
               FROM outcomes o JOIN tickets t ON t.id = o.ticket_id
               WHERE o.closed AND o.exit_ts::date = %(today)s
                 AND o.exit_ts <= %(as_of)s""",
            {"today": today, "as_of": as_of},
        )
        (daily_pnl,) = cursor.fetchone()
        cursor.execute(
            """SELECT COALESCE(SUM(o.pnl_rupees), 0)
               FROM outcomes o
               WHERE o.closed AND o.exit_ts::date >= %(week_start)s
                 AND o.exit_ts::date <= %(today)s
                 AND o.exit_ts <= %(as_of)s""",
            {"week_start": week_start, "today": today, "as_of": as_of},
        )
        (weekly_pnl,) = cursor.fetchone()

        # Open AS OF as_of: filled at or before it, and either never closed or
        # closed strictly after it. A position closed at 11:00 must still count
        # as open to a decision being made at 10:00.
        cursor.execute(
            """SELECT t.id, t.symbol, st.sector20,
                      COALESCE(t.sizing_risk_rupees, 0) AS risk_rupees
               FROM fills f
               JOIN tickets t ON t.id = f.ticket_id
               LEFT JOIN outcomes o ON o.ticket_id = t.id
               LEFT JOIN sector_taxonomy st ON st.symbol = t.symbol
               WHERE f.fill_ts <= %(as_of)s
                 AND (o.ticket_id IS NULL OR NOT o.closed
                      OR o.exit_ts IS NULL OR o.exit_ts > %(as_of)s)""",
            {"as_of": as_of},
        )
        open_positions = [
            {"ticket_id": r[0], "symbol": r[1], "sector20": r[2], "risk_rupees": float(r[3])}
            for r in cursor.fetchall()
        ]

        cursor.execute(
            """SELECT o.r_multiple FROM outcomes o
               WHERE o.closed AND o.r_multiple IS NOT NULL
                 AND o.exit_ts <= %(as_of)s
               ORDER BY o.exit_ts DESC LIMIT %(n)s""",
            {"n": KELLY_MIN_TRADES, "as_of": as_of},
        )
        r_multiples = [float(row[0]) for row in cursor.fetchall()]

    kelly_edge = None
    if len(r_multiples) >= KELLY_MIN_TRADES:
        wins = [r for r in r_multiples if r > 0]
        losses = [-r for r in r_multiples if r <= 0]
        if wins and losses:
            p = len(wins) / len(r_multiples)
            avg_win = sum(wins) / len(wins)
            avg_loss = sum(losses) / len(losses)
            b = avg_win / avg_loss if avg_loss else None
            if b:
                kelly_edge = {"p": p, "b": b, "n": len(r_multiples)}

    return RiskState(
        capital=capital,
        daily_pnl_pct=100.0 * float(daily_pnl) / capital if capital else 0.0,
        weekly_pnl_pct=100.0 * float(weekly_pnl) / capital if capital else 0.0,
        open_positions=open_positions,
        kelly_edge=kelly_edge,
    )


def kelly_risk_pct(edge: dict | None) -> float:
    """0.25x Kelly fraction of capital to risk at stop, or the
    fixed-fractional default (RISK_PER_TRADE_PCT).

    Kelly f* = p - (1-p)/b; negative f* (a measured negative edge) clamps to
    0 -- Kelly sizing must never SIZE UP a losing system.

    ASYMMETRY, added 2026-08-27: Kelly may size DOWN on a weak sample but may
    only size UP when the sample supports it. A 60-trade win rate has a
    standard error near 6 percentage points, so f* computed from a 60-trade p
    is a noisy point estimate, and 0.25x is a haircut on that noise rather
    than a defence against it. The break-even win rate for a given b is
    1/(1+b); the edge must sit at least KELLY_MIN_T_STAT standard errors
    above it before the Kelly number is allowed to exceed the fixed-fractional
    default. Below that bar the default stands -- unless Kelly says smaller,
    in which case the smaller number wins.

    Kelly's p and b are computed from `outcomes.r_multiple`, which is a return
    on PREMIUM (see backtest/exit_simulator.r_multiple) while this function's
    output governs risk AT STOP. A hit rate is scale-free and b is a ratio of
    two averages on the same scale, so both survive the change of unit; the
    resulting f* is nonetheless a heuristic, which is exactly why it is
    quartered and capped rather than trusted.
    """
    if edge is None:
        return RISK_PER_TRADE_PCT
    p, b, n = edge["p"], edge["b"], edge["n"]
    f_star = p - (1 - p) / b
    scaled = min(max(0.0, f_star) * KELLY_FRACTION * 100.0, RISK_PER_TRADE_HARD_CAP_PCT)
    if scaled <= RISK_PER_TRADE_PCT:
        return scaled                       # sizing down never needs evidence
    breakeven_p = 1.0 / (1.0 + b)
    se_p = ((p * (1.0 - p)) / n) ** 0.5 if n else None
    if not se_p or (p - breakeven_p) / se_p < KELLY_MIN_T_STAT:
        return RISK_PER_TRADE_PCT           # the sample cannot justify sizing up
    return scaled


def event_guard_blocks(connection, symbol: str, as_of: date | datetime) -> str | None:
    """Refuse unknown/stale calendars and the session before known results."""
    from zoneinfo import ZoneInfo
    from model.market_calendar import previous_session
    ist = ZoneInfo("Asia/Kolkata")
    known_at = (as_of.astimezone(ist) if isinstance(as_of, datetime)
                else datetime.combine(as_of, datetime.min.time(), ist))
    day = known_at.date()
    # A date-only replay is bounded to midnight, never to future disclosures.
    with connection.cursor() as cursor:
        cursor.execute(
            """SELECT run_at FROM ingest_log
               WHERE collector='results_calendar_board_meetings'
                 AND status IN ('ok', 'empty') AND run_at <= %(known_at)s
               ORDER BY run_at DESC LIMIT 1""", {"known_at": known_at})
        fresh = cursor.fetchone()
        required_day = previous_session(day)
        if not fresh or fresh[0].astimezone(ist).date() < required_day:
            return "event guard: results calendar unavailable or stale"
        cursor.execute(
            """SELECT results_date FROM results_calendar
               WHERE symbol = %(symbol)s AND results_date >= %(as_of)s
               ORDER BY results_date ASC LIMIT 1""",
            {"symbol": symbol, "as_of": day})
        row = cursor.fetchone()
    if row is not None and previous_session(row[0]) <= day <= row[0]:
        return f"event guard: {symbol} reports on {row[0].isoformat()}"
    return None


def sizing_coherence(stop_pct: float) -> dict:
    """Are the three configured numbers mutually satisfiable at this stop?

    THEY ARE NOT, at M6's current 15% stop, and this function exists so that
    fact is reported rather than discovered. The arithmetic:

        risk-at-stop per rupee of premium = stop_pct
        to risk RISK_PER_TRADE_PCT of capital you must hold
            RISK_PER_TRADE_PCT / stop_pct  of capital in premium
        = 0.75 / 0.15 = 5.00% of capital

    which the 1.50% premium cap forbids. So the premium cap always binds
    first, and the EFFECTIVE risk per trade is

        MAX_PREMIUM_PER_TRADE_PCT * stop_pct = 1.50 * 0.15 = 0.225%

    -- one third of the intended 0.75%. The -2.0% daily stand-down therefore
    still needs ~9 stop-outs against a 3-position concurrency cap, and cannot
    fire in a session. Moving sizing off the full-premium basis fixed a 6.7x
    looseness; a 3.3x looseness remains, and it lives in the CONFIGURATION,
    not in the code.

    Three coherent resolutions exist and picking between them is an owner
    decision, not a refactor:
      (a) raise MAX_PREMIUM_PER_TRADE_PCT to RISK_PER_TRADE_PCT / stop_pct
          (5.0% here) -- honours the 0.75% risk figure, accepts a much larger
          single-trade gap exposure;
      (b) restate RISK_PER_TRADE_PCT as the achievable 0.225% and scale the
          daily/weekly stops to match (roughly -0.6% / -1.2% for the same
          "three stop-outs and stand down" behaviour the -2% number implies);
      (c) widen the stop -- a 50% stop makes 1.5% premium and 0.75% risk
          agree exactly, at the cost of a completely different trade.

    Nothing here changes any threshold. It returns the numbers so main(),
    the desk and any future retune argue from arithmetic instead of intent.
    """
    if stop_pct <= 0:
        return {"coherent": False, "reason": "stop_pct must be positive"}
    premium_needed_pct = RISK_PER_TRADE_PCT / stop_pct
    effective_risk_pct = MAX_PREMIUM_PER_TRADE_PCT * stop_pct
    binding = "premium" if premium_needed_pct > MAX_PREMIUM_PER_TRADE_PCT else "risk_at_stop"
    achieved = min(RISK_PER_TRADE_PCT, effective_risk_pct)
    return {
        "coherent": binding == "risk_at_stop",
        "stop_pct": stop_pct,
        "binding_cap": binding,
        "premium_needed_for_intended_risk_pct": premium_needed_pct,
        "premium_cap_pct": MAX_PREMIUM_PER_TRADE_PCT,
        "intended_risk_pct": RISK_PER_TRADE_PCT,
        "effective_risk_pct": achieved,
        "stopouts_to_daily_standdown": abs(DAILY_LOSS_STOP_PCT) / achieved if achieved else None,
        "stopouts_to_weekly_stop": abs(WEEKLY_LOSS_STOP_PCT) / achieved if achieved else None,
        "max_concurrent": MAX_CONCURRENT_POSITIONS,
        "daily_standdown_reachable_in_one_session":
            (abs(DAILY_LOSS_STOP_PCT) / achieved) <= MAX_CONCURRENT_POSITIONS if achieved else False,
    }


@dataclass
class SizingResult:
    allowed: bool
    reason: str | None = None
    lots: int = 0
    notional: float = 0.0
    #: Capital at risk if the stop fills as intended. The heat / daily / weekly
    #: limits are all denominated in this.
    risk_rupees: float = 0.0
    method: str | None = None
    #: Total premium paid -- the gap-to-zero exposure, capped separately.
    premium_rupees: float = 0.0
    risk_basis: str = "risk_at_stop"
    #: Which cap actually decided the size, so a ticket can say why it is small.
    binding_cap: str | None = None


def risk_check(state: RiskState, connection, *, symbol: str, sector20: str | None,
               entry_premium: float, stop_premium: float, lot_size: int, as_of: date) -> SizingResult:
    """The single veto/sizing entry point M6 calls per candidate, in gate
    order (cheapest/most decisive checks first)."""
    try:
        valid = (all(math.isfinite(float(value)) for value in (
            state.capital, state.daily_pnl_pct, state.weekly_pnl_pct,
            state.portfolio_heat_pct, entry_premium, stop_premium, lot_size))
            and state.capital > 0 and entry_premium > 0 and stop_premium >= 0
            and not isinstance(lot_size, bool) and lot_size > 0 and int(lot_size) == lot_size)
    except (TypeError, ValueError, OverflowError):
        valid = False
    if not valid:
        return SizingResult(False, "invalid risk state, premium or lot metadata")
    if state.stand_down:
        return SizingResult(False, f"STAND-DOWN: daily P&L {state.daily_pnl_pct:.2f}% <= {DAILY_LOSS_STOP_PCT}%")
    # The weekly stop was computed into RiskState from the start but never
    # consulted here, so a book could bleed past -4% for the week and keep
    # taking new risk as long as no single DAY breached -2% (confirmed by
    # adversarial review, 2026-08-27). Enforcing it is the whole point of
    # having computed it; a limit that is measured and then ignored is worse
    # than no limit, because the number sitting in the journal implies it was
    # being respected.
    if state.weekly_review_flag:
        return SizingResult(
            False,
            f"WEEKLY-STOP: weekly P&L {state.weekly_pnl_pct:.2f}% <= {WEEKLY_LOSS_STOP_PCT}% "
            "— no new risk until the week is reviewed",
        )
    if len(state.open_positions) >= MAX_CONCURRENT_POSITIONS:
        return SizingResult(False, f"max concurrent positions reached ({MAX_CONCURRENT_POSITIONS})")
    if sector20:
        same_sector = sum(1 for p in state.open_positions if p["sector20"] == sector20)
        if same_sector >= MAX_POSITIONS_PER_SECTOR20:
            return SizingResult(False, f"max positions in sector20={sector20} reached ({MAX_POSITIONS_PER_SECTOR20})")
    if state.portfolio_heat_pct >= MAX_PORTFOLIO_HEAT_PCT:
        return SizingResult(False, f"portfolio heat {state.portfolio_heat_pct:.2f}% >= {MAX_PORTFOLIO_HEAT_PCT}%")
    blocked = event_guard_blocks(connection, symbol, as_of)
    if blocked:
        return SizingResult(False, blocked)
    if entry_premium <= 0 or lot_size <= 0:
        return SizingResult(False, "invalid entry_premium or lot_size")
    if stop_premium >= entry_premium:
        return SizingResult(False, "stop_premium must be below entry_premium for a long option")

    risk_pct = kelly_risk_pct(state.kelly_edge)
    risk_budget_rupees = state.capital * risk_pct / 100.0

    # Two independent per-lot exposures; both caps are live (see the module
    # docstring for the arithmetic that made the premium cap dead code before).
    per_lot_risk = (entry_premium - stop_premium) * lot_size   # if the stop fills
    per_lot_premium = entry_premium * lot_size                  # if it gaps to zero
    if per_lot_risk <= 0 or per_lot_premium <= 0:
        return SizingResult(False, "non-positive per-lot risk or premium")

    max_premium_rupees = state.capital * MAX_PREMIUM_PER_TRADE_PCT / 100.0
    lots_by_risk_budget = int(risk_budget_rupees // per_lot_risk)
    lots_by_premium_cap = int(max_premium_rupees // per_lot_premium)
    lots = max(0, min(lots_by_risk_budget, lots_by_premium_cap))
    binding_cap = "risk_at_stop" if lots_by_risk_budget <= lots_by_premium_cap else "premium"
    if lots < 1:
        return SizingResult(
            False,
            f"position would round to 0 lots (risk budget allows {lots_by_risk_budget}, "
            f"premium cap allows {lots_by_premium_cap})",
        )

    remaining_heat_pct = MAX_PORTFOLIO_HEAT_PCT - state.portfolio_heat_pct
    risk_rupees = lots * per_lot_risk
    if 100.0 * risk_rupees / state.capital > remaining_heat_pct:
        capped_lots = int((remaining_heat_pct / 100.0 * state.capital) // per_lot_risk)
        if capped_lots < 1:
            return SizingResult(False, f"portfolio heat headroom ({remaining_heat_pct:.2f}%) too small for 1 lot")
        lots = capped_lots
        binding_cap = "portfolio_heat"
        risk_rupees = lots * per_lot_risk

    notional = lots * lot_size * entry_premium
    premium_rupees = lots * per_lot_premium
    method = f"kelly_{KELLY_FRACTION}x" if risk_pct != RISK_PER_TRADE_PCT else "fixed_fractional"
    return SizingResult(True, None, lots, notional, risk_rupees, method,
                        premium_rupees=premium_rupees, risk_basis="risk_at_stop",
                        binding_cap=binding_cap)


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--capital", type=float, default=DEFAULT_CAPITAL)
    parser.add_argument("--dsn", default=os.environ.get("VANGUARD_DATABASE_URL", DEFAULT_DSN))
    args = parser.parse_args()

    connection = psycopg2.connect(args.dsn)
    try:
        state = load_risk_state(connection, datetime.now(timezone.utc), args.capital)
        print(f"capital={args.capital:,.0f}  daily_pnl={state.daily_pnl_pct:+.3f}%  "
              f"weekly_pnl={state.weekly_pnl_pct:+.3f}%  stand_down={state.stand_down}  "
              f"weekly_review={state.weekly_review_flag}")
        print(f"open positions: {len(state.open_positions)}  portfolio_heat={state.portfolio_heat_pct:.3f}%")
        print(f"kelly edge: {state.kelly_edge}")
        risk_pct = kelly_risk_pct(state.kelly_edge)
        derived = "kelly-derived" if risk_pct != RISK_PER_TRADE_PCT else (
            "fixed-fractional fallback (< 60 closed trades)" if state.kelly_edge is None
            else "fixed-fractional (60-trade edge did not clear its own CI)")
        print(f"risk_pct this session: {risk_pct:.4f}% of capital AT STOP  ({derived})")
        print(f"premium cap: {MAX_PREMIUM_PER_TRADE_PCT:.2f}% of capital "
              f"(Rs{args.capital * MAX_PREMIUM_PER_TRADE_PCT / 100:,.0f} per trade)")

        from fusion.m6_select import STOP_PCT
        coherence = sizing_coherence(STOP_PCT)
        print(f"\nsizing coherence at M6's {STOP_PCT:.0%} stop:")
        print(f"  intended risk/trade      {coherence['intended_risk_pct']:.3f}% of capital")
        print(f"  premium that would need  {coherence['premium_needed_for_intended_risk_pct']:.2f}% "
              f"(cap is {coherence['premium_cap_pct']:.2f}%)")
        print(f"  binding cap              {coherence['binding_cap']}")
        print(f"  EFFECTIVE risk/trade     {coherence['effective_risk_pct']:.3f}% of capital")
        print(f"  stop-outs to -{abs(DAILY_LOSS_STOP_PCT):.1f}% daily stand-down: "
              f"{coherence['stopouts_to_daily_standdown']:.1f} "
              f"(max {MAX_CONCURRENT_POSITIONS} positions open at once)")
        if not coherence["daily_standdown_reachable_in_one_session"]:
            print("  ** the daily stand-down still cannot fire in a single session. This is a")
            print("     CONFIGURATION inconsistency, not a code defect -- see sizing_coherence().")
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
