from copy import deepcopy

from macd_trader.pattern_profile_research import analyse_rules
from macd_trader.strategy_lab import ENTRY_RULES, select_training_configurations


def pattern_event(index, feature, return_pct):
    return {
        "date": f"2026-08-{3 + index // 5:02d}",
        "entry_time": index,
        "candidate_return_pct": return_pct,
        feature: True,
    }


def pattern_training():
    return [
        pattern_event(index, feature, return_pct)
        for feature, return_pct in (("option_reclaim_ema20", 10), ("option_inside_bar", 1))
        for index in range(20)
    ]


def pattern_selection(rows):
    return [(row["rule"], row["train"], row["positive_train_blocks"]) for row in rows]


def test_pattern_holdout_profits_cannot_choose_or_rank_rules():
    holdout = [
        pattern_event(index, feature, 20)
        for feature in ("option_reclaim_ema20", "option_inside_bar")
        for index in range(3)
    ]
    first = analyse_rules(pattern_training(), holdout)
    changed = deepcopy(holdout)
    for row in changed:
        if row.get("option_reclaim_ema20"):
            row["candidate_return_pct"] = -50

    second = analyse_rules(pattern_training(), changed)

    assert pattern_selection(first) == pattern_selection(second)
    assert second[0]["rule"] == "option_reclaim_ema20"
    assert second[0]["test"]["average_return_pct"] == -50
    assert second[0]["holdout_positive"] is False


def test_pattern_zero_holdout_coverage_does_not_remove_training_rule():
    first = analyse_rules(pattern_training(), [pattern_event(0, "option_reclaim_ema20", 10)] * 3)

    second = analyse_rules(pattern_training(), [])

    assert pattern_selection(first) == pattern_selection(second)
    assert second[0]["test"]["trades"] == 0


def configuration(name, rule, train_net, test_net):
    return {
        "config": name,
        "entry_rule": rule,
        "in_sample": {"net": train_net},
        "out_of_sample": {"net": test_net, "trades": 10, "win_rate": 50, "profit_factor": 1},
        "capture_out": test_net / 100,
    }


def config_selection(result):
    return {
        "ranked": [row["config"] for row in result["ranked"]],
        "worst": [row["config"] for row in result["worst"]],
        "by_rule": {rule: row["best_config"] for rule, row in result["by_rule"].items()},
    }


def test_strategy_holdout_profits_cannot_choose_global_or_per_rule_winner():
    first_rule, second_rule = list(ENTRY_RULES)[:2]
    rows = [
        configuration("strong_train", first_rule, 200, 10),
        configuration("test_luck", first_rule, 100, 1000),
        configuration("other_rule", second_rule, 50, 500),
    ]
    before = select_training_configurations(rows)
    changed = deepcopy(rows)
    changed[0]["out_of_sample"]["net"] = -100000
    changed[0]["capture_out"] = -1000
    changed[1]["out_of_sample"]["net"] = 1000000

    after = select_training_configurations(changed)

    assert config_selection(before) == config_selection(after)
    assert after["ranked"][0]["config"] == "strong_train"
    assert after["by_rule"][first_rule]["best_config"] == "strong_train"
    assert after["by_rule"][first_rule]["oos_net"] == -100000
    assert after["selection"]["holdout_used_for_selection"] is False
    assert after["selection"]["holdout_independence_verified"] is False
    assert after["selection"]["validated_for_deployment"] is False


def test_strategy_all_negative_training_fallback_ignores_test_luck():
    rule = next(iter(ENTRY_RULES))
    rows = [
        configuration("least_bad_train", rule, -10, -1000),
        configuration("test_luck", rule, -100, 100000),
    ]

    result = select_training_configurations(rows)

    assert result["ranked"][0]["config"] == "least_bad_train"
    assert result["by_rule"][rule]["best_config"] == "least_bad_train"
    assert result["selection"]["positive_training_candidates"] == 0
