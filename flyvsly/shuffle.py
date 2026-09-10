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


def _recording_path(runs_dir, run_id: str) -> Path:
    """Locate a recording by run id, or by label if no id matches.

    A campaign cannot name its own run ids in advance (they are timestamps), so a reference
    may also be a label: the newest recording carrying that label is used.
    """
    root = Path(runs_dir)
    for candidate in (root / run_id / "recording.json", root / f"{run_id}.json"):
        if candidate.exists():
            return candidate
    matches: list[tuple[float, Path]] = []
    for candidate in root.glob("*/recording.json"):
        try:
            recording = json.loads(candidate.read_text())
        except (OSError, ValueError):
            continue
        if recording.get("run", {}).get("label") == run_id:
            matches.append((recording["run"].get("created", 0), candidate))
    if matches:
        return max(matches)[1]
    raise FileNotFoundError(
        f"No recording for reference {run_id!r} under {runs_dir} "
        "(tried <id>/recording.json, <id>.json, and a matching run label)"
    )


def load_schedule(runs_dir, run_id: str) -> list[str]:
    """The per-bar stimulus kinds a previous run delivered to its experimental arm."""
    path = _recording_path(runs_dir, run_id)
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
