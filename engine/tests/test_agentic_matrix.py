"""Decision matrix: grades per index x side from the tested factors, and the risk budget that sizes every new trade."""
import sqlite3
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from macd_trader import agentic_brain as brain
from macd_trader import agentic_features as feat
from macd_trader import agentic_matrix as mx

IST = ZoneInfo("Asia/Kolkata")
SPOT = "NSE:NIFTY50-INDEX"
PE = "NSE:NIFTY26OCT23000PE"
CE = "NSE:NIFTY26OCT23000CE"
DAY = date(2026, 10, 13)                                   # a Tuesday; ten earlier sessions are seeded


def at(day, hh, mm):
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=IST)


def drive_up(n=30, start=23000.0):
    """One-minute (open, high, low, close) bars that leave the open at once and close near the high."""
    return [(start + i * 3, start + i * 3 + 2, start + i * 3 - 0.5 if i else start - 0.5, start + i * 3 + 1.5) for i in range(n)]


# ------------------------------------------------------------------------------------------- open type and grades

def test_open_type_matches_the_study_rules():
    assert mx.open_type(drive_up(), atr=200.0) == ("OD", 1)
    down = [(o, 2 * 23000 - l, 2 * 23000 - h, 2 * 23000 - c) for o, h, l, c in drive_up()]
    down = [(23000.0, *b[1:]) if i == 0 else (2 * 23000 - drive_up()[i][0], *b[1:]) for i, b in enumerate(down)]
    assert mx.open_type(down, atr=200.0) == ("OD", -1)
    flat = [(23000, 23001, 22999, 23000)] * 30
    assert mx.open_type(flat, atr=200.0) == ("OA", 0)
    assert mx.open_type(flat[:5], atr=200.0) == ("unknown", 0)


BROKEN_UP = {"open_type": "OD", "open_dir": 1, "ib_high": 23100.0, "ib_low": 23000.0, "ib_atr": 0.40,
             "prev_range_atr": 1.0, "prev_colour": 1, "prev_nr7": False, "prev_inside": False}


def test_a_drive_with_trend_through_a_narrow_first_hour_is_grade_a_and_the_other_side_is_d():
    up = mx.evaluate(BROKEN_UP, "CE", "10:40", 23130.0, trend=1)
    assert (up["grade"], up["score"]) == ("A", 5)            # +2 break, +1 drive agrees, +1 narrow, +1 trend
    down = mx.evaluate(BROKEN_UP, "PE", "10:40", 23130.0, trend=1)
    assert down["grade"] == "D" and down["size_multiplier"] == 0.0


def test_before_0945_the_grade_is_capped_and_open_auction_costs_a_point():
    early = mx.evaluate({**BROKEN_UP, "open_type": None, "ib_high": None}, "CE", "09:30", 23050.0, trend=1)
    assert early["grade"] == "C"
    oa_inside = mx.evaluate({**BROKEN_UP, "open_type": "OA", "open_dir": 0}, "CE", "11:00", 23050.0, trend=1)
    assert oa_inside["score"] == 1 and oa_inside["grade"] == "C"   # inside 0, OA -1, narrow +1, trend +1


def test_small_or_wide_prior_days_and_rich_options_cost_points():
    base = mx.evaluate(BROKEN_UP, "CE", "10:40", 23130.0, trend=1)["score"]
    assert mx.evaluate({**BROKEN_UP, "prev_nr7": True}, "CE", "10:40", 23130.0, trend=1)["score"] == base - 1
    assert mx.evaluate({**BROKEN_UP, "prev_range_atr": 2.1, "prev_colour": 1}, "CE", "10:40", 23130.0, trend=1)["score"] == base - 1
    assert mx.evaluate({**BROKEN_UP, "prev_range_atr": 2.1, "prev_colour": -1}, "CE", "10:40", 23130.0, trend=1)["score"] == base
    assert mx.evaluate(BROKEN_UP, "CE", "10:40", 23130.0, trend=1, iv_ratio=1.4)["score"] == base - 1


def test_an_armed_break_is_graded_as_if_it_had_broken():
    inside = mx.evaluate(BROKEN_UP, "CE", "11:00", 23050.0, trend=1)
    assumed = mx.evaluate(BROKEN_UP, "CE", "11:00", 23050.0, trend=1, assume_break=True)
    assert assumed["score"] == inside["score"] + 2


def test_risk_budget_halves_for_each_aggravating_condition():
    cell = {"size_multiplier": 1.0}
    full, _ = mx.risk_budget(cell, 1_000_000, 0.015, overnight=False, scorecard_agrees=False, expiry_today=False,
                             clock="11:00", day_pnl=0)
    assert full == 15_000
    worst, notes = mx.risk_budget(cell, 1_000_000, 0.015, overnight=True, scorecard_agrees=False, expiry_today=True,
                                  clock="14:40", day_pnl=-20_000)
    assert worst == 15_000 / 16 and len(notes) == 4
    assert round(mx.risk_per_lot(100.0, None, 75)) == 3450    # no stop -> 40%, +15% slippage
    assert round(mx.risk_per_lot(100.0, 95.0, 75)) == 1725    # a 5% stop counts as 20%


# ------------------------------------------------------------------------------------------- the validator

def ctx(matrix, **kw):
    base = {"candidates": {"C01": {"symbol": PE, "ltp": 100.0, "lot_size": 75, "expiry": "2026-10-20",
                                   "underlying": "NIFTY", "type": "PE"},
                           "C02": {"symbol": CE, "ltp": 100.0, "lot_size": 75, "expiry": "2026-10-20",
                                   "underlying": "NIFTY", "type": "CE"}},
            "positions": {}, "book": {"cash": 1_000_000, "deployed": 0, "day_pnl": 0}, "clock": "11:00",
            "today": "2026-10-13", "market_open": True, "matrix": matrix, "risk_per_trade_pct": 0.015,
            "index_prices": {"NIFTY": 23050.0}, "armed_count": 0}
    base.update(kw)
    return base


def matrix_of(price=23130.0, clock="10:40"):
    return {"NIFTY": {side: {**mx.evaluate(BROKEN_UP, side, clock, price, trend=1),
                             "if_break": mx.evaluate(BROKEN_UP, side, clock, price, trend=1, assume_break=True),
                             "scorecard_agrees": False} for side in mx.SIDES}}


def order(action, cid=None, lots=10, stop=60.0, pid=None, horizon="intraday", **extra):
    return {"action": action, "contract_id": cid, "position_id": pid, "lots": lots, "stop_premium": stop,
            "target_premium": 200.0, "horizon": horizon, "reason": "test", **extra}


def test_grade_a_trims_lots_to_the_risk_budget_and_grade_d_blocks_the_side():
    out = brain.validate({"actions": [order("open", "C02", lots=10), order("open", "C01", lots=2)]},
                         ctx(matrix_of()), brain.Limits(capital=1_000_000))
    ce = out.accepted[0]
    assert ce["grade"] == "A" and ce["lots"] == 4             # Rs 15,000 / (40 x 75 x 1.15 = Rs 3,450 per lot)
    assert "grade A" in ce["note"] and "trimmed from 10 to 4" in ce["note"]
    assert "grade D" in out.rejected[0]["rejected"]


def test_overnight_without_the_scorecard_halves_and_an_add_shares_the_budget():
    out = brain.validate({"actions": [order("open", "C02", lots=10, horizon="overnight")]}, ctx(matrix_of()),
                         brain.Limits(capital=1_000_000))
    assert out.accepted[0]["lots"] == 2                       # Rs 7,500 / Rs 3,450
    held = {"P01": {"symbol": CE, "lots": 3, "lot_size": 75, "ltp": 100.0, "value": 22_500, "expiry": "2026-10-20",
                    "underlying": "NIFTY", "type": "CE", "stop_premium": 60.0}}
    out = brain.validate({"actions": [order("add", pid="P01", lots=10)]}, ctx(matrix_of(), positions=held),
                         brain.Limits(capital=1_000_000))
    assert out.accepted[0]["lots"] == 1                       # Rs 15,000 - 3 x Rs 3,450 already at risk


def test_a_trigger_at_the_edge_is_sized_as_a_break_and_one_lot_too_many_is_explained():
    inside = matrix_of(price=23050.0, clock="11:00")         # inside the first hour: CE grade B (score 3)
    arm = order("arm", "C02", lots=10, trigger_index="NIFTY", trigger_op="above", trigger_level=23100,
                valid_until="14:00")
    out = brain.validate({"actions": [arm]}, ctx(inside), brain.Limits(capital=1_000_000))
    assert out.accepted[0]["grade"] == "A" and out.accepted[0]["lots"] == 4
    poor = {"NIFTY": {s: {**v, "size_multiplier": 0.3, "grade": "C", "if_break": None} for s, v in inside["NIFTY"].items()}}
    big = ctx(poor, candidates={"C02": {"symbol": CE, "ltp": 400.0, "lot_size": 75, "expiry": "2026-10-20",
                                        "underlying": "NIFTY", "type": "CE"}})
    out = brain.validate({"actions": [order("open", "C02", lots=1, stop=None)]}, big, brain.Limits(capital=1_000_000))
    assert "not even one lot fits" in out.rejected[0]["rejected"]


def test_no_matrix_in_the_context_keeps_the_old_limits():
    context = ctx(None)
    context.pop("matrix")
    out = brain.validate({"actions": [order("open", "C02", lots=10)]}, context, brain.Limits(capital=1_000_000))
    assert out.accepted[0]["lots"] == 10 and out.accepted[0]["grade"] is None


# ------------------------------------------------------------------------------------------- inputs from minute bars

def test_matrix_inputs_read_atr_prior_day_open_type_and_first_hour(tmp_path):
    db = sqlite3.connect(tmp_path / "h.sqlite3")
    db.execute("""CREATE TABLE historical_candles (symbol TEXT, timeframe_seconds INTEGER, timestamp INTEGER, open REAL,
                  high REAL, low REAL, close REAL, volume INTEGER)""")
    rows, day, seeded = [], DAY, []
    while len(seeded) < 12:
        day = feat.previous_session(day)
        seeded.append(day)
    for k, d in enumerate(sorted(seeded)):
        width = 60 if d == max(seeded) else 200                # the last session before DAY is small (an NR7)
        base = 23000 + k * 10
        for i in range(375):
            t = at(d, 9, 15) + timedelta(minutes=i)
            p = base + width * (i / 374 - 0.5)
            rows.append((SPOT, 60, int(t.timestamp()), p, p + 0.5, p - 0.5, p, 0))
    for i, (o, h, l, c) in enumerate(drive_up(70, 23200.0)):  # DAY: opens and drives up for 70 minutes
        rows.append((SPOT, 60, int((at(DAY, 9, 15) + timedelta(minutes=i)).timestamp()), o, h, l, c, 0))
    db.executemany("INSERT INTO historical_candles VALUES (?,?,?,?,?,?,?,?)", rows)
    db.commit()
    got = feat.matrix_inputs(db, SPOT, at(DAY, 10, 25))
    assert got["open_type"] == "OD" and got["open_dir"] == 1
    assert got["ib_low"] < got["ib_high"] and got["ib_atr"] > 0
    assert got["prev_nr7"] is True and got["prev_range_atr"] < 0.6
    early = feat.matrix_inputs(db, SPOT, at(DAY, 9, 40))
    assert "open_type" not in early and "ib_high" not in early
