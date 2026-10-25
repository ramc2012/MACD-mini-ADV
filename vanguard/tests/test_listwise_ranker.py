import numpy as np

from model.listwise_ranker import (
    ListwiseMLP,
    fit_listwise_mlp,
    graded_relevance,
    ranking_metrics,
)
from model.preclose_swing import select_top


def test_relevance_is_relative_to_each_candidate_list():
    relevance = graded_relevance(np.asarray([-0.3, 0.4, 0.1, -0.1]), top_n=2)
    assert relevance.tolist() == [2.0, 3.0, 3.0, 2.0]


def test_listwise_artifact_round_trip_preserves_scores():
    rng = np.random.default_rng(7)
    x = rng.normal(size=(48, 3))
    y = x[:, 0] - 0.2 * x[:, 1]
    groups = np.asarray([f"d{index // 8}" for index in range(48)])
    model, _ = fit_listwise_mlp(
        x[:32], y[:32], groups[:32], x[32:], y[32:], groups[32:],
        ("a", "b", "c"), epochs=3, seed=11,
    )
    restored = ListwiseMLP.from_artifact(model.to_artifact())
    assert np.allclose(model.score(x), restored.score(x))
    assert ranking_metrics(y, model.score(x), groups)["groups"] == 6


def test_the_ranking_is_top_ten_per_side_not_a_mixed_top_ten():
    """Owner plan 2026-09-04: "top-ten CE and top-ten PE opportunities".

    A single mixed top-ten collapses to whichever side the market favoured --
    on 2026-09-03 that was ten PE and zero CE, which reports the absence of a
    CE ranking rather than the ranking. Each side is ranked on its own; a name
    may appear once per side, never twice within one.
    """
    rows = [
        {"symbol": "A", "option_type": "CE", "horizon_sessions": 1,
         "strike": 100, "combined_score": 0.9, "direction_score": 1.0, "contract_score": 1.0},
        {"symbol": "A", "option_type": "CE", "horizon_sessions": 2,
         "strike": 100, "combined_score": 0.85, "direction_score": 1.0, "contract_score": 0.9},
        {"symbol": "A", "option_type": "PE", "horizon_sessions": 2,
         "strike": 90, "combined_score": 0.8, "direction_score": 0.9, "contract_score": 0.8},
        {"symbol": "B", "option_type": "PE", "horizon_sessions": 2,
         "strike": 200, "combined_score": 0.7, "direction_score": 0.8, "contract_score": 0.7},
    ]
    selected = select_top(rows, 10)
    # A's second CE expression is dropped; its PE expression is not.
    assert [(row["option_type"], row["symbol"], row["side_rank"]) for row in selected] == [
        ("CE", "A", 1), ("PE", "A", 1), ("PE", "B", 2)]
    assert [row["rank"] for row in selected] == [1, 2, 3]


def test_each_side_is_capped_independently():
    rows = [{"symbol": f"S{i}", "option_type": side, "horizon_sessions": 1, "strike": 100,
             "combined_score": 0.9 - i / 100, "direction_score": 0.5, "contract_score": 0.5}
            for side in ("CE", "PE") for i in range(15)]
    selected = select_top(rows, 10)
    assert len(selected) == 20
    assert sum(1 for row in selected if row["option_type"] == "CE") == 10
    assert sum(1 for row in selected if row["option_type"] == "PE") == 10
