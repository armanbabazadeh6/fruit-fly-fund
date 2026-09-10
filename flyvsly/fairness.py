"""Runtime fairness checks for the two-arm comparison.

The claim this project wants to be able to make is narrow: *the only configured
difference between the two flies is whether memory updates are applied*. That is easy to
assert in prose and easy to break in code, so it is asserted here as a hard runtime check.
"""

import dataclasses
import hashlib
import json
from decimal import Decimal

from stonkfly.config import Settings

from .config import EXPERIMENTAL_VARIABLE, EXTENSION_FIELDS, ArenaRules


def arm_settings(rules: ArenaRules, learning: bool) -> Settings:
    """Upstream settings for one arm. All validation still comes from upstream."""
    return Settings(**rules.as_settings_kwargs(learning))


def _comparable(settings: Settings) -> dict:
    payload = dataclasses.asdict(settings)
    payload.pop(EXPERIMENTAL_VARIABLE, None)
    return payload


def protocol_digest(settings: Settings) -> str:
    """Hash of everything except the experimental variable."""
    return hashlib.sha256(
        json.dumps(_comparable(settings), sort_keys=True, default=str).encode()
    ).hexdigest()


def assert_only_learning_differs(on: Settings, off: Settings) -> dict:
    """Raise unless the two accounts differ in exactly one configured field."""
    if on.learning is not True or off.learning is not False:
        raise AssertionError(
            "Arms must be constructed as (learning=True, learning=False); got "
            f"({on.learning!r}, {off.learning!r})"
        )
    left, right = _comparable(on), _comparable(off)
    differing = sorted(
        k for k in set(left) | set(right) if left.get(k) != right.get(k)
    )
    if differing:
        raise AssertionError(
            "Unfair comparison: arms differ in fields other than "
            f"{EXPERIMENTAL_VARIABLE!r}: {differing}"
        )
    return {
        "differing_fields": [EXPERIMENTAL_VARIABLE],
        "identical_fields_sha256": protocol_digest(on),
        "identical_fields": sorted(left),
        "on_learning": on.learning,
        "off_learning": off.learning,
    }


def assert_decimal_equal(label: str, a: Decimal, b: Decimal, tolerance: Decimal = Decimal(0)):
    """Same starting condition check for money values, used by tests and the arena."""
    if abs(a - b) > tolerance:
        raise AssertionError(f"{label} differs between arms: {a} vs {b}")


def assert_exam_is_fair(kind: str, starting: dict) -> dict:
    """An exam differs in exactly one thing too, and it is not `learning`.

    Both flies run frozen, under identical rules, on the same market. The single difference is
    which brain each one carried in: one from an earlier season, one never trained (or, for a
    reset, the same trained brain with its learned efficacies wiped).
    """
    if kind not in ("exam", "reset"):
        raise AssertionError(f"assert_exam_is_fair called with kind {kind!r}")
    labels = {arm: str(spec).split(":")[0] for arm, spec in starting.items()}
    if all(label == "baseline" for label in labels.values()):
        raise AssertionError("An exam needs at least one trained starting point")
    if labels["gordon"] == labels["warren"]:
        raise AssertionError(
            f"Both flies start from {labels['gordon']!r}: nothing distinguishes them"
        )
    if kind == "reset" and "reset" not in labels.values():
        raise AssertionError("A reset run must start one fly from a reset checkpoint")
    return {
        "differing_fields": ["starting_weights"],
        "starting_weights": {
            arm: {"kind": str(spec).split(":")[0], "spec": str(spec)}
            for arm, spec in starting.items()
        },
        "both_frozen": True,
    }


def starting_conditions(rules: ArenaRules, kind: str = "competition", starting=None) -> dict:
    """Human- and machine-checkable statement of the shared starting line."""
    on = arm_settings(rules, True)
    off = arm_settings(rules, False)
    if kind == "competition":
        check = assert_only_learning_differs(on, off)
    else:
        check = assert_exam_is_fair(kind, starting or {})
    extensions = {field: getattr(rules, field) for field in EXTENSION_FIELDS}
    return {
        "kind": kind,
        "extensions_identical_for_both_flies": extensions,
        "capital_usdc": str(rules.capital),
        "order_limit_usdc": str(rules.order_limit),
        "paper_fee_per_side": str(rules.paper_fee),
        "slippage_limit": str(rules.slippage),
        "spread_limit": str(rules.spread_limit),
        "daily_order_limit": rules.daily_orders,
        "order_cooldown_seconds": rules.interval_seconds,
        "decoder_threshold_hz": rules.decoder_threshold_hz,
        "neural_ms_per_observation": rules.neural_ms,
        "fairness": check,
    }
