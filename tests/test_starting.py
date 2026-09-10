"""The exam and reset controls are only meaningful if the weights are exactly reproduced.

A real ``MemoryBrain`` is a 166,700-neuron graph and cannot be built in a unit test, but
``starting`` only ever talks to a tiny surface of it: ``controller.restore``, and
``weight`` / ``baseline_plastic`` / ``circuit`` on the brain. So these tests drive that
surface with a fake whose ``__getattr__`` raises if the real module reaches for anything
else; a silent AttributeError would let a wrong implementation pass.
"""

import hashlib
from pathlib import Path

import numpy as np
import pytest

from flyvsly.starting import KINDS, StartingWeights, apply, parse, save_brains

N = 10
EDGES = np.array([1, 3, 4, 8], dtype=np.int64)


class FakeBrain:
    """The only brain members ``starting`` may touch; anything else is a bug."""

    def __init__(self, weight, edges=EDGES):
        self.weight = np.asarray(weight, dtype=np.float32).copy()
        self.circuit = {"edges": np.asarray(edges, dtype=np.int64)}
        self.baseline_plastic = self.weight[self.circuit["edges"]].copy()

    def __getattr__(self, name):
        raise AssertionError(f"FakeBrain does not model {name!r}")


class FakeController:
    """Upstream's ``restore`` writes the checkpoint's weight array back in place."""

    def __init__(self, brain):
        self.brain = brain

    def restore(self, path):
        with np.load(path, allow_pickle=False) as archive:
            saved = archive["weight"]
        if saved.shape != self.brain.weight.shape:
            raise ValueError("Checkpoint array mismatch")
        self.brain.weight[:] = saved

    def __getattr__(self, name):
        raise AssertionError(f"FakeController does not model {name!r}")


class FakeBackend:
    def __init__(self, weight, edges=EDGES):
        self.controller = FakeController(FakeBrain(weight, edges))

    def save(self, path):
        np.savez_compressed(path, weight=self.controller.brain.weight)

    def __getattr__(self, name):
        raise AssertionError(f"FakeBackend does not model {name!r}")


def base_weight():
    return np.linspace(0.1, 1.0, N, dtype=np.float32)


def trained_backend():
    """A brain that has learned: plastic efficacies moved, and so did some others."""
    backend = FakeBackend(base_weight())
    backend.controller.brain.weight[EDGES] += np.float32(0.5)
    backend.controller.brain.weight[[0, 9]] += np.float32(3.0)
    return backend


def test_fake_brain_refuses_unmodelled_attributes():
    brain = FakeBrain(base_weight())
    with pytest.raises(AssertionError, match="memory_w"):
        brain.memory_w


def test_parse_baseline():
    weights = parse("baseline")
    assert weights == StartingWeights("baseline")
    assert weights.kind == "baseline"
    assert weights.path is None
    assert weights.label() == "baseline"
    assert weights.describe() == {
        "kind": "baseline",
        "label": "baseline",
        "file": None,
        "sha256": None,
    }


def test_parse_trained_and_reset_on_a_real_file(tmp_path):
    path = tmp_path / "season-01.npz"
    path.write_bytes(b"checkpoint bytes")
    trained = parse(f"trained:{path}")
    assert trained.kind == "trained"
    assert trained.path == path
    assert trained.label() == "trained:season-01.npz"
    reset = parse(f"reset:{path}")
    assert reset.kind == "reset"
    assert reset.label() == "reset:season-01.npz"


def test_parse_accepts_a_relative_path(tmp_path, monkeypatch):
    (tmp_path / "warren.npz").write_bytes(b"x")
    monkeypatch.chdir(tmp_path)
    weights = parse("trained:warren.npz")
    assert weights.path == Path("warren.npz")
    assert weights.label() == "trained:warren.npz"


def test_describe_digests_the_real_file(tmp_path):
    path = tmp_path / "season-01.npz"
    payload = b"not a checkpoint, just bytes with a known digest"
    path.write_bytes(payload)
    assert parse(f"trained:{path}").describe() == {
        "kind": "trained",
        "label": "trained:season-01.npz",
        "file": str(path),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def test_parse_error_names_the_valid_kinds():
    with pytest.raises(ValueError) as caught:
        parse("frozen:whatever.npz")
    for kind in KINDS:
        assert kind in str(caught.value)


@pytest.mark.parametrize(
    "spec, fragment",
    [
        ("", "Unknown starting kind"),
        ("frozen:/tmp/x.npz", "Unknown starting kind"),
        ("trained", "checkpoint path"),
        ("trained:", "checkpoint path"),
        ("trained:/definitely/not/here.npz", "does not exist"),
        ("baseline:/tmp/x.npz", "no checkpoint path"),
    ],
)
def test_parse_rejects_bad_specs(spec, fragment):
    with pytest.raises(ValueError, match=fragment):
        parse(spec)


def test_direct_construction_is_validated():
    with pytest.raises(ValueError, match="Unknown starting kind"):
        StartingWeights("frozen")
    with pytest.raises(ValueError, match="checkpoint path"):
        StartingWeights("trained")
    with pytest.raises(ValueError, match="no checkpoint path"):
        StartingWeights("baseline", Path("x.npz"))


def test_save_brains_writes_files_and_reports_digests(tmp_path):
    gordon = trained_backend()
    warren = FakeBackend(base_weight())
    report = save_brains(tmp_path, {"gordon": gordon, "warren": warren})
    assert set(report) == {"gordon", "warren"}
    for arm in ("gordon", "warren"):
        path = tmp_path / f"{arm}.npz"
        assert path.exists()
        assert report[arm]["file"] == f"{arm}.npz"
        assert report[arm]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
        assert report[arm]["bytes"] == path.stat().st_size


def test_save_brains_creates_the_directory(tmp_path):
    directory = tmp_path / "runs" / "season-01"
    save_brains(directory, {"gordon": trained_backend()})
    assert (directory / "gordon.npz").exists()


def test_save_brains_refuses_to_overwrite(tmp_path):
    first = save_brains(tmp_path, {"gordon": trained_backend()})
    with pytest.raises(FileExistsError, match="gordon.npz"):
        save_brains(tmp_path, {"gordon": FakeBackend(base_weight())})
    digest = hashlib.sha256((tmp_path / "gordon.npz").read_bytes()).hexdigest()
    assert digest == first["gordon"]["sha256"]


def test_one_existing_checkpoint_blocks_the_whole_batch(tmp_path):
    (tmp_path / "gordon.npz").write_bytes(b"an earlier training run")
    with pytest.raises(FileExistsError):
        save_brains(
            tmp_path, {"gordon": trained_backend(), "warren": FakeBackend(base_weight())}
        )
    assert not (tmp_path / "warren.npz").exists()


def test_round_trip_trained_reproduces_the_saved_efficacies(tmp_path):
    source = trained_backend()
    save_brains(tmp_path, {"gordon": source})
    target = FakeBackend(base_weight())
    result = apply(parse(f"trained:{tmp_path / 'gordon.npz'}"), target)
    assert result["applied"] is True
    assert result["kind"] == "trained"
    assert result["label"] == "trained:gordon.npz"
    assert result["plastic_edges"] == len(EDGES)
    assert np.array_equal(
        target.controller.brain.weight, source.controller.brain.weight
    )
    assert np.array_equal(
        target.controller.brain.weight[EDGES], source.controller.brain.weight[EDGES]
    )


def test_reset_returns_plastic_efficacies_to_the_never_trained_brain(tmp_path):
    source = trained_backend()
    save_brains(tmp_path, {"gordon": source})

    trained = apply(
        parse(f"trained:{tmp_path / 'gordon.npz'}"), FakeBackend(base_weight())
    )
    assert trained["changed_before_reset"] == len(EDGES)

    target = FakeBackend(base_weight())
    result = apply(parse(f"reset:{tmp_path / 'gordon.npz'}"), target)
    assert result["applied"] is True
    assert result["kind"] == "reset"
    assert result["changed_before_reset"] == len(EDGES)
    assert result["plastic_edges"] == len(EDGES)

    never = FakeBrain(base_weight())
    assert np.array_equal(
        target.controller.brain.weight[EDGES], never.weight[EDGES]
    )
    assert np.array_equal(
        target.controller.brain.weight[EDGES],
        target.controller.brain.baseline_plastic,
    )


def test_reset_leaves_non_plastic_weights_from_the_checkpoint(tmp_path):
    source = trained_backend()
    save_brains(tmp_path, {"gordon": source})
    target = FakeBackend(base_weight())
    apply(parse(f"reset:{tmp_path / 'gordon.npz'}"), target)
    rest = np.array([i for i in range(N) if i not in set(EDGES.tolist())])
    restored = target.controller.brain.weight[rest]
    assert np.array_equal(restored, source.controller.brain.weight[rest])
    # and they really did move relative to this brain's own baseline
    assert not np.array_equal(restored, base_weight()[rest])


def test_apply_baseline_changes_nothing():
    backend = FakeBackend(base_weight())
    before = backend.controller.brain.weight.copy()
    assert apply(parse("baseline"), backend) == {
        "applied": False,
        "reason": "baseline weights",
    }
    assert np.array_equal(backend.controller.brain.weight, before)
