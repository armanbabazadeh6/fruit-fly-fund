"""Context benchmarks: buy-and-hold and cash, on the identical bar series and fee.

Both competitors face upstream's execution rules: at most $10 per order, at least 60
seconds of market time between attempts, at most 24 attempts per UTC day. Buy-and-hold
faces none of those limits — it makes one unrestricted fill at the first bar. That
asymmetry is intentional and is stated in the UI, because rising prices alone make almost
any buyer look skilled.
"""

from decimal import Decimal

from stonkfly.config import D, down

from .market import INCREMENTS


def buy_and_hold(season, capital: Decimal, fee_rate: Decimal) -> dict:
    first = season.quote(0)
    step = INCREMENTS["base_increment"]
    budget = capital / (1 + fee_rate)
    size = down(budget / first.ask, step)
    cost = (size * first.ask).quantize(Decimal("0.00000001"))
    fee = (cost * fee_rate).quantize(Decimal("0.00000001"))
    cash = capital - cost - fee
    curve = []
    for i in range(season.bars):
        equity = cash + size * season.quote(i).bid
        curve.append(float(equity))
    return {
        "id": "buy_and_hold",
        "label": "Buy & hold",
        "detail": (
            "One unrestricted fill at the first bar's ask, then never trades. "
            "Faces no order-size, cooldown or daily-order limits."
        ),
        "initial_capital": str(capital),
        "base_size": str(size),
        "entry_price": str(first.ask),
        "entry_fee": str(fee),
        "residual_cash": str(cash),
        "curve": curve,
        "fills": 1,
    }


def cash(season, capital: Decimal) -> dict:
    return {
        "id": "cash",
        "label": "Cash",
        "detail": "Never trades. The floor any strategy has to beat to be worth running.",
        "initial_capital": str(capital),
        "curve": [float(capital) for _ in range(season.bars)],
        "fills": 0,
    }


def benchmark_deployment(arm_summary: dict, season, rules) -> dict:
    """How exposed each competitor actually got.

    A fly that only ever held cash cannot be compared on returns alone, and a fly that
    spent most of the season unable to buy is telling you about the rule set as much as
    about the market.
    """
    exposure = arm_summary.get("exposure_bars", 0)
    return {
        "bars": season.bars,
        "bars_holding": exposure,
        "holding_fraction": round(exposure / season.bars, 4) if season.bars else 0.0,
        "order_limit_usdc": str(rules.order_limit),
        "daily_order_limit": rules.daily_orders,
        "cooldown_seconds": rules.interval_seconds,
    }
