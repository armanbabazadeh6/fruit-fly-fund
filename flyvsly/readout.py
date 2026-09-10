"""A trading readout fitted from the fly's *own* recorded population activity.

The hand-drawn rule ("buy when two population rates differ by more than N Hz") encodes a
human's guess about which pattern matters. This module replaces that guess with a small
logistic readout trained on the exact spike-count vectors the brain produced, so the side
of the trade comes from a pattern the fly actually exhibits rather than one we imposed.

Design choices that matter for the experiment's validity:

* Standardisation (mean/scale) is fitted on the training split only and stored with the
  model. A readout has to be usable bar-by-bar on live vectors, and it must not depend on
  a statistic of the whole recording it happened to be trained on.
* The split is temporal, never random. Bars are a time series; training on bars that sit
  after the bars we score would leak the future into the past.
* The target is the sign of the forward return, so the metrics report accuracy *against
  the majority-class base rate*. An accuracy of 0.51 on a series that is 55% up bars is a
  losing model, and the report has to make that visible instead of hiding it.
* Training is a plain deterministic batch gradient descent: no shuffling, no random init,
  so a recording replays byte-for-byte.
"""

from __future__ import annotations

import json

import numpy as np

SCHEMA = "flyvsly.readout/v1"

# scikit-learn is not a dependency of this project and must not become one, so the readout
# is a handful of numpy lines rather than a pipeline import.

TOP_FEATURES = 8


def _sigmoid(z: np.ndarray) -> np.ndarray:
    """Numerically stable logistic; the naive form overflows on large |z|."""
    out = np.empty_like(z)
    positive = z >= 0
    out[positive] = 1.0 / (1.0 + np.exp(-z[positive]))
    exp_z = np.exp(z[~positive])
    out[~positive] = exp_z / (1.0 + exp_z)
    return out


def _accuracy(scores: np.ndarray, targets: np.ndarray) -> float:
    if targets.size == 0:
        return 0.0
    predicted = np.where(scores >= 0.0, 1.0, -1.0)
    return float(np.mean(predicted == targets))


def _base_rate(targets: np.ndarray) -> float:
    """Majority-class frequency: the score a model must beat to be worth trading."""
    if targets.size == 0:
        return 0.0
    up = float(np.mean(targets > 0.0))
    return max(up, 1.0 - up)


class Readout:
    """Standardised logistic readout over one arm's per-bar population vector."""

    def __init__(self, weights, bias, mean, scale, metrics, trained_on=""):
        self.weights = np.asarray(weights, dtype=np.float64)
        self.bias = float(bias)
        self.mean = np.asarray(mean, dtype=np.float64)
        self.scale = np.asarray(scale, dtype=np.float64)
        self.metrics = dict(metrics)
        self.trained_on = str(trained_on)

    @property
    def features(self) -> int:
        return int(self.weights.size)

    def _standardise(self, vector) -> np.ndarray:
        x = np.asarray(vector, dtype=np.float64).reshape(-1)
        if x.size != self.features:
            raise ValueError(
                f"readout expects {self.features} features, got {x.size}"
            )
        return (x - self.mean) / self.scale

    def score(self, vector) -> float:
        """Log-odds of the next bar being up, given this bar's population vector."""
        return float(np.dot(self.weights, self._standardise(vector)) + self.bias)

    def decide(self, vector, margin: float = 0.15) -> str:
        """Trade only when the score clears `margin`; otherwise stay flat.

        The margin is what keeps the readout from trading on noise: a score near zero is
        the model admitting it cannot tell up from down.
        """
        value = self.score(vector)
        if value >= margin:
            return "BUY"
        if value <= -margin:
            return "SELL"
        return "HOLD"

    def save(self, path) -> None:
        payload = {
            "schema": SCHEMA,
            "weights": self.weights.tolist(),
            "bias": self.bias,
            "mean": self.mean.tolist(),
            "scale": self.scale.tolist(),
            "metrics": self.metrics,
            "trained_on": self.trained_on,
        }
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)

    @classmethod
    def load(cls, path) -> "Readout":
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except json.JSONDecodeError as error:
            # A truncated or non-JSON artifact is a corrupt experiment record, and the
            # caller needs ValueError rather than a json-internal exception type.
            raise ValueError(f"readout file is not valid JSON: {path}") from error
        if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
            raise ValueError(
                f"readout schema mismatch: expected {SCHEMA!r}, "
                f"got {payload.get('schema') if isinstance(payload, dict) else payload!r}"
            )
        try:
            return cls(
                weights=payload["weights"],
                bias=payload["bias"],
                mean=payload["mean"],
                scale=payload["scale"],
                metrics=payload.get("metrics", {}),
                trained_on=payload.get("trained_on", ""),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"readout file is missing required fields: {path}") from error

    def describe(self) -> dict:
        order = np.argsort(-np.abs(self.weights))[:TOP_FEATURES]
        top = [
            {"index": int(index), "weight": float(self.weights[index])}
            for index in order
        ]
        notes = (
            f"logistic readout over {self.features} population features, "
            f"horizon {self.metrics.get('horizon', '?')} bar(s); "
            "score is a log-odds of the next bar closing up."
        )
        if self.trained_on:
            notes = f"{notes} Trained on {self.trained_on}."
        return {
            "schema": SCHEMA,
            "horizon": int(self.metrics.get("horizon", 0)),
            "features": self.features,
            "train": dict(self.metrics.get("train", {})),
            "holdout": dict(self.metrics.get("holdout", {})),
            "trained_on": self.trained_on,
            "top_features": top,
            "notes": notes,
        }


def fit(
    vectors,
    closes,
    *,
    horizon: int = 1,
    train_fraction: float = 0.75,
    epochs: int = 600,
    learning_rate: float = 0.5,
    l2: float = 1e-3,
    trained_on: str = "",
) -> Readout:
    """Fit a readout to predict the sign of `closes[i + horizon] - closes[i]`.

    `vectors` are the per-bar population vectors for a single arm, in time order, and
    `closes` that arm's bar closes. Bounded batches of batch gradient descent keep this
    fast enough to run inside a trading loop; the objective is the usual logistic
    log-loss, so the score is calibrated-ish log-odds rather than an arbitrary sum.
    """
    if horizon < 1:
        raise ValueError("horizon must be at least one bar")
    if not 0.0 < train_fraction < 1.0:
        raise ValueError("train_fraction must lie strictly between 0 and 1")

    features = np.asarray(vectors, dtype=np.float64)
    if features.ndim != 2 or features.shape[0] == 0 or features.shape[1] == 0:
        raise ValueError("vectors must be a non-empty 2-D array of per-bar features")
    prices = np.asarray(closes, dtype=np.float64).reshape(-1)
    if prices.size != features.shape[0]:
        raise ValueError(
            f"vectors ({features.shape[0]}) and closes ({prices.size}) must agree in length"
        )

    # Drop the tail with no forward return; the remaining bars all have a target.
    usable = features.shape[0] - horizon
    if usable < 2:
        raise ValueError("not enough bars to fit a readout for this horizon")

    inputs = features[:usable]
    # A flat forward move has no direction to learn; treat it as an up bar so the label
    # is always ±1 rather than an ambiguous zero.
    deltas = prices[horizon:] - prices[:usable]
    targets = np.where(deltas >= 0.0, 1.0, -1.0)

    split = int(usable * train_fraction)
    # At least one bar on each side, otherwise "holdout accuracy" is meaningless.
    split = max(1, min(split, usable - 1))

    train_inputs, holdout_inputs = inputs[:split], inputs[split:]
    train_targets, holdout_targets = targets[:split], targets[split:]

    # Standardisation comes from the training split alone — the holdout must stay unseen.
    mean = train_inputs.mean(axis=0)
    scale = train_inputs.std(axis=0)
    scale[scale == 0.0] = 1.0  # constant feature: leave it unscaled, its weight shrinks
    train_std = (train_inputs - mean) / scale
    holdout_std = (holdout_inputs - mean) / scale

    weights = np.zeros(features.shape[1], dtype=np.float64)
    bias = 0.0
    for _ in range(epochs):
        logits = train_std @ weights + bias
        probabilities = _sigmoid(logits)
        errors = probabilities - (train_targets > 0.0)
        weights -= learning_rate * (train_std.T @ errors / split + l2 * weights)
        bias -= learning_rate * float(errors.mean())

    train_scores = train_std @ weights + bias
    holdout_scores = holdout_std @ weights + bias

    metrics = {
        "horizon": int(horizon),
        "features": int(features.shape[1]),
        "epochs": int(epochs),
        "learning_rate": float(learning_rate),
        "l2": float(l2),
        "train_fraction": float(train_fraction),
        "train": {
            "bars": int(train_targets.size),
            "accuracy": _accuracy(train_scores, train_targets),
            "base_rate": _base_rate(train_targets),
        },
        "holdout": {
            "bars": int(holdout_targets.size),
            "accuracy": _accuracy(holdout_scores, holdout_targets),
            "base_rate": _base_rate(holdout_targets),
        },
        # The split is temporal and contiguous; exposing the ranges lets a caller (and a
        # test) confirm the holdout really is the later stretch rather than a random pick.
        "split": {"train": [0, split], "holdout": [split, int(usable)]},
    }
    return Readout(weights, bias, mean, scale, metrics, trained_on=trained_on)
