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


def personas_for(kind: str) -> tuple:
    """Who the two flies are in this kind of run.

    A competition differs in `learning`; an exam or a reset freezes both and differs in the
    brain each one carried in, so the role labels have to say that rather than claim one fly
    is learning when it is not.
    """
    if kind == "competition":
        return PERSONAS
    trained = "trained brain, frozen" if kind == "exam" else "trained, then reset, frozen"
    return (
        {
            **PERSONAS[0],
            # Frozen, both of them. The persona labels said "frozen" while `learning` stayed
            # True on the experimental arm, so the exam was still learning during the exam and
            # the fairness block claimed otherwise. The flag is what matters, not the label.
            "learning": False,
            "role": "experimental",
            "role_label": trained.capitalize(),
            "tagline": "Brought weights from an earlier season.",
            "detail": (
                "Both flies run with their weight updates frozen: nothing is learned during "
                "this run, so the only difference between the two is the brain each one "
                "carried in."
            ),
        },
        {
            **PERSONAS[1],
            "learning": False,
            "role": "control",
            "role_label": "Fresh brain, frozen",
            "tagline": "Never trained on anything.",
            "detail": (
                "Identical rules and identical frozen updates; its 7,835 eligible efficacies "
                "are the reconstructed baseline."
            ),
        },
    )


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
                VirtualClock(self.arena.season.timestamp(0)),
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


class Arena:
    def __init__(self, config: ArenaConfig, on_event=None, data_root="data"):
        self.config = config.validate()
        self.on_event = on_event or (lambda kind, payload: None)
        self.data_root = data_root
        self.rules = config.rules
        self.season = None
        self.out_dir = None
        self.personas = personas_for(config.kind)

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

        conditions = starting_conditions(rules, config.kind, config.starting)
        if config.kind == "competition":
            assert_only_learning_differs(arm_settings(rules, True), arm_settings(rules, False))

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
        duration = time.time() - started

        benchmarks = {
            "buy_and_hold": buy_and_hold(season, D(rules.capital), D(rules.paper_fee)),
            "cash": cash(season, D(rules.capital)),
        }
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
                "wall_mode": (
                    "accelerated replay: one completed market bar per observation, no "
                    "wall-clock waiting"
                ),
                "inputs_identical_every_bar": self.inputs_identical,
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
            arms.append(
                Arm(
                    persona,
                    self.rules,
                    backend,
                    out,
                    VirtualClock(self.season.timestamp(0)),
                ).open_account()
            )
        self.emit("arms_ready", arms=[arm.describe() for arm in arms])
        self.observations = []
        self.seconds_per_bar_mean = 0.0
        self.truncated = False
        self.inputs_identical = True
        times = []
        for i in range(self.season.bars):
            context = self._context(i)
            per_arm = {}
            for arm in arms:
                arm.clock.set(context["timestamp"])
                per_arm[arm.id] = self._bar(arm, context)
                arm.curve.append(float(per_arm[arm.id]["portfolio"]["equity"]))
            self._publish(i, context, times, per_arm)
        self.seconds_per_bar_mean = round(sum(times) / len(times), 4) if times else 0.0
        for arm in arms:
            arm.close()
        return arms

    def _run_parallel(self, run_id, season, out):
        """One thread per arm. Both arms receive the same frame object every bar."""
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

        self.observations = []
        self.seconds_per_bar_mean = 0.0
        self.truncated = False
        self.inputs_identical = True
        times = []
        budget = self.config.max_wall_seconds
        started = time.monotonic()
        try:
            for i in range(season.bars):
                context = self._context(i)
                bar_started = time.perf_counter()
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
                for arm in arms:
                    arm.curve.append(float(per_arm[arm.id]["portfolio"]["equity"]))
                self._publish(i, context, times, per_arm, bar_started)
                if budget and time.monotonic() - started > budget:
                    self.truncated = True
                    break
        finally:
            for thread in threads:
                thread.stop.set()
                thread.go.set()
            for thread in threads:
                thread.join(timeout=120)
        self.seconds_per_bar_mean = round(sum(times) / len(times), 4) if times else 0.0
        return arms

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
