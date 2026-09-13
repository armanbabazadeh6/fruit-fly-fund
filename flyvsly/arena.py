"""The arena: two flies, one market, one rule set, one experimental variable.

Per bar, in this order, for each arm independently:

1. The execution guard checks the shared quote (the same `Quote` object for both arms).
2. Marked-to-bid equity is compared with the anchor committed at the previous bar and the
   engineered reward/aversive pulse is scheduled. This is upstream's rule, evaluated per
   bar; tiny changes are not accumulated.
3. The backend observes the shared 320×180 RGB frame. The neural arm cannot see prices.
4. The anchor is committed, then the fixed decoder's proposal goes to the guard, the order
   intent is persisted, and the paper broker fills it.

The only difference between the arms is `Settings.learning`, asserted at runtime by
`flyvsly.fairness`.

Each arm runs on its own long-lived thread. That is a performance decision (the native
kernel releases the GIL, and one 500 ms observation costs seconds of CPU) and a
correctness one: SQLite connections created by upstream's `Ledger` are bound to their
creating thread, so each arm's ledger must be created on the thread that uses it.
"""

import dataclasses
import hashlib
import json
import os
import platform
import sys
import threading
import time
from decimal import Decimal
from pathlib import Path

from stonkfly.broker import PaperBroker
from stonkfly.config import D
from stonkfly.display import market_frame
from stonkfly.ledger import Ledger
from stonkfly.reinforcement import reinforcement
from stonkfly.risk import Veto

from . import SCHEMA_VERSION
from .benchmark import benchmark_deployment, buy_and_hold, cash
from .config import ArenaConfig
from .fairness import arm_settings, assert_only_learning_differs, starting_conditions
from .market import build_season
from .replay import ReplayGuard, VirtualClock
from .telemetry import (
    execution_explanation,
    neural_explanation,
    portfolio_view,
    procedural_explanation,
    summarise_curve,
    write_jsonl,
    write_recording,
)

PERSONAS = (
    {
        "id": "gordon",
        "name": "Gordon Flykko",
        "learning": True,
        "role": "experimental",
        "role_label": "Memory updates ON",
        "tagline": "Every fee is a lesson. Allegedly.",
        "accent": "#ffb454",
        "accent_soft": "#3d2c10",
        "detail": (
            "After each observation the candidate anti-Hebbian rule adapts the 7,835 "
            "existing KC→MBON07/11 efficacies from actual spike counts and the engineered "
            "reward/aversive pulse. The changed weights feed the next observation."
        ),
    },
    {
        "id": "warren",
        "name": "Warren Buzzett",
        "learning": False,
        "role": "control",
        "role_label": "Memory updates OFF",
        "tagline": "Same fly. No memory of what it supposedly learned.",
        "accent": "#5ec8ff",
        "accent_soft": "#10303f",
        "detail": (
            "Identical integration, identical sensory input, identical reward pulses. The "
            "weight-update step is frozen, so its 7,835 eligible efficacies stay at the "
            "baseline graph for the whole season."
        ),
    },
)

DISCLAIMERS = [
    "Paper trading only. This project places no real orders and holds no account credentials.",
    "Engineered reinforcement is not pain, pleasure or consciousness. Positive P&L need not be realised profit.",
    "The DNp20 decoder is a fixed engineered interface, not a discovered buy/sell neuron.",
    "Memory updates are not assumed to help. Profitable learning has not been demonstrated upstream or here.",
    "Rivalry commentary is entertainment, not analysis.",
    "Buy & hold faces no order-size, cooldown or daily-order limits; the flies do.",
]


def personas_for(kind: str, starting=None) -> tuple:
    """Who the two flies are in this kind of run, derived from the starting specs.

    A competition differs in `learning`. An exam or a reset freezes both and differs in the
    brain each one carried in, so the labels follow the specs rather than assuming that the
    experimental fly is always the one holding the weights: a run whose weights sit on the
    other arm would otherwise describe itself backwards in prose while its machine-checkable
    block was right.
    """
    if kind == "competition":
        return PERSONAS
    specs = starting or {}
    carrying_label = (
        "Trained brain, frozen" if kind == "exam" else "Trained, then reset, frozen"
    )
    personas = []
    for persona in PERSONAS:
        kind_of = str(specs.get(persona["id"], "baseline")).split(":")[0]
        carries = kind_of in ("trained", "reset")
        personas.append(
            {
                **persona,
                # Frozen, both of them: the flag is what matters, not the label.
                "learning": False,
                "role": "experimental" if carries else "control",
                "role_label": carrying_label if carries else "Fresh brain, frozen",
                "tagline": (
                    "Brought weights from an earlier season."
                    if carries
                    else "Never trained on anything."
                ),
                "detail": (
                    f"Restored from a checkpoint and frozen ({kind_of}): nothing is learned "
                    "during this run, so the only difference between the two is the brain "
                    "each one carried in."
                    if carries
                    else (
                        "Identical rules and identical frozen updates; its 7,835 eligible "
                        "efficacies are the reconstructed baseline."
                    )
                ),
            }
        )
    return tuple(personas)


class Arm:
    """One competitor: its own neural engine, its own account, its own ledger."""

    def __init__(self, persona, rules, backend, out_dir, clock, starting=None):
        self.persona = persona
        self.starting = starting
        self.starting_report = None
        self.id = persona["id"]
        self.name = persona["name"]
        self.learning = bool(persona["learning"])
        self.rules = rules
        self.settings = arm_settings(rules, self.learning)
        self.backend = backend
        self.clock = clock
        self.out_dir = Path(out_dir)
        self.ledger = None
        self.guard = None
        self.broker = None
        self.stats = {
            "fills": 0,
            "vetoes": 0,
            "blocked": 0,
            "fees_paid": Decimal(0),
            "exposure_bars": 0,
            "halted_reason": None,
        }
        self.trades = []
        self.curve = []
        self.last_memory = None
        self.initial = D(rules.capital)

    def open_account(self):
        """Create the account on the thread that will use it."""
        self.ledger = Ledger(
            self.out_dir / f"{self.id}.sqlite", self.settings, "paper"
        )
        self.guard = ReplayGuard(
            self.settings, self.ledger, self.clock, self.out_dir / f"{self.id}.STOP"
        )
        self.broker = PaperBroker(self.settings, self.ledger)
        return self

    def apply_starting(self):
        """Put this fly's brain where the experiment says it starts."""
        if self.starting is None:
            return
        from .starting import apply as apply_starting

        self.starting_report = apply_starting(self.starting, self.backend)

    def describe(self):
        backend = self.backend.describe()
        return {
            "id": self.id,
            "name": self.name,
            "role": self.persona["role"],
            "role_label": self.persona["role_label"],
            "tagline": self.persona["tagline"],
            "accent": self.persona["accent"],
            "accent_soft": self.persona["accent_soft"],
            "detail": self.persona["detail"],
            "learning": self.learning,
            "starting_capital": str(self.initial),
            "starting_weights": (
                self.starting.describe() if self.starting is not None else None
            ),
            "starting_report": self.starting_report,
            "settings": dataclasses.asdict(self.settings),
            "settings_signature": self.settings.signature(),
            "backend": backend,
        }

    def close(self):
        if self.ledger is not None:
            self.ledger.close()


def _build_backend(engine, settings, data_root, seed, rules=None):
    if engine == "neural":
        from .backends.neural import NeuralBackend

        return NeuralBackend(
            settings,
            data_root=data_root,
            require_gate=rules.require_gate if rules else True,
            population_sample=rules.population_sample if rules else 256,
            readout=rules.readout if rules else None,
            readout_margin=rules.readout_margin if rules else 0.15,
        )
    from .backends.procedural import ProceduralBackend

    return ProceduralBackend(settings, seed=seed)


def hardware_report(data_root="data") -> dict:
    import numpy
    import pandas

    manifest = None
    path = Path(data_root) / "manifest.json"
    if path.exists():
        manifest = json.loads(path.read_text())
    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
        "python": sys.version.split()[0],
        "numpy": numpy.__version__,
        "pandas": pandas.__version__,
        "data_manifest": manifest,
        "gpu_acceleration": "none: the vendored engine integrates on the CPU only",
    }


class _ArmThread(threading.Thread):
    """A long-lived worker for one arm: account, loop, teardown.

    Handshake per bar is two events so a failing arm can never deadlock the driver:
    the driver sets `go`, the arm sets `finished`, and any exception is recorded and
    re-raised by the driver after the bar.
    """

    def __init__(self, arena, persona, index):
        super().__init__(name=f"arm-{persona['id']}", daemon=True)
        self.arena = arena
        self.persona = persona
        self.index = index
        self.go = threading.Event()
        self.finished = threading.Event()
        self.stop = threading.Event()
        self.launched = threading.Event()
        self.error = None
        self.context = None
        self.result = None
        self.arm_obj = None

    def run(self):
        try:
            settings = arm_settings(self.arena.rules, self.persona["learning"])
            backend = _build_backend(
                self.arena.config.engine,
                settings,
                self.arena.data_root,
                self.arena.config.market.seed + self.index,
                self.arena.rules,
            )
            self.arm_obj = Arm(
                self.persona,
                self.arena.rules,
                backend,
                self.arena.out_dir,
                VirtualClock(self.arena._clock_start()),
                starting=self.arena.starting_for(self.persona["id"]),
            ).open_account()
            self.arm_obj.apply_starting()
        except BaseException as error:
            self.error = error
            self.launched.set()
            return
        self.launched.set()
        while True:
            self.go.wait()
            self.go.clear()
            if self.stop.is_set():
                break
            try:
                self.result = self.arena._bar(self.arm_obj, self.context)
            except BaseException as error:
                self.error = error
            finally:
                self.finished.set()
        self.arm_obj.close()


def _slim_signal(signal):
    """Per-bar signal without the static decoder cell identities (they live in the arm)."""
    if signal is None:
        return None
    return {k: v for k, v in signal.items() if k != "cell_ids"}


def _trade_from_observation(observation, record) -> dict:
    """The trade an observation describes, in the shape `_bar` appends as it fills.

    Read back from the log rather than rebuilt from the ledger: the log is what the session
    recorded at the time, so a continuation's trade list says the same thing for a bar it
    adopted as for one it decided itself.
    """
    fill = ((record.get("execution") or {}).get("fill")) or {}
    steps = ((record.get("decision") or {}).get("explanation") or {}).get("steps") or []
    return {
        "i": observation["i"],
        "t": observation["t"],
        "side": (record.get("decision") or {}).get("side"),
        "product": observation.get("product"),
        "base_size": fill.get("base"),
        "price": fill.get("price"),
        "quote_size": fill.get("quote"),
        "fee": fill.get("fee"),
        "reason": steps[-1] if steps else (record.get("execution") or {}).get("reason"),
        "equity_after": (record.get("portfolio") or {}).get("equity"),
        "memory_changed_edges": ((record.get("signal") or {}).get("memory") or {}).get(
            "changed_edges"
        ),
    }


def _adopt(arm, observations) -> None:
    """Give an arm the bars a killed session already traded for it.

    Needs no access to the ledgers — it is the log that says what those bars did to each
    account — and it is what keeps a continued run's recording one session rather than a tail:
    the curve starts where the session started, the fills and fees are the session's own, and
    the bars traded after the resume are appended to theirs.
    """
    product = arm.rules.products[0]
    arm.curve = []
    arm.trades = []
    blocked = 0
    exposure = 0
    last_portfolio: dict = {}
    for observation in observations:
        record = (observation.get("arms") or {}).get(arm.id) or {}
        portfolio = record.get("portfolio") or {}
        arm.curve.append(float(portfolio.get("equity", arm.initial)))
        if ((record.get("decision") or {}).get("side")) == "BLOCKED":
            blocked += 1
        if Decimal(str(portfolio.get("positions", {}).get(product, 0) or 0)) > 0:
            exposure += 1
        if (record.get("execution") or {}).get("fill"):
            arm.trades.append(_trade_from_observation(observation, record))
        memory = (record.get("signal") or {}).get("memory")
        if memory:
            arm.last_memory = memory
        last_portfolio = portfolio or last_portfolio
    arm.stats["fills"] = int(last_portfolio.get("fills", 0) or 0)
    arm.stats["vetoes"] = int(last_portfolio.get("vetoes", 0) or 0)
    arm.stats["fees_paid"] = Decimal(str(last_portfolio.get("fees_paid", "0") or "0"))
    arm.stats["blocked"] = blocked
    arm.stats["exposure_bars"] = exposure
    arm.stats["halted_reason"] = last_portfolio.get("halted")


# Where a live session checkpoints each fly's brain, inside its own run directory. A session
# that never writes one has nothing to continue from: the learned efficacies live in memory.
BRAIN_DIR = "brains"


class Arena:
    def __init__(self, config: ArenaConfig, on_event=None, data_root="data"):
        self.config = config.validate()
        self.on_event = on_event or (lambda kind, payload: None)
        self.data_root = data_root
        self.rules = config.rules
        self.season = None
        self.out_dir = None
        self.personas = personas_for(config.kind, config.starting)

    def _check_disjoint(self, season):
        """Refuse a season that shares bars with the run it is supposed to be independent of."""
        reference = self.config.must_not_overlap
        if not reference:
            return None
        import json as _json

        from .report import recording_path

        # References normally live in the same directory this run writes to, but a run with a
        # one-off --out should still be able to point at the campaign it is being graded against.
        try:
            path = recording_path(self.config.out, reference)
        except FileNotFoundError:
            path = recording_path("runs", reference)
        other = _json.loads(path.read_text())
        mine = {season.timestamp(i) for i in range(season.bars)}
        theirs = {bar["t"] for bar in other["season"]["bars"]}
        shared = mine & theirs
        if shared:
            raise RuntimeError(
                f"This season shares {len(shared)} bars with {reference!r} "
                f"({path.parent.name}). An exam must be graded on a market it has not seen: "
                "raise --window-offset (the offset counts bars, so it must be at least the "
                "season length) or point --must-not-overlap at the right run."
            )
        return {"must_not_overlap": reference, "reference_run": path.parent.name, "shared_bars": 0}

    def starting_for(self, arm_id: str):
        """Parse this arm's starting weights once, so every path uses the same object."""
        from .starting import parse

        if arm_id not in self.config.starting:
            return None
        return parse(self.config.starting[arm_id])

    def emit(self, kind, **payload):
        try:
            self.on_event(kind, payload)
        except Exception:
            pass

    def run(self, run_id=None, season=None, repeat=0, out_root=None) -> dict:
        config = self.config
        rules = self.rules
        started = time.time()
        run_id = run_id or time.strftime("%Y%m%d-%H%M%S", time.localtime(started))
        out = Path(out_root or config.out) / run_id
        out.mkdir(parents=True, exist_ok=True)
        self.out_dir = out
        self.season = season or build_season(config.market)
        season = self.season
        self._prepare_reinforcement(season)

        # The settings these personas actually produce, not a guessed pair.
        on_settings = arm_settings(rules, self.personas[0]["learning"])
        off_settings = arm_settings(rules, self.personas[1]["learning"])
        conditions = starting_conditions(
            rules, config.kind, config.starting, on_settings, off_settings
        )
        if config.kind == "competition":
            assert_only_learning_differs(arm_settings(rules, True), arm_settings(rules, False))

        # An exam is only an exam if the market was genuinely unseen. The offset is easy to get
        # wrong — a 48-bar season spans about 64 minutes of market time, so stepping back 48
        # minutes leaves a quarter of the "new" season inside the previous one — so this is
        # checked against the recording it claims to be independent of, not assumed.
        overlap = self._check_disjoint(season)

        self.emit(
            "season_ready",
            describe=season.describe(),
            provenance=season.provenance,
            bars=[
                {
                    "t": season.timestamp(i),
                    "mid": season.mid(i),
                    "bid": str(season.quote(i).bid),
                    "ask": str(season.quote(i).ask),
                }
                for i in range(season.bars)
            ],
        )
        if config.engine == "neural":
            arms = self._run_parallel(run_id, season, out)
        else:
            arms = self._run_sequential(out)
        return self._finalise(
            run_id=run_id,
            season=season,
            arms=arms,
            started=started,
            repeat=repeat,
            overlap=overlap,
            conditions=conditions,
            out=out,
            wall_mode=(
                "accelerated replay: one completed market bar per observation, no "
                "wall-clock waiting"
            ),
        )

    def _finalise(
        self,
        run_id,
        season,
        arms,
        started,
        repeat,
        overlap,
        conditions,
        out,
        wall_mode,
        extra=None,
    ):
        """Summarise a finished run, write its recording, and return it.

        Shared by a fixed season and a live session, so both produce the same artifact. The
        only differences are the wall-clock note and whatever the caller puts in `extra`.
        """
        config, rules = self.config, self.rules
        duration = time.time() - started

        # A live session can end before the exchange closes its first bar. There is no market
        # to benchmark against then, and an empty block says so rather than inventing a 0%.
        benchmarks = (
            {
                "buy_and_hold": buy_and_hold(season, D(rules.capital), D(rules.paper_fee)),
                "cash": cash(season, D(rules.capital)),
            }
            if season.bars
            else {}
        )
        summary = {
            "bars": season.bars,
            "initial_capital": str(D(rules.capital)),
            "duration_seconds": round(duration, 3),
            "seconds_per_bar_mean": self.seconds_per_bar_mean,
            "truncated": self.truncated,
            "arms": {},
            "benchmarks": benchmarks,
        }
        for arm in arms:
            summary["arms"][arm.id] = {
                "name": arm.name,
                **summarise_curve(arm.curve, float(arm.initial)),
                "curve": arm.curve,
                "fills": arm.stats["fills"],
                "vetoes": arm.stats["vetoes"],
                "blocked_bars": arm.stats["blocked"],
                "fees_paid": str(arm.stats["fees_paid"]),
                "halted": arm.stats["halted_reason"],
                "exposure_bars": arm.stats["exposure_bars"],
                "trades": arm.trades,
                "deployment": benchmark_deployment(arm.stats, season, rules),
                "final_memory": arm.last_memory,
            }
        on_summary = summary["arms"]["gordon"]
        off_summary = summary["arms"]["warren"]
        delta = float(D(on_summary["final_equity"]) - D(off_summary["final_equity"]))
        summary["comparison"] = {
            "memory_on": "gordon",
            "memory_off": "warren",
            "equity_delta_usdc": round(delta, 6),
            "return_delta_pct": round(
                on_summary["return_pct"] - off_summary["return_pct"], 6
            ),
            "leader": "gordon" if delta > 0 else "warren" if delta < 0 else "tie",
            "single_season_note": (
                "One season cannot distinguish a consistent result from one lucky path. "
                "Run several seasons before reading anything into this number."
            ),
        }
        if extra:
            summary.update(extra)

        recording = {
            "schema": SCHEMA_VERSION,
            "run": {
                "id": run_id,
                "created": started,
                "label": config.label or f"{config.engine} run",
                "engine": config.engine,
                "repeat": repeat,
                "bars": season.bars,
                "bar_seconds": config.market.bar_seconds,
                "season": season.describe(),
                "rules": json.loads(json.dumps(dataclasses.asdict(rules), default=str)),
                "kind": config.kind,
                "exam_window": overlap,
                "reinforcement_mode": rules.reinforcement,
                "rule_preset": config.preset,
                "population": (
                    arms[0].backend.population_description
                    if hasattr(arms[0].backend, "population_description")
                    else None
                ),
                "readout": (
                    arms[0].backend.readout.describe()
                    if getattr(arms[0].backend, "readout", None)
                    else None
                ),
                "decoder_gate_required": rules.require_gate,
                "starting_conditions": conditions,
                "hardware": hardware_report(self.data_root),
                "duration_seconds": summary["duration_seconds"],
                "seconds_per_bar_mean": summary["seconds_per_bar_mean"],
                "truncated": summary["truncated"],
                "wall_mode": wall_mode,
                "inputs_identical_every_bar": self.inputs_identical,
                **(extra or {}),
            },
            "arms": [arm.describe() for arm in arms],
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
            "observations": self.observations,
            "summary": summary,
            "disclaimers": DISCLAIMERS,
        }
        write_recording(out / "recording.json", recording)

        # Checkpoint only after the recording is on disk, and only for the last season of a
        # multi-season run: `save_brains` refuses to overwrite, because a campaign must not
        # silently replace an earlier training run, and what "trained" means here is the
        # brain as it stands after the final season.
        if config.save_brains and repeat == config.repeats - 1:
            from .starting import save_brains

            saved = save_brains(
                Path(config.save_brains), {arm.id: arm.backend for arm in arms}
            )
            recording["run"]["brains_saved"] = saved
            write_recording(out / "recording.json", recording)
            self.emit("brains_saved", **saved)
        write_recording(
            out / "manifest.json",
            {
                "schema": SCHEMA_VERSION,
                "run": recording["run"],
                "arms": recording["arms"],
                "summary": summary,
                "disclaimers": DISCLAIMERS,
            },
        )
        write_jsonl(out / "observations.jsonl", self.observations)
        self.emit("recording", run_id=run_id, summary=summary)
        # Each driver closes its own arms: a ledger may only be closed on the thread
        # that created it (parallel arms own their threads, sequential arms run here).
        return recording

    # -- engine-specific loops -------------------------------------------------------

    def _run_sequential(self, out):
        """Procedural arms are microseconds per bar; no threads, no surprises."""
        arms, threads = self._open_arms(out)
        self._begin_bar_loop()
        times = []
        try:
            for i in range(self.season.bars):
                self._step(arms, threads, i, times)
        finally:
            self._close_arms(arms, threads)
        self._finish_bar_loop(times)
        return arms

    # -- arm lifecycle, shared by fixed seasons and live sessions --------------------

    def _clock_start(self):
        """The market time a fresh account starts on.

        A fixed season starts at its bar 0. A live session has no bar 0 at the moment its
        accounts open — the exchange has not closed one yet — so it starts on the open time
        of the bar that is about to arrive.
        """
        if self.season.bars:
            return self.season.timestamp(0)
        return int(getattr(self.season, "next_bar_opened", 0))

    def _open_arms(self, out):
        """Build both arms and, for the neural engine, their threads.

        Returns ``(arms, threads)``; ``threads`` is empty for the procedural engine, which
        runs in the calling thread.
        """
        if self.config.engine != "neural":
            arms = []
            for index, persona in enumerate(self.personas):
                settings = arm_settings(self.rules, persona["learning"])
                backend = _build_backend(
                    self.config.engine,
                    settings,
                    self.data_root,
                    self.config.market.seed + index,
                    self.rules,
                )
                arm = Arm(
                    persona,
                    self.rules,
                    backend,
                    out,
                    VirtualClock(self._clock_start()),
                    starting=self.starting_for(persona["id"]),
                ).open_account()
                # Same as the threaded path: without this a sequential run silently ignored
                # `--starting` and ran both flies from the baseline. The CLI refuses exam and
                # reset on the procedural engine, so it was reachable only through the library.
                arm.apply_starting()
                arms.append(arm)
            self.emit("arms_ready", arms=[arm.describe() for arm in arms])
            return arms, []

        threads = [
            _ArmThread(self, persona, index) for index, persona in enumerate(self.personas)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.launched.wait()
        failure = next((t.error for t in threads if t.error), None)
        if failure:
            for thread in threads:
                thread.stop.set()
                thread.go.set()
            for thread in threads:
                thread.join(timeout=60)
            raise failure
        arms = [thread.arm_obj for thread in threads]
        self.emit("arms_ready", arms=[arm.describe() for arm in arms])
        return arms, threads

    def _step(self, arms, threads, i, times, bar_started=None):
        """One bar for every arm: one shared frame, one decision per fly.

        Both engines go through here, so a live session's bars are produced by exactly the
        code that produces a recorded one.
        """
        context = self._context(i)
        if threads:
            for thread in threads:
                thread.arm_obj.clock.set(context["timestamp"])
                thread.context = context
                thread.finished.clear()
                thread.go.set()
            for thread in threads:
                thread.finished.wait()
            failure = next((t.error for t in threads if t.error), None)
            if failure:
                raise failure
            per_arm = {thread.arm_obj.id: thread.result for thread in threads}
        else:
            per_arm = {}
            for arm in arms:
                arm.clock.set(context["timestamp"])
                per_arm[arm.id] = self._bar(arm, context)
        for arm in arms:
            arm.curve.append(float(per_arm[arm.id]["portfolio"]["equity"]))
        self._publish(i, context, times, per_arm, bar_started)
        return per_arm

    def _close_arms(self, arms, threads):
        """Stop the arms. A ledger closes on the thread that opened it.

        Upstream's `Ledger` binds its SQLite connection to its creating thread, so a threaded
        arm closes its own inside `_ArmThread.run`, while a sequential arm closes here.
        """
        if threads:
            for thread in threads:
                thread.stop.set()
                thread.go.set()
            for thread in threads:
                thread.join(timeout=120)
            return
        for arm in arms:
            arm.close()

    def _begin_bar_loop(self):
        self.observations = []
        # How many of them are already in `observations.jsonl`. Zero for a session that starts
        # now; a continuation adopts its log and continues writing after it.
        self._observations_logged = 0
        self.seconds_per_bar_mean = 0.0
        self.truncated = False
        self.inputs_identical = True

    def _finish_bar_loop(self, times):
        self.seconds_per_bar_mean = round(sum(times) / len(times), 4) if times else 0.0

    def _run_parallel(self, run_id, season, out):
        """One thread per arm. Both arms receive the same frame object every bar."""
        arms, threads = self._open_arms(out)
        self._begin_bar_loop()
        times = []
        budget = self.config.max_wall_seconds
        started = time.monotonic()
        try:
            for i in range(season.bars):
                self._step(arms, threads, i, times, time.perf_counter())
                if budget and time.monotonic() - started > budget:
                    self.truncated = True
                    break
        finally:
            self._close_arms(arms, threads)
        self._finish_bar_loop(times)
        return arms

    def run_live(self, feed, run_id=None, out_root=None, stop=None, resume=None, brain_every=0) -> dict:
        """Trade a growing season on the wall clock: one decision per completed bar.

        The exchange's clock decides when a bar exists — the loop waits for the feed to close
        one and then trades it. A machine slower than the bar interval falls behind, and the
        lag is recorded, but nothing is skipped, reordered, or traded on a forming bar, which
        is what keeps a live session comparable with a recorded one.

        ``stop`` is polled between bars, so Ctrl-C or a stop file ends the session between two
        decisions rather than in the middle of one.

        ``resume`` continues a session that was killed, from the artifacts it left behind (see
        `flyvsly.salvage.load_resume`): the same run directory, the bars it had already traded
        read back rather than traded again, and each arm's account reopened rather than created.
        ``brain_every`` checkpoints each fly's brain into ``brains/`` every N bars — the learned
        efficacies never reach the disk on their own, so that snapshot is the only brain a
        continuation has to put back, and `resumed` in the recording says which bar it is from.
        """
        config = self.config
        started = time.time()
        if resume is not None:
            # A continuation *is* the session it continues, so it writes to the session's own
            # directory: the recording it ends with covers the whole run, not the tail of it.
            run_id = resume.run_id
        run_id = run_id or time.strftime("%Y%m%d-%H%M%S", time.localtime(started))
        out = Path(out_root or config.out) / run_id
        out.mkdir(parents=True, exist_ok=True)
        self.out_dir = out
        if config.must_not_overlap:
            raise ValueError(
                "a live session cannot be an exam: it has no fixed window to be checked "
                "against. Drop --must-not-overlap."
            )
        if self.rules.reinforcement in ("decoy", "shuffled"):
            raise ValueError(
                f"reinforcement={self.rules.reinforcement!r} needs the whole season before the "
                "first bar — a benchmark curve, or a permutation of a finished recording — "
                "which a live session does not have yet. Use 'pnl' or 'none'."
            )

        if resume is None:
            season = self.season = feed.prime()
            # Read once, at the start: `next_bar_opened` is a property of the growing window, so
            # reading it later would report the last bar's successor as the session's start.
            session_opened = season.next_bar_opened
            prior: list = []
        else:
            # The session's polls continue rather than restarting, so `polls` counts for the
            # whole session; this look at the venue is the continuation's first.
            feed.polls = max(0, int(resume.checkpoint.get("polls") or 0))
            season = self.season = feed.resume(resume.observations)
            # The session's own start, not this process's: a continuation of a session opened
            # three days ago is still that session, and a reader dates it from where it began.
            session_opened = resume.session_opened
            prior = list(resume.observations)
        stop = stop or (lambda: False)
        self.emit(
            "season_ready",
            describe=season.describe(),
            provenance=season.provenance,
            bars=[],
            live=True,
            warmup=season.warmup_bars,
            next_bar_opened=session_opened if resume is None else season.next_bar_opened,
        )

        on_settings = arm_settings(self.rules, self.personas[0]["learning"])
        off_settings = arm_settings(self.rules, self.personas[1]["learning"])
        conditions = starting_conditions(
            self.rules, config.kind, config.starting, on_settings, off_settings
        )
        if config.kind == "competition":
            assert_only_learning_differs(
                arm_settings(self.rules, True), arm_settings(self.rules, False)
            )
        if resume is not None and resume.starting_conditions:
            # The run's own starting line, from its checkpoint: a continuation does not change
            # the experiment. What it does change is the brain each fly restarts with, and that
            # is stated separately, per arm, in `resumed`.
            conditions = resume.starting_conditions

        arms, threads = self._open_arms(out)
        self._begin_bar_loop()
        brains: dict = {}
        if resume is not None:
            for arm in arms:
                _adopt(arm, prior)
            self.observations = list(prior)
            self._observations_logged = len(prior)
            brains = self._restore_resume_brains(arms, resume)
        times = []
        processed = len(prior)
        lag = 0.0
        # Described once and reused: a killed session leaves nothing in memory, so the
        # checkpoint has to carry enough for `flyvsly salvage` to rebuild a recording from it.
        live_meta = {
            "label": config.label or "live session",
            "engine": config.engine,
            "kind": config.kind,
            "rules": json.loads(json.dumps(dataclasses.asdict(self.rules), default=str)),
            "starting_conditions": conditions,
            "arms": [arm.describe() for arm in arms],
            "population": (
                arms[0].backend.population_description
                if hasattr(arms[0].backend, "population_description")
                else None
            ),
            # What a restart can and cannot pick up, stated rather than implied. The brain line
            # is the one that matters: everything else here survives on its own.
            "on_restart": {
                "market": (
                    "the window is rebuilt from the bars in observations.jsonl, in front of "
                    "whatever warm-up the venue still has"
                ),
                "accounts": "durable: each arm writes its own SQLite ledger per bar",
                "brain": (
                    f"checkpointed to {BRAIN_DIR}/ every {int(brain_every)} bars; a continuation "
                    "restores the newest snapshot and records which bar it came from"
                    if brain_every
                    else "not checkpointed (--brain-every 0): a continuation cannot put back "
                    "what was learned, and records that it did not"
                ),
                "how": "flyvsly live --resume <run id>",
            },
        }
        if resume is not None:
            live_meta["resumed"] = self._resume_block(season, resume, brains, prior)
        # A session states itself from the first moment, before it has traded anything: an
        # interrupted session must be visible as a session, not as an empty directory.
        self._checkpoint_live(out, season, feed, session_opened, lag, processed, live_meta)
        try:
            while not stop():
                if not feed.poll():
                    self._idle(feed.poll_seconds, stop)
                    continue
                # Every bar the exchange closed while the previous one was being decided is
                # still traded, in market order. Dropping the backlog would quietly turn a
                # slow machine into a different strategy.
                while processed < season.bars and not stop():
                    bar_started = time.perf_counter()
                    self._step(arms, threads, processed, times, bar_started)
                    processed += 1
                    # Before the checkpoint that names it, so a checkpoint never points at a
                    # snapshot that is not there yet.
                    if brain_every and processed % int(brain_every) == 0:
                        self._checkpoint_brains(arms, season, processed, live_meta)
                    # Lag is measured on the clock the session itself polls with, so it means
                    # "how far behind the exchange is this decision" rather than "how far from
                    # this process's wall clock".
                    lag = round(
                        feed.clock() - (season.timestamp(processed - 1) + config.market.bar_seconds),
                        3,
                    )
                    # Durable first, announce second: a bar that reached the page but not
                    # the disk would be a bar nobody can audit.
                    self._checkpoint_live(out, season, feed, session_opened, lag, processed, live_meta)
                    self.emit(
                        "live_progress",
                        bars=processed,
                        available=season.bars,
                        lag_seconds=lag,
                        polls=feed.polls,
                    )
        finally:
            self._close_arms(arms, threads)
        self._finish_bar_loop(times)
        # A live season has no last bar, so every session ends by a stop — and a stop can land
        # inside the backlog drain. The season still holds the bars the exchange closed after
        # the last decision, and `_finalise` reads its bar count from the season: without this
        # cut a stopped session writes a recording that contradicts its own observations
        # (phantom bars in the chart, untraded bars counted as flat in the exposure fraction).
        if processed < season.bars:
            self.truncated = True
            del season.closes[season.warmup_bars + processed :]
            del season.times[season.warmup_bars + processed :]
            season._rebuild()
        if resume is None:
            wall_mode = (
                "live: one decision per completed market bar, paced by the exchange's clock. "
                "Bars that closed while a decision was still running are traded in market "
                "order, so the session can run behind wall time but never ahead of it."
            )
        else:
            wall_mode = (
                f"live, continued: this session was killed after {len(prior)} bars and resumed "
                "from its own checkpoint. The bars it had already traded are read back from its "
                "log rather than traded again, the curves cover the whole session, and "
                "`resumed` states which brain each fly restarted with."
            )
        extra = {
            "live": {
                "product": season.spec.product,
                "bar_seconds": season.spec.bar_seconds,
                "warmup_bars": season.warmup_bars,
                "session_opened": session_opened,
                "bars_traded": len(self.observations),
                "lag_seconds_at_end": lag,
                "polls": feed.polls,
            }
        }
        if resume is not None:
            extra["resumed"] = live_meta["resumed"]
        recording = self._finalise(
            run_id=run_id,
            season=season,
            arms=arms,
            started=started,
            repeat=0,
            overlap=None,
            conditions=conditions,
            out=out,
            wall_mode=wall_mode,
            extra=extra,
        )
        # The checkpoint is the live view of the same session; it stops saying "running" only
        # once the recording it belongs to is on disk.
        checkpoint = out / "live_state.json"
        if checkpoint.exists():
            state = json.loads(checkpoint.read_text())
            state["status"] = "finished"
            state["bars_traded"] = len(self.observations)
            state["summary"] = recording["summary"]
            write_recording(checkpoint, state)
        return recording

    def _restore_resume_brains(self, arms, resume) -> dict:
        """Put each fly's brain back where the killed session left it, and record where that was.

        The learned efficacies never reached the disk on their own: the only brain a killed
        session has is what it checkpointed into ``brains/``, which is at most one cadence
        behind the bar it died on. So this restores the newest snapshot its checkpoint names and
        records which bar that came from — and when there is none, it records that the arm
        restarted from the baseline graph rather than letting a reader assume otherwise. A
        restored brain is not the brain that was killed, and nothing here says it is.
        """
        report: dict = {}
        for arm in arms:
            snapshot = (resume.brains or {}).get(arm.id) or {}
            weights = snapshot.get("weights")
            if snapshot.get("missing"):
                report[arm.id] = {
                    "mode": "baseline",
                    "reason": (
                        f"the checkpoint names {snapshot['missing']} as this fly's brain, and "
                        "it is not in the run directory"
                    ),
                }
                continue
            if weights is None:
                report[arm.id] = {
                    "mode": "baseline",
                    "reason": (
                        "no brain snapshot was written before the kill, so the efficacies this "
                        "fly had learned were in memory and are gone"
                        if arm.learning
                        else "this fly is frozen at the baseline graph by design, so there was "
                        "nothing to lose"
                    ),
                }
                continue
            controller = getattr(arm.backend, "controller", None)
            if controller is None:
                report[arm.id] = {
                    "mode": "baseline",
                    "reason": (
                        f"the {arm.backend.engine} engine keeps no learned state, so there is "
                        "nothing a brain checkpoint could restore"
                    ),
                }
                continue
            # `restore`, not `starting.apply`: an exam freezes the brain it hands a fly, and a
            # resumed competition must keep learning. The checkpoint carries the flag the arm
            # flew with, and a disagreement here would silently freeze one arm of the run.
            controller.restore(weights["file"])
            frozen = bool(getattr(controller.brain, "weights_frozen", not arm.learning))
            if frozen != (not arm.learning):
                raise RuntimeError(
                    f"{arm.id}'s brain snapshot ({weights['label']}) carries "
                    f"weights_frozen={frozen}, which is the wrong flag for an arm with "
                    f"learning={arm.learning}. Restoring it would change what this run "
                    "measures, so it is refused rather than quietly applied."
                )
            bar = snapshot.get("bar")
            report[arm.id] = {
                "mode": "checkpoint",
                "file": weights["file"],
                "label": weights["label"],
                "sha256": weights["sha256"],
                "bar": bar,
                "t": snapshot.get("t"),
                "behind_bars": (
                    len(resume.observations) - int(bar) if bar is not None else None
                ),
            }
        return report

    def _resume_block(self, season, resume, brains, prior) -> dict:
        """What the recording says about a session that was continued rather than started.

        A reader has to be able to tell a continued run from one that never died, and to see
        the parts of it that are not what they would have been: the brain each fly restarted
        with, and the market window the fly's chart was rebuilt from. The earlier continuations
        ride along, so a run that was killed twice says so twice.
        """
        return {
            "run_id": resume.run_id,
            "continued_from_bar": len(prior),
            "continued_from_t": int(prior[-1]["t"]) if prior else None,
            "session_opened": resume.session_opened,
            "killed_at": {
                "status": resume.checkpoint.get("status"),
                "bars_traded": resume.checkpoint.get("bars_traded"),
                "last_bar": resume.checkpoint.get("last_bar"),
            },
            "market": (
                f"the {len(prior)} bars this session had already traded, read back from "
                "observations.jsonl rather than fetched again"
            ),
            "warmup": season.provenance.get("warmup"),
            "accounts": (
                "each arm's SQLite ledger was reopened, not created: cash, positions, anchor "
                "and the order history carry over from the killed session"
            ),
            "brains": brains,
            "dropped_partial_line": bool(resume.dropped_tail),
            "not_recovered": (
                "the decision the flies were making when the process died, and any learning "
                "after the last brain snapshot: neither was ever written down"
            ),
            "earlier": resume.checkpoint.get("resumed"),
        }

    def _checkpoint_brains(self, arms, season, processed, meta) -> None:
        """Snapshot each fly's brain, so a session that dies can be continued with it.

        Each snapshot replaces the previous one — what a continuation wants is the latest state,
        not a history of it — and it is written before the checkpoint that names it, so a
        checkpoint never points at a file that is not there yet. An engine that saves nothing
        (the procedural stand-in) is not reported as having a brain on disk.
        """
        saved = {}
        for arm in arms:
            path = self.out_dir / BRAIN_DIR / f"{arm.id}.npz"
            arm.backend.save(path)
            if path.is_file():
                saved[arm.id] = {
                    "file": f"{BRAIN_DIR}/{path.name}",
                    "bar": int(processed),
                    "t": int(season.timestamp(processed - 1)),
                }
        if saved:
            meta.setdefault("brains", {}).update(saved)

    def _checkpoint_live(self, out, season, feed, session_opened, lag, processed, meta):
        """Make the session's own record durable as it trades.

        A live session can be stopped, powered off or killed at any minute. Each arm's SQLite
        ledger is already durable bar by bar; this appends the observation and rewrites a small
        state file, so a session that never reached its last bar still reads as the session it
        was. The final artifacts are rewritten whole by `_finalise`.

        One `processed` bar means one observation to append — except on the first checkpoint of
        a continuation, which owns no new bar and must not write a second copy of the last bar
        the log already holds.
        """
        if len(self.observations) > self._observations_logged:
            with (out / "observations.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(self.observations[-1], default=str) + "\n")
            self._observations_logged = len(self.observations)
        write_recording(
            out / "live_state.json",
            {
                "schema": SCHEMA_VERSION,
                "run_id": out.name,
                "status": "running",
                "product": season.spec.product,
                "venue": feed.venue,
                "bar_seconds": season.spec.bar_seconds,
                "warmup_bars": season.warmup_bars,
                "session_opened": session_opened,
                "bars_traded": processed,
                "last_bar": season.timestamp(processed - 1) if processed else None,
                "lag_seconds": lag,
                "polls": feed.polls,
                **meta,
            },
        )

    def _idle(self, seconds, stop):
        """Wait between polls, in small slices, so a stop is honoured promptly."""
        deadline = time.monotonic() + max(0.0, float(seconds))
        while not stop() and time.monotonic() < deadline:
            time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))

    # -- per-bar plumbing ------------------------------------------------------------

    def _stimulus(self, i, equity, anchor):
        """The engineered reward/aversive pulse for one bar, under the configured mode.

        `pnl` is upstream's rule: the fly's own marked-to-bid equity change. The other modes
        exist to separate "the memory rule used its own outcome" from "any dopamine pulse
        moves weights at all" — see `docs/activity.md`.
        """
        mode = self.rules.reinforcement
        if mode == "pnl":
            return reinforcement(equity, anchor, self.rules.reward_deadband)
        if mode == "none":
            return "none", D(0)
        if mode == "decoy":
            # The benchmark's change, not the fly's: same distribution of pulses over the
            # season, causally unrelated to anything the fly did.
            if i == 0:
                return "none", D(0)
            curve = self.decoy_curve
            return reinforcement(curve[i], curve[i - 1], self.rules.reward_deadband)
        if mode == "shuffled":
            kind = self.shuffled_schedule[i % len(self.shuffled_schedule)]
            return kind, D(0)
        raise ValueError(f"Unknown reinforcement mode {mode!r}")

    def _prepare_reinforcement(self, season):
        """Build whatever the configured mode needs before the first bar."""
        mode = self.rules.reinforcement
        if mode == "decoy":
            from .benchmark import buy_and_hold

            curve = buy_and_hold(season, D(self.rules.capital), D(self.rules.paper_fee))["curve"]
            self.decoy_curve = [D(str(value)) for value in curve]
        elif mode == "shuffled":
            from .shuffle import load_schedule, permute

            reference = self.config.shuffle_reference
            if not reference:
                raise ValueError(
                    "reinforcement='shuffled' needs a reference recording: pass "
                    "--shuffle-reference <run id>"
                )
            schedule = load_schedule(self.config.out, reference)
            self.shuffled_schedule = permute(schedule, self.config.shuffle_seed)

    def _context(self, i):
        product = self.rules.products[0]
        season = self.season
        quote = season.quote(i)
        history = season.history(i)
        frame = market_frame(product, history, quote.bid, quote.ask)
        return {
            "i": i,
            "timestamp": season.timestamp(i),
            "quote": quote,
            "quotes": {product: quote},
            "frame": frame,
            "history": history,
            "frame_sha256": hashlib.sha256(frame.tobytes()).hexdigest(),
            "product": product,
        }

    def _publish(self, i, context, times, per_arm, bar_started=None):
        quote = context["quote"]
        hashes = {
            record["signal"]["input_sha256"]
            for record in per_arm.values()
            if record and record["signal"] and "input_sha256" in record["signal"]
        }
        identical = len(hashes) <= 1
        self.inputs_identical &= identical
        observation = {
            "i": i,
            "t": context["timestamp"],
            "product": context["product"],
            "market": {
                "bid": str(quote.bid),
                "ask": str(quote.ask),
                "mid": float((quote.bid + quote.ask) / 2),
            },
            "frame_sha256": context["frame_sha256"],
            "same_frame_both_arms": True,
            "same_neural_input_both_arms": identical,
            "arms": per_arm,
        }
        self.observations.append(observation)
        elapsed = (
            time.perf_counter() - bar_started
            if bar_started is not None
            else float(per_arm["gordon"]["compute_seconds"])
        )
        times.append(elapsed)
        self.emit(
            "bar",
            i=i,
            bars=self.season.bars,
            t=context["timestamp"],
            market=observation["market"],
            frame_sha256=context["frame_sha256"],
            arms=per_arm,
            same_neural_input_both_arms=identical,
            elapsed=round(elapsed, 3),
            truncated=self.truncated,
        )

    def _bar(self, arm: Arm, context: dict) -> dict:
        rules = self.rules
        started = time.perf_counter()
        product = rules.products[0]
        quotes = context["quotes"]
        quote = context["quote"]
        blocked = None
        try:
            arm.guard.check(quotes, None)
        except Veto as error:
            blocked = str(error)
            arm.stats["blocked"] += 1
            halted = arm.ledger.get("halted")
            if halted and not arm.stats["halted_reason"]:
                arm.stats["halted_reason"] = halted
        equity = arm.ledger.equity(quotes)
        kind, delta = self._stimulus(context["i"], equity, arm.ledger.get("anchor"))
        signal = None
        if blocked is None:
            signal = arm.backend.observe(context["frame"], kind, context["history"])
            if signal.get("memory"):
                arm.last_memory = signal["memory"]
            arm.ledger.commit_tick(equity, None, None)
            explanation = (
                neural_explanation(signal, rules.decoder_threshold_hz)
                if signal["signal_source"] == "neural"
                else procedural_explanation(signal)
            )
            execution = {"status": "HOLD"}
            if signal["side"] != "HOLD":
                try:
                    plan = arm.guard.plan(product, signal["side"], quotes)
                    plan["neural_observation"] = {
                        "bar": context["i"],
                        "t": context["timestamp"],
                    }
                    plan = arm.ledger.reserve(plan, arm.clock.now())
                    fill = arm.broker.execute(plan, arm.guard.before_submit)
                    price = D(fill["quote"]) / D(fill["base"])
                    arm.stats["fills"] += 1
                    arm.stats["fees_paid"] += D(fill["fee"])
                    execution = {
                        "status": "FILLED",
                        "plan": plan,
                        "fill": {**fill, "price": str(price)},
                        "slippage_limit": str(rules.slippage),
                        "fee_rate": str(rules.paper_fee),
                    }
                    arm.trades.append(
                        {
                            "i": context["i"],
                            "t": context["timestamp"],
                            "side": signal["side"],
                            "product": product,
                            "base_size": fill["base"],
                            "price": str(price),
                            "quote_size": fill["quote"],
                            "fee": fill["fee"],
                            "reason": explanation["steps"][-1],
                            "equity_after": str(arm.ledger.equity(quotes)),
                            "memory_changed_edges": (signal.get("memory") or {}).get(
                                "changed_edges"
                            ),
                        }
                    )
                except Veto as error:
                    execution = {"status": "VETO", "reason": str(error)}
                    arm.stats["vetoes"] += 1
            execution["explanation"] = execution_explanation(
                execution, {"bid": str(quote.bid), "ask": str(quote.ask)}
            )
            decision = {"side": signal["side"], "explanation": explanation}
        else:
            decision = {
                "side": "BLOCKED",
                "explanation": {
                    "kind": "blocked",
                    "rule": (
                        "The execution guard runs before the neural observation, matching "
                        "upstream's order of operations."
                    ),
                    "measured": {},
                    "steps": [
                        f"Guard checked the shared quote before any neural time advanced: {blocked}",
                        "No neural observation was taken on this bar.",
                    ],
                    "result": "BLOCKED",
                },
            }
            execution = {
                "status": "BLOCKED",
                "reason": blocked,
                "explanation": [
                    f"No observation and no order: {blocked}",
                    "Both flies are checked by the same guard rules on every bar.",
                ],
            }
        positions = arm.ledger.positions
        if positions.get(product, D(0)) > 0:
            arm.stats["exposure_bars"] += 1
        equity = arm.ledger.equity(quotes)
        view = portfolio_view(arm.ledger.cash, positions, equity, arm.initial, arm.stats)
        return {
            "signal": _slim_signal(signal),
            "decision": decision,
            "execution": execution,
            "portfolio": view,
            "stimulus": {"kind": kind, "delta_usdc": str(delta)},
            "compute_seconds": round(time.perf_counter() - started, 4),
        }
