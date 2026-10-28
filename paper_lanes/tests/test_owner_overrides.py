"""Owner overrides of 28 Oct 2026: CBE Rs 2 lakh per position on session-volume liquidity; Vanguard keeps the published
contract (no ATM re-check), judges liquidity on session volume and accepts a 5% spread. Applied once, audited."""
import json
from datetime import timedelta

from paper_lanes import book as book_module
from paper_lanes.tests.test_books import OPEN, approved, book, cycle, enqueue, quote


def thin(**kw):
    """A quote with tiny displayed size but heavy traded volume (a liquid large cap at the top of the book)."""
    q = quote(**kw)
    q.update(bid_qty=2, ask_qty=2, volume=5_000_000)
    return q


def test_cbe_two_lakh_ticket_ignores_displayed_size_and_is_audited_once(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPER_OWNER_OVERRIDES", book_module.OWNER_OVERRIDE_TAG)
    b = book(tmp_path, "cbe")
    assert b.terms["position_amount"] == 200000 and b.terms["liquidity_basis"] == "session_volume"
    assert enqueue(b) == 1
    q = {"NSE_EQ|TEST": thin(bid=1000, ask=1000.5)}
    approved(b, q)
    cycle(b, q)
    pos = b.snapshot(OPEN)["positions"][0]
    assert 190000 <= pos["quantity"] * pos["entry"] <= 200000          # ~Rs 2 lakh, not 2 shares
    restarted = book(tmp_path, "cbe")
    with restarted.db() as db:
        events = [r[0] for r in db.execute("SELECT kind FROM events WHERE kind='POLICY_UPDATED_BY_OWNER'")]
    assert events == ["POLICY_UPDATED_BY_OWNER"]                       # applied exactly once


def test_cbe_exit_does_not_wait_for_displayed_depth_under_the_override(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPER_OWNER_OVERRIDES", book_module.OWNER_OVERRIDE_TAG)
    b = book(tmp_path, "cbe")
    enqueue(b)
    q = {"NSE_EQ|TEST": thin(bid=1000, ask=1000.5)}
    approved(b, q)
    cycle(b, q)
    later = OPEN + timedelta(minutes=5)
    cycle(b, {"NSE_EQ|TEST": thin(now=later, bid=900, ask=900.5)}, later)  # below the 5% stop
    snap = b.snapshot(later)
    assert not snap["positions"] and snap["closed"] and snap["closed"][0]["exit_reason"] == "STOP"


def test_vanguard_keeps_the_published_contract_and_wider_spread(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPER_OWNER_OVERRIDES", book_module.OWNER_OVERRIDE_TAG)
    b = book(tmp_path, "vanguard")
    assert b.terms["atm_recheck"] is False and b.terms["max_spread"] == 0.05 and b.terms["model_gate"] is False
    guard = {"spot_instrument": "NSE_EQ|S", "strikes": [950, 1000, 1050], "strike": 1000}
    row = dict(instrument="NSE_FO|X", symbol="X", sector="TEST", lot_size=50, tick=.05, expiry="2026-10-29",
               candidate={"contract_kind": "ATM", "atm_guard": guard}, exit_due=OPEN + timedelta(days=1))
    assert b.enqueue("new", [row], OPEN - timedelta(seconds=10), str(OPEN.date()), OPEN - timedelta(seconds=5),
                     OPEN + timedelta(minutes=10), b.account_id) == 1
    q = {"NSE_FO|X": quote(bid=100, ask=104), "NSE_EQ|S": quote(bid=1039, ask=1041)}   # 3.8% spread, spot off ATM
    approved(b, q)
    cycle(b, q)
    assert b.snapshot(OPEN)["positions"]


def test_without_the_setting_defaults_stay_unchanged(tmp_path, monkeypatch):
    monkeypatch.delenv("PAPER_OWNER_OVERRIDES", raising=False)
    b = book(tmp_path, "cbe")
    assert "position_amount" not in b.terms and b.terms.get("owner_override") is None
    with b.db() as db:
        assert json.loads(db.execute("SELECT policy FROM account").fetchone()[0]).get("owner_override") is None
