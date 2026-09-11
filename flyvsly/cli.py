"""Fly vs. Fly command line.

    flyvsly doctor                 what this machine can run, and what it costs
    flyvsly prepare                download and compile the MaleCNS v1.0 graph
    flyvsly run                    one or more seasons of the competition
    flyvsly serve                  serve the browser experience with a live feed
    flyvsly list / report          what has been run, and what the repeats say

Paper trading only. No credentials, no account, no order leaves this process.
"""

import argparse
import datetime
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .config import (
    PRESETS,
    REINFORCEMENT_MODES,
    RUN_KINDS,
    ArenaConfig,
    ArenaRules,
    MarketSpec,
)

ROOT = Path(__file__).resolve().parent.parent


def _rules(args) -> ArenaRules:
    """Rule set for a run: a preset, with any explicit flag overriding it.

    Presets exist because upstream's caps are those of a live-trading experiment ($10 per
    order, 24 orders a day, a required spike gate). Paper runs can afford to be busier, and
    both flies get exactly the same rules either way.
    """
    preset = PRESETS[args.preset]
    return ArenaRules(
        products=tuple(args.products),
        capital=args.capital,
        order_limit=args.order_limit or preset["order_limit"],
        daily_orders=args.daily_orders or preset["daily_orders"],
        require_gate=preset["require_gate"] if args.require_gate is None else args.require_gate,
        reinforcement=args.reinforcement,
        paper_fee=args.paper_fee,
        decoder_threshold_hz=args.decoder_threshold_hz,
        neural_ms=args.neural_ms,
        readout=args.readout,
        readout_margin=args.readout_margin,
        population_sample=args.population_sample,
    ).validate()


def _starting(args) -> dict:
    """Per-arm starting weights from `--starting arm=spec` pairs, validated up front.

    Every spec is parsed here rather than when a brain is built, so a typo in a checkpoint
    path fails immediately instead of after ten minutes of simulation — and instead of
    silently running from the baseline on an engine that has no brain to load.
    """
    from .starting import parse

    specs = {"gordon": "baseline", "warren": "baseline"}
    for item in args.starting or []:
        if "=" not in item:
            raise SystemExit(f"--starting expects arm=spec, got {item!r}")
        arm, spec = item.split("=", 1)
        if arm not in specs:
            raise SystemExit(f"--starting expects an arm name in {sorted(specs)}, got {arm!r}")
        specs[arm] = spec
    for arm, spec in specs.items():
        try:
            parse(spec)
        except ValueError as error:
            raise SystemExit(f"--starting {arm}: {error}") from None
    return specs


def _market(args, repeat: int) -> MarketSpec:
    if args.market == "synthetic":
        return MarketSpec(kind="synthetic", product=args.products[0], bars=args.bars,
                          seed=args.seed + repeat * 7919, initial_price=args.initial_price)
    if args.start is None:
        # Disjoint repeat seasons: step a whole season further back each time. An explicit
        # window offset is how an exam is kept away from the season its brains trained on.
        return MarketSpec(
            kind="coinbase",
            product=args.products[0],
            bars=args.bars,
            window_offset_bars=int(args.window_offset) + repeat * args.bars,
        )
    start = datetime.datetime.fromisoformat(args.start.replace("Z", "+00:00"))
    start = start + datetime.timedelta(seconds=args.bars * 60 * repeat)
    return MarketSpec(
        kind="coinbase",
        product=args.products[0],
        bars=args.bars,
        start_iso=start.isoformat().replace("+00:00", "Z"),
    )


def _progress(kind, payload):
    if kind == "bar":
        arms = payload["arms"]
        parts = []
        for arm_id in ("gordon", "warren"):
            record = arms[arm_id]
            side = record["decision"]["side"]
            status = record["execution"]["status"]
            equity = record["portfolio"]["equity"]
            parts.append(f"{arm_id[:4]} {side:<7} {status:<7} {equity:>10}")
        print(
            f"[{payload['i'] + 1:>4}/{payload['bars']}] "
            f"mid {payload['market']['mid']:.2f}  " + " | ".join(parts)
            + f"  ({payload['elapsed']:.1f}s)",
            flush=True,
        )
    elif kind == "arms_ready":
        for arm in payload["arms"]:
            print(
                f"  {arm['name']}: {arm['role_label']} — {arm['backend']['label']}",
                flush=True,
            )
    elif kind == "recording":
        summary = payload["summary"]
        print(
            f"  done: gordon {summary['arms']['gordon']['return_pct']:+.3f}%  "
            f"warren {summary['arms']['warren']['return_pct']:+.3f}%  "
            f"buy&hold {_bench_pct(summary['benchmarks']['buy_and_hold']):+.3f}%",
            flush=True,
        )


def _bench_pct(benchmark):
    curve = benchmark["curve"]
    initial = float(benchmark["initial_capital"])
    return (curve[-1] / initial - 1) * 100 if initial else 0.0


def cmd_doctor(args):
    import numpy

    total_ram = _total_ram_gb()
    data = Path(args.data)
    graph = data / "graph.npz"
    report = {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "ram_gb": round(total_ram, 1) if total_ram else None,
        "python": sys.version.split()[0],
        "numpy": numpy.__version__,
        "c_compiler": shutil.which("c++") or shutil.which("g++") or None,
        "graph_present": graph.exists(),
        "graph_bytes": graph.stat().st_size if graph.exists() else 0,
        "gpu_acceleration": "not used: the vendored kernel integrates on the CPU",
        "measured_on_apple_m2_8gb": {
            "preparation_peak_gb": 1.33,
            "two_arms_resident_gb": 0.7,
            "observation_seconds_cold_cache": [2.2, 8.0],
            "observation_seconds_warm_cache": 2.2,
            "full_season_48_bars_seconds": [320, 660],
            "note": (
                "Two 500 ms observations run concurrently on separate threads, so a bar "
                "costs roughly one arm's time on an 8-core machine. Wall time, not memory, "
                "bounds the season length. `--measure` times this machine instead."
            ),
        },
    }
    if total_ram and total_ram < 4:
        report["warning"] = "Less than 4 GB RAM: the full graph will not fit."
    if args.measure and graph.exists():
        report["measured"] = _measure()
    print(json.dumps(report, indent=2))
    return 0


def _total_ram_gb():
    try:
        if sys.platform == "darwin":
            return int(
                subprocess.check_output(["sysctl", "-n", "hw.memsize"]).strip()
            ) / 2**30
        pages = os.sysconf("SC_PHYS_PAGES")
        size = os.sysconf("SC_PAGE_SIZE")
        return pages * size / 2**30
    except Exception:
        return None


def _measure():
    """One real observation, measured now. Never part of a recorded run."""
    import resource

    from stonkfly.display import market_frame

    from .backends.neural import NeuralBackend
    from .fairness import arm_settings
    from .market import build_season

    settings = arm_settings(ArenaRules(), True)
    season = build_season(MarketSpec(kind="synthetic", bars=4, seed=1))
    quote = season.quote(0)
    frame = market_frame("BTC-USDC", season.history(0), quote.bid, quote.ask)
    started = time.perf_counter()
    backend = NeuralBackend(settings, data_root="data")
    build = time.perf_counter() - started
    started = time.perf_counter()
    out = backend.observe(frame, "none")
    observe = time.perf_counter() - started
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {
        "brain_build_seconds": round(build, 2),
        "observation_seconds": round(observe, 2),
        "total_spikes": out["total_spikes"],
        "KC_spikes": out["KC_spikes"],
        "side": out["side"],
        "process_peak_rss_gb": round(peak / (2**30 if sys.platform == "darwin" else 2**20), 2),
    }


def cmd_prepare(args):
    from stonkfly.data import prepare, verify

    started = time.perf_counter()
    prepare(args.reuse_doomfly)
    print(
        f"prepare: {time.perf_counter() - started:.1f}s, verified {json.dumps(verify())}",
        flush=True,
    )
    return 0


def cmd_run(args):
    from .arena import Arena
    from .market import build_season

    starting = _starting(args)
    if args.engine != "neural" and (
        args.kind != "competition" or any(spec != "baseline" for spec in starting.values())
    ):
        print(
            "exam, reset and trained starting weights need the neural engine: the procedural "
            "demo has no brain to carry anything between seasons.",
            file=sys.stderr,
        )
        return 2
    if args.readout and args.engine != "neural":
        print("a fitted readout decides from population vectors, which need the neural engine.", file=sys.stderr)
        return 2
    if args.kind != "competition" and all(spec == "baseline" for spec in starting.values()):
        print(
            f"A {args.kind} run needs trained weights: --starting gordon=trained:<checkpoint>",
            file=sys.stderr,
        )
        return 2
    config = ArenaConfig(
        kind=args.kind,
        starting=starting,
        save_brains=Path(args.save_brains) if args.save_brains else None,
        must_not_overlap=args.must_not_overlap,
        rules=_rules(args),
        market=_market(args, 0),
        engine=args.engine,
        repeats=args.repeats,
        out=Path(args.out),
        label=args.label,
        max_wall_seconds=args.max_wall_seconds,
        preset=args.preset,
        shuffle_reference=args.shuffle_reference,
        shuffle_seed=args.shuffle_seed,
    )
    if args.engine == "neural":
        graph = Path(args.data) / "graph.npz"
        if not graph.exists():
            print(
                "Neural engine needs the compiled graph. Run: flyvsly prepare",
                file=sys.stderr,
            )
            return 2
    label = args.label or f"{args.engine}:{config.market.describe()}"
    for repeat in range(args.repeats):
        market = _market(args, repeat)
        season = build_season(market)
        arena = Arena(config, on_event=_progress if not args.quiet else None, data_root=args.data)
        run_id = time.strftime("%Y%m%d-%H%M%S", time.localtime()) + f"-r{repeat}"
        print(
            f"run {run_id}: {label} | {market.describe()} | bars={market.bars} "
            f"engine={config.engine}",
            flush=True,
        )
        recording = arena.run(run_id=run_id, season=season, repeat=repeat)
        _write_batch(args.out, label, recording, repeat)
    if args.repeats > 1:
        from .report import load_manifests, summarise

        print(json.dumps(summarise(load_manifests(args.out))["groups"][0], indent=2))
    return 0


def _write_batch(out, label, recording, repeat):
    path = Path(out) / "batches" / f"{label.replace('/', '_')}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.loads(path.read_text()) if path.exists() else {"label": label, "runs": []}
    payload["runs"] = [r for r in payload["runs"] if r["id"] != recording["run"]["id"]]
    payload["runs"].append(
        {
            "id": recording["run"]["id"],
            "repeat": repeat,
            "season": recording["run"]["season"],
            "comparison": recording["summary"]["comparison"],
            "arms": {
                k: {
                    "return_pct": v["return_pct"],
                    "final_equity": v["final_equity"],
                    "fills": v["fills"],
                }
                for k, v in recording["summary"]["arms"].items()
            },
            "benchmarks": {
                "buy_and_hold_pct": _bench_pct(recording["summary"]["benchmarks"]["buy_and_hold"])
            },
        }
    )
    path.write_text(json.dumps(payload, indent=1) + "\n")


def cmd_serve(args):
    from .server import serve

    return serve(
        port=args.port,
        runs=Path(args.runs),
        web=Path(args.web),
        data=args.data,
        open_browser=not args.no_open,
    )


def cmd_live(args):
    """Trade the live market on paper, one decision per completed bar, until stopped.

    The session is a real one: the bars come from the public Coinbase candle endpoint as the
    exchange closes them, and both flies decide on them with the same rules a recorded season
    uses. Nothing here can place an order — there is no key, no account and no order path.
    """
    import threading

    from .server import RunHub, http_server, load_manifests

    runs = Path(args.runs)
    hub = RunHub(runs, args.data)
    options = {
        "engine": args.engine,
        "product": args.product,
        "bar_seconds": args.bar_seconds,
        "bars": args.bars,
        "warmup": args.warmup,
        "poll_seconds": args.poll,
        "source": args.source,
        "capital": args.capital,
        "order_limit": args.order_limit,
        "daily_orders": args.daily_orders,
        "require_gate": not args.gate_off,
        "reinforcement": args.reinforcement,
        "neural_ms": args.neural_ms,
        "label": args.label or f"live {args.product} {args.bar_seconds}s",
    }

    httpd = None
    if args.serve:
        httpd = http_server(hub, args.port, Path(args.web))
        threading.Thread(target=httpd.serve_forever, daemon=True, name="http").start()
        print(
            f"watching live at http://127.0.0.1:{args.port}/  "
            f"(recordings: {len(load_manifests(runs))})\n"
            f"stop with Ctrl-C, POST /api/run/stop, or the stop file",
            flush=True,
        )
    else:
        print(
            f"live session: {options['label']} · engine={args.engine} · "
            f"one decision per {args.bar_seconds}s bar · paper only\n"
            f"stop with Ctrl-C or the stop file",
            flush=True,
        )

    def stop_file_seen():
        return bool(args.stop_file) and Path(args.stop_file).exists()

    hub.start_live(options)
    try:
        while hub.thread and hub.thread.is_alive():
            if stop_file_seen():
                print("\nstop file seen: finishing this bar and writing the recording", flush=True)
                hub.stop_live()
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\nstopping after this bar…", flush=True)
        hub.stop_live()
        hub.thread.join(timeout=600)
    finally:
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()

    state = hub.state
    if state.get("status") == "failed":
        print(f"live session failed: {state.get('error')}", flush=True)
        return 1
    summary = state.get("summary") or {}
    comparison = summary.get("comparison") or {}
    print(
        f"live session finished: {summary.get('bars', 0)} bars traded · "
        f"{state.get('run_id')} · leader {comparison.get('leader', 'n/a')}",
        flush=True,
    )
    return 0


def cmd_publish(args):
    """Copy recordings next to the web bundle so a static host has real data to show."""
    import shutil

    from .report import load_manifests, summarise

    web = Path(args.web)
    web.mkdir(parents=True, exist_ok=True)
    manifests = load_manifests(args.runs)
    if args.neural_only:
        manifests = [m for m in manifests if m["run"].get("engine") == "neural"]
    chosen = manifests[: args.limit]
    listings = []
    for manifest in chosen:
        source = Path(manifest["path"]) / "recording.json"
        if not source.exists():
            continue
        shutil.copyfile(source, web / f"{manifest['id']}.json")
        summary = manifest.get("summary") or {}
        arms = summary.get("arms") or {}
        benchmarks = summary.get("benchmarks") or {}
        listings.append(
            {
                "id": manifest["id"],
                "label": manifest["run"].get("label"),
                "engine": manifest["run"].get("engine"),
                "season": manifest["run"].get("season"),
                "market": (manifest["run"].get("season") or "").split(":")[0],
                "bars": summary.get("bars"),
                "created": manifest["run"].get("created"),
                "duration_seconds": summary.get("duration_seconds"),
                "seconds_per_bar_mean": summary.get("seconds_per_bar_mean"),
                "truncated": summary.get("truncated", False),
                "inputs_identical_every_bar": manifest["run"].get(
                    "inputs_identical_every_bar"
                ),
                "returns": {
                    "gordon": (arms.get("gordon") or {}).get("return_pct"),
                    "warren": (arms.get("warren") or {}).get("return_pct"),
                    "buy_and_hold": _bench_pct(benchmarks.get("buy_and_hold") or {"curve": [], "initial_capital": "1"}),
                },
                "memory_changed_edges": (
                    (arms.get("gordon") or {}).get("final_memory") or {}
                ).get("changed_edges"),
            }
        )
    (web / "index.json").write_text(json.dumps(listings, indent=1) + "\n")
    (web / "report.json").write_text(
        json.dumps(summarise(manifests), indent=1) + "\n"
    )
    print(f"published {len(listings)} recording(s) to {web}")
    return 0


def cmd_fitreadout(args):
    """Fit a readout from recorded population vectors, on one arm's own brain activity.

    The training data is real recordings, so this command only works after a run has stored
    population vectors: `--population-sample` is on by default, so any neural run already has
    them. The default arm is the control, because a readout fitted on the arm that is being
    changed by the memory rule would be entangled with the thing under test.
    """
    from .readout import fit
    from .report import load_manifests

    wanted = set(args.label)
    candidates = [
        manifest
        for manifest in load_manifests(args.runs)
        if manifest["run"].get("label") in wanted
    ]
    if not candidates:
        print(f"no recordings labelled {sorted(wanted)} under {args.runs}", file=sys.stderr)
        return 2

    # One recording per (market season, fly, starting weights). The same brain on the same
    # market produces identical rows, so pooling repeats of an experiment puts copies of the
    # holdout into the training set — which showed up as a perfect holdout, i.e. leakage
    # dressed as a result. Newest recording per group wins.
    groups: dict[tuple, dict] = {}
    duplicates = []
    for manifest in candidates:
        recording = json.loads((Path(manifest["path"]) / "recording.json").read_text())
        arm_meta = next((a for a in recording["arms"] if a["id"] == args.arm), None)
        if arm_meta is None:
            continue
        starting = (arm_meta.get("starting_weights") or {}).get("label") or "baseline"
        key = (recording["run"]["season"], args.arm, starting)
        # `load_manifests` returns newest first, so the first one seen for a key is the newest
        # and the later ones are the repeats that must be dropped — not the other way round.
        if key in groups:
            duplicates.append(recording["run"]["id"])
            continue
        groups[key] = recording

    vectors: list[list[int]] = []
    closes: list[float] = []
    boundaries: list[int] = []
    used = []
    # Chronological by the season's own first bar, so the temporal split inside the fit is a
    # split in market time rather than in file order.
    for key, recording in sorted(
        groups.items(), key=lambda item: item[1]["season"]["bars"][0]["t"]
    ):
        bars = recording["season"]["bars"]
        season_vectors, season_closes = [], []
        for observation in recording["observations"]:
            signal = observation["arms"].get(args.arm, {}).get("signal")
            if not signal or "population" not in signal:
                continue
            season_vectors.append(signal["population"])
            season_closes.append(bars[observation["i"]]["mid"])
        if len(season_vectors) <= args.horizon:
            continue
        # No trimming here: `fit` is told where the seasons begin and drops the samples whose
        # forward return would cross into the next one. Trimming the tails was not enough —
        # the seasons stay adjacent in the pool, so the boundary label still crossed.
        boundaries.append(len(vectors))
        vectors.extend(season_vectors)
        closes.extend(season_closes)
        used.append(
            {
                "run": recording["run"]["id"],
                "season": recording["run"]["season"],
                "starting_weights": key[2],
                "bars": len(season_vectors),
            }
        )

    if len(vectors) < args.minimum_bars:
        print(
            f"only {len(vectors)} usable bars for a readout (need {args.minimum_bars}); "
            "run more seasons and try again",
            file=sys.stderr,
        )
        return 2

    model = fit(
        vectors,
        closes,
        horizon=args.horizon,
        trained_on=f"{'+'.join(sorted(wanted))}:{args.arm}",
        boundaries=boundaries,
    )

    # The margin decides how often the readout acts. It belongs to the model's own score scale
    # (log-odds, so values well above 1 are normal), and it is stored with the model so a run
    # that does not pass --readout-margin inherits a sensible band instead of a stale 0.15.
    import numpy as np

    scores = np.abs(np.asarray([model.score(vector) for vector in vectors], dtype=np.float64))
    percentiles = {
        f"p{value}": round(float(np.percentile(scores, value)), 4) for value in (50, 60, 75, 90)
    }
    model.metrics["absolute_score_percentiles"] = percentiles
    model.save(Path(args.out))

    description = model.describe()
    description["fitted_on"] = used
    description["usable_bars"] = len(vectors)
    description["distinct_seasons"] = len(used)
    description["dropped_cross_boundary_samples"] = model.metrics.get(
        "dropped_cross_boundary_samples", 0
    )
    description["season_boundaries"] = model.metrics.get("season_boundaries", 1)
    if duplicates:
        description["skipped_as_repeated_experiments"] = duplicates

    description["absolute_score_percentiles"] = percentiles
    description["suggested_margin"] = percentiles["p60"]

    # A saturated fit is the normal failure mode here: 256 features against a few dozen bars
    # will always separate the training set. Say so, because the metrics alone invite a reader
    # to believe the holdout number.
    train = description.get("train", {})
    holdout = description.get("holdout", {})
    warnings = []
    if train.get("bars", 0) <= description.get("features", 0) * 2:
        warnings.append(
            f"only {train.get('bars')} training bars for {description.get('features')} "
            "features: this fit is a demonstration of the pipeline, not a model to trust"
        )
    if train.get("accuracy", 0) >= 0.999:
        warnings.append("training accuracy is 1.0, which means the model memorised the bars")
    if len(used) < 2:
        warnings.append(
            "fewer than two distinct seasons: the holdout comes from the same market as the "
            "training data, so it is not an out-of-sample test"
        )
    if holdout.get("bars", 0) < 30:
        warnings.append(
            f"the holdout is {holdout.get('bars')} bars, far too few to tell a signal from noise"
        )
    if warnings:
        description["warnings"] = warnings
    print(json.dumps(description, indent=2))
    return 0


def cmd_list(args):


    from .report import load_manifests

    for manifest in load_manifests(args.runs):
        summary = manifest.get("summary") or {}
        arms = summary.get("arms") or {}
        print(
            f"{manifest['id']}  {manifest['run'].get('engine','?'):<10} "
            f"{manifest['run'].get('season','?'):<46} "
            f"gordon {(arms.get('gordon') or {}).get('return_pct', 0):+7.2f}%  "
            f"warren {(arms.get('warren') or {}).get('return_pct', 0):+7.2f}%"
        )
    return 0


def cmd_report(args):
    from .report import load_manifests, markdown_table, summarise

    report = summarise(load_manifests(args.runs))
    if args.json:
        print(json.dumps(report, indent=2))
        return 0
    if args.table:
        table = markdown_table(report)
        if args.write:
            path = Path(args.write)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(table + "\n")
            print(f"wrote {path}")
        else:
            print(table)
        return 0
    for group in report["groups"]:
        print(
            f"\n{group['label']}  ({group['repeats']} season(s), engine={group['engine']})\n"
            f"  mean return   gordon {group['mean_gordon_pct']:+.3f}%   "
            f"warren {group['mean_warren_pct']:+.3f}%   "
            f"buy&hold {group['mean_buy_hold_pct']:+.3f}%\n"
            f"  paired delta (gordon − warren): mean {group['mean_delta_pct']:+.3f}%  "
            f"spread {group['delta_spread_pct']:.3f}%  "
            f"range [{group['delta_min_pct']:+.3f}, {group['delta_max_pct']:+.3f}]\n"
            f"  memory-on wins {group['memory_on_wins']} / "
            f"memory-off wins {group['memory_off_wins']} / ties {group['ties']}\n"
            f"  mean changed KC→MBON efficacies (memory-on): {group['mean_changed_edges']}"
        )
    print(f"\n{report['reading_note']}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="flyvsly", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", help="environment and cost report")
    doctor.add_argument("--data", default="data")
    doctor.add_argument("--measure", action="store_true", help="build a brain and time one observation")
    doctor.set_defaults(func=cmd_doctor)

    prepare = sub.add_parser("prepare", help="download and compile MaleCNS v1.0")
    prepare.add_argument("--reuse-doomfly", type=Path)
    prepare.set_defaults(func=cmd_prepare)

    run = sub.add_parser("run", help="run the competition (paper only)")
    run.add_argument("--engine", choices=["neural", "procedural"], default="neural")
    run.add_argument("--market", choices=["synthetic", "coinbase"], default="synthetic")
    run.add_argument("--bars", type=int, default=48)
    run.add_argument("--repeats", type=int, default=1)
    run.add_argument("--seed", type=int, default=7)
    run.add_argument(
        "--start",
        default=None,
        help="coinbase window start (ISO 8601); omit to anchor to the newest available data",
    )
    run.add_argument("--initial-price", default="60000")
    run.add_argument("--label")
    run.add_argument(
        "--preset",
        choices=sorted(PRESETS),
        default="upstream",
        help="upstream keeps Stonkfly's conservative caps; active is a busier paper-only rule set",
    )
    run.add_argument(
        "--kind",
        choices=RUN_KINDS,
        default="competition",
        help="competition: one fly learns. exam/reset: both frozen, the brain is the difference",
    )
    run.add_argument(
        "--starting",
        nargs="+",
        # `extend`, not the default: a second --starting flag must add to the first rather than
        # replace it, which silently dropped one arm's weights and left the run refusing to
        # start with a message about the arm that was still there.
        action="extend",
        default=None,
        help=(
            "per-arm starting weights, either as one flag with both pairs "
            "(--starting gordon=trained:c.npz warren=baseline) or as two flags"
        ),
    )
    run.add_argument("--save-brains", default=None, help="directory to checkpoint both brains into")
    run.add_argument(
        "--must-not-overlap",
        default=None,
        help="run id or label this season must not share bars with (an exam must be unseen)",
    )
    run.add_argument(
        "--window-offset",
        type=int,
        default=0,
        help="coinbase: how many completed bars further back the season ends",
    )
    run.add_argument("--readout", default=None, help="fitted readout JSON to decide from")
    run.add_argument(
        "--readout-margin",
        type=float,
        default=None,
        help="HOLD band on the model's own score scale; defaults to the value stored with the model",
    )
    run.add_argument("--population-sample", type=int, default=256)
    run.add_argument(
        "--reinforcement",
        choices=REINFORCEMENT_MODES,
        default="pnl",
        help="pnl is upstream's own-outcome pulse; decoy/shuffled/none are the controls",
    )
    run.add_argument("--shuffle-reference", default=None, help="run id whose pulse schedule to permute")
    run.add_argument("--shuffle-seed", type=int, default=0)
    run.add_argument("--require-gate", action=argparse.BooleanOptionalAction, default=None)
    run.add_argument("--out", default="runs")
    run.add_argument("--data", default="data")
    run.add_argument("--products", nargs="+", default=["BTC-USDC"])
    run.add_argument("--capital", default="100")
    run.add_argument("--order-limit", default=None)
    run.add_argument("--paper-fee", default="0.006")
    run.add_argument("--daily-orders", type=int, default=None)
    run.add_argument("--decoder-threshold-hz", type=float, default=2)
    run.add_argument("--neural-ms", type=float, default=500)
    run.add_argument("--max-wall-seconds", type=float, default=None)
    run.add_argument("--quiet", action="store_true")
    run.set_defaults(func=cmd_run)

    serve = sub.add_parser("serve", help="serve the web experience with live runs")
    serve.add_argument("--port", type=int, default=7777)
    serve.add_argument("--runs", default="runs")
    serve.add_argument("--web", default="web/dist")
    serve.add_argument("--data", default="data")
    serve.add_argument("--no-open", action="store_true")
    serve.set_defaults(func=cmd_serve)

    live = sub.add_parser(
        "live", help="trade the live market on paper, one decision per completed bar"
    )
    live.add_argument("--engine", choices=["neural", "procedural"], default="neural")
    live.add_argument("--product", default="BTC-USDC")
    live.add_argument(
        "--source",
        choices=["kraken", "coinbase"],
        default="kraken",
        help=(
            "where the bars come from; Coinbase Exchange has delisted every pair upstream"
            " allows, so Kraken is the default that can actually close a bar"
        ),
    )
    live.add_argument("--bar-seconds", type=int, default=60, help="bar length, and the cadence")
    live.add_argument(
        "--bars",
        type=int,
        default=48,
        help="season length the rules were validated against; a live session grows past it",
    )
    live.add_argument(
        "--warmup",
        type=int,
        default=120,
        help="completed bars the fly's first chart shows before the session starts trading",
    )
    live.add_argument("--poll", type=float, default=15.0, help="seconds between candle polls")
    live.add_argument("--capital", default="100")
    live.add_argument("--order-limit", default="10")
    live.add_argument("--daily-orders", type=int, default=24)
    live.add_argument("--neural-ms", type=float, default=500)
    live.add_argument(
        "--reinforcement",
        choices=["pnl", "none"],
        default="pnl",
        help="decoy/shuffled need a whole season up front, which a live session does not have",
    )
    live.add_argument("--gate-off", action="store_true", help="drop DNpe017's spike gate")
    live.add_argument("--label")
    live.add_argument("--runs", default="runs")
    live.add_argument("--data", default="data")
    live.add_argument(
        "--stop-file",
        default=None,
        help="stop when this file appears (checked between bars)",
    )
    live.add_argument("--serve", action="store_true", help="also serve the web experience")
    live.add_argument("--port", type=int, default=7777)
    live.add_argument("--web", default="web/dist")
    live.set_defaults(func=cmd_live)

    fitreadout = sub.add_parser(
        "fitreadout", help="fit a readout model from recorded population vectors"
    )
    fitreadout.add_argument("--runs", default="runs")
    fitreadout.add_argument(
        "--label",
        required=True,
        # Repeatable: the readout pools every recording that has population vectors, which is
        # how a fit gets more bars than the ~35 that a single season can offer.
        action="append",
        help="label of a recording to train on; repeat to pool several",
    )
    fitreadout.add_argument("--arm", default="warren", help="arm whose activity trains the model")
    fitreadout.add_argument("--out", default="models/readout.json")
    fitreadout.add_argument("--horizon", type=int, default=1, help="bars ahead to predict")
    fitreadout.add_argument("--minimum-bars", type=int, default=120)
    fitreadout.set_defaults(func=cmd_fitreadout)

    publish = sub.add_parser(
        "publish", help="copy recordings next to the web bundle for a static host"
    )
    publish.add_argument("--runs", default="runs")
    publish.add_argument("--web", default="web/public/recordings")
    publish.add_argument("--limit", type=int, default=8)
    publish.add_argument("--neural-only", action="store_true")
    publish.set_defaults(func=cmd_publish)

    listing = sub.add_parser("list", help="list recordings")
    listing.add_argument("--runs", default="runs")
    listing.set_defaults(func=cmd_list)

    report = sub.add_parser("report", help="aggregate repeated seasons")
    report.add_argument("--runs", default="runs")
    report.add_argument("--json", action="store_true")
    report.add_argument("--table", action="store_true", help="markdown table of every group")
    report.add_argument("--write", default=None, help="write the table to this path")
    report.set_defaults(func=cmd_report)

    args = parser.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    raise SystemExit(main())
