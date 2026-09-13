"""The live aggregate: pool what is comparable, and say what is not.

Live sessions accumulate one per day and no two cover the same market window, so the failure
mode this report can have is not an arithmetic slip — it is quietly averaging two different
experiments, or reading a killed session's last bar as a finish line. The fixtures below are
manifests written by hand, so a test states exactly which sessions existed and what each one
reported, and nothing here touches the network or a real `runs/`.
"""

import json

import pytest

from flyvsly.cli import main
from flyvsly.report import live_markdown_table, live_text, load_manifests, summarise_live

EPOCH = 1789149720
BAR = 60

RULES = {
    "products": ["BTC-USDC"],
    "capital": "100",
    "order_limit": "10",
    "daily_orders": 24,
    "paper_fee": "0.006",
    "interval_seconds": 60,
    "require_gate": True,
    "reinforcement": "pnl",
}


def arm(return_pct, fees, fills, curve=(100.0, 99.0)):
    """One arm's summary. `curve` is a separate argument on purpose: a rebuilt curve can
    disagree with the return the accounts settled on, and the aggregate must not use it."""
    return {
        "return_pct": return_pct,
        "fees_paid": str(fees),
        "fills": fills,
        "final_equity": round(100 + return_pct, 6),
        "curve": list(curve),
    }


def write_live_run(
    root,
    run_id,
    gordon,
    warren,
    *,
    product="BTC-USDC",
    bar_seconds=BAR,
    engine="neural",
    kind="competition",
    rules=None,
    bars=10,
    salvaged=False,
    truncated=False,
    created=EPOCH,
    buy_hold=-0.5,
):
    """A run directory with the manifest `flyvsly live` writes (and the shape salvage keeps)."""
    run_dir = root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    run = {
        "id": run_id,
        "created": created,
        "label": f"live session {run_id}",
        "engine": engine,
        "kind": kind,
        "bar_seconds": bar_seconds,
        "bars": bars,
        "repeat": 0,
        "rule_preset": "upstream",
        "rules": dict(rules or RULES),
        "truncated": truncated or salvaged,
        "wall_mode": (
            "live, salvaged: this session was killed mid-flight and the recording was rebuilt"
            if salvaged
            else "live: one decision per completed market bar, paced by the exchange's clock"
        ),
        "live": {
            "product": product,
            "bar_seconds": bar_seconds,
            "warmup_bars": 120,
            "session_opened": created,
            "bars_traded": bars,
            "lag_seconds_at_end": 3.0,
            "polls": 20,
        },
    }
    if salvaged:
        run["salvaged"] = {
            "reason": "the session was killed before it could stop, so the recording was "
            "rebuilt"
        }
    summary = {
        "bars": bars,
        "initial_capital": "100",
        "truncated": truncated or salvaged,
        "arms": {"gordon": dict(gordon), "warren": dict(warren)},
        "benchmarks": {
            "buy_and_hold": {"initial_capital": "100", "curve": [100.0, 100 + buy_hold]},
            "cash": {"initial_capital": "100", "curve": [100.0, 100.0]},
        },
    }
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {"schema": "flyvsly.recording/v1", "run": run, "summary": summary, "arms": []}
        )
    )
    return run_dir


def report_for(root):
    return summarise_live(load_manifests(root))


def test_sessions_that_share_the_experiment_are_pooled_into_one_group(tmp_path):
    write_live_run(
        tmp_path, "live-a", arm(2.0, 1.0, 30), arm(1.0, 0.5, 20),
        created=EPOCH, bars=10, buy_hold=-0.25,
    )
    write_live_run(
        tmp_path, "live-b", arm(-1.0, 2.0, 40), arm(-0.5, 1.0, 25),
        created=EPOCH + 60, bars=20, buy_hold=0.75,
    )
    report = report_for(tmp_path)

    assert report["runs_read"] == 2
    assert report["live_sessions"] == 2
    assert len(report["groups"]) == 1
    group = report["groups"][0]
    # Newest first, and the group names the experiment it pooled.
    assert group["sessions"] == ["live-b", "live-a"]
    assert (group["product"], group["bar_seconds"], group["engine"]) == ("BTC-USDC", 60, "neural")

    pooled = group["pooled"]
    assert pooled["sessions"] == 2
    assert pooled["bars"] == 30
    assert pooled["span_seconds"] == 30 * BAR
    # One session one vote: (+1.0 and -0.5) / 2.
    assert pooled["mean_delta_pct"] == pytest.approx(0.25)
    assert pooled["delta_min_pct"] == pytest.approx(-0.5)
    assert pooled["delta_max_pct"] == pytest.approx(1.0)
    assert pooled["mean_gordon_pct"] == pytest.approx(0.5)
    assert pooled["mean_warren_pct"] == pytest.approx(0.25)
    assert pooled["mean_buy_hold_pct"] == pytest.approx(0.25)


def test_each_arm_reports_its_own_return_so_the_delta_is_the_paired_difference(tmp_path):
    write_live_run(tmp_path, "live-a", arm(2.0, 0.0, 1), arm(1.0, 0.0, 1))
    write_live_run(tmp_path, "live-b", arm(-1.0, 0.0, 1), arm(-0.5, 0.0, 1), created=EPOCH + 1)
    report = report_for(tmp_path)

    deltas = {row["id"]: row["delta_pct"] for row in report["sessions"]}
    assert deltas == {"live-a": pytest.approx(1.0), "live-b": pytest.approx(-0.5)}
    # ...and the win count is the sign of each session's own difference, not of the mean.
    pooled = report["groups"][0]["pooled"]
    assert (pooled["memory_on_wins"], pooled["memory_off_wins"], pooled["ties"]) == (1, 1, 0)


def test_win_count_splits_on_wins_losses_and_exact_ties(tmp_path):
    write_live_run(tmp_path, "live-up", arm(1.0, 0.0, 1), arm(0.0, 0.0, 1), created=EPOCH)
    write_live_run(tmp_path, "live-down", arm(0.0, 0.0, 1), arm(1.0, 0.0, 1), created=EPOCH + 1)
    write_live_run(tmp_path, "live-tie", arm(0.5, 0.0, 1), arm(0.5, 0.0, 1), created=EPOCH + 2)
    pooled = report_for(tmp_path)["groups"][0]["pooled"]

    assert pooled["memory_on_wins"] == 1
    assert pooled["memory_off_wins"] == 1
    assert pooled["ties"] == 1
    assert pooled["mean_delta_pct"] == pytest.approx(0.0)


def test_the_paired_difference_follows_the_arms_returns_not_their_curves(tmp_path):
    """A curve rebuilt from a killed session can disagree with the return on record; the
    aggregate must believe the return, which is what the session's own comparison used."""
    write_live_run(
        tmp_path, "live-odd",
        arm(1.5, 0.0, 1, curve=(100.0, 50.0)),
        arm(0.5, 0.0, 1, curve=(100.0, 200.0)),
    )
    report = report_for(tmp_path)

    assert report["sessions"][0]["delta_pct"] == pytest.approx(1.0)
    # Re-deriving from the curves would say (50/100 − 1) − (200/100 − 1) = −150.
    assert report["groups"][0]["pooled"]["mean_delta_pct"] == pytest.approx(1.0)


def test_fees_are_summed_per_arm_and_in_total(tmp_path):
    write_live_run(tmp_path, "live-a", arm(0.0, 1.25, 3), arm(0.0, 0.75, 2), created=EPOCH)
    write_live_run(
        tmp_path, "live-b", arm(0.0, 2.5, 6), arm(0.0, 1.5, 4), created=EPOCH + 1
    )
    pooled = report_for(tmp_path)["groups"][0]["pooled"]

    assert pooled["fees_gordon"] == pytest.approx(3.75)
    assert pooled["fees_warren"] == pytest.approx(2.25)
    assert pooled["fees_total"] == pytest.approx(6.0)
    assert pooled["fills_gordon"] == 9
    assert pooled["fills_warren"] == 6


def test_buy_and_hold_is_the_same_curve_benchmark_every_session_faced(tmp_path):
    write_live_run(tmp_path, "live-a", arm(0.0, 0.0, 0), arm(0.0, 0.0, 0), buy_hold=-2.0)
    write_live_run(
        tmp_path, "live-b", arm(0.0, 0.0, 0), arm(0.0, 0.0, 0), created=EPOCH + 1, buy_hold=-1.0
    )
    report = report_for(tmp_path)

    assert {row["id"]: row["buy_hold_pct"] for row in report["sessions"]} == {
        "live-a": pytest.approx(-2.0),
        "live-b": pytest.approx(-1.0),
    }
    assert report["groups"][0]["pooled"]["mean_buy_hold_pct"] == pytest.approx(-1.5)


def test_sessions_on_different_products_are_not_pooled(tmp_path):
    write_live_run(tmp_path, "live-btc", arm(1.0, 0.0, 1), arm(0.0, 0.0, 1), product="BTC-USDC")
    write_live_run(
        tmp_path, "live-eth", arm(-1.0, 0.0, 1), arm(0.0, 0.0, 1),
        product="ETH-USDC", created=EPOCH + 1,
    )
    report = report_for(tmp_path)

    assert report["live_sessions"] == 2
    assert len(report["groups"]) == 2
    assert "products" in report["pooling_note"]
    assert "BTC-USDC" in report["pooling_note"] and "ETH-USDC" in report["pooling_note"]
    # Each group's mean is that group's own session, never an average across the mismatch.
    means = {group["product"]: group["pooled"]["mean_delta_pct"] for group in report["groups"]}
    assert means == {"BTC-USDC": pytest.approx(1.0), "ETH-USDC": pytest.approx(-1.0)}


def test_sessions_on_different_bar_lengths_are_not_pooled(tmp_path):
    write_live_run(tmp_path, "live-60", arm(1.0, 0.0, 1), arm(0.0, 0.0, 1), bar_seconds=60)
    write_live_run(
        tmp_path, "live-300", arm(3.0, 0.0, 1), arm(0.0, 0.0, 1),
        bar_seconds=300, created=EPOCH + 1,
    )
    report = report_for(tmp_path)

    assert len(report["groups"]) == 2
    assert "bar lengths" in report["pooling_note"]
    assert "300s" in report["pooling_note"]


def test_sessions_under_different_rules_are_not_pooled_and_the_field_is_named(tmp_path):
    write_live_run(
        tmp_path, "live-wide", arm(1.0, 0.0, 1), arm(0.0, 0.0, 1),
        rules={**RULES, "order_limit": "10"},
    )
    write_live_run(
        tmp_path, "live-tight", arm(2.0, 0.0, 1), arm(0.0, 0.0, 1),
        rules={**RULES, "order_limit": "2"}, created=EPOCH + 1,
    )
    report = report_for(tmp_path)

    assert len(report["groups"]) == 2
    assert "rule sets" in report["pooling_note"]
    assert "order_limit" in report["pooling_note"]
    # A field that did not differ must not be blamed.
    assert "paper_fee" not in report["pooling_note"]
    assert [group["pooled"]["sessions"] for group in report["groups"]] == [1, 1]


def test_a_salvaged_session_is_marked_killed_rather_than_finished(tmp_path):
    write_live_run(
        tmp_path, "live-killed", arm(-1.0, 1.0, 5), arm(-0.5, 0.5, 3),
        salvaged=True, created=EPOCH,
    )
    write_live_run(
        tmp_path, "live-stopped", arm(-1.0, 1.0, 5), arm(-0.5, 0.5, 3), created=EPOCH + 1
    )
    report = report_for(tmp_path)
    rows = {row["id"]: row for row in report["sessions"]}

    assert rows["live-killed"]["salvaged"] is True
    assert rows["live-killed"]["ended"] == "salvaged"
    assert rows["live-killed"]["truncated"] is True
    assert rows["live-stopped"]["salvaged"] is False
    assert rows["live-stopped"]["ended"] == "stopped"
    assert report["groups"][0]["pooled"]["salvaged"] == 1

    text = live_text(report)
    assert "salvaged" in text and "stopped" in text


def test_a_stopped_session_cut_between_bars_is_not_a_salvage(tmp_path):
    """`truncated` also covers a stop that landed inside a backlog drain; the session stopped
    on purpose and must not be read as killed."""
    write_live_run(
        tmp_path, "live-cut", arm(0.0, 0.0, 1), arm(0.0, 0.0, 1), truncated=True
    )
    row = report_for(tmp_path)["sessions"][0]

    assert row["salvaged"] is False
    assert row["truncated"] is True
    assert row["ended"] == "stopped · short"


def test_a_session_that_traded_no_bars_is_excluded_with_its_reason(tmp_path):
    write_live_run(tmp_path, "live-empty", arm(0.0, 0.0, 0), arm(0.0, 0.0, 0), bars=0)
    report = report_for(tmp_path)

    assert report["live_sessions"] == 0
    assert report["groups"] == []
    assert [item["id"] for item in report["excluded"]] == ["live-empty"]
    assert "traded no bars" in report["excluded"][0]["reason"]


def test_a_session_without_a_recorded_rule_set_is_excluded_with_its_reason(tmp_path):
    run_dir = write_live_run(tmp_path, "live-norules", arm(0.0, 0.0, 1), arm(0.0, 0.0, 1))
    manifest = json.loads((run_dir / "manifest.json").read_text())
    del manifest["run"]["rules"]
    (run_dir / "manifest.json").write_text(json.dumps(manifest))
    report = report_for(tmp_path)

    assert report["live_sessions"] == 0
    assert "no rule set recorded" in report["excluded"][0]["reason"]


def test_recorded_seasons_without_a_live_block_are_not_live_sessions(tmp_path):
    run_dir = tmp_path / "season-a"
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "run": {"id": "season-a", "created": EPOCH, "engine": "neural", "rules": RULES},
                "summary": {"bars": 48, "arms": {}, "benchmarks": {}},
            }
        )
    )
    report = report_for(tmp_path)

    assert report["runs_read"] == 1
    assert report["live_sessions"] == 0
    assert report["groups"] == []
    assert report["excluded"] == []


def test_the_table_carries_the_projects_framing_that_a_handful_is_not_evidence(tmp_path):
    write_live_run(tmp_path, "live-a", arm(1.0, 0.0, 1), arm(0.0, 0.0, 1))
    report = report_for(tmp_path)

    assert "not evidence" in report["reading_note"]
    assert "not evidence" in live_markdown_table(report)
    assert "not evidence" in live_text(report)


def test_markdown_table_marks_the_salvage_and_keeps_groups_apart(tmp_path):
    write_live_run(tmp_path, "live-killed", arm(1.0, 1.0, 2), arm(0.0, 0.5, 1), salvaged=True)
    write_live_run(
        tmp_path, "live-eth", arm(2.0, 1.0, 2), arm(0.0, 0.5, 1),
        product="ETH-USDC", created=EPOCH + 1,
    )
    table = live_markdown_table(report_for(tmp_path))

    assert table.count("## ") == 2
    assert "| `live-killed` |" in table
    assert "| salvaged |" in table
    assert "Pooled (1 session)" in table


def test_the_command_reports_the_pool_human_json_and_markdown(tmp_path, capsys):
    write_live_run(
        tmp_path, "live-a", arm(2.0, 1.0, 30), arm(1.0, 0.5, 20), created=EPOCH, buy_hold=-0.25
    )
    write_live_run(
        tmp_path, "live-killed", arm(-1.0, 2.0, 40), arm(-0.5, 1.0, 25),
        created=EPOCH + 60, salvaged=True, buy_hold=0.75,
    )

    assert main(["live-report", "--runs", str(tmp_path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["live_sessions"] == 2
    assert payload["groups"][0]["pooled"]["fees_total"] == pytest.approx(4.5)
    assert payload["groups"][0]["pooled"]["mean_delta_pct"] == pytest.approx(0.25)

    assert main(["live-report", "--runs", str(tmp_path)]) == 0
    text = capsys.readouterr().out
    assert "live-a" in text and "live-killed" in text
    assert "salvaged" in text
    assert "pooled: mean paired delta" in text
    assert "not evidence" in text

    assert main(["live-report", "--runs", str(tmp_path), "--table"]) == 0
    table = capsys.readouterr().out
    assert table.startswith("# Live sessions")
    assert "| session |" in table

    written = tmp_path / "live-report.md"
    assert (
        main(["live-report", "--runs", str(tmp_path), "--table", "--write", str(written)]) == 0
    )
    assert "wrote" in capsys.readouterr().out
    assert "| session |" in written.read_text()


def test_the_command_names_why_it_will_not_pool_mismatched_sessions(tmp_path, capsys):
    write_live_run(tmp_path, "live-60", arm(1.0, 0.0, 1), arm(0.0, 0.0, 1), bar_seconds=60)
    write_live_run(
        tmp_path, "live-300", arm(1.0, 0.0, 1), arm(0.0, 0.0, 1),
        bar_seconds=300, created=EPOCH + 1,
    )

    assert main(["live-report", "--runs", str(tmp_path)]) == 0
    text = capsys.readouterr().out

    assert "not pooled across them" in text
    assert "bar lengths (60s, 300s)" in text
