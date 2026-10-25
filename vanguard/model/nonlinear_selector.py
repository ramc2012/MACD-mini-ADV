"""Small distributional neural network for cost-aware option selection.

M2-M5 readings are inputs, never sequential vetoes.  The model predicts the
10th/50th/90th percentiles of the next 30-minute mark-to-mark return for both
the ATM call and put.  The decision score penalises forecast width and deducts
an explicit round-trip cost.  Historical bid/ask is absent, so that cost is an
assumption and model artifacts say so.

Only NumPy is used.  Keeping the artifact as JSON makes inference reproducible
inside the existing Vanguard cycle without a new binary runtime dependency.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import numpy as np

QUANTILES = np.asarray([0.10, 0.50, 0.90], dtype=np.float64)
FAMILY = "mlp_quantile_v2_chain_ratios"
IST = ZoneInfo("Asia/Kolkata")

# Every market quantity is dimensionless: ratios, z-scores, percentiles,
# categorical flags or calendar fractions.  Raw premium/price/volume is never
# presented to the network.
FEATURE_NAMES = (
    "side_sign", "flow", "flow_aligned", "flow_age", "flow_ingredients",
    "rs", "rs_aligned", "rs_age", "gex_percentile", "regime_age",
    "timing_score", "timing_aligned", "rvol_log", "va_position",
    "best_lag", "leadlag_corr", "side_long_buildup", "side_short_covering",
    "side_long_unwind", "side_short_buildup", "other_long_buildup",
    "timing_ignition", "timing_compression", "timing_balanced",
    "regime_strong_neg", "regime_neg", "regime_neutral", "regime_pos",
    "premium_to_spot", "signed_moneyness", "iv", "abs_delta",
    "gamma_spot", "theta_to_premium", "vega_to_premium", "bar_range",
    "bar_return", "volume_to_oi", "sqrt_dte",
    "straddle_to_spot", "normalized_straddle", "strangle_straddle",
    "put_wing_iv_atm", "call_wing_iv_atm", "wing_iv_skew_log",
    "atm_put_call_premium_log", "atm_call_put_extrinsic_log",
    "premium_pcr_log", "side_itm_atm_extrinsic", "side_otm_atm_extrinsic",
    "ratio_chain_breadth", "minute_sin", "minute_cos",
)


def _number(value: Any, scale: float = 1.0) -> float:
    if value is None:
        return np.nan
    try:
        result = float(value) / scale
    except (TypeError, ValueError):
        return np.nan
    return result if np.isfinite(result) else np.nan


def _log_ratio(value: Any) -> float:
    ratio = _number(value)
    if np.isnan(ratio) or ratio <= 0:
        return np.nan
    # Bad/stale prints can create extreme ratios. The value remains ordinal
    # while a single near-zero denominator cannot dominate standardisation.
    return float(np.log(np.clip(ratio, 0.05, 20.0)))


def feature_row(inputs: dict[str, Any], instrument: dict[str, Any], option_type: str,
                ts) -> np.ndarray:
    """Build one side's causal, instrument-independent feature vector."""
    sign = 1.0 if option_type == "CE" else -1.0
    flow = _number(inputs.get("flow_score"), 100.0)
    rs = _number(inputs.get("rs_z20"), 3.0)
    timing = _number(inputs.get("timing_score"), 100.0)
    va = _number(inputs.get("va_position"))
    premium = _number(instrument.get("premium"))
    spot = _number(instrument.get("spot"))
    strike = _number(instrument.get("strike"))
    oi = _number(instrument.get("oi"))
    volume = _number(instrument.get("volume"))
    side_state = inputs.get("ce_state" if option_type == "CE" else "pe_state")
    other_state = inputs.get("pe_state" if option_type == "CE" else "ce_state")
    timing_state = str(inputs.get("timing_state") or "").upper()
    regime = str(inputs.get("regime") or "").upper()
    local_ts = ts.astimezone(IST) if getattr(ts, "tzinfo", None) else ts
    minute = local_ts.hour * 60 + local_ts.minute
    phase = 2.0 * np.pi * minute / (24.0 * 60.0)
    dte = _number(instrument.get("dte_days"))
    side_prefix = "call" if option_type == "CE" else "put"
    put_wing_iv = _number(instrument.get("put_wing_iv_ratio"))
    call_wing_iv = _number(instrument.get("call_wing_iv_ratio"))

    values = (
        sign, flow, flow * sign, _number(inputs.get("flow_age_sessions"), 3.0),
        _number(inputs.get("flow_n_ingredients"), 5.0), rs, rs * sign,
        _number(inputs.get("rs_age_sessions"), 3.0),
        _number(inputs.get("gex_percentile")),
        _number(inputs.get("regime_age_bars"), 2.0), timing,
        timing * sign * (1.0 if np.isnan(va) else np.sign(va) or 0.0),
        np.log1p(max(0.0, _number(inputs.get("rvol"))))
            if not np.isnan(_number(inputs.get("rvol"))) else np.nan,
        va, _number(inputs.get("best_lag"), 2.0),
        _number(inputs.get("leadlag_corr")),
        float(side_state == "long_buildup"), float(side_state == "short_covering"),
        float(side_state == "long_unwind"), float(side_state == "short_buildup"),
        float(other_state == "long_buildup"), float(timing_state == "IGNITION"),
        float(timing_state == "COMPRESSION"), float(timing_state == "BALANCED"),
        float(regime == "STRONG_NEG"), float(regime == "NEG"),
        float(regime == "NEUTRAL"), float(regime in {"POS", "STRONG_POS"}),
        premium / spot if premium > 0 and spot > 0 else np.nan,
        sign * (spot - strike) / spot if spot > 0 else np.nan,
        _number(instrument.get("iv")), abs(_number(instrument.get("delta"))),
        _number(instrument.get("gamma")) * spot if spot > 0 else np.nan,
        _number(instrument.get("theta")) / premium if premium > 0 else np.nan,
        _number(instrument.get("vega")) / premium if premium > 0 else np.nan,
        (_number(instrument.get("high")) - _number(instrument.get("low"))) / premium
            if premium > 0 else np.nan,
        premium / _number(instrument.get("open")) - 1.0
            if _number(instrument.get("open")) > 0 else np.nan,
        volume / oi if oi > 0 else np.nan,
        np.sqrt(max(0.0, dte)) / 10.0 if not np.isnan(dte) else np.nan,
        _number(instrument.get("straddle_to_spot")),
        _number(instrument.get("normalized_straddle")),
        _number(instrument.get("strangle_straddle_ratio")),
        put_wing_iv, call_wing_iv,
        (np.log(put_wing_iv / call_wing_iv)
         if put_wing_iv > 0 and call_wing_iv > 0 else np.nan),
        _log_ratio(instrument.get("atm_put_call_premium_ratio")),
        _log_ratio(instrument.get("atm_call_put_extrinsic_ratio")),
        _log_ratio(instrument.get("premium_pcr")),
        _number(instrument.get(f"{side_prefix}_itm_atm_extrinsic_ratio")),
        _number(instrument.get(f"{side_prefix}_otm_atm_extrinsic_ratio")),
        np.log1p(max(0.0, _number(instrument.get("ratio_n_strikes"))))
            if not np.isnan(_number(instrument.get("ratio_n_strikes"))) else np.nan,
        np.sin(phase), np.cos(phase),
    )
    row = np.asarray(values, dtype=np.float64)
    if row.shape != (len(FEATURE_NAMES),):
        raise RuntimeError("feature contract and vector length diverged")
    return row


@dataclass
class QuantileMLP:
    median: np.ndarray
    scale: np.ndarray
    weights: list[np.ndarray]
    biases: list[np.ndarray]
    selection_threshold: float
    cost_pct: float
    width_penalty: float = 0.25
    standardized_clip: float | None = None
    prediction_clip: tuple[float, float] | None = None
    version: str | None = None
    status: str | None = None

    def _prepare(self, values: np.ndarray) -> np.ndarray:
        x = np.asarray(values, dtype=np.float64)
        if x.ndim == 1:
            x = x.reshape(1, -1)
        missing = ~np.isfinite(x)
        x = np.where(missing, self.median, x)
        scaled = (x - self.median) / self.scale
        if self.standardized_clip is not None:
            scaled = np.clip(scaled, -self.standardized_clip, self.standardized_clip)
        return np.concatenate([scaled, missing.astype(np.float64)], axis=1)

    def predict(self, values: np.ndarray) -> np.ndarray:
        layer = self._prepare(values)
        for weight, bias in zip(self.weights[:-1], self.biases[:-1]):
            layer = np.tanh(layer @ weight + bias)
        output = layer @ self.weights[-1] + self.biases[-1]
        output = np.sort(output, axis=1)
        if self.prediction_clip is not None:
            output = np.clip(output, *self.prediction_clip)
        return output

    def conservative_edge(self, quantiles: np.ndarray) -> np.ndarray:
        q = np.asarray(quantiles)
        return q[:, 1] - self.width_penalty * (q[:, 2] - q[:, 0]) - self.cost_pct

    def to_artifact(self) -> dict[str, Any]:
        return {
            "family": FAMILY, "feature_names": list(FEATURE_NAMES),
            "quantiles": QUANTILES.tolist(), "median": self.median.tolist(),
            "scale": self.scale.tolist(),
            "weights": [value.tolist() for value in self.weights],
            "biases": [value.tolist() for value in self.biases],
            "selection_threshold": self.selection_threshold,
            "cost_pct": self.cost_pct, "width_penalty": self.width_penalty,
            "standardized_clip": self.standardized_clip,
            "prediction_clip": (list(self.prediction_clip)
                                if self.prediction_clip is not None else None),
        }

    @classmethod
    def from_artifact(cls, artifact: dict[str, Any], **metadata) -> "QuantileMLP":
        if artifact.get("family") != FAMILY:
            raise ValueError(f"unsupported model family: {artifact.get('family')}")
        if tuple(artifact.get("feature_names", ())) != FEATURE_NAMES:
            raise ValueError("model feature contract does not match runtime")
        return cls(
            median=np.asarray(artifact["median"], dtype=np.float64),
            scale=np.asarray(artifact["scale"], dtype=np.float64),
            weights=[np.asarray(v, dtype=np.float64) for v in artifact["weights"]],
            biases=[np.asarray(v, dtype=np.float64) for v in artifact["biases"]],
            selection_threshold=float(artifact["selection_threshold"]),
            cost_pct=float(artifact["cost_pct"]),
            width_penalty=float(artifact.get("width_penalty", 0.25)),
            standardized_clip=(float(artifact["standardized_clip"])
                               if artifact.get("standardized_clip") is not None else None),
            prediction_clip=(tuple(float(v) for v in artifact["prediction_clip"])
                             if artifact.get("prediction_clip") is not None else None),
            **metadata,
        )


def artifact_sha256(artifact: dict[str, Any]) -> str:
    blob = json.dumps(artifact, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def _pinball(y: np.ndarray, prediction: np.ndarray) -> float:
    error = y.reshape(-1, 1) - prediction
    return float(np.mean(np.maximum(QUANTILES * error, (QUANTILES - 1.0) * error)))


def fit_quantile_mlp(x_train: np.ndarray, y_train: np.ndarray,
                     x_validation: np.ndarray, y_validation: np.ndarray, *,
                     cost_pct: float = 0.01, seed: int = 20260829,
                     epochs: int = 80, batch_size: int = 1024) -> tuple[QuantileMLP, dict]:
    """Fit with Adam and early stopping on a strictly later validation slice."""
    rng = np.random.default_rng(seed)
    median = np.zeros(x_train.shape[1], dtype=np.float64)
    observed = np.any(np.isfinite(x_train), axis=0)
    median[observed] = np.nanmedian(x_train[:, observed], axis=0)
    filled = np.where(np.isfinite(x_train), x_train, median)
    scale = np.nanstd(filled, axis=0)
    scale = np.where(scale > 1e-8, scale, 1.0)

    shell = QuantileMLP(median, scale, [], [], 0.0, cost_pct)
    train = shell._prepare(x_train)
    validation = shell._prepare(x_validation)
    dims = (train.shape[1], 48, 24, 3)
    weights = [rng.normal(0.0, np.sqrt(2.0 / (a + b)), (a, b))
               for a, b in zip(dims[:-1], dims[1:])]
    biases = [np.zeros(b, dtype=np.float64) for b in dims[1:]]
    m_w = [np.zeros_like(w) for w in weights]
    v_w = [np.zeros_like(w) for w in weights]
    m_b = [np.zeros_like(b) for b in biases]
    v_b = [np.zeros_like(b) for b in biases]
    best = None
    best_loss = np.inf
    stale = 0
    step = 0

    for epoch in range(epochs):
        for start in range(0, len(train), batch_size):
            indices = rng.integers(0, len(train), size=min(batch_size, len(train)))
            layer = train[indices]
            activations = [layer]
            for weight, bias in zip(weights[:-1], biases[:-1]):
                layer = np.tanh(layer @ weight + bias)
                activations.append(layer)
            prediction = layer @ weights[-1] + biases[-1]
            target = y_train[indices].reshape(-1, 1)
            gradient = np.where(target >= prediction, -QUANTILES, 1.0 - QUANTILES)
            gradient /= len(indices)
            grad_w: list[np.ndarray] = [np.empty(0)] * len(weights)
            grad_b: list[np.ndarray] = [np.empty(0)] * len(biases)
            grad_w[-1] = activations[-1].T @ gradient + 1e-5 * weights[-1]
            grad_b[-1] = gradient.sum(axis=0)
            back = gradient @ weights[-1].T
            for index in range(len(weights) - 2, -1, -1):
                back *= 1.0 - activations[index + 1] ** 2
                grad_w[index] = activations[index].T @ back + 1e-5 * weights[index]
                grad_b[index] = back.sum(axis=0)
                if index:
                    back = back @ weights[index].T

            step += 1
            for index in range(len(weights)):
                for parameter, gradient_value, first, second in (
                    (weights[index], grad_w[index], m_w[index], v_w[index]),
                    (biases[index], grad_b[index], m_b[index], v_b[index]),
                ):
                    first *= 0.9
                    first += 0.1 * gradient_value
                    second *= 0.999
                    second += 0.001 * gradient_value * gradient_value
                    parameter -= 7e-4 * (first / (1.0 - 0.9 ** step)) / (
                        np.sqrt(second / (1.0 - 0.999 ** step)) + 1e-8)

        model = QuantileMLP(median, scale, weights, biases, 0.0, cost_pct)
        loss = _pinball(y_validation, model.predict(x_validation))
        if loss < best_loss - 1e-5:
            best_loss = loss
            best = ([w.copy() for w in weights], [b.copy() for b in biases], epoch + 1)
            stale = 0
        else:
            stale += 1
        if stale >= 10:
            break

    if best is None:
        raise RuntimeError("training did not produce a finite validation model")
    model = QuantileMLP(median, scale, best[0], best[1], 0.0, cost_pct)
    return model, {"best_epoch": best[2], "validation_pinball": best_loss}


def _load_model_for_horizon(connection, horizon_bars: int) -> QuantileMLP | None:
    """Load the newest compatible paper/shadow model for one target horizon.

    Feature-contract upgrades intentionally make older JSON artifacts
    incompatible. Walk past them rather than crashing the live cycle or,
    worse, scoring a new vector with old weights.
    """
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT version, status, artifact FROM vanguard_model_versions
                   WHERE status IN ('paper_active', 'shadow')
                     AND horizon_bars=%s
                   ORDER BY (status = 'paper_active') DESC, created_at DESC""",
                (horizon_bars,),
            )
            rows = cursor.fetchall()
    except Exception:
        # Fake test connections and pre-013 databases follow the legacy path.
        if hasattr(connection, "rollback"):
            connection.rollback()
        return None
    for row in rows:
        artifact = row[2]
        if isinstance(artifact, str):
            artifact = json.loads(artifact)
        try:
            return QuantileMLP.from_artifact(artifact, version=row[0], status=row[1])
        except ValueError:
            continue
    return None


def load_selector_model(connection) -> QuantileMLP | None:
    """Load only the intraday one-bar model used by M6 ticket diagnostics."""
    return _load_model_for_horizon(connection, 1)


def load_swing_model(connection) -> QuantileMLP | None:
    """Load the 1-2 session model used only for the daily shadow watchlist."""
    return _load_model_for_horizon(connection, 24)


# Kept for callers/tests written against the first implementation name.
load_paper_model = load_selector_model


def prediction_rows(model: QuantileMLP, values: Iterable[tuple]) -> list[dict]:
    """Score `(evaluation, instrument, option_type)` triples."""
    records = list(values)
    if not records:
        return []
    matrix = np.vstack([feature_row(e.inputs, instrument, side, e.ts)
                        for e, instrument, side in records])
    quantiles = model.predict(matrix)
    edges = model.conservative_edge(quantiles)
    rows = []
    for (evaluation, instrument, side), q, edge in zip(records, quantiles, edges):
        rows.append({
            "evaluation": evaluation, "instrument_data": instrument,
            "option_type": side, "direction": "bullish" if side == "CE" else "bearish",
            "q10": float(q[0]), "q50": float(q[1]), "q90": float(q[2]),
            "edge": float(edge), "model_version": model.version,
            "threshold": model.selection_threshold,
        })
    return rows
