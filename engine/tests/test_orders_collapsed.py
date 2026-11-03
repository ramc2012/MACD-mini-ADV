"""The order log folds each run of rejects into one row per contract, side and IST day."""
from datetime import UTC, datetime, timedelta

from macd_trader.models import Order
from macd_trader.repository import TradeRepository


def test_reject_runs_fold_and_everything_else_stays(tmp_path):
    repo = TradeRepository(str(tmp_path / "book.sqlite3"))
    t0 = datetime(2026, 10, 30, 4, 0, tzinfo=UTC)                     # 09:30 IST
    repo.save_order(Order("NSE:A26NOV100CE", "BUY", 50, status="FILLED", fill_price=10.0, created_at=t0))
    for k in range(5):
        repo.save_order(Order("NSE:A26NOV100CE", "SELL", 50, status="REJECTED", created_at=t0 + timedelta(seconds=k + 1),
                              reject_reason="ValueError: Sell quantity exceeds available holdings"))
    # 19:00 UTC is 00:30 IST the next day: a separate run.
    repo.save_order(Order("NSE:A26NOV100CE", "SELL", 50, status="REJECTED", created_at=datetime(2026, 10, 30, 19, 0, tzinfo=UTC)))
    rows = repo.orders_collapsed()
    assert len(rows) == 3
    folded = [r for r in rows if r.get("repeat_count") == 5][0]
    assert folded["created_at"].startswith("2026-10-30T04:00:05")
    assert folded["first_at"].startswith("2026-10-30T04:00:01")
    assert folded["reject_reasons"] == ["ValueError: Sell quantity exceeds available holdings"]
    assert [r for r in rows if r["status"] == "FILLED"][0].get("repeat_count") is None
    assert [r for r in rows if r.get("repeat_count") == 1][0]["reject_reasons"] == []
