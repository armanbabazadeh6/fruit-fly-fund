"""The readout is the experiment's learned trading rule, so the tests pin its honesty:
it must beat the base rate when a signal exists, admit defeat on noise, never train on the
future, and reproduce itself exactly."""

import json

import numpy as np
import pytest

from flyvsly.readout import SCHEMA, Readout, fit


def _series(features, returns, start=100.0):
    """Closes whose one-bar forward move is exactly `returns` (length == features)."""
    return start + np.cumsum(returns)


def _planted(seed=0, bars=500, features=6):
    rng = np.random.default_rng(seed)
    vectors = rng.normal(size=(bars, features))
    true_weights = np.array([1.2, -0.9, 0.7, 0.0, 0.0, 0.0])
    moves = vectors @ true_weights * 0.5 + rng.normal(scale=0.3, size=bars)
    return vectors.tolist(), _series(vectors, moves).tolist()


def test_signal_is_learned_and_beats_the_base_rate():
    vectors, closes = _planted()
    readout = fit(vectors, closes)
    holdout = readout.metrics["holdout"]
    # A planted linear signal has to survive the temporal holdout, not just memorise train.
    assert holdout["accuracy"] > holdout["base_rate"] + 0.15
    assert holdout["accuracy"] > 0.7


def test_pure_noise_reports_a_null_result_instead_of_hiding_it():
    rng = np.random.default_rng(7)
    vectors = rng.normal(size=(600, 5))
    # Closes are independent of the features: any learned edge is an illusion.
    closes = _series(vectors, rng.normal(scale=1.0, size=600))
    readout = fit(vectors, closes)
    holdout = readout.metrics["holdout"]
    assert abs(holdout["accuracy"] - 0.5) < 0.1
    # The metrics keep the base rate next to the accuracy so 0.5 on a 0.5 series reads as
    # "no edge", not as "50% correct".
    assert abs(holdout["base_rate"] - 0.5) < 0.15


def test_last_horizon_bars_are_dropped_from_training():
    vectors, closes = _planted(bars=200)
    readout = fit(vectors, closes, horizon=3)
    train = readout.metrics["train"]["bars"]
    holdout = readout.metrics["holdout"]["bars"]
    assert train + holdout == len(vectors) - 3


def test_split_is_temporal_holdout_is_strictly_later():
    vectors, closes = _planted(bars=400)
    readout = fit(vectors, closes, train_fraction=0.75)
    first_train, train_end = readout.metrics["split"]["train"]
    holdout_start, holdout_end = readout.metrics["split"]["holdout"]
    usable = len(vectors) - 1  # one bar is lost to the one-bar horizon
    assert first_train == 0
    assert train_end == int(usable * 0.75)
    # The two ranges are contiguous and the holdout begins after every training bar.
    assert holdout_start == train_end
    assert holdout_end == usable
    assert train_end - 1 < holdout_start  # every train index predates the holdout


def test_future_signal_never_leaks_into_the_holdout():
    # Signal exists only in the tail (the future). A random split would train on those
    # bars and score them well; a temporal split trains on the head, sees nothing, and the
    # holdout has to come out at chance.
    rng = np.random.default_rng(11)
    bars = 400
    vectors = rng.normal(size=(bars, 4))
    head = rng.normal(scale=1.0, size=int(bars * 0.7))
    tail_weights = np.array([2.0, -1.5, 1.0, 0.5])
    tail = vectors[int(bars * 0.7):] @ tail_weights * 0.5
    moves = np.concatenate([head, tail])
    closes = _series(vectors, moves)
    readout = fit(vectors, closes, train_fraction=0.7)
    assert readout.metrics["holdout"]["accuracy"] < 0.65


def test_save_load_round_trips_exactly(tmp_path):
    vectors, closes = _planted(bars=300)
    readout = fit(vectors, closes, trained_on="recording-42/arm-0")
    path = tmp_path / "readout.json"
    readout.save(path)

    loaded = Readout.load(path)
    assert np.array_equal(loaded.weights, readout.weights)
    assert np.array_equal(loaded.mean, readout.mean)
    assert np.array_equal(loaded.scale, readout.scale)
    assert loaded.bias == readout.bias
    assert loaded.metrics == readout.metrics
    assert loaded.trained_on == "recording-42/arm-0"
    # Scoring is the observable contract: same file, same decision.
    assert loaded.score(vectors[0]) == readout.score(vectors[0])


def test_load_rejects_a_wrong_schema(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"schema": "flyvsly.readout/v99", "weights": []}))
    with pytest.raises(ValueError):
        Readout.load(path)


def test_load_rejects_a_file_that_is_not_json(tmp_path):
    path = tmp_path / "garbage.json"
    path.write_text("{not json at all")
    with pytest.raises(ValueError):
        Readout.load(path)


def test_decide_respects_the_margin_including_the_boundary():
    readout = Readout(weights=[1.0], bias=0.0, mean=[0.0], scale=[1.0], metrics={})
    assert readout.decide([0.5]) == "BUY"
    assert readout.decide([0.15]) == "BUY"  # exactly at the margin counts as BUY
    assert readout.decide([0.1499]) == "HOLD"
    assert readout.decide([0.0]) == "HOLD"
    assert readout.decide([-0.1499]) == "HOLD"
    assert readout.decide([-0.15]) == "SELL"  # exactly at the margin counts as SELL
    assert readout.decide([-0.5]) == "SELL"


def test_decide_uses_the_requested_margin():
    readout = Readout(weights=[1.0], bias=0.0, mean=[0.0], scale=[1.0], metrics={})
    assert readout.decide([0.3], margin=0.5) == "HOLD"
    assert readout.decide([0.3], margin=0.1) == "BUY"


def test_describe_is_serialisable_and_ranks_features_by_absolute_weight():
    vectors, closes = _planted(bars=300)
    readout = fit(vectors, closes, trained_on="recording-9/arm-1")
    described = readout.describe()

    json.dumps(described)  # must not raise
    assert described["schema"] == SCHEMA
    assert described["features"] == 6
    assert described["trained_on"] == "recording-9/arm-1"
    assert described["train"]["bars"] > 0 and described["holdout"]["bars"] > 0

    top = described["top_features"]
    assert len(top) == 6  # fewer features than the cap: all of them, ranked
    magnitudes = [abs(item["weight"]) for item in top]
    assert magnitudes == sorted(magnitudes, reverse=True)
    assert all(isinstance(item["index"], int) for item in top)


def test_training_is_deterministic():
    vectors, closes = _planted(bars=300)
    first = fit(vectors, closes)
    second = fit(vectors, closes)
    assert np.array_equal(first.weights, second.weights)
    assert first.bias == second.bias
    assert first.metrics == second.metrics


def test_mismatched_lengths_are_rejected():
    vectors, closes = _planted(bars=100)
    with pytest.raises(ValueError):
        fit(vectors, closes[:-1])


def test_score_rejects_a_vector_of_the_wrong_width():
    readout = Readout(weights=[1.0, 2.0], bias=0.0, mean=[0.0, 0.0], scale=[1.0, 1.0], metrics={})
    with pytest.raises(ValueError):
        readout.score([1.0])
