"""Decision matrix for the agentic lane: grades every index x side (CE = long, PE = short) and sets the lots.

The model proposes; this matrix decides how much. Two grades per index x side, one per horizon:

INTRADAY grade (today's structure; research/judgement_study_20261020, candle_patterns_20261022)
  phase (one of)                                                     points
    before 09:45   the open type is not known yet                    grade capped at C
    09:45-10:15    open drive / test-drive with the side             +2   (IB then breaks that way 70-75%)
                   open-reject-reverse with the side                 +1
                   open auction                                       0   (breakouts barely pay: +0.02R)
                   reverse / drive against the side                  -1 / -2
    after 10:15    price beyond the first-hour range on the side     +2   (IB-break plan +0.09R, every year)
                   inside the first-hour range                        0   (wait for the break: arm a trigger)
                   beyond the other side                             -2
                   open drive / test-drive agrees / disagrees        +1 / -1
                   open auction                                      -1
                   first-hour range narrow (<0.44 ATR) / widest (>0.68)  +1 / -1
  context: with the 20-day trend +1; small prior day (<0.6 ATR, NR7, inside) -1; chasing a wide prior day
           (>=1.8 ATR) in its direction -1; options expensive (IV/realised >= 1.3) -1

SWING grade (multi-day; daily structure)
    index scorecard calls this side / the other side                 +2 / -1  (the tested next-day signal)
    with the 20-day trend; with the 50-day trend                     +1 each
    price beyond the prior 10-session high (CE) / low (PE)           +1   multi-day breakout
    50-day trend with the side and price within 0.5 ATR of the 20-day average   +1   pullback in a trend
    yesterday closed in its outer 30% on the side                    +1   (the follow-through comes as a gap)
    small prior day / chasing a wide prior day                       -1 each
    today's intraday grade A or B on the side                        +1   (timing)

  score >= 4 -> A, 2-3 -> B, 0-1 -> C, < 0 -> D. LEARNING PHASE (operator's choice, 22 Oct 2026): every grade may
  trade, in fixed lots -- A 4, B 3, C 2, D 1 -- so varied setups are tried at a size that is survivable and the
  setup scoreboard can tell which ones deserve more. Candle patterns are absent: none predicted direction.
"""
from __future__ import annotations

GRADES = ((4, "A", 1.0), (2, "B", 0.6), (0, "C", 0.3))
LOTS_BY_GRADE = {"A": 4, "B": 3, "C": 2, "D": 1}     # learning phase: every grade trades, sized by lots
NARROW_IB_ATR, WIDEST_IB_ATR = 0.44, 0.68          # quintile cuts of IB / ATR, index sessions 2021-2026
SMALL_DAY_ATR, WIDE_DAY_ATR = 0.60, 1.80
EXPENSIVE_RATIO = 1.30
DEFAULT_STOP_FRACTION = 0.40                         # sizing stop when the model gives none
MIN_STOP_FRACTION = 0.20                             # a tighter stop does not buy more lots
STOP_SLIPPAGE = 0.15                                 # stops fill past the level in a fast market (22 Oct: 206 vs ~230)
SIDES = {"CE": 1, "PE": -1}


def open_type(first30: list[tuple], atr: float) -> tuple[str, int]:
    """Open type of the first 30 one-minute bars [(open, high, low, close), ...] in units of the daily ATR.

    Same rule as research/judgement_study_20261020/mp_sessions.py: OD open-drive, OTD open-test-drive,
    ORR open-reject-reverse, OA open-auction; the int is the direction (+1 up, -1 down, 0 none)."""
    if len(first30) < 10 or not atr or atr <= 0:
        return "unknown", 0
    o = first30[0][0]
    hi, lo, c = max(b[1] for b in first30), min(b[2] for b in first30), first30[-1][3]
    rng = max(hi - lo, 1e-9)
    early = first30[:10]
    probe_up, probe_dn = max(b[1] for b in early) - o, o - min(b[2] for b in early)
    after5 = first30[5:]
    pos = (c - lo) / rng
    if pos >= 0.7 and (o - min(b[2] for b in after5)) <= 0.03 * atr and c > o:
        return "OD", 1
    if pos <= 0.3 and (max(b[1] for b in after5) - o) <= 0.03 * atr and c < o:
        return "OD", -1
    if probe_dn >= 0.10 * atr and c > o + 0.15 * atr and pos >= 0.67:
        return "OTD", 1
    if probe_up >= 0.10 * atr and c < o - 0.15 * atr and pos <= 0.33:
        return "OTD", -1
    if probe_up >= 0.10 * atr and c < o:
        return "ORR", -1
    if probe_dn >= 0.10 * atr and c > o:
        return "ORR", 1
    return "OA", 0


def evaluate(inputs: dict, side: str, clock: str, price: float | None, *, trend: int = 0, iv_ratio: float | None = None,
             move_ratio: float | None = None, assume_break: bool = False) -> dict:
    """Score one index x side. ``inputs`` from agentic_features.matrix_inputs; ``clock`` "HH:MM" IST;
    ``trend`` sign of price vs its 20-day average; ``assume_break`` grades a trigger as if its break had happened."""
    s = SIDES[side]
    factors: list[list] = []
    cap = None

    def add(points: int, why: str) -> None:
        factors.append([points, why])

    otype, odir = inputs.get("open_type") or "unknown", int(inputs.get("open_dir") or 0)
    ib_high, ib_low, ib_atr = inputs.get("ib_high"), inputs.get("ib_low"), inputs.get("ib_atr")
    if clock < "09:45" or otype == "unknown":
        cap = "C"
        add(0, "before 09:45: open type not known yet (grade capped at C)")
    elif clock < "10:15" or ib_high is None:
        if otype in ("OD", "OTD"):
            add(2 if odir == s else -2, f"{otype} {'with' if odir == s else 'against'} the side: IB breaks the drive's way 70-75%")
        elif otype == "ORR":
            add(1 if odir == s else -1, f"open-reject-reverse {'with' if odir == s else 'against'} the side")
        else:
            add(0, "open auction: rotation likely, breakouts barely pay")
    else:
        beyond = (price is not None and ((s > 0 and price > ib_high) or (s < 0 and price < ib_low)))
        against = (price is not None and ((s > 0 and price < ib_low) or (s < 0 and price > ib_high)))
        if beyond or assume_break:
            add(2, ("trigger beyond" if assume_break and not beyond else "price beyond") + " the first-hour range on the side (IB plan)")
        elif against:
            add(-2, "price broke the first-hour range the other way")
        else:
            add(0, "inside the first-hour range: wait for the break (arm a trigger at the edge)")
        if otype in ("OD", "OTD"):
            add(1 if odir == s else -1, f"open type {otype} {'agrees' if odir == s else 'disagrees'}")
        elif otype == "OA":
            add(-1, "open auction: IB breakouts barely pay (+0.02R)")
        if ib_atr is not None:
            if ib_atr < NARROW_IB_ATR:
                add(1, f"narrow first hour ({ib_atr:.2f} ATR)")
            elif ib_atr > WIDEST_IB_ATR:
                add(-1, f"widest first hour ({ib_atr:.2f} ATR): little room left")
    if trend == s:
        add(1, "with the 20-day trend")
    prev_rng = inputs.get("prev_range_atr")
    if prev_rng is not None:
        if prev_rng < SMALL_DAY_ATR or inputs.get("prev_nr7") or inputs.get("prev_inside"):
            label = "NR7" if inputs.get("prev_nr7") else "inside day" if inputs.get("prev_inside") else f"{prev_rng:.2f} ATR"
            add(-1, f"small prior day ({label}): the next move tends to be smaller")
        elif prev_rng >= WIDE_DAY_ATR and int(inputs.get("prev_colour") or 0) == s:
            add(-1, f"chasing a wide prior day ({prev_rng:.2f} ATR) in its direction: next-day fade risk")
    if (iv_ratio is not None and iv_ratio >= EXPENSIVE_RATIO) or (move_ratio is not None and move_ratio >= EXPENSIVE_RATIO):
        add(-1, "options expensive against realised movement")
    score = sum(p for p, _ in factors)
    grade, mult = "D", 0.0
    for floor, g, m in GRADES:
        if score >= floor:
            grade, mult = g, m
            break
    if cap == "C" and grade in ("A", "B"):
        grade, mult = "C", 0.3
    return {"side": side, "score": score, "grade": grade, "size_multiplier": mult, "factors": factors}


def risk_per_lot(ltp: float, stop: float | None, lot_size: int) -> float:
    """Premium at risk per lot to the stop, plus 15% for slippage; a missing stop is taken at 40% and a tighter
    one counts as 20%."""
    stop_frac = DEFAULT_STOP_FRACTION if not stop or stop >= ltp else (ltp - stop) / ltp
    return ltp * max(stop_frac, MIN_STOP_FRACTION) * (1 + STOP_SLIPPAGE) * lot_size


def _grade(score: int) -> tuple[str, float]:
    for floor, g, m in GRADES:
        if score >= floor:
            return g, m
    return "D", 0.0


def evaluate_swing(inputs: dict, side: str, price: float | None, *, trend20: int = 0, trend50: int = 0,
                   scorecard_call: str | None = None, intraday_grade: str | None = None,
                   ma20_gap_atr: float | None = None) -> dict:
    """Grade a multi-day position on one index x side from the daily structure (see the module docstring)."""
    s = SIDES[side]
    factors: list[list] = []

    def add(points: int, why: str) -> None:
        factors.append([points, why])

    if scorecard_call in SIDES:
        add(2 if scorecard_call == side else -1, f"index scorecard calls {scorecard_call}")
    if trend20 == s:
        add(1, "with the 20-day trend")
    if trend50 == s:
        add(1, "with the 50-day trend")
    hi10, lo10 = inputs.get("hi10"), inputs.get("lo10")
    if price is not None and ((s > 0 and hi10 and price > hi10) or (s < 0 and lo10 and price < lo10)):
        add(1, f"beyond the 10-session {'high' if s > 0 else 'low'}: multi-day breakout")
    if trend50 == s and ma20_gap_atr is not None and abs(ma20_gap_atr) <= 0.5:
        add(1, f"pullback to the 20-day average ({ma20_gap_atr:+.2f} ATR) inside the 50-day trend")
    clv = inputs.get("prev_clv")
    if clv is not None and ((s > 0 and clv >= 0.7) or (s < 0 and clv <= 0.3)):
        add(1, "yesterday closed near its " + ("high" if s > 0 else "low") + ": follow-through tends to come overnight")
    prev_rng = inputs.get("prev_range_atr")
    if prev_rng is not None:
        if prev_rng < SMALL_DAY_ATR or inputs.get("prev_nr7") or inputs.get("prev_inside"):
            add(-1, "small prior day: smaller next move")
        elif prev_rng >= WIDE_DAY_ATR and int(inputs.get("prev_colour") or 0) == s:
            add(-1, f"chasing a wide prior day ({prev_rng:.2f} ATR)")
    if intraday_grade in ("A", "B"):
        add(1, f"today's intraday grade {intraday_grade}: good timing")
    score = sum(p for p, _ in factors)
    grade, mult = _grade(score)
    return {"side": side, "score": score, "grade": grade, "size_multiplier": mult, "factors": factors}
