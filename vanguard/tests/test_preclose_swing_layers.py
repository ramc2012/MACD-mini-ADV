"""The swing lane's two daily layers (owner plan, 2026-09-04).

Layer 1 is a mandatory research ranking -- top ten CE and top ten PE.
Layer 2 is an actionable list of zero to ten contracts, after expected-return,
confidence, liquidity and M7 risk gates.
"""
import inspect
from datetime import datetime, timezone

import pytest

from fusion.m7_risk import RiskState, SizingResult
from model import preclose_swing

TS = datetime(2026, 9, 3, 8, 45, tzinfo=timezone.utc)


class _Model:
    def __init__(self, role, status):
        self.role, self.status, self.version = role, status, f"{role}_v1"


class _HorizonModel(_Model):
    feature_names = ("horizon_2", "horizon_3")


def _row(symbol, side, score, oi, mark=50.0):
    return {"symbol": symbol, "option_type": side, "horizon_sessions": 1,
            "expected_net_lower": 0.02, "strike": 100, "combined_score": score, "direction_score": score,
            "contract_score": score, "source_mark": mark, "lot_size": 100,
            "option_oi": oi, "option_volume": 0.0, "rank": 1, "side_rank": 1}


def _evaluations():
    return [{"symbol": "A", "sector20": "BANK"}, {"symbol": "B", "sector20": "IT"}]


@pytest.fixture
def sized(monkeypatch):
    monkeypatch.setattr(preclose_swing, "load_risk_state", lambda *a, **k: RiskState(
        capital=1_000_000.0, daily_pnl_pct=0.0, weekly_pnl_pct=0.0,
        open_positions=[], kelly_edge=None))
    monkeypatch.setattr(preclose_swing, "risk_check", lambda *a, **k: SizingResult(
        allowed=True, lots=1, notional=5000.0, risk_rupees=1500.0, method="fixed_fractional"))


def test_a_shadow_ranker_cannot_make_anything_actionable(sized):
    """Layer 1 still publishes; layer 2 refuses, once, with the reason on
    every row -- an unproven model must not produce rows that merely look
    unlucky."""
    refusal = preclose_swing._model_confidence_refusal(
        _Model("direction", "shadow"), _Model("contract", "shadow"))
    assert refusal is not None
    rows, note = preclose_swing.apply_actionable_gates(
        object(), [_row("A", "CE", 0.99, 1e6)], _evaluations(), TS, 1e6, refusal)
    assert [row["actionable"] for row in rows] == [False]
    assert rows[0]["actionable_reason"] == refusal == note


def test_one_promoted_ranker_is_not_enough(sized):
    refusal = preclose_swing._model_confidence_refusal(
        _Model("direction", "paper_active"), _Model("contract", "shadow"))
    assert refusal is not None and "contract" in refusal


def test_promoted_rankers_can_actually_produce_an_actionable_row(sized):
    """The gates must have a NON-EMPTY feasible set.

    A gate stack that can never pass is the commodity lane's pincer again: a
    setup that fires and executes zero, looking healthy the whole time.
    """
    rows, note = preclose_swing.apply_actionable_gates(
        object(), [_row("A", "CE", 0.99, 1e6)], _evaluations(), TS, 1e6, None)
    assert [row["actionable"] for row in rows] == [True]
    assert rows[0]["actionable_reason"] is None
    assert rows[0]["sizing_lots"] == 1
    assert note is None


def test_liquidity_is_never_gated_on_option_volume():
    """10,217 of the 10,231 option rows at a decision bar come from
    `upstox_chain`, which carries no volume at all. A volume floor would
    refuse the entire universe forever while reading like a prudent filter."""
    source = inspect.getsource(preclose_swing.apply_actionable_gates)
    assert "option_volume" not in source
    assert "option_oi" in source


def test_the_oi_floor_is_relative_to_the_days_own_cross_section(sized):
    """OI spans 82k..2.5m across names at one bar, so any constant floor is
    either inert or arbitrary. The SAME contract must clear in a thin-OI
    universe and be refused in a rich one -- that is what "relative" buys."""
    def verdict(peers):
        rows, _ = preclose_swing.apply_actionable_gates(
            object(),
            [_row("SUBJECT", "CE", 0.99, 100_000)]
            + [_row(f"P{i}", "PE", 0.99, oi) for i, oi in enumerate(peers)],
            _evaluations(), TS, 1e6, None)
        return next(row for row in rows if row["symbol"] == "SUBJECT")

    thin = verdict([10_000] * 7)
    rich = verdict([4_000_000] * 7)
    assert thin["actionable"] is True
    assert rich["actionable"] is False
    assert "cross-sectional floor" in rich["actionable_reason"]


def test_m7_vetoes_here_rather_than_flooring_the_size(monkeypatch):
    """M6 floors a refused sizing to keep observations coming; this layer is
    the one that claims to be actionable, so it may not."""
    monkeypatch.setattr(preclose_swing, "load_risk_state", lambda *a, **k: RiskState(
        capital=1_000_000.0, daily_pnl_pct=0.0, weekly_pnl_pct=0.0,
        open_positions=[], kelly_edge=None))
    monkeypatch.setattr(preclose_swing, "risk_check", lambda *a, **k: SizingResult(
        allowed=False, reason="premium cap allows 0"))
    rows, note = preclose_swing.apply_actionable_gates(
        object(), [_row("A", "CE", 0.99, 1e6)], _evaluations(), TS, 1e6, None)
    assert rows[0]["actionable"] is False
    assert "M7 refused sizing" in rows[0]["actionable_reason"]
    assert note == "every candidate refused by a layer-2 gate"


def test_the_actionable_list_is_capped_at_ten(sized):
    rows, _ = preclose_swing.apply_actionable_gates(
        object(), [_row(f"S{i}", "CE", 0.99, 1e6) for i in range(14)],
        _evaluations(), TS, 1e6, None)
    assert sum(1 for row in rows if row["actionable"]) == preclose_swing.MAX_ACTIONABLE
    assert any("cap" in (row["actionable_reason"] or "") for row in rows)


def test_d3_requires_a_model_artifact_that_declares_horizon_3():
    assert preclose_swing._horizon_features(_HorizonModel("direction", "shadow"), 1) == [0.0, 0.0]
    assert preclose_swing._horizon_features(_HorizonModel("direction", "shadow"), 2) == [1.0, 0.0]
    assert preclose_swing._horizon_features(_HorizonModel("direction", "shadow"), 3) == [0.0, 1.0]
    with pytest.raises(ValueError, match="horizon_3"):
        preclose_swing._horizon_features(_Model("direction", "shadow"), 3)


class _ChainCursor:
    """Minimal cursor over a (bar_time, breadth) table."""

    def __init__(self, rows):
        self._rows, self._result = rows, []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params):
        self._result = [row for row in self._rows if row[0] <= params["ts"]]

    def fetchall(self):
        return sorted(self._result, key=lambda row: row[0], reverse=True)


class _ChainConnection:
    def __init__(self, rows):
        self._rows = rows

    def cursor(self, **_):
        return _ChainCursor(self._rows)


def test_contracts_come_from_the_newest_chain_bar_that_actually_exists():
    """The chain sweep for a bar lands 60-75 minutes after it.

    At 14:57 IST on 2026-09-04 the 13:45 bar held 10,333 contract rows and the
    14:15 decision bar held 12 -- so pinning contracts to the decision bar
    emitted nothing on the very day the list is for. An EARLIER bar is still
    strictly causal, and the one used is stamped on every row.
    """
    decision = datetime(2026, 9, 4, 8, 45, tzinfo=timezone.utc)
    thin = decision                      # 5 of 207 symbols so far
    complete = datetime(2026, 9, 4, 8, 15, tzinfo=timezone.utc)
    symbols = [f"S{i}" for i in range(207)]
    connection = _ChainConnection([(complete, 207), (thin, 5)])
    assert preclose_swing.resolve_chain_bar(connection, decision, symbols) == complete


def test_a_future_chain_bar_can_never_be_used():
    decision = datetime(2026, 9, 4, 8, 45, tzinfo=timezone.utc)
    later = datetime(2026, 9, 4, 9, 15, tzinfo=timezone.utc)
    symbols = [f"S{i}" for i in range(207)]
    connection = _ChainConnection([(later, 207)])
    assert preclose_swing.resolve_chain_bar(connection, decision, symbols) is None


@pytest.mark.parametrize("field,value", [("combined_score",float("nan")),("source_mark",float("inf")),
    ("option_oi",float("nan")),("lot_size",0),("lot_size",1.5),("expected_net_lower",float("nan"))])
def test_bad_actionable_input_is_refused_without_aborting_other_candidates(sized,field,value):
    bad=_row("A","CE",.99,1e6);bad[field]=value
    good=_row("B","PE",.98,1e6)
    rows,_=preclose_swing.apply_actionable_gates(object(),[bad,good],_evaluations(),TS,1e6,None)
    assert not bad["actionable"] and bad["actionable_reason"]
    assert good["actionable"]
