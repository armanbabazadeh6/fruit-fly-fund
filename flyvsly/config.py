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

    def as_settings_kwargs(self, learning: bool) -> dict:
        return {**dataclasses.asdict(self), EXPERIMENTAL_VARIABLE: bool(learning)}

    def signature(self) -> str:
        payload = json.dumps(dataclasses.asdict(self), sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


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
    thread_arms: bool = True         # arms are independent; the native kernel releases the GIL
    max_wall_seconds: float | None = None

    def validate(self):
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
        return self
