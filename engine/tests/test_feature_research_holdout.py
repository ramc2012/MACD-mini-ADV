from copy import deepcopy
from datetime import UTC, datetime

from macd_trader.feature_research import discover, trade_outcome
from macd_trader.models import Candle


def event(day, volume_ratio, return_pct, *, closed=True, exit_time=None):
    return {
        "date": day,
        "volume_ratio": volume_ratio,
        "return_pct": return_pct,
        "closed": closed,
        "large_winner": return_pct >= 50,
        "exit_time": exit_time,
    }


def training_events():
    return [
        event("2026-08-10", index, 20 if index >= 12 else -30,
              exit_time="2026-08-10T12:00:00+05:30")
        for index in range(40)
    ]


def selected_rules(result):
    return [
        (row["rule"], row["score"], row["train"])
        for row in result["robust_candidates"]
    ]


def test_holdout_returns_features_and_coverage_cannot_change_rule_selection():
    holdout = [event(day, 30, 100) for day in ("2026-08-11", "2026-08-12") for _ in range(6)]
    result = discover(training_events() + holdout)

    changed = deepcopy(holdout)
    for row in changed:
        row["volume_ratio"] = -1000
        row["return_pct"] = -99
        row["large_winner"] = False
    changed_result = discover(training_events() + changed[:1] + changed[-1:])

    assert result["robust_candidates"]
    assert selected_rules(result) == selected_rules(changed_result)
    assert result["robust_candidates"][0]["test"]["signals"] > 0
    assert changed_result["robust_candidates"][0]["test"]["signals"] == 0
    assert result["selection"]["holdout_used_for_selection"] is False


def test_negative_holdout_results_remain_in_report_without_reranking():
    holdout = [event(day, 30, -50) for day in ("2026-08-11", "2026-08-12")]

    result = discover(training_events() + holdout)

    assert result["robust_candidates"]
    assert result["robust_candidates"][0]["test"]["average_return_pct"] == -50
    assert result["selection"]["validated_for_deployment"] is False


def test_training_outcomes_crossing_holdout_boundary_are_purged():
    overlap = event("2026-08-10", 99999, 10000, exit_time="2026-08-11T00:00:00+05:30")
    holdout = [event(day, 30, 1) for day in ("2026-08-11", "2026-08-12")]
    baseline = discover(training_events() + holdout)

    result = discover(training_events() + [overlap] + holdout)

    assert selected_rules(result) == selected_rules(baseline)
    assert result["baseline_train"] == baseline["baseline_train"]
    assert result["selection"]["training_labels_purged_at_or_after_test_start"] == 1
    assert result["selection"]["holdout_independence_verified"] is True


def test_legacy_training_labels_are_explicitly_unverified():
    train = training_events()
    train[0].pop("exit_time")
    holdout = [event(day, 30, 1) for day in ("2026-08-11", "2026-08-12")]

    result = discover(train + holdout)

    assert result["selection"]["training_labels_without_verified_exit_time"] == 1
    assert result["selection"]["holdout_independence_verified"] is False
    assert result["selection"]["evidence_status"] == "exploratory_unpurged"
    assert result["selection"]["validated_for_deployment"] is False


def test_open_holdout_events_do_not_move_signal_date_partition():
    holdout = [event(day, 30, 0, closed=False) for day in ("2026-08-11", "2026-08-12")]

    result = discover(training_events() + holdout)

    assert result["split"]["training_dates"] == ["2026-08-10"]
    assert result["split"]["test_dates"] == ["2026-08-11", "2026-08-12"]
    assert result["baseline_test"]["open"] == 2


def test_closed_outcome_is_not_available_until_exit_bar_close():
    timestamp = int(datetime(2026, 8, 10, 4, 0, tzinfo=UTC).timestamp())
    candle = Candle("TEST", timestamp, 100, 105, 60, 75, 100, True)

    outcome = trade_outcome([candle], 0)

    assert outcome["closed"] is True
    assert datetime.fromisoformat(outcome["exit_time"]).timestamp() == timestamp + 1800
