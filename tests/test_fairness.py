"""The fairness claim is the whole experiment, so it gets asserted, not described."""

import pytest

from flyvsly.config import ArenaRules
from flyvsly.fairness import (
    arm_settings,
    assert_only_learning_differs,
    protocol_digest,
    starting_conditions,
)


def test_arms_differ_in_learning_only():
    rules = ArenaRules()
    check = assert_only_learning_differs(arm_settings(rules, True), arm_settings(rules, False))
    assert check["differing_fields"] == ["learning"]
    assert check["on_learning"] is True
    assert check["off_learning"] is False


def test_protocol_digest_ignores_the_experimental_variable():
    rules = ArenaRules()
    on, off = arm_settings(rules, True), arm_settings(rules, False)
    assert protocol_digest(on) == protocol_digest(off)
    assert on.signature() != off.signature()


def test_a_second_difference_is_rejected():
    """Changing anything else must fail loudly rather than quietly skew the comparison."""
    on = arm_settings(ArenaRules(decoder_threshold_hz=2), True)
    off = arm_settings(ArenaRules(decoder_threshold_hz=3), False)
    with pytest.raises(AssertionError, match="decoder_threshold_hz"):
        assert_only_learning_differs(on, off)


def test_both_arms_start_from_the_same_account():
    conditions = starting_conditions(ArenaRules())
    assert conditions["capital_usdc"] == "100"
    assert conditions["order_limit_usdc"] == "10"
    assert conditions["paper_fee_per_side"] == "0.006"
    assert conditions["fairness"]["differing_fields"] == ["learning"]
    assert "learning" not in conditions["fairness"]["identical_fields"]


def test_upstream_caps_are_not_widened():
    """Capital and per-order caps stay inside what upstream validates for its own paths."""
    from stonkfly.config import Settings

    settings = arm_settings(ArenaRules(), True)
    assert isinstance(settings, Settings)
    assert settings.capital == "100"
    assert settings.order_limit == "10"
    with pytest.raises(ValueError):
        Settings(capital="100.01")
    with pytest.raises(ValueError):
        Settings(order_limit="10.01")
