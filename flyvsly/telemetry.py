"""Recording format shared by the arena, the server and the browser.

One recording = one season × one repeat × both arms. It contains the exact bars every arm
saw, the per-observation decision trail for each arm, and a summary. The browser renders
only what is in here; nothing is invented at display time.

The format is versioned. `schema` is checked by the server before a recording is served.
"""

import json
import os
import math
import statistics
from decimal import Decimal
from pathlib import Path

SCHEMA = "flyvsly.recording/v1"

READOUT_RULE = (
    "Fitted readout. A logistic model, trained on this fly's own recorded population "
    "vectors to predict the sign of the price change {horizon} bar(s) ahead, scores each "
    "bar's vector. BUY at or above +{margin:.3f}, SELL at or below -{margin:.3f}, otherwise "
    "HOLD. The same model file is applied to both flies; each fly supplies its own activity. "
    "The model is used unchanged during the run: nothing is fitted online."
)

NEURAL_RULE = (
    "Fixed decoder. BUY requires mean(right DNp20) − mean(left DNp20) ≥ "
    "{threshold:+.2f} Hz and at least one DNpe017 spike. SELL requires the same "
    "difference ≤ {threshold_neg:+.2f} Hz and at least one DNpe017 spike. Otherwise HOLD."
)

PROCEDURAL_RULE = (
    "Procedural demo rule. BUY when the momentum score ≥ {threshold:+.2f}, SELL when "
    "≤ {threshold_neg:+.2f}, otherwise HOLD. This is not a neural measurement."
)


def readout_explanation(signal: dict) -> dict:
    """Which fitted model scored which spikes, and what the fixed rule would have said."""
    score = float(signal["readout_score"])
    margin = float(signal.get("readout_margin", 0.15))
    side = signal["side"]
    fixed = signal.get("decoder_side")
    steps = [
        f"Population vector of {len(signal.get('population') or [])} cells taken from this "
        "bar's spike counts",
        f"Fitted readout score: {score:+.4f} against a margin of ±{margin:.3f}",
        f"Score is {'at or above' if score >= margin else 'at or below' if score <= -margin else 'inside'} "
        f"the margin, so the readout proposes {side}",
    ]
    if fixed is not None:
        steps.append(
            f"The fixed DNp20 rule reading the same spikes would have proposed {fixed} "
            "(kept in the log as the audit trail, not used to trade)"
        )
    return {
        "kind": "fitted-readout",
        "rule": READOUT_RULE.format(horizon=signal.get("readout_horizon", 1), margin=margin),
        "measured": {
            "readout_score": score,
            "readout_margin": margin,
            "population_size": len(signal.get("population") or []),
            "dnp20_difference_hz": float(signal["difference_hz"]),
            "gate_spikes": int(signal["gate_spikes"]),
        },
        "steps": steps,
        "result": side,
        "engineered_interface": True,
        "note": (
            "The readout is a model of recorded brain activity, not of the market: it can "
            "only be as informative as the fly's spikes are about the next price change."
        ),
    }


def neural_explanation(signal: dict, threshold: float) -> dict:
    """Which measured spikes and which fixed rule produced this proposal."""
    if signal.get("readout_score") is not None:
        return readout_explanation(signal)
    left = float(signal["left_hz"])
    right = float(signal["right_hz"])
    difference = float(signal["difference_hz"])
    gate = int(signal["gate_spikes"])
    side = signal["side"]
    steps = [
        f"Mean right DNp20 firing: {right:.3f} Hz over {signal['seconds']:.3f} s of neural time",
        f"Mean left DNp20 firing: {left:.3f} Hz",
        f"Decoded difference right − left = {difference:+.3f} Hz",
        f"DNpe017 gate spikes: {gate}",
    ]
    if gate < 1:
        steps.append("Gate condition failed (no DNpe017 spike): HOLD regardless of difference")
    elif abs(difference) < threshold:
        steps.append(
            f"|{difference:+.3f} Hz| < {threshold:.2f} Hz threshold: HOLD"
        )
    else:
        steps.append(
            f"{difference:+.3f} Hz is beyond the ±{threshold:.2f} Hz threshold with the gate "
            f"satisfied: {side}"
        )
    return {
        "kind": "neural-threshold",
        "rule": NEURAL_RULE.format(threshold=threshold, threshold_neg=-threshold),
        "measured": {
            "dnp20_left_hz": left,
            "dnp20_right_hz": right,
            "difference_hz": difference,
            "gate_spikes": gate,
            "threshold_hz": threshold,
            "seconds": signal["seconds"],
        },
        "steps": steps,
        "result": side,
        "engineered_interface": True,
        "note": (
            "The DNp20 readout is an engineered interface, not a discovered buy/sell "
            "neuron. Persistent network bias can therefore become persistent buying."
        ),
    }


def procedural_explanation(signal: dict) -> dict:
    threshold = float(signal["threshold"])
    steps = [
        "Procedural demo signal. No spikes, no connectome, no dopamine cells.",
        f"Trailing return over {signal['lookback']} observed bars: {signal['momentum']:+.4%}",
        f"Realised volatility of that window: {signal['volatility']:.4%}",
        f"Momentum score (momentum ÷ volatility, squashed): {signal['score']:+.3f}",
    ]
    if signal["simulated_memory_delta"] is not None:
        steps.append(
            f"Simulated memory bias applied: {signal['simulated_memory_delta']:+.3f} "
            "(a stand-in for the neural memory rule, not a measurement of one)"
        )
    steps.append(f"{signal['score']:+.3f} against threshold ±{threshold:.2f}: {signal['side']}")
    return {
        "kind": "procedural",
        "rule": PROCEDURAL_RULE.format(threshold=threshold, threshold_neg=-threshold),
        "measured": {
            "momentum": signal["momentum"],
            "volatility": signal["volatility"],
            "score": signal["score"],
            "threshold": threshold,
            "lookback": signal["lookback"],
        },
        "steps": steps,
        "result": signal["side"],
        "engineered_interface": True,
        "note": "Procedural demo mode exists to build and test the interface. It is not neural activity.",
    }


def execution_explanation(order: dict, quote: dict) -> list[str]:
    """Why the order did or did not reach the account."""
    status = order.get("status")
    if status == "HOLD":
        return ["No proposal to size: the fly's decoder proposed HOLD this bar."]
    lines = [
        f"Observed book: bid {quote['bid']} / ask {quote['ask']} "
        f"(bar close, half-spread applied identically for both flies)"
    ]
    plan = order.get("plan")
    if plan:
        lines.append(
            f"Guard sized a price-bounded order: {plan['base_size']} {plan['product'].split('-')[0]} "
            f"at limit {plan['limit_price']} (slippage cap {order.get('slippage_limit')})"
        )
        lines.append(f"Fee ceiling reserved before sending: {plan['fee_ceiling']} USDC")
    fill = order.get("fill")
    if status == "FILLED" and fill:
        lines.append(
            f"Paper fill: {fill['base']} at {fill['price']} = {fill['quote']} USDC, "
            f"fee {fill['fee']} USDC"
        )
    elif status == "VETO":
        lines.append(f"Execution guard vetoed the proposal: {order.get('reason')}")
    return lines


def portfolio_view(cash: Decimal, positions: dict, equity: Decimal, initial: Decimal, stats: dict) -> dict:
    return {
        "cash": str(cash),
        "positions": {k: str(v) for k, v in positions.items()},
        "equity": str(equity),
        "return_pct": float((equity / initial - 1) * 100) if initial else 0.0,
        "fees_paid": str(stats["fees_paid"]),
        "fills": stats["fills"],
        "vetoes": stats["vetoes"],
        "halted": stats["halted_reason"],
    }


def summarise_curve(values: list[float], initial: float) -> dict:
    peak = initial
    max_drawdown = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - value) / peak)
    final = values[-1] if values else initial
    simple = []
    for previous, current in zip(values, values[1:]):
        if previous:
            simple.append(current / previous - 1)
    volatility = statistics.pstdev(simple) * math.sqrt(len(simple)) if simple else 0.0
    return {
        "final_equity": round(final, 6),
        "return_pct": round((final / initial - 1) * 100, 6) if initial else 0.0,
        "max_drawdown_pct": round(max_drawdown * 100, 6),
        "path_volatility": round(volatility * 100, 6),
    }


def write_jsonl(path, rows):
    with open(path, "w") as handle:
        for row in rows:
            handle.write(json.dumps(row, allow_nan=False, sort_keys=True) + "\n")


def write_recording(path, recording):
    """Write a recording so a reader never sees half of one.

    `live_state.json` is rewritten every bar of a live session, and a live session is exactly
    the thing most likely to be killed mid-write. A plain open/write can leave a truncated
    file — an unreadable JSON document claiming to be a checkpoint. Writing a sibling temp file
    and renaming means the reader sees either the previous checkpoint or the new one, never a
    fragment of either.
    """
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    with open(temporary, "w") as handle:
        json.dump(recording, handle, allow_nan=False, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
