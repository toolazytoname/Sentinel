"""Unit tests for S1TrendFollow strategy pure logic."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from strategies.s1_trend_follow import (
    StrategyParams,
    compute_indicators,
    entry_signal,
    exit_signal,
)


@pytest.fixture
def uptrend_with_cross_df() -> pd.DataFrame:
    """500-bar V-shape: 200 bars downtrend then 300 bars strong uptrend.

    This guarantees a golden cross around bar 289 (the V bottom + recovery
    point), unlike a monotonic uptrend where fast EMA stays ahead of slow.
    Length must exceed 200 EMA warmup + ADX warmup (28) + buffer.
    """
    down = np.linspace(200, 100, 200) + np.random.default_rng(7).normal(0, 0.5, 200)
    up = np.linspace(100, 250, 300) + np.random.default_rng(11).normal(0, 0.5, 300)
    close = np.concatenate([down, up])
    high = close + 1.0
    low = close - 1.0
    return pd.DataFrame(
        {
            "open": close,
            "high": high,
            "low": low,
            "close": close,
            "volume": np.ones(len(close)) * 1000,
        }
    )


@pytest.fixture
def sideways_no_cross_df() -> pd.DataFrame:
    """500-bar truly mean-reverting (small steps, strong detrend); ADX must stay < 25.

    Random walk with 0.15 std steps + linear drift removal keeps ADX low.
    Wider random walks generate localized trends that ADX picks up.
    """
    n = 500
    rng = np.random.default_rng(99)
    close = 100 + np.cumsum(rng.normal(0, 0.15, n))
    close -= np.linspace(close[0], close[-1], n) * 0.95  # strong detrend
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 0.2,
            "low": close - 0.2,
            "close": close,
            "volume": np.ones(n) * 1000,
        }
    )


class TestComputeIndicators:
    def test_adds_expected_columns(self, uptrend_with_cross_df):
        params = StrategyParams()
        out = compute_indicators(uptrend_with_cross_df, params)
        for col in ["ema_fast", "ema_slow", "adx", "golden_cross", "death_cross"]:
            assert col in out.columns

    def test_slow_ema_warmup_is_nan(self, uptrend_with_cross_df):
        params = StrategyParams()
        out = compute_indicators(uptrend_with_cross_df, params)
        # First 199 rows of slow EMA should be NaN
        assert out["ema_slow"].iloc[:199].isna().all()
        # First 49 rows of fast EMA should be NaN
        assert out["ema_fast"].iloc[:49].isna().all()

    def test_uptrend_yields_golden_cross(self, uptrend_with_cross_df):
        params = StrategyParams()
        out = compute_indicators(uptrend_with_cross_df, params)
        assert out["golden_cross"].any(), "Up trend should produce at least one golden cross"

    def test_sideways_yields_no_entry_signal(self, sideways_no_cross_df):
        """Sideways produces spurious golden_cross events (mechanical),
        but entry_signal must filter them all out via ADX check.

        NOTE: assert uses `==` not `is` because entry_signal may return
        numpy.bool_ which `is` distinguishes from Python's True/False.
        """
        params = StrategyParams()
        out = compute_indicators(sideways_no_cross_df, params)
        cross_indices = out.index[out["golden_cross"]]
        if len(cross_indices) > 0:
            for idx in cross_indices:
                row = out.loc[idx]
                assert entry_signal(row, params) == False, (
                    f"entry_signal should be False for sideways cross at bar {idx}"
                )


class TestEntrySignal:
    def test_entry_on_golden_cross_with_trending_adx(self, uptrend_with_cross_df):
        params = StrategyParams()
        out = compute_indicators(uptrend_with_cross_df, params)
        cross_idx = out.index[out["golden_cross"]][0]
        row = out.loc[cross_idx].copy()
        # Ensure ADX is above threshold for a deterministic positive assertion
        row["adx"] = max(float(row["adx"]), params.adx_entry_threshold + 1)
        assert entry_signal(row, params) is True

    def test_no_entry_when_adx_below_threshold(self, uptrend_with_cross_df):
        params = StrategyParams()
        out = compute_indicators(uptrend_with_cross_df, params)
        cross_idx = out.index[out["golden_cross"]][0]
        row = out.loc[cross_idx].copy()
        row["adx"] = 10.0  # below threshold
        assert entry_signal(row, params) is False

    def test_no_entry_on_nan_row(self):
        params = StrategyParams()
        nan_row = pd.Series(
            {"close": 100, "ema_fast": np.nan, "ema_slow": np.nan, "adx": np.nan, "golden_cross": True}
        )
        assert entry_signal(nan_row, params) is False


class TestExitSignal:
    def test_hard_stop_fires_on_eight_percent_loss(self):
        params = StrategyParams()
        row = pd.Series({"close": 91.0, "ema_fast": 95, "ema_slow": 100, "adx": 30, "death_cross": False})
        should, reason = exit_signal(row, entry_price=100.0, peak_since_entry=102.0, params=params)
        assert should is True
        assert reason == "hard_stop"

    def test_trailing_stop_fires_on_five_percent_pullback(self):
        params = StrategyParams()
        # Price rose to 120, now back to 114 (5% pullback)
        row = pd.Series({"close": 114.0, "ema_fast": 118, "ema_slow": 110, "adx": 30, "death_cross": False})
        should, reason = exit_signal(row, entry_price=100.0, peak_since_entry=120.0, params=params)
        assert should is True
        assert reason == "trailing_stop"

    def test_hold_when_in_profit_and_trending(self):
        params = StrategyParams()
        row = pd.Series({"close": 110.0, "ema_fast": 115, "ema_slow": 105, "adx": 30, "death_cross": False})
        should, reason = exit_signal(row, entry_price=100.0, peak_since_entry=110.0, params=params)
        assert should is False
        assert reason == "hold"

    def test_adx_collapse_triggers_exit(self):
        params = StrategyParams()
        row = pd.Series({"close": 105.0, "ema_fast": 115, "ema_slow": 105, "adx": 15, "death_cross": False})
        should, reason = exit_signal(row, entry_price=100.0, peak_since_entry=105.0, params=params)
        assert should is True
        assert reason == "adx_collapse"

    def test_death_cross_triggers_exit(self):
        params = StrategyParams()
        row = pd.Series({"close": 105.0, "ema_fast": 95, "ema_slow": 105, "adx": 30, "death_cross": True})
        should, reason = exit_signal(row, entry_price=100.0, peak_since_entry=105.0, params=params)
        assert should is True
        assert reason == "death_cross"

    def test_no_position_returns_hold(self):
        params = StrategyParams()
        row = pd.Series({"close": 100.0, "ema_fast": 100, "ema_slow": 100, "adx": 30})
        should, reason = exit_signal(row, entry_price=0, peak_since_entry=0, params=params)
        assert should is False
        assert reason == "no_position"

    def test_priority_hard_stop_beats_trailing(self):
        # Hard stop should beat trailing even when both would trigger
        params = StrategyParams()
        row = pd.Series({"close": 80.0, "ema_fast": 95, "ema_slow": 100, "adx": 30, "death_cross": False})
        # Entry 100, peak 90 (never got above entry) — both hard_stop and trailing fire
        should, reason = exit_signal(row, entry_price=100.0, peak_since_entry=90.0, params=params)
        assert should is True
        assert reason == "hard_stop", "Hard stop must take priority over trailing stop"