"""Finish a live session that was killed rather than stopped.

A clean stop runs `_finalise`, which writes `recording.json` and `manifest.json` from state
that lived in memory: the equity curves, the trade list, the arm descriptions. A kill — Docker
going down, a power cut, `docker rm -f` — takes all of that with it, and because the app lists
runs by their manifest, the session becomes invisible in the browser even though every traded
bar is on disk.

It is not lost, though. The session wrote its observations and a checkpoint as it traded, so
everything a recording needs can be rebuilt from them: the bars, the curves, the fills, the
fees. What cannot be rebuilt is whatever only lived in memory, and this module says exactly
which parts those are rather than dressing a guess up as a recording.

A kill is not necessarily the end of the session, either. `load_resume` reads the same two
artifacts as a *continuation* rather than a conclusion — for `flyvsly live --resume` — and the
line between the two is the same line throughout: what the session wrote down survives, and
what was only in memory is stated as lost rather than reconstructed.
"""

import dataclasses
import json
import sqlite3
from decimal import Decimal
from pathlib import Path

from .arena import DISCLAIMERS, SCHEMA_VERSION, arm_settings, personas_for
from .benchmark import buy_and_hold, cash
from .config import ArenaRules, MarketSpec
from .live import LIVE_DEFAULTS, observed_bars
from .market import Season, _iso
from .starting import StartingWeights
from .telemetry import summarise_curve, write_jsonl, write_recording


def _bars_from_observations(observations) -> tuple[list[float], list[int]]:
    bars = observed_bars(observations)
    return [close for _, close in bars], [opened for opened, _ in bars]


def _trades_for(arm_id: str, observations) -> list[dict]:
    trades = []
    for observation in observations:
        record = (observation.get("arms") or {}).get(arm_id) or {}
        execution = record.get("execution") or {}
        fill = execution.get("fill")
        if not fill:
            continue
        signal = record.get("signal") or {}
        memory = signal.get("memory") or {}
        trades.append(
            {
                "i": observation["i"],
                "t": observation["t"],
                "side": (record.get("decision") or {}).get("side"),
                "product": observation.get("product"),
                "base_size": fill.get("base"),
                "price": fill.get("price"),
                "quote_size": fill.get("quote"),
                "fee": fill.get("fee"),
                "reason": execution.get("reason"),
                "equity_after": (record.get("portfolio") or {}).get("equity"),
                "memory_changed_edges": memory.get("changed_edges"),
            }
        )
    return trades


def _arm_summary(arm_id: str, observations, initial: float, rules, season) -> dict:
    curve = [float(((o.get("arms") or {}).get(arm_id) or {}).get("portfolio", {}).get("equity", initial)) for o in observations]
    last = ((observations[-1].get("arms") or {}).get(arm_id) or {}) if observations else {}
    portfolio = last.get("portfolio") or {}
    signal = last.get("signal") or {}
    # A blocked bar is a bar the guard refused before any observation was taken; `_finalise`
    # reports `arm.stats["blocked"]`, so a reconstruction that hard-codes zero would misreport
    # exactly the halted sessions salvage exists for — a loss-stop halt blocks every remaining bar.
    blocked = sum(
        1
        for o in observations
        if (((o.get("arms") or {}).get(arm_id) or {}).get("decision") or {}).get("side") == "BLOCKED"
    )
    memories = [
        ((o.get("arms") or {}).get(arm_id) or {}).get("signal", {}).get("memory")
        for o in observations
        if ((o.get("arms") or {}).get(arm_id) or {}).get("signal", {}).get("memory")
    ]
    exposure = sum(
        1
        for o in observations
        if float((((o.get("arms") or {}).get(arm_id) or {}).get("portfolio") or {}).get("positions", {}).get(season.spec.product, 0) or 0) > 0
    )
    fills = int(portfolio.get("fills", 0) or 0)
    fees = Decimal(str(portfolio.get("fees_paid", "0") or "0"))
    trades = _trades_for(arm_id, observations)
    return {
        "curve": curve,
        **summarise_curve(curve, initial),
        "fills": fills,
        "vetoes": int(portfolio.get("vetoes", 0) or 0),
        "blocked_bars": blocked,
        "fees_paid": str(fees),
        "halted": portfolio.get("halted"),
        "exposure_bars": exposure,
        "trades": trades,
        "deployment": {
            "bars": season.bars,
            "bars_holding": exposure,
            "holding_fraction": round(exposure / season.bars, 4) if season.bars else 0.0,
            "order_limit_usdc": str(rules.order_limit),
            "daily_order_limit": rules.daily_orders,
            "cooldown_seconds": rules.interval_seconds,
        },
        "final_memory": memories[-1] if memories else None,
    }


def salvage_run(run_dir, force: bool = False) -> dict:
    """Rebuild a killed live session's recording from its checkpoint and observations.

    Refuses to touch a run that already finished: a real recording from a clean stop is
    evidence, and this is a reconstruction.
    """
    run_dir = Path(run_dir)
    checkpoint_path = run_dir / "live_state.json"
    observations_path = run_dir / "observations.jsonl"
    if (run_dir / "recording.json").exists() and not force:
        raise FileExistsError(f"{run_dir.name} already has a recording.json; pass force to replace it")
    if not checkpoint_path.exists() or not observations_path.exists():
        raise FileNotFoundError(f"{run_dir.name} has no checkpoint and observations to salvage")

    checkpoint = json.loads(checkpoint_path.read_text())
    observations = [json.loads(line) for line in observations_path.read_text().splitlines() if line.strip()]
    if not observations:
        raise ValueError(f"{run_dir.name} traded no bars, so there is nothing to reconstruct")

    rules_data = checkpoint.get("rules")
    rules = ArenaRules(**rules_data).validate() if rules_data else ArenaRules().validate()
    spec = MarketSpec(
        kind="coinbase",
        product=str(checkpoint.get("product", LIVE_DEFAULTS["product"])),
        bars=len(observations),
        bar_seconds=int(checkpoint.get("bar_seconds", LIVE_DEFAULTS["bar_seconds"])),
    )
    closes, times = _bars_from_observations(observations)
    # `Season` always keeps at least one warm-up bar in front of the tradable window, and a
    # live session's warm-up was never written down. One placeholder — the first observed
    # bar's own mid — keeps bar i meaning observation i, which is what the curves and the
    # benchmarks are aligned to.
    placeholder_close, placeholder_time = closes[0], times[0] - spec.bar_seconds
    season = Season(
        spec,
        [placeholder_close] + closes,
        [placeholder_time] + times,
        {
            "source": checkpoint.get("venue", "unknown"),
            "mode": "live",
            "rebuilt_from": "observations.jsonl",
            "note": (
                "bar mids exactly as they were observed live; the warm-up that preceded the "
                "session was not stored and one placeholder bar stands in for it"
            ),
        },
    )

    recorded_arms = checkpoint.get("arms")
    if recorded_arms:
        arms = recorded_arms
        arm_metadata = "recorded in the checkpoint"
    else:
        personas = personas_for(str(checkpoint.get("kind", LIVE_DEFAULTS["kind"])))
        arms = []
        for persona in personas:
            settings = arm_settings(rules, persona["learning"])
            arms.append(
                {
                    "id": persona["id"],
                    "name": persona["name"],
                    "role": persona["role"],
                    "role_label": persona["role_label"],
                    "tagline": persona["tagline"],
                    "accent": persona["accent"],
                    "accent_soft": persona["accent_soft"],
                    "detail": persona["detail"],
                    "learning": persona["learning"],
                    "starting_capital": str(rules.capital),
                    "starting_weights": None,
                    "starting_report": None,
                    "settings": dataclasses.asdict(settings),
                    "settings_signature": settings.signature(),
                    "backend": {"rebuilt": True, "label": "Rebuilt from the live defaults after a kill."},
                }
            )
        arm_metadata = "rebuilt from the live defaults: this checkpoint predates the metadata"

    initial = float(rules.capital)
    summary = {
        "bars": len(observations),
        "initial_capital": str(Decimal(rules.capital)),
        "duration_seconds": int(observations[-1]["t"]) - int(observations[0]["t"]) + spec.bar_seconds,
        "seconds_per_bar_mean": round(
            sum(float(((o.get("arms") or {}).get("gordon") or {}).get("compute_seconds", 0) or 0) for o in observations)
            / len(observations),
            4,
        ),
        "truncated": True,
        "arms": {arm["id"]: _arm_summary(arm["id"], observations, initial, rules, season) for arm in arms},
        "benchmarks": {
            "buy_and_hold": buy_and_hold(season, Decimal(rules.capital), Decimal(rules.paper_fee)),
            "cash": cash(season, Decimal(rules.capital)),
        },
    }
    on_id = arms[0]["id"]
    off_id = arms[1]["id"]
    delta = float(Decimal(str(summary["arms"][on_id]["final_equity"])) - Decimal(str(summary["arms"][off_id]["final_equity"])))
    summary["comparison"] = {
        "memory_on": on_id,
        "memory_off": off_id,
        "equity_delta_usdc": round(delta, 6),
        "return_delta_pct": round(summary["arms"][on_id]["return_pct"] - summary["arms"][off_id]["return_pct"], 6),
        "leader": on_id if delta > 0 else off_id if delta < 0 else "tie",
        "single_season_note": (
            "One session cannot distinguish a consistent result from one lucky path. "
            "Run several before reading anything into this number."
        ),
    }
    summary["salvaged"] = {
        "reason": "the session was killed before it could stop, so the recording was rebuilt",
        "from": ["live_state.json", "observations.jsonl"],
        "arm_metadata": arm_metadata,
        "checkpoint_status": checkpoint.get("status"),
        "bars_traded_at_kill": checkpoint.get("bars_traded"),
    }

    run = {
        "id": run_dir.name,
        "created": times[0],
        "label": checkpoint.get("label") or f"live session {run_dir.name}",
        "engine": str(checkpoint.get("engine", LIVE_DEFAULTS["engine"])),
        "repeat": 0,
        "bars": len(observations),
        "bar_seconds": spec.bar_seconds,
        "season": season.describe(),
        "rules": json.loads(json.dumps(dataclasses.asdict(rules), default=str)),
        "kind": str(checkpoint.get("kind", LIVE_DEFAULTS["kind"])),
        "exam_window": None,
        "reinforcement_mode": rules.reinforcement,
        "rule_preset": "upstream",
        "population": checkpoint.get("population"),
        "readout": None,
        "decoder_gate_required": rules.require_gate,
        "starting_conditions": checkpoint.get("starting_conditions"),
        "hardware": checkpoint.get("hardware"),
        "duration_seconds": summary["duration_seconds"],
        "seconds_per_bar_mean": summary["seconds_per_bar_mean"],
        "truncated": True,
        "wall_mode": (
            "live, salvaged: this session was killed mid-flight and the recording was rebuilt "
            "from the bars and checkpoint it had already written"
        ),
        "inputs_identical_every_bar": bool(
            all(o.get("same_neural_input_both_arms", True) for o in observations)
        ),
        "live": checkpoint.get("live") or {
            "product": spec.product,
            "bar_seconds": spec.bar_seconds,
            "warmup_bars": checkpoint.get("warmup_bars", LIVE_DEFAULTS["warmup_bars"]),
            "session_opened": checkpoint.get("session_opened"),
            "bars_traded": len(observations),
            "lag_seconds_at_end": checkpoint.get("lag_seconds"),
            "polls": checkpoint.get("polls"),
        },
        "salvaged": summary["salvaged"],
    }
    recording = {
        "schema": SCHEMA_VERSION,
        "run": run,
        "arms": arms,
        "season": {
            "describe": season.describe(),
            "provenance": season.provenance,
            "bars": [
                {
                    "t": season.timestamp(i),
                    "mid": season.mid(i),
                    "bid": str(season.quote(i).bid),
                    "ask": str(season.quote(i).ask),
                }
                for i in range(season.bars)
            ],
        },
        "observations": observations,
        "summary": summary,
        "disclaimers": DISCLAIMERS,
    }
    write_recording(run_dir / "recording.json", recording)
    write_recording(
        run_dir / "manifest.json",
        {
            "schema": SCHEMA_VERSION,
            "id": run_dir.name,
            "run": run,
            "arms": arms,
            "summary": summary,
            "disclaimers": DISCLAIMERS,
        },
    )
    write_jsonl(run_dir / "observations.jsonl", observations)
    checkpoint["status"] = "salvaged"
    checkpoint["salvaged_bars"] = len(observations)
    write_recording(checkpoint_path, checkpoint)
    return recording


# -- continuing a killed session rather than finishing it --------------------------------


@dataclasses.dataclass(frozen=True)
class ResumeState:
    """What a killed live session left behind, in the shape a continuation needs.

    What a resume cannot know is deliberately *absent* from this object rather than filled in
    with something plausible: the brain the flies had when the process died, the decision that
    was being made, the chart they were looking at. Everything here is something the session
    wrote down before it was killed, and the arena states, per arm, which of it was enough.

    ``brains`` is keyed by arm and carries the snapshot the session checkpointed (its bar and
    the same ``weights`` provenance block a recorded run uses for a trained brain), or the name
    of the file its checkpoint pointed at and did not find.
    """

    run_id: str
    run_dir: Path
    checkpoint: dict
    observations: tuple
    session_opened: int
    bar_seconds: int
    warmup_bars: int
    product: str
    venue: str
    engine: str
    kind: str
    rules: dict | None
    starting_conditions: dict | None
    label: str | None
    brains: dict
    dropped_tail: bool = False

    @property
    def bars_traded(self) -> int:
        return len(self.observations)


def _read_observations(path: Path) -> tuple[list[dict], bool]:
    """Every complete observation in the log, and whether a partial last line was dropped.

    A kill can land inside the append of the line that was being written. That leaves a
    truncated tail, and dropping it is the honest reading: everything before it is whole, and
    the bar it was going to describe was never announced. A line that fails to parse anywhere
    else is a corrupt log, and a corrupt log is not a session anyone can continue.
    """
    if not path.exists():
        return [], False
    text = path.read_text()
    lines = [line for line in text.splitlines() if line.strip()]
    rows: list[dict] = []
    for index, line in enumerate(lines):
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            if index == len(lines) - 1 and not text.endswith("\n"):
                return rows, True
            raise ValueError(
                f"{path.name} line {index + 1} is not an observation; the log is corrupt"
            ) from None
    return rows, False


def _intended_bars(run_dir: Path) -> dict[str, int]:
    """The newest bar each arm's ledger holds an order intent for, by arm.

    Every order carries the bar it was decided on (`plan.neural_observation`), and the intent
    is reserved before the fill and before the bar reaches the log. So the newest intent in a
    ledger is the bar the session was working on when it died: normally the log's own last
    bar, and — for a kill that landed inside the order path — one the log never recorded.
    """
    newest: dict[str, int] = {}
    for path in sorted(Path(run_dir).glob("*.sqlite")):
        try:
            # Read-only, and no WAL recovery: the process that wrote this is gone, and a
            # continuation must not be the thing that mutates a killed session's account.
            with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
                rows = db.execute("SELECT plan FROM orders").fetchall()
        except sqlite3.Error:
            # A ledger that predates the orders table holds no intents. Its cash and positions
            # are still read from it when the arena reopens the account.
            continue
        bars = []
        for (plan,) in rows:
            try:
                bar = (json.loads(plan).get("neural_observation") or {}).get("t")
            except (TypeError, ValueError):
                continue
            if bar is not None:
                bars.append(int(bar))
        if bars:
            newest[path.stem] = max(bars)
    return newest


def _checkpointed_brains(run_dir: Path, checkpoint: dict) -> dict:
    """The brain snapshots the session wrote, as its own checkpoint names them.

    A live session checkpoints each fly's brain every few bars for exactly this reason: the
    learned efficacies live in memory, so a session that checkpoints nothing has nothing for a
    continuation to put back. The snapshot the checkpoint names is the newest one it had
    written; a file it names and that is not on disk is reported as missing rather than
    silently replaced by the baseline.
    """
    brains: dict[str, dict] = {}
    for arm_id, entry in (checkpoint.get("brains") or {}).items():
        entry = entry or {}
        file = str(entry.get("file") or "")
        path = run_dir / file
        snapshot = {"bar": entry.get("bar"), "t": entry.get("t")}
        if file and path.is_file():
            # The provenance block a recording uses for any brain, so a resumed brain is
            # checked with the same tools as a trained one.
            snapshot["weights"] = StartingWeights("trained", path).describe()
        else:
            snapshot["missing"] = file or "the checkpoint named no file"
        brains[str(arm_id)] = snapshot
    return brains


def load_resume(run_dir) -> ResumeState:
    """Read a killed live session as the continuation it can be.

    Three refusals, all of them about not rewriting something that is over or not counting a
    bar twice. A run with a `recording.json` is a closed experiment whose record is evidence.
    A checkpoint that no longer says `running` is a session that stopped, whatever is next to
    it on disk. A session whose ledgers hold an order for a bar the log never recorded was
    killed inside the order path, and continuing it would either trade that bar again or
    account for its fill twice. Everything else is read back and reported.
    """
    run_dir = Path(run_dir)
    name = run_dir.name
    checkpoint_path = run_dir / "live_state.json"
    if not checkpoint_path.exists():
        raise FileNotFoundError(
            f"{name} has no live_state.json: there is no live session to continue"
        )
    if (run_dir / "recording.json").exists():
        raise FileExistsError(
            f"{name} already has a recording.json: it finished, or it was salvaged from a "
            "live_state.json that had no recording. A continuation would rewrite a closed "
            "session's record from artifacts it can no longer be sure of."
        )

    checkpoint = json.loads(checkpoint_path.read_text())
    status = checkpoint.get("status")
    if status != "running":
        raise ValueError(
            f"{name} says its own session is {status!r}, not running. Only a session a kill "
            "interrupted can be continued; a continuation writes the session's own directory, "
            "its log and its accounts, and this one is over."
        )
    observations, dropped_tail = _read_observations(run_dir / "observations.jsonl")
    for index, observation in enumerate(observations):
        if int(observation.get("i", -1)) != index:
            raise ValueError(
                f"{name} did not trade a continuous run of bars: observation {index} says "
                f"i={observation.get('i')!r}. A continuation numbers its bars from that log, "
                "so a hole in it would renumber every bar after the hole."
            )
    last_t = int(observations[-1]["t"]) if observations else None
    ahead = {
        arm: bar
        for arm, bar in _intended_bars(run_dir).items()
        if last_t is None or bar > last_t
    }
    if ahead:
        held = ", ".join(f"{arm} at {_iso_bar(bar)}" for arm, bar in sorted(ahead.items()))
        raise ValueError(
            f"{name} was killed inside a bar, not between two: an order intent ({held}) names "
            "a bar that observations.jsonl never recorded. Continuing would either trade that "
            "bar again or account for its fill twice, so this session cannot be resumed. "
            "Salvage it and start a new session."
        )

    bar_seconds = int(checkpoint.get("bar_seconds", LIVE_DEFAULTS["bar_seconds"]))
    if checkpoint.get("session_opened") is not None:
        session_opened = int(checkpoint["session_opened"])
    elif observations:
        session_opened = int(observations[0]["t"])
    elif checkpoint.get("last_bar") is not None:
        session_opened = int(checkpoint["last_bar"]) + bar_seconds
    else:
        raise ValueError(
            f"{name}'s checkpoint does not say when the session opened — neither "
            "`session_opened` nor `last_bar` is in it — so a continuation has no session "
            "start to record."
        )

    return ResumeState(
        run_id=str(checkpoint.get("run_id") or name),
        run_dir=run_dir,
        checkpoint=checkpoint,
        observations=tuple(observations),
        session_opened=session_opened,
        bar_seconds=bar_seconds,
        warmup_bars=int(checkpoint.get("warmup_bars", LIVE_DEFAULTS["warmup_bars"])),
        product=str(checkpoint.get("product", LIVE_DEFAULTS["product"])),
        venue=str(checkpoint.get("venue", LIVE_DEFAULTS["venue"])),
        engine=str(checkpoint.get("engine") or LIVE_DEFAULTS["engine"]),
        kind=str(checkpoint.get("kind") or LIVE_DEFAULTS["kind"]),
        rules=checkpoint.get("rules"),
        starting_conditions=checkpoint.get("starting_conditions"),
        label=checkpoint.get("label"),
        brains=_checkpointed_brains(run_dir, checkpoint),
        dropped_tail=dropped_tail,
    )


def _iso_bar(seconds) -> str:
    return f"{_iso(int(seconds))} ({int(seconds)})"
