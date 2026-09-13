"""Aggregate repeated results into a comparison that can show a lucky run as lucky.

A single season is an anecdote. `summarise` pairs runs by label, so `--repeats 6` over six
different market seasons produces six paired differences (memory-on minus memory-off) and
the spread around their mean. It reports counts and spread, never a significance claim:
with this many seasons, a small mean difference is indistinguishable from noise and the
report says so.

`summarise_live` does the same for the sessions `flyvsly live` accumulates over days, which
are a different unit: each grows for as long as it is left running, none covers the same
market window as another, and one may have been killed and rebuilt by `flyvsly salvage`
rather than stopped. Those differences are where a live aggregate can lie, so unlike the
season grouping by label this one *refuses to pool* rather than pools by default: sessions
are averaged together only when they ran the same product, bar length, engine, run kind and
rule set, and anything else is reported as a separate group with the reason it stands apart.
The two aggregates live in one module because they are the same reading: a count of paired
differences, the spread around their mean, and the benchmarks both arms faced.
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


# -- live sessions ------------------------------------------------------------------
#
# A live session is read from its own manifest (`run.live` is the marker `flyvsly live`
# writes), and every number comes from the summary that session settled on. Nothing here is
# re-derived from an equity curve: a salvaged curve was rebuilt from an observation log and
# can disagree with the return the accounts actually recorded, so the paired difference is
# the difference of the two arms' own `return_pct`, which is the same quantity
# `summary.comparison` reports for a single session.
#
# The comparability key is the whole experiment: two sessions are pooled only if they ran the
# same product, bar length, engine, run kind and rule set. Anything else is two experiments,
# and averaging them would be the mistake `flyvsly/config.py` and the exam windows refuse in
# their own domains.


def _live_rules_json(rules) -> str:
    return json.dumps(rules, sort_keys=True, default=str)


def _live_group_key(row) -> tuple:
    return (
        row["product"],
        row["bar_seconds"],
        row["engine"],
        row["kind"],
        _live_rules_json(row["rules"]),
    )


def _live_row(manifest):
    """One session's comparison row, or the reason it cannot be pooled with anything.

    Returns ``(row, None)`` or ``(None, reason)``. A session without a product, a bar length
    or a recorded rule set cannot be placed in a group at all, and one that traded no bars has
    no result to contribute; both are refused by name rather than dropped in silence.
    """
    run = manifest.get("run") or {}
    live = run.get("live") or {}
    summary = manifest.get("summary") or {}
    arms = summary.get("arms") or {}
    if "gordon" not in arms or "warren" not in arms:
        return None, "no gordon/warren pair to compare"
    product = live.get("product")
    if not product:
        return None, "no product recorded, so it cannot be compared with another session"
    bar_seconds = live.get("bar_seconds") or run.get("bar_seconds")
    if not bar_seconds:
        return None, "no bar length recorded, so it cannot be compared with another session"
    rules = run.get("rules")
    if not rules:
        return None, "no rule set recorded, so it cannot be compared with another session"
    bars = summary.get("bars")
    if not bars:
        return None, "traded no bars, so there is nothing to compare"
    if arms["gordon"].get("return_pct") is None or arms["warren"].get("return_pct") is None:
        return None, "no return recorded for both arms"

    gordon, warren = arms["gordon"], arms["warren"]
    # Killed, not stopped: `salvage` leaves all three marks, and any one of them is enough.
    # The distinction is the point — a salvaged session's last bar is where the host died, not
    # a finish line, and the aggregate must not present it as one.
    salvaged = (
        bool(run.get("salvaged"))
        or bool(summary.get("salvaged"))
        or "salvaged" in str(run.get("wall_mode") or "")
    )
    benchmark = (summary.get("benchmarks") or {}).get("buy_and_hold")
    truncated = bool(run.get("truncated") or summary.get("truncated"))
    gordon_pct = round(float(gordon["return_pct"]), 6)
    warren_pct = round(float(warren["return_pct"]), 6)
    return (
        {
            "id": manifest.get("id"),
            "label": run.get("label"),
            "created": run.get("created", 0),
            "product": str(product),
            "bar_seconds": int(bar_seconds),
            "engine": run.get("engine"),
            "kind": run.get("kind"),
            "preset": run.get("rule_preset"),
            "rules": rules,
            "bars": int(bars),
            # The market span the session covered, counted from the bars it traded: a live
            # session runs behind the exchange by design and a salvaged one has no wall clock
            # at all, so `summary.duration_seconds` means two different things in the two
            # cases and cannot be the column.
            "span_seconds": int(bars) * int(bar_seconds),
            "ended": _ended(salvaged, truncated),
            "salvaged": salvaged,
            "truncated": truncated,
            "gordon_pct": gordon_pct,
            "warren_pct": warren_pct,
            "delta_pct": round(gordon_pct - warren_pct, 6),
            "fills": {
                "gordon": int(gordon.get("fills") or 0),
                "warren": int(warren.get("fills") or 0),
            },
            "fees": {
                "gordon": float(gordon.get("fees_paid") or 0.0),
                "warren": float(warren.get("fees_paid") or 0.0),
            },
            "buy_hold_pct": _pct(benchmark) if benchmark else None,
        },
        None,
    )


def _mean_rounded(values, digits=4):
    values = [v for v in values if v is not None]
    return round(mean(values), digits) if values else None


def _pooled_live(rows) -> dict:
    """The group's line: the paired difference, who won each session, and the fee bill.

    The mean is one session one vote, not bar-weighted: sessions are not the same length, and
    weighting a long session more would turn "how long it happened to run" into evidence.
    """
    deltas = [row["delta_pct"] for row in rows]
    fees_gordon = sum(row["fees"]["gordon"] for row in rows)
    fees_warren = sum(row["fees"]["warren"] for row in rows)
    return {
        "sessions": len(rows),
        "bars": sum(row["bars"] for row in rows),
        "span_seconds": sum(row["span_seconds"] for row in rows),
        "salvaged": sum(1 for row in rows if row["salvaged"]),
        "mean_gordon_pct": _mean_rounded([row["gordon_pct"] for row in rows]),
        "mean_warren_pct": _mean_rounded([row["warren_pct"] for row in rows]),
        "mean_buy_hold_pct": _mean_rounded([row["buy_hold_pct"] for row in rows]),
        "mean_delta_pct": _mean_rounded(deltas),
        "delta_spread_pct": round(pstdev(deltas), 4) if len(deltas) > 1 else 0.0,
        "delta_min_pct": round(min(deltas), 4),
        "delta_max_pct": round(max(deltas), 4),
        "memory_on_wins": sum(1 for d in deltas if d > 0),
        "memory_off_wins": sum(1 for d in deltas if d < 0),
        "ties": sum(1 for d in deltas if d == 0),
        "fills_gordon": sum(row["fills"]["gordon"] for row in rows),
        "fills_warren": sum(row["fills"]["warren"] for row in rows),
        "fees_gordon": round(fees_gordon, 6),
        "fees_warren": round(fees_warren, 6),
        "fees_total": round(fees_gordon + fees_warren, 6),
    }


def _differing_rule_fields(rows) -> list[str]:
    fields = sorted({key for row in rows for key in (row["rules"] or {})})
    differing = []
    for field in fields:
        values = {
            json.dumps((row["rules"] or {}).get(field), sort_keys=True, default=str)
            for row in rows
        }
        if len(values) > 1:
            differing.append(field)
    return differing


def _live_pooling_note(rows, groups):
    """Why the sessions above are in more than one group, named field by named field."""
    if len(groups) < 2:
        return None
    reasons = []
    products = sorted({row["product"] for row in rows})
    if len(products) > 1:
        reasons.append("products (" + ", ".join(products) + ")")
    lengths = sorted({row["bar_seconds"] for row in rows})
    if len(lengths) > 1:
        reasons.append("bar lengths (" + ", ".join(f"{value}s" for value in lengths) + ")")
    engines = sorted({str(row["engine"]) for row in rows})
    if len(engines) > 1:
        reasons.append("engines (" + ", ".join(engines) + ")")
    kinds = sorted({str(row["kind"]) for row in rows})
    if len(kinds) > 1:
        reasons.append("run kinds (" + ", ".join(kinds) + ")")
    differing = _differing_rule_fields(rows)
    if differing:
        reasons.append("rule sets (differ in " + ", ".join(differing) + ")")
    if not reasons:
        reasons.append("groups " + ", ".join(group["label"] for group in groups))
    return (
        f"{len(rows)} session(s) fall into {len(groups)} groups and are not pooled across "
        "them: " + "; ".join(reasons) + ". Each group is pooled on its own."
    )


def summarise_live(manifests) -> dict:
    """Pool every live session that can be pooled, and name the ones that cannot.

    `manifests` is what `load_manifests` returns; runs without a `run.live` block are ordinary
    recorded seasons and belong to `summarise`, not here.
    """
    rows, excluded = [], []
    for manifest in manifests:
        if not (manifest.get("run") or {}).get("live"):
            continue
        row, reason = _live_row(manifest)
        if row is None:
            excluded.append({"id": manifest.get("id"), "reason": reason})
            continue
        rows.append(row)
    rows.sort(key=lambda row: row["created"], reverse=True)

    by_key: dict[tuple, list] = {}
    for row in rows:
        by_key.setdefault(_live_group_key(row), []).append(row)
    groups = []
    for (product, bar_seconds, engine, kind, _), members in by_key.items():
        groups.append(
            {
                "label": f"{product} {bar_seconds}s · {engine} · {kind}",
                "product": product,
                "bar_seconds": bar_seconds,
                "engine": engine,
                "kind": kind,
                "rules": members[0]["rules"],
                "sessions": [member["id"] for member in members],
                "pooled": _pooled_live(members),
            }
        )
    groups.sort(key=lambda group: (-group["pooled"]["sessions"], group["label"]))
    return {
        "runs_read": len(manifests),
        "live_sessions": len(rows),
        "sessions": rows,
        "groups": groups,
        "excluded": excluded,
        "pooling_note": _live_pooling_note(rows, groups),
        "reading_note": (
            "Memory-on wins N of M sessions is a count, not evidence. A handful of live "
            "sessions is a shape, not a result: each covers a few hours on a market path no "
            "other session shares, the pooled mean gives every session one vote regardless of "
            "how long it ran, and every session ends where the operator stopped it or the host "
            "died — a salvaged session's last bar is not a finish line. No run here "
            "demonstrates profitable learning."
        ),
    }


def _span(seconds) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    hours, rest = divmod(seconds, 3600)
    minutes = rest // 60
    return f"{hours}h{minutes:02d}m" if hours else f"{minutes}m"


def _sessions(count) -> str:
    return f"{count} session" if count == 1 else f"{count} sessions"


def _ended(salvaged, truncated) -> str:
    if salvaged:
        return "salvaged"
    return "stopped · short" if truncated else "stopped"


def _pct_text(value) -> str:
    return "—" if value is None else f"{value:+.3f}%"


def live_markdown_table(report: dict) -> str:
    """The live aggregate as a table, for committing next to the code.

    The reading note travels with the table for the same reason the season table carries it:
    a row of numbers over a handful of sessions invites a conclusion the sample cannot hold.
    """
    lines = [
        "# Live sessions",
        "",
        "Generated by `flyvsly live-report --table --write results/live-report.md`. One row per",
        "session, read from each run's manifest; sessions are pooled only with sessions that ran",
        "the same product, bar length, engine, run kind and rule set. `salvaged` means the host",
        "died mid-session and the record was rebuilt, so its last bar is not a finish line.",
        "",
    ]
    if report["pooling_note"]:
        lines += [report["pooling_note"], ""]
    if not report["groups"]:
        lines += ["No live sessions under `--runs`.", ""]
    by_id = {row["id"]: row for row in report["sessions"]}
    for group in report["groups"]:
        pooled = group["pooled"]
        lines += [
            f"## `{group['product']}` {group['bar_seconds']}s · {group['engine']} · "
            f"{group['kind']} — {_sessions(pooled['sessions'])}",
            "",
            "| session | bars | span | ended | memory on | memory off | buy & hold "
            "| paired delta | fills on/off | fees on/off |",
            "| --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
        for session_id in group["sessions"]:
            row = by_id[session_id]
            lines.append(
                f"| `{row['id']}` | {row['bars']} | {_span(row['span_seconds'])} "
                f"| {_ended(row['salvaged'], row['truncated'])} "
                f"| {_pct_text(row['gordon_pct'])} "
                f"| {_pct_text(row['warren_pct'])} | {_pct_text(row['buy_hold_pct'])} "
                f"| **{_pct_text(row['delta_pct'])}** "
                f"| {row['fills']['gordon']}/{row['fills']['warren']} "
                f"| {row['fees']['gordon']:.3f}/{row['fees']['warren']:.3f} |"
            )
        lines += [
            "",
            f"**Pooled ({_sessions(pooled['sessions'])}):** mean paired delta "
            f"**{_pct_text(pooled['mean_delta_pct'])}** ± {pooled['delta_spread_pct']:.3f}%; "
            f"memory-on wins {pooled['memory_on_wins']} / memory-off wins "
            f"{pooled['memory_off_wins']} / ties {pooled['ties']}; fees "
            f"{pooled['fees_gordon']:.3f} on + {pooled['fees_warren']:.3f} off = "
            f"**{pooled['fees_total']:.3f} total**.",
            "",
        ]
    if report["excluded"]:
        lines += ["Excluded from the pool:", ""]
        for item in report["excluded"]:
            lines.append(f"- `{item['id']}`: {item['reason']}")
        lines.append("")
    lines += [report["reading_note"], ""]
    return "\n".join(lines)


def _rules_text(rules) -> str:
    """The rule fields a reader compares by eye, in the order the CLI takes them."""
    rules = rules or {}
    shown = (
        "capital",
        "order_limit",
        "daily_orders",
        "paper_fee",
        "interval_seconds",
        "require_gate",
        "reinforcement",
    )
    parts = [f"{key} {rules[key]}" for key in shown if rules.get(key) is not None]
    return "rules " + ", ".join(parts) if parts else "rules (none recorded)"


def _table(headers, rows, aligns) -> list[str]:
    widths = [len(header) for header in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    def line(cells):
        return "  ".join(
            cell.rjust(width) if align == "r" else cell.ljust(width)
            for cell, width, align in zip(cells, widths, aligns)
        ).rstrip()
    return [line(headers), line(["-" * width for width in widths])] + [line(row) for row in rows]


def live_text(report: dict) -> str:
    """The live aggregate in the terminal: one aligned table per comparable group."""
    lines = [
        f"live sessions: {report['live_sessions']} of {report['runs_read']} run(s) read, "
        f"{len(report['groups'])} comparable group(s)"
    ]
    if report["pooling_note"]:
        lines += ["", report["pooling_note"]]
    by_id = {row["id"]: row for row in report["sessions"]}
    for group in report["groups"]:
        pooled = group["pooled"]
        lines += [
            "",
            f"{group['product']} {group['bar_seconds']}s · {group['engine']} · "
            f"{group['kind']} · {_rules_text(group['rules'])} — {_sessions(pooled['sessions'])}",
            "",
        ]
        body = []
        for session_id in group["sessions"]:
            row = by_id[session_id]
            body.append(
                (
                    row["id"],
                    str(row["bars"]),
                    _span(row["span_seconds"]),
                    _ended(row["salvaged"], row["truncated"]),
                    _pct_text(row["gordon_pct"]),
                    _pct_text(row["warren_pct"]),
                    _pct_text(row["buy_hold_pct"]),
                    _pct_text(row["delta_pct"]),
                    f"{row['fills']['gordon']}/{row['fills']['warren']}",
                    f"{row['fees']['gordon']:.3f}/{row['fees']['warren']:.3f}",
                )
            )
        headers = (
            "session", "bars", "span", "ended", "gordon", "warren", "buy & hold", "delta",
            "fills on/off", "fees on/off",
        )
        lines += ["  " + row for row in _table(headers, body, "lllrrrrrll")]
        lines += [
            "",
            f"  pooled: mean paired delta {_pct_text(pooled['mean_delta_pct'])} "
            f"± {pooled['delta_spread_pct']:.3f}%  ·  wins on/off/tie "
            f"{pooled['memory_on_wins']}/{pooled['memory_off_wins']}/{pooled['ties']}  ·  "
            f"fees {pooled['fees_gordon']:.3f} on + {pooled['fees_warren']:.3f} off = "
            f"{pooled['fees_total']:.3f} total",
        ]
        if pooled["sessions"] == 1:
            lines.append("  one session: a single market path, not evidence.")
    if report["excluded"]:
        lines += ["", "excluded from the pool:"]
        for item in report["excluded"]:
            lines.append(f"  {item['id']}: {item['reason']}")
    lines += ["", report["reading_note"]]
    return "\n".join(lines)
