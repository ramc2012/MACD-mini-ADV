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


def test_list_test_override_layers_on_the_first_and_lifts_vanguard_caps(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPER_OWNER_OVERRIDES", book_module.OWNER_OVERRIDE_TAG)
    book(tmp_path, "vanguard")                                         # an account already on the first override
    monkeypatch.setenv("PAPER_OWNER_OVERRIDES", book_module.LIST_TEST_TAG)
    b = book(tmp_path, "vanguard")
    assert b.terms["model_gate"] is False and b.terms["max_positions"] == 60 and b.terms["target"] is None
    assert b.terms["owner_overrides"] == [book_module.OWNER_OVERRIDE_TAG, book_module.LIST_TEST_TAG]
    with b.db() as db:
        tags = [json.loads(r[0])["tag"] for r in db.execute("SELECT detail FROM events WHERE kind='POLICY_UPDATED_BY_OWNER'")]
    assert tags == [book_module.OWNER_OVERRIDE_TAG, book_module.LIST_TEST_TAG]
    assert book(tmp_path, "cbe").terms.get("max_positions") == 10    # CBE has nothing in the second override


def test_vanguard_list_pick_has_no_target_and_is_scored_by_session(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPER_OWNER_OVERRIDES", book_module.LIST_TEST_TAG)
    b = book(tmp_path, "vanguard")
    rows = [dict(instrument=f"NSE_FO|P{i}", symbol=f"NSE_FO|P{i}", sector="IT", lot_size=50, tick=.05,
                 expiry="2026-11-26", candidate={"rank": i + 1, "option_type": "CE" if i % 2 else "PE"},
                 exit_due=OPEN + timedelta(days=3)) for i in range(12)]    # more picks than the old 10-position cap
    assert b.enqueue("list", rows, OPEN - timedelta(seconds=10), str(OPEN.date()), OPEN - timedelta(seconds=5),
                     OPEN + timedelta(minutes=10), b.account_id) == 12
    qs = {f"NSE_FO|P{i}": quote() for i in range(12)}
    for _ in range(4):
        approved(b, qs)
        cycle(b, qs)
    snap = b.snapshot(OPEN)
    assert len(snap["positions"]) == 12
    assert all(abs(p["entry"] * p["quantity"] - 1000000 / 60) < 100 * 50 for p in snap["positions"])
    day1, day2 = OPEN + timedelta(days=1), OPEN + timedelta(days=2)
    up = {k: quote(now=day1, bid=200, ask=200.2) for k in qs}          # +100%: no target, still open
    up["NSE_FO|P0"] = quote(now=day1, bid=80, ask=80.2)                # P0 hits the 15% stop on D1
    cycle(b, up, day1)
    cycle(b, {k: quote(now=day2, bid=150, ask=150.2) for k in qs if k != "NSE_FO|P0"}, day2)
    snap = b.snapshot(day2)
    assert len(snap["positions"]) == 11 and snap["closed"][0]["exit_reason"] == "STOP"
    test = snap["list_tests"][0]
    assert test["picks"] == test["filled"] == 12 and test["stopped"] == 1 and test["open"] == 11
    rows = {r["symbol"]: r for r in test["rows"]}
    stopped, held = rows["NSE_FO|P0"]["returns"], rows["NSE_FO|P1"]["returns"]
    assert stopped[0] == stopped[1] < -0.15 and stopped[2] == stopped[0]   # the stop's return carries forward
    assert held[0] > 0.98 and 0.48 < held[1] < 0.5 and held[2] is None     # D3 not reached yet


def test_a_lot_above_the_share_is_still_entered_when_cash_allows(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPER_OWNER_OVERRIDES", book_module.LIST_TEST_TAG)
    b = book(tmp_path, "vanguard")                                     # share 16,667; one lot 50 x 300 = 15,000+
    enqueue(b, instrument="NSE_FO|DEAR", exit_due=OPEN + timedelta(hours=5))
    q = {"NSE_FO|DEAR": quote(bid=400, ask=400.5)}                    # one lot ~ 20,000 > share, < 2x share
    approved(b, q)
    cycle(b, q)
    assert b.snapshot(OPEN)["positions"][0]["quantity"] == 50
