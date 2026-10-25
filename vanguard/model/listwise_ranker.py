"""Small dependency-free listwise ranker for Vanguard's pre-close lane.

Rows are never treated as independent observations: every loss term is formed
inside a source-session query group.  The model learns a scalar ordering score;
it is not a calibrated return forecast and cannot create a ticket or order.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import numpy as np


def _softmax(values: np.ndarray) -> np.ndarray:
    shifted = values - np.max(values)
    exp = np.exp(np.clip(shifted, -40.0, 40.0))
    return exp / max(float(exp.sum()), 1e-12)


def graded_relevance(targets: np.ndarray, top_n: int = 10) -> np.ndarray:
    """Session-relative relevance: top 10 > top 20 > positive > non-positive."""
    y = np.asarray(targets, dtype=np.float64)
    order = np.argsort(-y, kind="stable")
    relevance = np.zeros(len(y), dtype=np.float64)
    relevance[y > 0.0] = 1.0
    relevance[order[: min(20, len(order))]] = 2.0
    relevance[order[: min(top_n, len(order))]] = 3.0
    return relevance


def ndcg_at_k(targets: np.ndarray, scores: np.ndarray, k: int = 10) -> float:
    relevance = graded_relevance(targets, k)
    predicted = np.argsort(-np.asarray(scores), kind="stable")[:k]
    ideal = np.argsort(-relevance, kind="stable")[:k]
    discounts = 1.0 / np.log2(np.arange(2, len(predicted) + 2))
    dcg = float(np.sum((2.0 ** relevance[predicted] - 1.0) * discounts))
    idcg = float(np.sum((2.0 ** relevance[ideal] - 1.0) * discounts))
    return dcg / idcg if idcg > 0 else 0.0


def ranking_metrics(targets: np.ndarray, scores: np.ndarray,
                    groups: Iterable[str], k: int = 10) -> dict[str, Any]:
    group_values = np.asarray(list(groups))
    daily = []
    for group in sorted(set(group_values.tolist())):
        indices = np.flatnonzero(group_values == group)
        if not len(indices):
            continue
        take = min(k, len(indices))
        predicted = indices[np.argsort(-scores[indices], kind="stable")[:take]]
        actual = indices[np.argsort(-targets[indices], kind="stable")[:take]]
        overlap = len(set(predicted.tolist()) & set(actual.tolist()))
        daily.append({
            "group": group,
            "n": int(len(indices)),
            "overlap": overlap,
            "precision_at_10": overlap / take,
            "ndcg_at_10": ndcg_at_k(targets[indices], scores[indices], take),
            "selected_mean": float(np.mean(targets[predicted])),
            "selected_median": float(np.median(targets[predicted])),
        })
    return {
        "groups": len(daily),
        "selected": sum(min(k, row["n"]) for row in daily),
        "overlap_at_10": float(np.mean([row["overlap"] for row in daily])) if daily else None,
        "precision_at_10": float(np.mean([row["precision_at_10"] for row in daily])) if daily else None,
        "ndcg_at_10": float(np.mean([row["ndcg_at_10"] for row in daily])) if daily else None,
        "selected_mean": float(np.mean([row["selected_mean"] for row in daily])) if daily else None,
        "positive_group_rate": float(np.mean([row["selected_mean"] > 0 for row in daily])) if daily else None,
        "worst_group_mean": float(min(row["selected_mean"] for row in daily)) if daily else None,
        "daily": daily,
    }


@dataclass
class ListwiseMLP:
    feature_names: tuple[str, ...]
    median: np.ndarray
    scale: np.ndarray
    weights: list[np.ndarray]
    biases: list[np.ndarray]
    version: str | None = None
    role: str | None = None
    status: str | None = None
    return_calibration: dict | None = None

    def _prepare(self, values: np.ndarray) -> np.ndarray:
        x = np.asarray(values, dtype=np.float64)
        if x.ndim == 1:
            x = x.reshape(1, -1)
        missing = ~np.isfinite(x)
        x = np.where(missing, self.median, x)
        scaled = np.clip((x - self.median) / self.scale, -3.0, 3.0)
        return np.concatenate([scaled, missing.astype(np.float64)], axis=1)

    def score(self, values: np.ndarray) -> np.ndarray:
        layer = self._prepare(values)
        for weight, bias in zip(self.weights[:-1], self.biases[:-1]):
            layer = np.tanh(layer @ weight + bias)
        return (layer @ self.weights[-1] + self.biases[-1]).reshape(-1)

    def to_artifact(self) -> dict[str, Any]:
        return {
            "family": "listwise_mlp_v1",
            "feature_names": list(self.feature_names),
            "median": self.median.tolist(),
            "scale": self.scale.tolist(),
            "weights": [value.tolist() for value in self.weights],
            "biases": [value.tolist() for value in self.biases],
            "standardized_clip": 3.0,
            "return_calibration": self.return_calibration,
        }

    @classmethod
    def from_artifact(cls, artifact: dict[str, Any], **metadata) -> "ListwiseMLP":
        if artifact.get("family") != "listwise_mlp_v1":
            raise ValueError(f"unsupported listwise family: {artifact.get('family')}")
        return cls(
            feature_names=tuple(artifact["feature_names"]),
            return_calibration=artifact.get("return_calibration"),
            median=np.asarray(artifact["median"], dtype=np.float64),
            scale=np.asarray(artifact["scale"], dtype=np.float64),
            weights=[np.asarray(value, dtype=np.float64) for value in artifact["weights"]],
            biases=[np.asarray(value, dtype=np.float64) for value in artifact["biases"]],
            **metadata,
        )


def fit_listwise_mlp(x_train: np.ndarray, y_train: np.ndarray, groups_train: Iterable[str],
                     x_validation: np.ndarray, y_validation: np.ndarray,
                     groups_validation: Iterable[str], feature_names: tuple[str, ...], *,
                     epochs: int = 80, seed: int = 20260904) -> tuple[ListwiseMLP, dict[str, Any]]:
    rng = np.random.default_rng(seed)
    median = np.zeros(x_train.shape[1], dtype=np.float64)
    observed = np.any(np.isfinite(x_train), axis=0)
    median[observed] = np.nanmedian(x_train[:, observed], axis=0)
    filled = np.where(np.isfinite(x_train), x_train, median)
    scale = np.nanstd(filled, axis=0)
    scale = np.where(scale > 1e-8, scale, 1.0)
    shell = ListwiseMLP(feature_names, median, scale, [], [])
    train = shell._prepare(x_train)
    dims = (train.shape[1], 32, 16, 1)
    weights = [rng.normal(0.0, np.sqrt(2.0 / (a + b)), (a, b))
               for a, b in zip(dims[:-1], dims[1:])]
    biases = [np.zeros(b, dtype=np.float64) for b in dims[1:]]
    m_w = [np.zeros_like(value) for value in weights]
    v_w = [np.zeros_like(value) for value in weights]
    m_b = [np.zeros_like(value) for value in biases]
    v_b = [np.zeros_like(value) for value in biases]
    group_values = np.asarray(list(groups_train))
    unique_groups = np.asarray(sorted(set(group_values.tolist())))
    best = None
    best_ndcg = -np.inf
    stale = 0
    step = 0

    for epoch in range(epochs):
        for group in rng.permutation(unique_groups):
            indices = np.flatnonzero(group_values == group)
            layer = train[indices]
            activations = [layer]
            for weight, bias in zip(weights[:-1], biases[:-1]):
                layer = np.tanh(layer @ weight + bias)
                activations.append(layer)
            logits = (layer @ weights[-1] + biases[-1]).reshape(-1)
            target_probability = _softmax(graded_relevance(y_train[indices]) * 1.5)
            gradient = (_softmax(logits) - target_probability).reshape(-1, 1)
            grad_w: list[np.ndarray] = [np.empty(0)] * len(weights)
            grad_b: list[np.ndarray] = [np.empty(0)] * len(biases)
            grad_w[-1] = activations[-1].T @ gradient + 1e-4 * weights[-1]
            grad_b[-1] = gradient.sum(axis=0)
            back = gradient @ weights[-1].T
            for index in range(len(weights) - 2, -1, -1):
                back *= 1.0 - activations[index + 1] ** 2
                grad_w[index] = activations[index].T @ back + 1e-4 * weights[index]
                grad_b[index] = back.sum(axis=0)
                if index:
                    back = back @ weights[index].T
            step += 1
            for index in range(len(weights)):
                for parameter, grad, first, second in (
                    (weights[index], grad_w[index], m_w[index], v_w[index]),
                    (biases[index], grad_b[index], m_b[index], v_b[index]),
                ):
                    first *= 0.9
                    first += 0.1 * grad
                    second *= 0.999
                    second += 0.001 * grad * grad
                    parameter -= 3e-4 * (first / (1.0 - 0.9 ** step)) / (
                        np.sqrt(second / (1.0 - 0.999 ** step)) + 1e-8)

        candidate = ListwiseMLP(feature_names, median, scale, weights, biases)
        validation_scores = candidate.score(x_validation)
        metric = ranking_metrics(y_validation, validation_scores, groups_validation)
        ndcg = metric["ndcg_at_10"] or 0.0
        if ndcg > best_ndcg + 1e-5:
            best_ndcg = ndcg
            best = ([value.copy() for value in weights],
                    [value.copy() for value in biases], epoch + 1)
            stale = 0
        else:
            stale += 1
        if stale >= 12:
            break
    if best is None:
        raise RuntimeError("listwise training did not produce a finite model")
    model = ListwiseMLP(feature_names, median, scale, best[0], best[1])
    return model, {"best_epoch": best[2], "validation_ndcg_at_10": best_ndcg}
