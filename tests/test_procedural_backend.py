"""The procedural backend must be impossible to mistake for the fly."""

import json

from flyvsly.arena import Arena
from flyvsly.config import ArenaConfig, ArenaRules, MarketSpec
from flyvsly.fairness import arm_settings
from flyvsly.market import build_season
from flyvsly.backends.procedural import ProceduralBackend


def make_backend(learning: bool, seed: int = 0):
    return ProceduralBackend(arm_settings(ArenaRules(), learning), seed=seed)


def test_procedural_signal_has_no_neural_fields():
    backend = make_backend(True)
    history = [100 + i * 0.5 for i in range(40)]
    signal = backend.observe(None, "none", history)
    assert signal["signal_source"] == "procedural"
    assert signal["simulated"] is True
    for field in ("total_spikes", "KC_spikes", "spike_sha256", "gate_spikes", "left_hz"):
        assert field not in signal
    assert signal["memory"]["simulated"] is True
    assert signal["memory"]["changed_edges"] is None


def test_procedural_backend_is_deterministic():
    history = [100 + (i % 7) * 0.4 for i in range(40)]
    a, b = make_backend(True), make_backend(True)
    assert [a.observe(None, "none", history)["side"] for _ in range(6)] == [
        b.observe(None, "none", history)["side"] for _ in range(6)
    ]


def test_only_the_learning_arm_accumulates_simulated_bias():
    rising = [100 * (1 + 0.004) ** i for i in range(40)]
    on, off = make_backend(True), make_backend(False)
    for _ in range(8):
        on.observe(None, "reward", rising)
        off.observe(None, "reward", rising)
    assert off.bias == 0.0
    assert on.bias != 0.0


def test_procedural_run_records_its_own_disclaimer(tmp_path):
    config = ArenaConfig(
        rules=ArenaRules(),
        market=MarketSpec(kind="synthetic", bars=64, seed=4),
        engine="procedural",
        out=tmp_path,
        label="test",
    )
    arena = Arena(config)
    recording = arena.run(run_id="proc-test", season=build_season(config.market, cache_dir=tmp_path))
    assert recording["run"]["engine"] == "procedural"
    assert recording["schema"] == "flyvsly.recording/v1"
    for arm in recording["arms"]:
        assert arm["backend"]["simulated"] is True
        assert "not neural" in arm["backend"]["label"].lower()
    signal = recording["observations"][-1]["arms"]["gordon"]["signal"]
    assert signal["simulated"] is True
    assert "total_spikes" not in signal
    assert recording["run"]["starting_conditions"]["fairness"]["differing_fields"] == ["learning"]
    assert recording["summary"]["arms"]["warren"]["final_memory"]["enabled"] is False
    json.dumps(recording)  # the recording must be serialisable as-is


def test_recorded_arm_settings_differ_only_in_learning(tmp_path):
    config = ArenaConfig(
        rules=ArenaRules(),
        market=MarketSpec(kind="synthetic", bars=32, seed=2),
        engine="procedural",
        out=tmp_path,
    )
    recording = Arena(config).run(
        run_id="proc-fairness", season=build_season(config.market, cache_dir=tmp_path)
    )
    on, off = recording["arms"]
    left = {k: v for k, v in on["settings"].items() if k != "learning"}
    right = {k: v for k, v in off["settings"].items() if k != "learning"}
    assert left == right
    assert on["settings"]["learning"] is True
    assert off["settings"]["learning"] is False
    assert on["settings_signature"] != off["settings_signature"]
