"""The arena's three run kinds, driven end to end through a stub backend.

Every existing test of the exam and reset protocols checks either the setup (fairness
guards, personas, starting-weight parsing) or `flyvsly.starting` on its own. None runs
`Arena.run` through those modes, because a real run needs the 166,700-neuron graph and
about six seconds per bar. This module closes that gap with a backend double that
satisfies the arena's backend surface in microseconds, so the parts that are actually
worth exercising — the ledger, the execution guard, the paper broker, and the recording —
are the real ones.

Nothing here touches the connectome, starts a neural run, or asserts that an internal
function was called. Every claim is read back out of the `recording.json` a reader would
open.
"""

import json

import numpy as np
import pytest

from flyvsly import arena as arena_module
from flyvsly import starting as starting_module
from flyvsly.arena import Arena
from flyvsly.config import ArenaConfig, ArenaRules, MarketSpec
from flyvsly.market import build_season

STUB_LABEL = "Stub backend (test double, not a fly)"

# `exam` runs start the experimental arm from a `trained:` checkpoint; `reset` runs from a
# `reset:` one. The run kind and the starting spec kind are not the same vocabulary.
STARTING_SPEC_KIND = {"exam": "trained", "reset": "reset"}


class StubBackend:
    """A test double for the fly, not a fly.

    It satisfies only the surface the arena reads from a backend: `observe`, `describe`,
    `save`, plus the absence of a `population_description` that the recording tolerates.
    It measures no neurons, holds no efficacies and learns nothing; its `describe()` says
    so, so a recording produced by it can never be mistaken for a neural run.
    """

    def __init__(self, settings, seed=0):
        self.settings = settings
        self.learning = bool(settings.learning)
        self.seed = int(seed)

    def describe(self) -> dict:
        return {
            "signal_source": "stub",
            "test_double": True,
            "simulated": True,
            "label": STUB_LABEL,
            "memory_updates_applied": self.learning,
            "rule": "Fixed sign-of-the-last-return rule. No neurons are involved.",
            "claims": (
                "This backend measures nothing. It exists only to drive the arena's "
                "competition, exam and reset plumbing in tests."
            ),
            "parameters": {"seed": self.seed},
        }

    def observe(self, frame, reinforcement: str, visible_history=None) -> dict:
        history = list(visible_history or [])
        last = history[-1] if history else 0.0
        previous = history[-2] if len(history) >= 2 else last
        momentum = (last / previous - 1.0) if previous else 0.0
        if momentum > 0:
            side = "BUY"
        elif momentum < 0:
            side = "SELL"
        else:
            side = "HOLD"
        score = max(-1.0, min(1.0, momentum * 1000.0))
        # The arena's explanatory branch only distinguishes "neural" from everything else,
        # so the non-neural fields it expects are supplied even though the numbers are
        # noise. `signal_source` still says "stub" so the recording is not lying.
        return {
            "signal_source": "stub",
            "simulated": True,
            "label": STUB_LABEL,
            "side": side,
            "score": score,
            "threshold": 0.0,
            "momentum": momentum,
            "volatility": 0.0,
            "lookback": 1,
            "simulated_memory_delta": None,
            "stimulus": reinforcement,
            "memory": {
                "enabled": self.learning,
                "simulated": True,
                "bias": 0.0,
                "changed_edges": 0 if self.learning else None,
                "note": "Test double. No synapse exists in this backend.",
            },
        }

    def save(self, path):
        with open(path, "wb") as handle:
            handle.write(b"stub backend: not a real checkpoint\n")


def _build_stub(engine, settings, data_root, seed, rules=None):
    return StubBackend(settings, seed=seed)


@pytest.fixture
def stub_arena(monkeypatch):
    """Every arena in this module builds the test double instead of a real backend."""
    monkeypatch.setattr(arena_module, "_build_backend", _build_stub)


def _install_apply_stub(monkeypatch):
    """Replace the real restore, which needs a connectome, with a reporting double.

    `parse` still checks the spec against a real file on disk, so the tests write a small
    ``.npz``; what the run then does with it is reported through `starting_report`, which
    the recording carries.
    """

    def apply(weights, backend):
        if weights.kind == "baseline":
            return {"applied": False, "reason": "baseline weights"}
        return {
            "applied": True,
            "kind": weights.kind,
            "label": weights.label(),
            "file": str(weights.path),
            "test_double": True,
            "note": "restore needs the connectome; the arena plumbing is the subject here",
        }

    monkeypatch.setattr(starting_module, "apply", apply)


def _checkpoint(path):
    np.savez_compressed(path, weight=np.zeros(4, dtype=np.float32))
    return path


def _run_recording(config, tmp_path, run_id):
    """Run the arena and read back the artifact, not the in-memory return value."""
    season = build_season(config.market, cache_dir=tmp_path / "markets")
    Arena(config).run(run_id=run_id, season=season)
    return json.loads((config.out / run_id / "recording.json").read_text())


def _config(tmp_path, **overrides):
    base = dict(
        rules=ArenaRules(),
        market=MarketSpec(kind="synthetic", bars=8, seed=11),
        engine="procedural",
        out=tmp_path / "runs",
    )
    base.update(overrides)
    return ArenaConfig(**base)


def test_competition_records_only_learning_as_the_difference(stub_arena, tmp_path):
    recording = _run_recording(_config(tmp_path), tmp_path, "competition")

    run = recording["run"]
    assert run["kind"] == "competition"
    assert run["starting_conditions"]["fairness"]["differing_fields"] == ["learning"]

    experimental, control = recording["arms"]
    assert experimental["role"] == "experimental"
    assert control["role"] == "control"
    assert experimental["settings"]["learning"] is True
    assert control["settings"]["learning"] is False
    on = {k: v for k, v in experimental["settings"].items() if k != "learning"}
    off = {k: v for k, v in control["settings"].items() if k != "learning"}
    assert on == off


def test_recording_reports_the_run_kind_and_no_population_for_a_populationless_backend(
    stub_arena, tmp_path
):
    recording = _run_recording(_config(tmp_path), tmp_path, "populationless")

    run = recording["run"]
    assert run["kind"] == "competition"
    # The stub has no `population_description`; the recording must say None, not crash.
    assert run["population"] is None
    assert run["readout"] is None


@pytest.mark.parametrize("kind", sorted(STARTING_SPEC_KIND))
def test_exam_modes_freeze_both_arms_in_the_recording(stub_arena, monkeypatch, tmp_path, kind):
    _install_apply_stub(monkeypatch)
    spec_kind = STARTING_SPEC_KIND[kind]
    checkpoint = _checkpoint(tmp_path / f"{spec_kind}-brain.npz")
    config = _config(
        tmp_path,
        # The parallel path is the one that applies starting weights, so the stub backend
        # is reached through the "neural" engine branch; no connectome is ever built.
        engine="neural",
        kind=kind,
        starting={"gordon": f"{spec_kind}:{checkpoint}", "warren": "baseline"},
    )

    recording = _run_recording(config, tmp_path, kind)

    run = recording["run"]
    assert run["kind"] == kind
    fairness = run["starting_conditions"]["fairness"]
    assert fairness["differing_fields"] == ["starting_weights"]
    assert fairness["both_frozen"] is True

    # The bug that shipped once: the labels said frozen while the experimental arm's
    # settings still had learning on. Read the flags out of the recording.
    for arm in recording["arms"]:
        assert arm["settings"]["learning"] is False

    experimental, control = recording["arms"]
    assert experimental["starting_weights"]["kind"] == spec_kind
    assert experimental["starting_weights"]["label"] == f"{spec_kind}:{checkpoint.name}"
    assert experimental["starting_report"]["applied"] is True
    assert control["starting_weights"]["kind"] == "baseline"
    assert control["starting_report"] == {"applied": False, "reason": "baseline weights"}


def test_reset_refuses_to_start_without_the_checkpoint(stub_arena, monkeypatch, tmp_path):
    _install_apply_stub(monkeypatch)
    missing = tmp_path / "never-trained.npz"
    config = _config(
        tmp_path,
        engine="neural",
        kind="reset",
        starting={"gordon": f"reset:{missing}", "warren": "baseline"},
    )

    with pytest.raises(ValueError, match="does not exist"):
        _run_recording(config, tmp_path, "reset-missing")

    assert not (config.out / "reset-missing" / "recording.json").exists()


def test_validate_rejects_a_competition_starting_from_trained_weights(tmp_path):
    config = _config(
        tmp_path,
        kind="competition",
        starting={"gordon": "trained:ghost.npz", "warren": "baseline"},
    )
    with pytest.raises(ValueError, match="competition runs start both flies from baseline"):
        config.validate()


def test_validate_rejects_an_exam_with_two_baseline_brains(tmp_path):
    config = _config(
        tmp_path,
        kind="exam",
        starting={"gordon": "baseline", "warren": "baseline"},
    )
    with pytest.raises(ValueError, match="at least one fly starting from trained"):
        config.validate()
