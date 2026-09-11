"""Reinforcement schedules for the controls that separate outcome from pulse.

`pnl` rewards a fly for its own profit. The other modes exist because that tells us nothing
on its own: if a memory-on fly differs from a memory-off fly, we cannot say whether the
*contingency* between its actions and the pulse mattered, or whether any dopamine pulse at all
moves those synapses. These modes deliver pulses with matched statistics and no contingency.
"""

import json
import random
from pathlib import Path

MODES = ("pnl", "decoy", "shuffled", "none")


def load_schedule(runs_dir, run_id: str) -> list[str]:
    """The per-bar stimulus kinds a previous run delivered to its experimental arm."""
    from .report import recording_path

    path = recording_path(runs_dir, run_id)
    recording = json.loads(path.read_text())
    schedule = [
        observation["arms"]["gordon"]["stimulus"]["kind"]
        for observation in recording["observations"]
    ]
    if not schedule:
        raise ValueError(f"Reference run {run_id!r} has no observations")
    return schedule


def permute(schedule: list[str], seed: int) -> list[str]:
    """A fixed permutation: the same pulses, at unrelated times."""
    order = list(schedule)
    random.Random(seed).shuffle(order)
    return order


def counts(schedule: list[str]) -> dict:
    summary = {"reward": 0, "aversive": 0, "none": 0}
    for kind in schedule:
        summary[kind] = summary.get(kind, 0) + 1
    return summary
