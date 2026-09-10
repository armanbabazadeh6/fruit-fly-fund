"""The neural arm: upstream Stonkfly's MaleCNS v1.0 engine, unmodified.

Nothing here selects a trade. The fly receives a 320×180 RGB chart and the engineered
reward/aversive pulse, and the fixed DNp20 decoder proposes buy, sell or hold. The only
difference between the two competitors in this project is that one of these backends is
constructed with `learning=True` and the other with `learning=False`.
"""

import os
import sys
from pathlib import Path

import numpy as np


def ensure_data_root(root: Path) -> Path:
    """Point the vendored engine at our data directory before it is imported.

    `stonkfly.neural.common` resolves the data root at import time, so this must run
    first. If the engine is already imported with a different root we refuse rather than
    silently train on a different graph.
    """
    root = Path(root).resolve()
    os.environ["STONKFLY_DATA"] = str(root)
    existing = sys.modules.get("stonkfly.neural.common")
    if existing is not None and Path(existing.DATA) != root:
        raise RuntimeError(
            f"Neural engine already bound to {existing.DATA}, cannot rebind to {root}"
        )
    return root


class NeuralBackend:
    engine = "neural"

    def __init__(self, settings, data_root="data", require_gate=True):
        ensure_data_root(Path(data_root))
        from stonkfly.neural.common import annotations
        from stonkfly.neural.controller import FlyController

        from ..decoder import ConfigurableDecoder

        self.controller = FlyController(settings)
        # Swap in our gate-optional decoder, constructed from the same cell identities
        # upstream selected, so the only difference is whether the gate is required.
        self.controller.decoder = ConfigurableDecoder(
            self.controller.brain.ids,
            annotations(self.controller.brain.ids),
            settings.decoder_threshold_hz,
            require_gate=require_gate,
        )
        brain = self.controller.brain
        self.settings = settings
        self.label = (
            f"MaleCNS v1.0 retained graph, {brain.n:,} neurons, {len(brain.post):,} connections"
        )
        self.learning = bool(settings.learning)
        self.require_gate = bool(require_gate)
        self.memory_rule = brain.rule_parameters
        self.plastic_edges = int(len(brain.circuit["edges"]))

    def describe(self) -> dict:
        brain = self.controller.brain
        return {
            "signal_source": "neural",
            "label": self.label,
            "release": "MaleCNS v1.0",
            "neurons": int(brain.n),
            "directed_edges": int(len(brain.post)),
            "plastic_edges": self.plastic_edges,
            "memory_updates_applied": self.learning,
            "model": brain.circuit["report"],
            "vision": brain.visual_report,
            "memory_rule": self.memory_rule,
            "decoder_cells": self.controller.decoder.identities,
            "decoder": (
                "DNp20 mean right-minus-left firing, engineered interface rather than a "
                "discovered buy/sell neuron. "
                + (
                    "Upstream's DNpe017 spike gate is required."
                    if self.require_gate
                    else "The DNpe017 gate is disabled (our change): the difference alone "
                    "decides, which is why this run trades far more often."
                )
            ),
            "decoder_gate_required": self.require_gate,
            "claims": (
                "Neural state is real simulation state from the retained MaleCNS v1.0 graph. "
                "Profitable learning has not been demonstrated by upstream or here."
            ),
        }

    def observe(self, frame: np.ndarray, reinforcement: str, visible_history=None) -> dict:
        """Advance 500 ms of neural time. `visible_history` is deliberately unused.

        The fly's only market input is the rendered RGB frame. Prices are passed in by
        the arena for the procedural backend, and this signature documents that the neural
        arm cannot read them. `tests/test_neural_arm.py` asserts that perturbing the price
        history does not change this arm's output.
        """
        out = self.controller.observe(frame, reinforcement)
        memory = out["memory"]
        return {
            "signal_source": "neural",
            "label": self.label,
            "side": out["side"],
            "seconds": self.settings.neural_ms / 1000,
            "left_hz": out["left_hz"],
            "right_hz": out["right_hz"],
            "difference_hz": out["difference_hz"],
            "gate_spikes": out["gate_spikes"],
            "stimulus": out["stimulus"],
            "stimulus_ms": out["stimulus_ms"],
            "reward_spikes": out["reward_spikes"],
            "aversive_spikes": out["aversive_spikes"],
            "KC_spikes": out["KC_spikes"],
            "total_spikes": out["total_spikes"],
            "brain_ms": out["brain_ms"],
            "compute_seconds": out["compute_seconds"],
            "spike_sha256": out["spike_sha256"],
            "input_sha256": out["input_sha256"],
            "cell_ids": out["cell_ids"],
            "memory": {
                "enabled": self.learning,
                "model": memory["model"],
                "plastic_edges": memory["plastic_edges"],
                "changed_edges": memory["changed_edges"],
                "mean_efficacy": memory["mean_efficacy"],
                "minimum_efficacy": memory["minimum_efficacy"],
                "sha256": memory["sha256"],
            },
        }

    def save(self, path):
        self.controller.save(path)

    def reset(self, keep_memory=False):
        self.controller.brain.reset(keep_memory=keep_memory)
