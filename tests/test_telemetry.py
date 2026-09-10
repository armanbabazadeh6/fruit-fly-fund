"""The explanation layer is the product claim: say which signals and rules made the trade."""

from flyvsly.telemetry import (
    PROCEDURAL_RULE,
    execution_explanation,
    neural_explanation,
    procedural_explanation,
    summarise_curve,
)


def neural_signal(**overrides):
    base = {
        "signal_source": "neural",
        "side": "HOLD",
        "seconds": 0.5,
        "left_hz": 36.0,
        "right_hz": 50.0,
        "difference_hz": 14.0,
        "gate_spikes": 0,
    }
    return {**base, **overrides}


def test_gate_failure_is_named_as_the_reason_for_holding():
    explanation = neural_explanation(neural_signal(), 2.0)
    assert explanation["result"] == "HOLD"
    assert any("Gate condition failed" in step for step in explanation["steps"])
    assert explanation["measured"]["dnp20_right_hz"] == 50.0
    assert explanation["measured"]["gate_spikes"] == 0
    assert explanation["engineered_interface"] is True


def test_threshold_and_gate_together_produce_the_side():
    explanation = neural_explanation(
        neural_signal(side="BUY", difference_hz=6.0, gate_spikes=21), 2.0
    )
    assert any("beyond the ±2.00 Hz threshold" in step for step in explanation["steps"])
    assert explanation["result"] == "BUY"


def test_small_difference_is_reported_as_below_threshold():
    explanation = neural_explanation(
        neural_signal(side="HOLD", difference_hz=0.5, gate_spikes=4), 2.0
    )
    assert any("threshold: HOLD" in step for step in explanation["steps"])


def test_rule_text_carries_the_configured_threshold():
    rule = neural_explanation(neural_signal(), 3.0)["rule"]
    assert "≥ +3.00 Hz" in rule and "≤ -3.00 Hz" in rule


def test_procedural_explanation_disclaims_neural_measurement():
    explanation = procedural_explanation(
        {
            "score": 0.62,
            "threshold": 0.45,
            "momentum": 0.004,
            "volatility": 0.001,
            "lookback": 20,
            "side": "BUY",
            "simulated_memory_delta": 0.03,
        }
    )
    text = " ".join(explanation["steps"]) + explanation["rule"] + explanation["note"]
    assert "no spikes, no connectome" in text.lower()
    assert "not a neural measurement" in text.lower()
    assert PROCEDURAL_RULE.split(".")[0] in explanation["rule"]
    # No measured field may be a spike count: the demo backend has none to report.
    assert not [key for key in explanation["measured"] if "spike" in key]


def test_execution_lines_state_what_the_guard_did():
    hold = execution_explanation({"status": "HOLD"}, {"bid": "1", "ask": "2"})
    assert "HOLD" in hold[0]

    veto = execution_explanation(
        {"status": "VETO", "reason": "Order cooldown"}, {"bid": "1", "ask": "2"}
    )
    assert any("Order cooldown" in line for line in veto)

    filled = execution_explanation(
        {
            "status": "FILLED",
            "plan": {"base_size": "0.00015", "product": "BTC-USDC", "limit_price": "61400.00", "fee_ceiling": "0.2"},
            "fill": {"base": "0.00015", "price": "61000.00", "quote": "9.15", "fee": "0.0549"},
        },
        {"bid": "60990.00", "ask": "61010.00"},
    )
    assert any("Fee ceiling" in line for line in filled)
    assert any("Paper fill" in line for line in filled)


def test_summary_reports_drawdown_and_return():
    summary = summarise_curve([100.0, 110.0, 99.0, 105.0], 100.0)
    assert summary["final_equity"] == 105.0
    assert summary["return_pct"] == 5.0
    assert summary["max_drawdown_pct"] == 10.0
