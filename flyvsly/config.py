"""Frozen rule set for the competition.

Every field here is passed to upstream's own `stonkfly.config.Settings` for both
competitors. `learning` is the single experimental variable and is never read from this
file directly: `arm_settings()` sets it per arm, and `fairness.assert_only_learning_differs`
proves at runtime that nothing else differs between the two accounts.

Values are upstream defaults, not tuned. `capital` and `order_limit` sit inside the caps
Stonkfly enforces for its own paper and live paths ($100 and $10). We deliberately do not
relax them: both flies trade the same restricted order book discipline, and the two
benchmarks are reported with that asymmetry stated.
"""

import dataclasses
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

# The one and only experimental difference between the two competitors.
EXPERIMENTAL_VARIABLE = "learning"

# Rule fields this project adds on top of upstream's Settings. They must be identical for
# both arms, and they are recorded in the fairness block so a reader can see them.
EXTENSION_FIELDS = (
    "require_gate",
    "reinforcement",
    "population_sample",
    "readout",
    "readout_margin",
)

# What kind of comparison a run is. The two flies always differ in exactly one thing:
#   competition - live: one learns, one is frozen (the default)
#   exam        - neither learns; one starts from weights learned in an earlier season
#   reset       - like exam, but the learned efficacies are wiped back to the reconstructed
#                 baseline first, which asks whether the weights are where any advantage lives
RUN_KINDS = ("competition", "exam", "reset")

REINFORCEMENT_MODES = ("pnl", "decoy", "shuffled", "none")

# Activity presets. `upstream` keeps Stonkfly's conservative live-trading caps; `active`
# raises only the paper-only execution limits, identically for both flies.
# `active` cannot raise the per-order cap: upstream's Settings validates order_limit against
# min(capital, 10) and capital against 100, and this project does not patch vendored code. So
# activity comes from the two levers that are ours — the gate, and the daily order limit
# (upstream allows up to 100) — plus running more bars.
# Over a long season the binding constraint stops being the gate and becomes the wallet:
# with $10 orders on $100 of capital, a fly is fully invested after about nine fills and every
# later BUY is vetoed for funds. `scalper` takes the other end of what upstream's validation
# permits — many small orders — which is how a 48-bar season can fill on nearly every bar.
PRESETS = {
    "upstream": {"order_limit": "10", "daily_orders": 24, "require_gate": True},
    "active": {"order_limit": "10", "daily_orders": 100, "require_gate": False},
    "scalper": {"order_limit": "2", "daily_orders": 100, "require_gate": False},
}


@dataclass(frozen=True)
class ArenaRules:
    """Shared market, fee and execution rules. Identical for both arms."""

    products: tuple[str, ...] = ("BTC-USDC",)
    capital: str = "100"
    order_limit: str = "10"
    loss_stop: str = "20"
    fee_reserve: str = "0.02"
    slippage: str = "0.005"
    spread_limit: str = "0.005"
    daily_orders: int = 24
    interval_seconds: float = 60
    max_quote_age: float = 15
    neural_ms: float = 500
    neural_bin_ms: float = 10
    pulse_ms: float = 200
    pulse_current: float = 20
    reward_deadband: str = "0.01"
    decoder_threshold_hz: float = 2
    paper_fee: str = "0.006"

    # --- our extensions, not upstream's trading rules -------------------------------
    # Upstream requires a DNpe017 spike before the DNp20 difference may act. Dropping that
    # requirement is the single biggest lever on how much the flies trade; measured, the
    # gate was open on 27-60% of bars while the directional difference exceeded threshold on
    # 90-98%. Applied identically to both flies and recorded in every run.
    require_gate: bool = True
    # How the engineered reward/aversive pulse is scheduled. See `docs/activity.md`.
    #   pnl     - upstream: the fly's own marked-to-bid equity change (default)
    #   decoy   - the buy-and-hold benchmark's change: same statistics, decoupled from the
    #             fly's own actions, so a difference cannot be read as credit assignment
    #   shuffled- a seeded permutation of a reference run's pulse schedule
    #   none    - no pulse at all
    reinforcement: str = "pnl"
    # How many cells the recorded population vector carries (see flyvsly/population.py).
    population_sample: int = 256
    # A fitted readout replaces the fixed DNp20 rule when set (see flyvsly/readout.py). The
    # same model file is used for both flies: the model is shared, the brain is not.
    readout: str | None = None
    readout_margin: float = 0.15

    def as_settings_kwargs(self, learning: bool) -> dict:
        # Upstream's Settings knows nothing about our extensions, so they are stripped here
        # and carried separately. `fairness.starting_conditions` records them.
        payload = dataclasses.asdict(self)
        for field in EXTENSION_FIELDS:
            payload.pop(field)
        return {**payload, EXPERIMENTAL_VARIABLE: bool(learning)}

    def signature(self) -> str:
        payload = json.dumps(dataclasses.asdict(self), sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def validate(self):
        if self.reinforcement not in REINFORCEMENT_MODES:
            raise ValueError(
                f"Unknown reinforcement mode {self.reinforcement!r}; "
                f"expected one of {REINFORCEMENT_MODES}"
            )
        if self.population_sample < 0:
            raise ValueError("population_sample cannot be negative")
        if not 0 <= self.readout_margin <= 1:
            raise ValueError("readout_margin must be between 0 and 1")
        return self


@dataclass(frozen=True)
class MarketSpec:
    """A season of identical bars fed to every arm.

    `synthetic` seasons are generated from a seed and labelled as synthetic in the
    recording. `coinbase` seasons are cached from the public Coinbase Exchange candle
    endpoint once, checksummed, and then replayed identically for every arm and every
    repeat.
    """

    kind: str = "synthetic"          # "synthetic" | "coinbase"
    product: str = "BTC-USDC"
    bars: int = 480
    bar_seconds: int = 60
    seed: int = 7
    start_iso: str | None = None     # coinbase only; None anchors to the newest data
    window_offset_bars: int = 0      # coinbase repeats step this far further back
    half_spread_bps: float = 2.5     # per side, from the mid close of each bar
    initial_price: str = "60000"     # synthetic only

    def describe(self) -> str:
        if self.kind == "synthetic":
            return f"synthetic:{self.product}:seed={self.seed}:bars={self.bars}"
        window = self.start_iso or f"newest-{self.window_offset_bars}bars"
        return f"coinbase:{self.product}:{window}:bars={self.bars}"

    def signature(self) -> str:
        payload = json.dumps(dataclasses.asdict(self), sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def validate(self):
        if self.reinforcement not in REINFORCEMENT_MODES:
            raise ValueError(
                f"Unknown reinforcement mode {self.reinforcement!r}; "
                f"expected one of {REINFORCEMENT_MODES}"
            )
        if self.population_sample < 0:
            raise ValueError("population_sample cannot be negative")
        if not 0 <= self.readout_margin <= 1:
            raise ValueError("readout_margin must be between 0 and 1")
        return self


@dataclass(frozen=True)
class ArenaConfig:
    """Run-level configuration, separate from the rules both flies obey."""

    rules: ArenaRules = field(default_factory=ArenaRules)
    market: MarketSpec = field(default_factory=MarketSpec)
    engine: str = "neural"           # "neural" | "procedural"
    repeats: int = 1
    out: Path = Path("runs")
    label: str | None = None
    checkpoint_every: int = 0        # 0 = only at end of run
    shuffle_reference: str | None = None   # run id whose pulse schedule is permuted
    shuffle_seed: int = 0
    preset: str = "upstream"
    # competition | exam | reset. See RUN_KINDS.
    kind: str = "competition"
    # Per-arm starting weights, e.g. {"gordon": "trained:runs/brains/train/gordon.npz",
    # "warren": "baseline"}. Parsed by flyvsly/starting.py.
    starting: dict = field(
        default_factory=lambda: {"gordon": "baseline", "warren": "baseline"}
    )
    # Where to write each arm's checkpoint at the end of the run, for a later exam.
    save_brains: Path | None = None
    # A reference recording whose population vectors are fitted into a readout, and the
    # label to record as provenance for it.
    fit_readout: Path | None = None
    thread_arms: bool = True         # arms are independent; the native kernel releases the GIL
    max_wall_seconds: float | None = None

    def validate(self):
        if self.kind not in RUN_KINDS:
            raise ValueError(f"Unknown run kind {self.kind!r}; expected one of {RUN_KINDS}")
        if self.engine not in ("neural", "procedural"):
            raise ValueError(f"Unknown engine: {self.engine}")
        if self.repeats < 1:
            raise ValueError("repeats must be >= 1")
        if self.market.kind not in ("synthetic", "coinbase"):
            raise ValueError(f"Unknown market kind: {self.market.kind}")
        if self.market.bars < 4:
            raise ValueError("A season needs at least 4 bars")
        if self.market.bar_seconds < self.rules.interval_seconds:
            raise ValueError(
                "Bar interval is shorter than the execution cooldown; the cooldown rule "
                "would veto nearly every order. Raise bar_seconds or lower interval_seconds."
            )
        if self.market.bars % 2:
            raise ValueError("Use an even number of bars so ordered pairs stay aligned")
        if set(self.starting) != {"gordon", "warren"}:
            raise ValueError("starting weights must name both flies")
        if self.kind != "competition":
            # An exam is not about learning during the run: both flies are frozen, and the
            # experimental variable is the brain each one carries in.
            if any(spec != "baseline" for spec in self.starting.values()) is False:
                raise ValueError(
                    f"A {self.kind} run needs at least one fly starting from trained "
                    "weights: pass --starting gordon=trained:<checkpoint>"
                )
        elif any(spec != "baseline" for spec in self.starting.values()):
            raise ValueError(
                "competition runs start both flies from baseline weights; "
                "use --kind exam to start from trained weights"
            )
        return self
