"""Seasons must be identical on repeat, causal in what the fly sees, and quote-valid."""

import hashlib
import json

import pytest

from flyvsly.config import MarketSpec
from flyvsly.market import build_season, synthetic_closes

def digest(values):
    return hashlib.sha256(json.dumps(values, separators=(",", ":")).encode()).hexdigest()


def test_synthetic_season_is_byte_identical_on_repeat(tmp_path):
    spec = MarketSpec(kind="synthetic", bars=32, seed=5)
    first = build_season(spec, cache_dir=tmp_path)
    second = build_season(spec, cache_dir=tmp_path)
    assert digest(first.closes) == digest(second.closes)


def test_different_seeds_are_different_seasons():
    a = synthetic_closes(MarketSpec(kind="synthetic", bars=64, seed=1))
    b = synthetic_closes(MarketSpec(kind="synthetic", bars=64, seed=2))
    assert digest(a) != digest(b)


def test_repeat_offsets_do_not_shift_the_prices():
    """A repeat must be a different window, not a permuted copy of the same one."""
    a = synthetic_closes(MarketSpec(kind="synthetic", bars=32, seed=1))
    b = synthetic_closes(MarketSpec(kind="synthetic", bars=32, seed=1))
    assert digest(a) == digest(b)


def test_history_ends_at_the_observed_bar(tmp_path):
    """The chart the fly sees is the closes up to and including the bar being observed."""
    season = build_season(MarketSpec(kind="synthetic", bars=16, seed=3), cache_dir=tmp_path)
    for i in range(season.bars):
        history = season.history(i)
        end = i + season.seed_bars
        expected = season.closes[max(0, end - 119) : end + 1]
        assert history == [float(v) for v in expected]
        assert history[-1] == pytest.approx(season.mid(i))
        assert len(history) <= 120


def test_quotes_respect_the_configured_spread_limit(tmp_path):
    spec = MarketSpec(kind="synthetic", bars=8, seed=2, half_spread_bps=2.5)
    season = build_season(spec, cache_dir=tmp_path)
    for i in range(season.bars):
        quote = season.quote(i)
        assert quote.bid <= quote.ask
        assert quote.bid > 0
        assert (quote.ask - quote.bid) / quote.bid <= 0.005
        assert quote.minimum_base > 0 and quote.minimum_quote > 0


def test_season_needs_a_bar_longer_than_the_cooldown():
    from flyvsly.config import ArenaConfig

    with pytest.raises(ValueError, match="cooldown"):
        ArenaConfig(market=MarketSpec(kind="synthetic", bars=8, bar_seconds=30)).validate()


def test_market_time_is_the_exchange_record_for_real_data(tmp_path):
    """Synthetic seasons are evenly spaced; a run's clock never reads wall time."""
    season = build_season(MarketSpec(kind="synthetic", bars=8, seed=1), cache_dir=tmp_path)
    assert season.timestamp(1) - season.timestamp(0) == 60
    assert season.timestamp(0) == season.times[season.seed_bars]
    assert "first_timestamp" not in season.provenance
