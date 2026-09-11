"""Market seasons: one identical bar series per run, shared by every arm.

Two sources, both cached to disk so a season is byte-identical on repeat, on the desktop,
and between competitors:

- ``synthetic``: generated from a seed with a SHA-256 counter stream, so the series does
  not depend on the NumPy or Python version. Labelled synthetic everywhere it is used.
- ``coinbase``: fetched once from the public Coinbase Exchange candle endpoint (no API
  key, no account, no SDK) and stored with a checksum of the raw response.

Bars are converted to a bid/ask quote around the bar close using a fixed half-spread.
Only completed bars are used, and the price history handed to the fly never includes a
bar the fly has not observed yet.
"""

import contextlib
import hashlib
import json
import math
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from decimal import Decimal

from stonkfly.market import Quote

DATA_ROOT = "data"

# Fixed product increments. These are the values upstream's own offline fixture uses for
# BTC-USDC and are applied to every product so the two arms are rounded identically.
INCREMENTS = {
    "base_increment": Decimal("0.00000001"),
    "quote_increment": Decimal("0.01"),
    "price_increment": Decimal("0.01"),
    "minimum_quote": Decimal("1"),
    "minimum_base": Decimal("0.00000001"),
}

COINBASE_CANDLES = "https://api.exchange.coinbase.com/products/{product}/candles"

# Kraken, for the pairs Coinbase Exchange no longer lists. Every product upstream allows —
# BTC-USDC, ETH-USDC, SOL-USDC — is delisted there: `/ticker` answers "Not allowed for
# delisted products" and the candles stop at 2022-07-13, so a live session on Coinbase can
# never see a bar close. Kraken still quotes them.
KRAKEN_OHLC = "https://api.kraken.com/0/public/OHLC"

# Kraken's own pair names, and the bar length it expects in *minutes*.
KRAKEN_PAIRS = {"BTC-USDC": "XBTUSDC", "ETH-USDC": "ETHUSDC", "SOL-USDC": "SOLUSDC"}
KRAKEN_MINUTES = {60: 1, 300: 5, 900: 15, 1800: 30, 3600: 60, 14400: 240, 86400: 1440}

# Bump when the *fetch semantics* change, not just the parameters. The cache is keyed by
# product/offset/bars, so a change in what an offset means would otherwise be served from
# files an older build wrote — which is exactly how a fixed window offset kept producing
# overlapping seasons. v2: `window_offset_bars` counts rows rather than minutes.
CACHE_VERSION = 2


def _seed_stream(seed: int):
    """Deterministic uniform stream: independent of Python/NumPy RNG internals."""
    counter = 0
    while True:
        digest = hashlib.sha256(f"flyvsly:{seed}:{counter}".encode()).digest()
        for offset in range(0, 32, 4):
            word = int.from_bytes(digest[offset : offset + 4], "big")
            yield word / 2**32
        counter += 1


def _uniforms(seed: int, count: int) -> list[float]:
    stream = _seed_stream(seed)
    return [next(stream) for _ in range(count)]


def synthetic_closes(spec) -> list[float]:
    """Trend-and-volatility-regime series. Synthetic, and labelled as such."""
    need = spec.bars + 120
    draws = _uniforms(spec.seed, need * 12)
    price = float(Decimal(spec.initial_price))
    closes = []
    # Four regimes over the season so the two flies see trends, chop and a selloff.
    regimes = [
        (0.35, 0.0, need // 4),
        (0.9, 0.0, need // 2),
        (0.5, 0.0, 3 * need // 4),
        (1.4, 0.0, need),
    ]
    for i in range(need):
        j = 0
        while j < len(regimes) - 1 and i >= regimes[j][2]:
            j += 1
        vol, _, _ = regimes[j]
        block = draws[i * 12 : i * 12 + 12]
        # Irwin-Hall(12) - 6: bounded, deterministic, version-stable normal proxy.
        shock = sum(block) - 6.0
        # A slow drift that only becomes visible after long stretches of noise.
        drift = 0.00035 * math.sin(i / 47.0) + 0.00012 * math.sin(i / 11.0)
        step = drift + 0.0016 * vol * shock
        price = max(price * (1.0 + step), 0.01)
        closes.append(round(price, 2))
    return closes


def kraken_candles(product: str, bar_seconds: int) -> list[list]:
    """The newest page of Kraken's public OHLC, in the shape :func:`_candles` returns.

    Kraken reports ``[time, open, high, low, close, vwap, volume, count]``, oldest first, with
    the minute still forming as the last row — the same trap the Coinbase endpoint sets, and
    the reason the live feed only ever trades a row whose period has ended.

    Public, keyless, and read once per poll: this is the same class of request as the
    Coinbase one, just at a venue that still trades these pairs.
    """
    pair = KRAKEN_PAIRS.get(product)
    if pair is None:
        raise ValueError(
            f"Kraken does not list {product!r} under a name this project knows; "
            f"known pairs: {', '.join(sorted(KRAKEN_PAIRS))}"
        )
    minutes = KRAKEN_MINUTES.get(int(bar_seconds))
    if minutes is None:
        raise ValueError(
            f"Kraken intervals are fixed ({', '.join(str(m * 60) for m in sorted(KRAKEN_MINUTES))} "
            f"seconds); {bar_seconds} is not one of them"
        )
    url = KRAKEN_OHLC + "?" + urllib.parse.urlencode({"pair": pair, "interval": minutes})
    request = urllib.request.Request(url, headers={"User-Agent": "flyvsly/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as error:
        raise RuntimeError(
            f"Kraken OHLC request failed ({error.code}) for {url}. "
            "Use a synthetic season if the venue is unreachable."
        ) from error
    except urllib.error.URLError as error:
        raise RuntimeError(
            f"Cannot reach the Kraken public OHLC endpoint ({error.reason}). "
            "Synthetic seasons work offline."
        ) from error
    if payload.get("error"):
        raise RuntimeError(f"Kraken OHLC error for {pair}: {payload['error']}")
    result = payload.get("result") or {}
    rows = next((value for key, value in result.items() if key != "last" and isinstance(value, list)), [])
    # Normalise to the Coinbase column order so one parser reads both venues.
    return [[int(row[0]), float(row[3]), float(row[2]), float(row[1]), float(row[4]), float(row[6])] for row in rows]


def _candles(product: str, bar_seconds: int, start=None, end=None) -> list[list]:
    """One page (at most 300 buckets) from the public candle endpoint."""
    params = {"granularity": bar_seconds}
    if start:
        params["start"] = start
    if end:
        params["end"] = end
    url = COINBASE_CANDLES.format(product=product) + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": "flyvsly/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        detail = ""
        with contextlib.suppress(Exception):
            detail = error.read().decode()[:200]
        raise RuntimeError(
            f"Coinbase candle request failed ({error.code}) {detail} for {url}. "
            "Use an explicit --start window, or a synthetic season."
        ) from error
    except urllib.error.URLError as error:
        raise RuntimeError(
            f"Cannot reach the Coinbase public candle endpoint ({error.reason}). "
            "Synthetic seasons work offline."
        ) from error


def _fetch_coinbase(
    product: str, bars: int, bar_seconds: int, start_iso=None, offset_bars=0, minimum=None
) -> dict:
    """Collect up to `bars` completed candles, and at least `minimum`, with both bounds.

    `bars` includes the warm-up the fly sees on its first chart. The public exchange does
    not always reach back far enough for a long warm-up, and it omits minutes with no
    trades, so a shortfall is tolerated down to `minimum` (the tradable season itself) and
    reported as `truncated_warmup` instead of failing the run.

    The window is anchored to the newest bucket the exchange actually has — not to this
    machine's clock — so a cached season is reproducible even where the local clock and the
    exchange disagree, and a recorded run never depends on wall time.

    `offset_bars` counts *bars*, not minutes: the endpoint omits minutes with no trades, so a
    48-bar season spans about 64 minutes of market time and stepping back 48 minutes leaves a
    quarter of the "new season" inside the previous one. Fetching `bars + offset` rows and
    keeping the older `bars` of them makes the offset mean what its name says.

    The public endpoint ignores `end` on its own, so both bounds are always sent.
    """
    rows: dict[int, list] = {}
    wanted = bars + max(0, offset_bars)
    if start_iso:
        start = int(_iso_seconds(start_iso))
        end = start + wanted * bar_seconds
    else:
        probe = _candles(product, bar_seconds)
        if not probe:
            raise RuntimeError(f"Coinbase returned no recent candles for {product}")
        newest = max(int(row[0]) for row in probe)
        # `wanted` rows ending at the newest completed bucket; the offset is applied by
        # dropping rows from the end below, so it counts bars whatever the gaps are.
        end = newest - bar_seconds
        start = end - (wanted - 1) * bar_seconds
    # The endpoint omits minutes with no trades, so a window can come back short. Widen it
    # backwards and retry rather than pretending the missing minutes exist.
    attempts = 0
    while True:
        cursor = start
        while cursor <= end:
            chunk_end = min(cursor + 299 * bar_seconds, end)
            for row in _candles(product, bar_seconds, _iso(cursor), _iso(chunk_end)):
                timestamp = int(row[0])
                if start <= timestamp <= end:
                    rows[timestamp] = row
            cursor = chunk_end + bar_seconds
        completed = [rows[t] for t in sorted(rows)]
        if len(completed) >= wanted or attempts >= 6:
            break
        attempts += 1
        start -= (wanted - len(completed)) * bar_seconds

    # The offset shifts the window back by whole rows, whatever the gaps are. `end_index` is
    # the row the season ends at; the window is the `bars` rows before it.
    end_index = len(completed) - max(0, offset_bars)
    floor = bars if minimum is None else minimum
    if end_index - bars < 0 or len(completed[:end_index]) < floor:
        raise RuntimeError(
            f"Coinbase returned {len(completed)} completed bars, need {wanted} "
            f"({_iso(start)} to {_iso(end)}) to place a {bars}-bar window {offset_bars} bars "
            "back. Check the requested window or reduce the offset."
        )
    window = completed[end_index - bars : end_index]
    return {
        "bars": [{"t": int(r[0]), "close": float(r[4])} for r in window],
        "raw_sha256": hashlib.sha256(
            json.dumps(sorted(rows.items()), separators=(",", ":")).encode()
        ).hexdigest(),
        "endpoint": COINBASE_CANDLES.format(product=product),
        "requested_start": start_iso,
        "window_start": _iso(window[0][0]),
        "window_end": _iso(window[-1][0]),
        "truncated_warmup": len(window) - bars < 0,
        "warmup_bars": max(0, len(window) - bars),
        "offset_bars": offset_bars,
    }


def _iso(seconds: int) -> str:
    import datetime

    return (
        datetime.datetime.fromtimestamp(seconds, datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _iso_seconds(value: str) -> float:
    import datetime

    parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Season start needs an explicit timezone")
    return parsed.timestamp()


@dataclass(frozen=True)
class Season:
    spec: object
    closes: list[float]   # 120-bar visible warm-up, then the tradable season
    times: list[int]      # real (or synthetic) timestamps, one per close
    provenance: dict

    @property
    def seed_bars(self) -> int:
        """Warm-up bars visible on the first chart.

        Derived from the loaded data rather than fixed at 120, because the exchange does not
        always reach back far enough for a full warm-up window.
        """
        return max(1, len(self.closes) - self.spec.bars)

    @property
    def bars(self) -> int:
        return self.spec.bars

    def mid(self, i: int) -> float:
        """Mid close of tradable bar ``i``."""
        return self.closes[self.seed_bars + i]

    def timestamp(self, i: int) -> int:
        """Market time of tradable bar ``i``.

        Real timestamps for a Coinbase season, evenly spaced ones for a synthetic season.
        Gaps are preserved rather than smoothed: a missing exchange minute makes the
        market clock jump, which is the exchange's own record of time.
        """
        return int(self.times[self.seed_bars + i])

    def quote(self, i: int, product: str | None = None) -> Quote:
        spec = self.spec
        product = product or spec.product
        step = INCREMENTS["price_increment"]
        mid = Decimal(str(self.mid(i)))
        half = Decimal(str(spec.half_spread_bps / 10000.0))
        bid = (mid * (1 - half)).quantize(step).copy_abs()
        ask = (mid * (1 + half)).quantize(step).copy_abs()
        if bid <= 0:
            bid = step
        if ask < bid:
            ask = bid
        return Quote(
            product,
            bid,
            ask,
            float(self.timestamp(i)),
            INCREMENTS["base_increment"],
            INCREMENTS["quote_increment"],
            INCREMENTS["price_increment"],
            INCREMENTS["minimum_quote"],
            INCREMENTS["minimum_base"],
        )

    def history(self, i: int) -> list[float]:
        """Closes visible to the fly at bar ``i``. Never includes future bars."""
        start = i + self.seed_bars
        window = self.closes[max(0, start - 119) : start + 1]
        return [float(v) for v in window]

    def describe(self) -> str:
        return self.spec.describe()


def build_season(spec, cache_dir="data/markets") -> Season:
    import pathlib

    cache = pathlib.Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    if spec.kind == "synthetic":
        closes = synthetic_closes(spec)
        epoch = int(_iso_seconds("2026-01-01T00:00:00Z"))
        times = [epoch + i * spec.bar_seconds for i in range(len(closes))]
        digest = hashlib.sha256(
            json.dumps(closes, separators=(",", ":")).encode()
        ).hexdigest()
        return Season(
            spec,
            closes,
            times,
            {
                "source": "synthetic",
                "generator": "flyvsly.synthetic-v1 (SHA-256 counter stream, Irwin-Hall shocks)",
                "seed": spec.seed,
                "series_sha256": digest,
                "window_start": _iso(times[0]),
                "window_end": _iso(times[-1]),
                "disclaimer": "Synthetic price path. Not real market data.",
            },
        )
    window = (spec.start_iso or f"auto-{spec.window_offset_bars}").replace(":", "")
    name = f"{spec.product}-{spec.bar_seconds}s-v{CACHE_VERSION}-{window}-{spec.bars}.json"
    path = cache / name
    if path.exists():
        payload = json.loads(path.read_text())
    else:
        payload = _fetch_coinbase(
            spec.product,
            spec.bars + 120,
            spec.bar_seconds,
            spec.start_iso,
            spec.window_offset_bars,
            minimum=spec.bars,
        )
        payload["start_iso"] = spec.start_iso
        path.write_text(json.dumps(payload, indent=1) + "\n")
    rows = payload["bars"]
    closes = [float(r["close"]) for r in rows]
    times = [int(r["t"]) for r in rows]
    return Season(
        spec,
        closes,
        times,
        {
            "source": "coinbase-public-candles",
            "endpoint": payload.get("endpoint"),
            "raw_response_sha256": payload.get("raw_sha256"),
            "window_start": payload.get("window_start"),
            "window_end": payload.get("window_end"),
            "warmup_bars": payload.get("warmup_bars"),
            "truncated_warmup": payload.get("truncated_warmup", False),
            "series_sha256": hashlib.sha256(
                json.dumps(closes, separators=(",", ":")).encode()
            ).hexdigest(),
            "cache_file": str(path),
            "disclaimer": (
                "Real public Coinbase Exchange one-minute candle closes. Paper fills only; "
                "no account, key or order is involved."
            ),
        },
    )
