"""The activity levers and the reinforcement controls.

Two different claims are checked here. First, that our gate-optional decoder is upstream's
decoder when the gate is required — otherwise every measurement in this project would be
against a silently different readout. Second, that the control modes really do decouple the
pulse from the fly's own outcome while keeping its statistics.
"""

import numpy as np
import pandas as pd
import pytest

from stonkfly.neural.controller import Decoder

from flyvsly.config import PRESETS, ArenaRules
from flyvsly.decoder import ConfigurableDecoder
from flyvsly.fairness import arm_settings, starting_conditions
from flyvsly.shuffle import counts, load_schedule, permute


def annotation():
    return pd.DataFrame(
        {"type": ["DNp20", "DNp20", "DNpe017"], "somaSide": ["L", "R", "L"]}
    )


def make(require_gate: bool, threshold: float = 2.0):
    ids = np.array([1, 2, 3])
    return ConfigurableDecoder(ids, annotation(), threshold, require_gate=require_gate), Decoder(
        ids, annotation(), threshold
    )


@pytest.mark.parametrize(
    "counts_row",
    [
        (0, 0, 0),
        (0, 10, 0),
        (0, 10, 1),
        (10, 0, 1),
        (4, 4, 1),
        (2, 12, 3),
        (12, 2, 0),
    ],
)
def test_with_the_gate_required_we_match_upstream_exactly(counts_row):
    ours, theirs = make(True)
    spikes = np.array(counts_row)
    mine = ours.decode(spikes, 0.5)
    # `gate_required` is the one field we add; every upstream field must match exactly.
    assert {k: v for k, v in mine.items() if k != "gate_required"} == theirs.decode(spikes, 0.5)


def test_without_the_gate_the_difference_decides():
    ours, _ = make(False)
    # A strong directional difference with no gate spike: upstream holds, we trade.
    assert ours.decode(np.array([0, 12, 0]), 0.5)["side"] == "BUY"
    assert ours.decode(np.array([12, 0, 0]), 0.5)["side"] == "SELL"


def test_the_threshold_still_applies_without_the_gate():
    ours, _ = make(False, threshold=2.0)
    # counts over one second map straight to Hz, so this is a +1 Hz difference.
    assert ours.decode(np.array([4, 5, 0]), 1.0)["side"] == "HOLD"
    assert ours.decode(np.array([4, 7, 0]), 1.0)["side"] == "BUY"


def test_the_gate_flag_is_reported_with_every_decision():
    ours, _ = make(False)
    assert ours.decode(np.array([0, 12, 0]), 0.5)["gate_required"] is False


def test_active_preset_is_busier_but_identical_for_both_flies():
    rules = ArenaRules(
        order_limit=PRESETS["active"]["order_limit"],
        daily_orders=PRESETS["active"]["daily_orders"],
        require_gate=PRESETS["active"]["require_gate"],
    ).validate()
    assert rules.require_gate is False
    assert arm_settings(rules, True).daily_orders == 100
    # The per-order cap is upstream's and cannot be raised without patching it.
    assert arm_settings(rules, True).order_limit == "10"
    extensions = starting_conditions(rules)["extensions_identical_for_both_flies"]
    assert extensions == {"require_gate": False, "reinforcement": "pnl"}


def test_unknown_reinforcement_mode_is_rejected():
    with pytest.raises(ValueError, match="Unknown reinforcement mode"):
        ArenaRules(reinforcement="wishful").validate()


def test_permutation_keeps_the_pulse_statistics_but_moves_them():
    schedule = ["reward"] * 6 + ["aversive"] * 4 + ["none"] * 10
    shuffled = permute(schedule, 7)
    assert counts(shuffled) == counts(schedule)
    assert shuffled != schedule
    assert permute(schedule, 7) == shuffled
    assert permute(schedule, 8) != shuffled


def test_a_reference_schedule_can_be_read_from_a_published_recording():
    schedule = load_schedule("web/public/recordings", "20260910-143345-r0")
    assert len(schedule) == 48
    assert set(schedule) <= {"reward", "aversive", "none"}
    assert counts(schedule)["reward"] + counts(schedule)["aversive"] > 0


def test_shuffled_mode_refuses_to_run_without_a_reference(tmp_path):
    from flyvsly.arena import Arena
    from flyvsly.config import ArenaConfig, MarketSpec
    from flyvsly.market import build_season

    config = ArenaConfig(
        rules=ArenaRules(reinforcement="shuffled"),
        market=MarketSpec(kind="synthetic", bars=4, seed=1),
        engine="procedural",
        out=tmp_path,
    )
    arena = Arena(config)
    season = build_season(config.market, cache_dir=tmp_path)
    with pytest.raises(ValueError, match="reference recording"):
        arena.run(run_id="shuffle-missing", season=season)
