"""The two-arm claim, tested against the real MaleCNS engine.

Opt in with FLYVSLY_NEURAL_TEST=1: this builds two full 166,700-neuron brains (~0.6 GB
resident) and advances them, which takes about a minute on a laptop. It is the only test
that can show that the control arm is genuinely frozen while the experimental arm's
synapses move, so it is worth the cost when the engine or the arena changes.
"""

import os

import pytest

from flyvsly.config import ArenaConfig, ArenaRules, MarketSpec
from flyvsly.market import build_season

pytestmark = pytest.mark.slow

ENABLED = os.environ.get("FLYVSLY_NEURAL_TEST") == "1"
DATA_ROOT = os.environ.get("FLYVSLY_DATA", "data")

requires_engine = pytest.mark.skipif(
    not ENABLED, reason="set FLYVSLY_NEURAL_TEST=1 to run the full connectome test"
)
requires_graph = pytest.mark.skipif(
    not (os.path.exists(os.path.join(DATA_ROOT, "graph.npz"))),
    reason="run `flyvsly prepare` first",
)


@pytest.fixture(scope="module")
def recording(tmp_path_factory):
    if not ENABLED:
        pytest.skip("set FLYVSLY_NEURAL_TEST=1 to run the full connectome test")
    from flyvsly.arena import Arena

    out = tmp_path_factory.mktemp("neural")
    config = ArenaConfig(
        rules=ArenaRules(),
        market=MarketSpec(kind="synthetic", bars=4, seed=1),
        engine="neural",
        out=out,
        label="test-neural",
    )
    season = build_season(config.market, cache_dir=tmp_path_factory.mktemp("market"))
    return Arena(config, data_root=DATA_ROOT).run(run_id="neural-test", season=season)


@requires_engine
@requires_graph
def test_both_flies_run_the_retained_graph(recording):
    on, off = recording["arms"]
    assert on["backend"]["neurons"] == 166700
    assert on["backend"]["directed_edges"] == 25582938
    assert on["backend"]["plastic_edges"] == 7835
    assert off["backend"]["plastic_edges"] == on["backend"]["plastic_edges"]
    assert on["backend"]["memory_updates_applied"] is True
    assert off["backend"]["memory_updates_applied"] is False


@requires_engine
@requires_graph
def test_every_bar_gave_both_flies_the_same_retinal_input(recording):
    assert recording["run"]["inputs_identical_every_bar"] is True
    for observation in recording["observations"]:
        assert observation["same_frame_both_arms"] is True
        hashes = {
            arm["signal"]["input_sha256"]
            for arm in observation["arms"].values()
            if arm["signal"]
        }
        assert len(hashes) == 1


@requires_engine
@requires_graph
def test_the_control_arm_never_moves_a_synapse(recording):
    for observation in recording["observations"]:
        control = observation["arms"]["warren"]["signal"]["memory"]
        assert control["changed_edges"] == 0
        assert control["enabled"] is False
    changed = [
        observation["arms"]["gordon"]["signal"]["memory"]["changed_edges"]
        for observation in recording["observations"]
    ]
    assert max(changed) > 0
    assert any(
        observation["arms"]["gordon"]["signal"]["memory"]["mean_efficacy"] != 1.0
        for observation in recording["observations"]
    )


@requires_engine
@requires_graph
def test_the_flies_can_diverge_once_weights_differ(recording):
    """Not a claim that divergence helps: only that the arms are not the same run twice."""
    decisions = [
        (
            observation["arms"]["gordon"]["decision"]["side"],
            observation["arms"]["warren"]["decision"]["side"],
        )
        for observation in recording["observations"]
    ]
    assert all(side != "BLOCKED" for pair in decisions for side in pair)


@requires_engine
@requires_graph
def test_the_losing_arm_ignores_prices_it_is_not_shown(recording):
    """The neural arm's output must not depend on the price list the arena passes it.

    A fresh engine is reset between the two calls, so the only thing that differs is the
    visible history argument — which the neural backend never reads.
    """
    from stonkfly.display import market_frame

    from flyvsly.backends.neural import NeuralBackend
    from flyvsly.fairness import arm_settings

    engine = NeuralBackend(arm_settings(ArenaRules(), False), data_root=DATA_ROOT)
    season = build_season(MarketSpec(kind="synthetic", bars=2, seed=1), "data/markets")
    quote = season.quote(0)
    frame = market_frame("BTC-USDC", season.history(0), quote.bid, quote.ask)

    engine.reset()
    first = engine.observe(frame, "none", [1.0, 2.0, 3.0])
    engine.reset()
    second = engine.observe(frame, "none", [90_000.0, 91_000.0, 92_000.0])

    assert first["spike_sha256"] == second["spike_sha256"]
    assert first["side"] == second["side"]
    assert first["difference_hz"] == second["difference_hz"]
