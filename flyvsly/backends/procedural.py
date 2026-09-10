"""Procedural demo backend.

This is a stand-in, not a fly. It exists so the interface, the recording format and the
server can be built and tested on a laptop without several hundred megabytes of connectome
state. Every field it emits is marked `simulated`, it produces no spike counts and no
synaptic efficacies, and the browser refuses to render it as neural activity.

The rules are causal: they read only the price history the fly has already been shown.
`learning=True` applies a small simulated adaptation term so the two-arm interface can be
exercised end to end; the term is a simulation of memory, never a measurement of it.
"""

import math
import time

SCORE_THRESHOLD = 0.45
LOOKBACK = 20


class ProceduralBackend:
    engine = "procedural"
    label = "Procedural demo (not neural)"

    def __init__(self, settings, seed=0):
        self.settings = settings
        self.learning = bool(settings.learning)
        self.seed = int(seed)
        self.bias = 0.0
        self.last_score = 0.0
        self.last_side = "HOLD"

    def describe(self) -> dict:
        return {
            "signal_source": "procedural",
            "simulated": True,
            "label": self.label,
            "memory_updates_applied": self.learning,
            "rule": (
                "Momentum divided by realised volatility, compared against a fixed "
                "threshold. Stand-in for the real engine."
            ),
            "claims": (
                "This backend is a demonstration of the interface. It measures no neurons, "
                "runs no connectome and its memory term is simulated. Never present a "
                "procedural run as live neural activity."
            ),
            "parameters": {
                "score_threshold": SCORE_THRESHOLD,
                "lookback": LOOKBACK,
                "simulated_memory_decay": 0.9,
                "simulated_memory_gain": 0.35,
            },
        }

    def observe(self, frame, reinforcement: str, visible_history=None) -> dict:
        started = time.perf_counter()
        history = list(visible_history or [])
        momentum = 0.0
        volatility = 0.0
        score = 0.0
        delta = None
        if len(history) > LOOKBACK:
            window = history[-(LOOKBACK + 1) :]
            momentum = window[-1] / window[0] - 1.0
            returns = [
                window[i + 1] / window[i] - 1.0
                for i in range(len(window) - 1)
                if window[i]
            ]
            mean = sum(returns) / len(returns) if returns else 0.0
            volatility = math.sqrt(
                sum((r - mean) ** 2 for r in returns) / len(returns)
            ) if returns else 0.0
            score = math.tanh(momentum / (volatility * math.sqrt(LOOKBACK) + 1e-6))
            if self.learning:
                # A stand-in for a memory update: reinforce the direction that paid last.
                delta = self.bias
                score = max(-1.0, min(1.0, score + self.bias))
        side = (
            "BUY"
            if score >= SCORE_THRESHOLD
            else "SELL"
            if score <= -SCORE_THRESHOLD
            else "HOLD"
        )
        if self.learning and self.last_side != "HOLD":
            signed_reward = {"reward": 1.0, "aversive": -1.0}.get(reinforcement, 0.0)
            self.bias = self.bias * 0.9 + 0.35 * 0.02 * signed_reward * (
                1.0 if self.last_side == "BUY" else -1.0
            )
        self.last_score = score
        self.last_side = side
        return {
            "signal_source": "procedural",
            "simulated": True,
            "label": self.label,
            "side": side,
            "score": score,
            "threshold": SCORE_THRESHOLD,
            "momentum": momentum,
            "volatility": volatility,
            "lookback": LOOKBACK,
            "simulated_memory_delta": delta,
            "base_score": score - (delta or 0.0),
            "compute_seconds": time.perf_counter() - started,
            "stimulus": reinforcement,
            "memory": {
                "enabled": self.learning,
                "simulated": True,
                "bias": self.bias,
                "changed_edges": None,
                "note": "Simulated adaptation term. No synapse exists in this backend.",
            },
        }

    def save(self, path):
        pass

    def reset(self, keep_memory=False):
        self.bias = 0.0
        self.last_score = 0.0
        self.last_side = "HOLD"
