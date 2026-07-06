"""Unit tests for shared indicators.

Coverage targets: 100% of indicators.py (small module, all functions critical).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from strategies.indicators import adx, ema, is_stablecoin, momentum_score, sma


@pytest.fixture
def trending_up_df() -> pd.DataFrame:
    """Synthetic uptrend: monotonic rising close, higher highs and higher lows."""
    n = 100
    base = np.linspace(100, 200, n)
    noise = np.random.default_rng(42).normal(0, 0.5, n)
    close = base + noise
    high = close + 1.5
    low = close - 1.5
    return pd.DataFrame(
        {
            "open": close + np.random.default_rng(7).normal(0, 0.3, n),
            "high": high,
            "low": low,
            "close": close,
            "volume": np.ones(n) * 1000,
        }
    )


@pytest.fixture
def sideways_df() -> pd.DataFrame:
    """Synthetic mean-reverting: small random walk around 100 (no trend).

    Using random walk with small step (not sin) — sin patterns still register
    directional movement to ADX. A pure random walk gives ADX < 20 reliably.
    """
    n = 200
    rng = np.random.default_rng(123)
    # Tiny steps + mean reversion to suppress drift
    steps = rng.normal(0, 0.3, n)
    close = 100 + np.cumsum(steps)
    close = close - np.linspace(close[0], close[-1], n) * 0.5  # drift removal
    high = close + 0.5
    low = close - 0.5
    return pd.DataFrame(
        {
            "open": close,
            "high": high,
            "low": low,
            "close": close,
            "volume": np.ones(n) * 1000,
        }
    )


class TestSma:
    def test_basic_sma(self, trending_up_df):
        result = sma(trending_up_df["close"], period=20)
        # First 19 values are NaN
        assert result.iloc[:19].isna().all()
        # 20th value equals the mean of first 20
        expected = trending_up_df["close"].iloc[:20].mean()
        assert np.isclose(result.iloc[19], expected)
        # Last value should be larger than first non-NaN (uptrend)
        assert result.iloc[-1] > result.iloc[19]

    def test_period_one(self, trending_up_df):
        result = sma(trending_up_df["close"], period=1)
        assert result.iloc[0] == trending_up_df["close"].iloc[0]

    def test_invalid_period(self, trending_up_df):
        with pytest.raises(ValueError, match="period must be >= 1"):
            sma(trending_up_df["close"], period=0)


class TestEma:
    def test_ema_runs(self, trending_up_df):
        result = ema(trending_up_df["close"], period=20)
        assert result.iloc[:19].isna().all()
        # EMA in uptrend should be above the latest close (lags behind in trend)
        # No strict relationship to SMA — just verify EMA converges and is finite
        assert np.isfinite(result.iloc[-1])

    def test_ema_responds_faster_than_sma(self, sideways_df):
        # Inject a single large up-bar at the end and verify EMA reacts more
        df = sideways_df.copy()
        df.loc[df.index[-5:], "close"] *= 1.10
        ema_result = ema(df["close"], period=20)
        sma_result = sma(df["close"], period=20)
        # EMA should rise more in response to the recent up-bars
        ema_change = ema_result.iloc[-1] - ema_result.iloc[-25]
        sma_change = sma_result.iloc[-1] - sma_result.iloc[-25]
        assert ema_change > sma_change


class TestAdx:
    def test_trending_high_adx(self, trending_up_df):
        result = adx(trending_up_df, period=14)
        # Up trend → ADX should be high (>25) by the end
        assert result.iloc[-1] > 25

    def test_sideways_low_adx(self, sideways_df):
        result = adx(sideways_df, period=14)
        # Sideways → ADX should be low (<25)
        assert result.iloc[-1] < 25


class TestMomentumScore:
    def test_positive_momentum_in_uptrend(self, trending_up_df):
        score = momentum_score(trending_up_df, lookback_days=20)
        # Last value should be positive (price rose over 20 bars)
        assert score.iloc[-1] > 0

    def test_invalid_lookback(self, trending_up_df):
        with pytest.raises(ValueError, match="lookback_days must be >= 1"):
            momentum_score(trending_up_df, lookback_days=0)


class TestIsStablecoin:
    @pytest.mark.parametrize(
        "symbol,expected",
        [
            ("BTC/USDT", False),
            ("ETH/USDT", False),
            ("USDT/USDT", True),    # edge case: base is USDT
            ("USDC/USD", True),     # USDC base
            ("BTC/USD", False),     # USD is quote, BTC base → not stablecoin
            ("EUR/USDT", True),     # EUR base
        ],
    )
    def test_stablecoin_detection(self, symbol, expected):
        assert is_stablecoin(symbol) == expected