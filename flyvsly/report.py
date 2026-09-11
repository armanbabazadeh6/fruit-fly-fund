"""Aggregate repeated seasons into a comparison that can show a lucky run as lucky.

A single season is an anecdote. This module pairs runs by label, so `--repeats 6` over six
different market seasons produces six paired differences (memory-on minus memory-off) and
the spread around their mean. It reports counts and spread, never a significance claim:
with this many seasons, a small mean difference is indistinguishable from noise and the
report says so.
"""

import json
from pathlib import Path
from statistics import mean, pstdev


def load_manifests(runs_dir="runs"):
    runs_dir = Path(runs_dir)
    manifests = []
    for path in sorted(runs_dir.glob("*/manifest.json")):
        try:
            payload = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        payload["path"] = str(path.parent)
        payload["id"] = path.parent.name
        manifests.append(payload)
    manifests.sort(key=lambda m: m["run"].get("created", 0), reverse=True)
    return manifests


def recording_path(runs_dir, ref: str):
    """Resolve a run id, or the newest recording carrying that label.

    A campaign cannot name its own run ids in advance (they are timestamps), so both forms are
    accepted wherever a recording is named as a reference.
    """
    root = Path(runs_dir)
    for candidate in (root / ref / "recording.json", root / f"{ref}.json"):
        if candidate.exists():
            return candidate
    matches = []
    for candidate in root.glob("*/recording.json"):
        try:
            recording = json.loads(candidate.read_text())
        except (OSError, ValueError):
            continue
        if recording.get("run", {}).get("label") == ref:
            matches.append((recording["run"].get("created", 0), candidate))
    if matches:
        return max(matches)[1]
    raise FileNotFoundError(
        f"No recording for reference {ref!r} under {runs_dir} "
        "(tried <id>/recording.json, <id>.json, and a matching run label)"
    )


def summarise(manifests) -> dict:
    groups = {}
    for manifest in manifests:
        label = manifest["run"].get("label") or "unlabelled"
        groups.setdefault(label, []).append(manifest)
    out = []
    for label, runs in groups.items():
        rows = []
        for manifest in runs:
            summary = manifest.get("summary") or {}
            arms = summary.get("arms") or {}
            if "gordon" not in arms or "warren" not in arms:
                continue
            benchmarks = summary.get("benchmarks") or {}
            rows.append(
                {
                    "id": manifest["id"],
                    "season": manifest["run"].get("season"),
                    "engine": manifest["run"].get("engine"),
                    "repeat": manifest["run"].get("repeat", 0),
                    "bars": summary.get("bars"),
                    "gordon_pct": arms["gordon"]["return_pct"],
                    "warren_pct": arms["warren"]["return_pct"],
                    "buy_hold_pct": _pct(benchmarks.get("buy_and_hold")),
                    "cash_pct": 0.0,
                    "delta_pct": arms["gordon"]["return_pct"]
                    - arms["warren"]["return_pct"],
                    "gordon_fills": arms["gordon"]["fills"],
                    "warren_fills": arms["warren"]["fills"],
                    "changed_edges": (arms["gordon"].get("final_memory") or {}).get(
                        "changed_edges"
                    ),
                    "seconds_per_bar": summary.get("seconds_per_bar_mean"),
                }
            )
        if not rows:
            continue
        deltas = [row["delta_pct"] for row in rows]
        out.append(
            {
                "label": label,
                "engine": rows[0]["engine"],
                "repeats": len(rows),
                "runs": rows,
                "mean_gordon_pct": round(mean([r["gordon_pct"] for r in rows]), 4),
                "mean_warren_pct": round(mean([r["warren_pct"] for r in rows]), 4),
                "mean_buy_hold_pct": round(mean([r["buy_hold_pct"] for r in rows]), 4),
                "mean_delta_pct": round(mean(deltas), 4),
                "delta_spread_pct": round(pstdev(deltas), 4) if len(deltas) > 1 else 0.0,
                "delta_min_pct": round(min(deltas), 4),
                "delta_max_pct": round(max(deltas), 4),
                "memory_on_wins": sum(1 for d in deltas if d > 0),
                "memory_off_wins": sum(1 for d in deltas if d < 0),
                "ties": sum(1 for d in deltas if d == 0),
                "mean_changed_edges": _mean_optional(
                    [r["changed_edges"] for r in rows]
                ),
            }
        )
    out.sort(key=lambda group: group["repeats"], reverse=True)
    return {
        "groups": out,
        "recordings": len(manifests),
        "reading_note": (
            "Memory-on wins N of M seasons is a count, not evidence. With few seasons the "
            "paired spread usually swamps the mean difference, and no run here "
            "demonstrates profitable learning."
        ),
    }


def _pct(benchmark):
    if not benchmark:
        return 0.0
    curve = benchmark.get("curve") or []
    if not curve:
        return 0.0
    initial = float(benchmark.get("initial_capital", curve[0]) or curve[0])
    return round((curve[-1] / initial - 1) * 100, 6) if initial else 0.0


def _mean_optional(values):
    values = [v for v in values if v is not None]
    return round(mean(values), 2) if values else None


def markdown_table(report: dict) -> str:
    """Every group as one table, for committing results next to the code.

    The table is the experiment's output, not decoration: it carries the paired difference
    and its spread, because a mean without the spread around it invites the reader to
    believe a single season.
    """
    lines = [
        "# Recorded campaigns",
        "",
        "Generated by `flyvsly report --table --write results/report.md`. Every row is one",
        "label: a set of seasons run under identical rules, differing between the two flies",
        "only in `learning`. Compiled from the recordings in `runs/`, which are not committed;",
        "the published ones live in `web/public/recordings/`.",
        "",
        "| campaign | engine | seasons | memory on | memory off | buy & hold | paired delta (on − off) | spread | wins on/off/tie | mean rewrites |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: |",
    ]
    for group in report["groups"]:
        rewrites = group["mean_changed_edges"]
        lines.append(
            f"| `{group['label']}` | {group['engine']} | {group['repeats']} "
            f"| {group['mean_gordon_pct']:+.3f}% | {group['mean_warren_pct']:+.3f}% "
            f"| {group['mean_buy_hold_pct']:+.3f}% | **{group['mean_delta_pct']:+.3f}%** "
            f"| ±{group['delta_spread_pct']:.3f}% "
            f"| {group['memory_on_wins']}/{group['memory_off_wins']}/{group['ties']} "
            f"| {rewrites if rewrites is not None else '—'} |"
        )
    lines += ["", report["reading_note"], ""]
    return "\n".join(lines)
