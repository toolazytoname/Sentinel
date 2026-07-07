"""Equivalence guard for S1 vectorized signal builders (Task RC.3).

Proves the vectorized `build_entry_signals` / `build_exit_signals` produce
element-for-element identical output to the row-by-row logic they replace:

  - build_entry_signals  ==  applying the already-tested `entry_signal` per row
  - build_exit_signals   ==  a reference loop replicating the CURRENT
                             populate_exit_trend behavior (death_cross + adx_collapse)

Includes warmup rows with NaN indicators to prove NaN handling matches the
loop's `pd.isna` guards. These run WITHOUT freqtrade (the builders are
module-level pure functions); an extra check exercises the freqtrade class
output only if freqtrade is importable.
"""
from __future__ import annotations

import importlib.util

import numpy as np
import pandas as pd
import pytest

from strategies.s1_trend_follow import (
    StrategyParams,
    build_entry_signals,
    build_exit_signals,
    compute_indicators,
    entry_signal,
)

FREQTRADE_AVAILABLE = importlib.util.find_spec("freqtrade") is not None


@pytest.fixture
def uptrend_with_cross_df() -> pd.DataFrame:
    """500-bar V-shape guaranteeing a golden cross + a trending regime.

    Same shape as the fixture in test_strategy.py: 200 bars down then 300 up.
    Guarantees warmup NaNs (200 EMA) at the head so NaN handling is exercised.
    """
    down = np.linspace(200, 100, 200) + np.random.default_rng(7).normal(0, 0.5, 200)
    up = np.linspace(100, 250, 300) + np.random.default_rng(11).normal(0, 0.5, 300)
    close = np.concatenate([down, up])
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": np.ones(len(close)) * 1000,
        }
    )


@pytest.fixture
def sideways_no_cross_df() -> pd.DataFrame:
    """500-bar mean-reverting series; ADX stays low so entries are filtered."""
    n = 500
    rng = np.random.default_rng(99)
    close = 100 + np.cumsum(rng.normal(0, 0.15, n))
    close -= np.linspace(close[0], close[-1], n) * 0.95
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 0.2,
            "low": close - 0.2,
            "close": close,
            "volume": np.ones(n) * 1000,
        }
    )


def _reference_exit_loop(df: pd.DataFrame, params: StrategyParams) -> pd.Series:
    """Row-by-row replication of the CURRENT populate_exit_trend logic.

    Pins build_exit_signals to the exact pre-refactor behavior. Intentionally
    mirrors the old loop line-for-line (death_cross clause + adx_collapse clause).
    """
    out = np.zeros(len(df), dtype=int)
    for i in range(len(df)):
        row = df.iloc[i]
        if pd.notna(row.get("ema_fast")) and pd.notna(row.get("ema_slow")):
            if row["ema_fast"] < row["ema_slow"] and row.get("death_cross", False):
                out[i] = 1
        if pd.notna(row["adx"]) and row["adx"] < params.adx_exit_threshold:
            out[i] = 1
    return pd.Series(out, index=df.index)


class TestEntryEquivalence:
    @pytest.mark.parametrize("fixture_name", ["uptrend_with_cross_df", "sideways_no_cross_df"])
    def test_vectorized_entry_equals_row_by_row(self, fixture_name, request):
        df = request.getfixturevalue(fixture_name)
        params = StrategyParams()
        out = compute_indicators(df, params)

        vectorized = build_entry_signals(out, params)
        reference = out.apply(lambda r: int(entry_signal(r, params)), axis=1)

        # Align name/dtype for a strict element-for-element comparison.
        reference = reference.rename(vectorized.name).astype(vectorized.dtype)
        pd.testing.assert_series_equal(vectorized, reference)

    def test_entry_warmup_head_is_zero(self, uptrend_with_cross_df):
        """NaN indicators at the head (200-EMA warmup) must yield 0, matching
        entry_signal's pd.isna guard."""
        params = StrategyParams()
        out = compute_indicators(uptrend_with_cross_df, params)
        vectorized = build_entry_signals(out, params)
        # slow EMA NaN for first 199 rows → no entry possible there.
        assert (vectorized.iloc[:199] == 0).all()

    def test_entry_actually_fires_somewhere(self, uptrend_with_cross_df):
        """Guard against a vacuously-passing equivalence (all-zeros == all-zeros).
        The uptrend fixture must produce at least one real entry."""
        params = StrategyParams()
        out = compute_indicators(uptrend_with_cross_df, params)
        assert build_entry_signals(out, params).sum() >= 1


class TestExitEquivalence:
    @pytest.mark.parametrize("fixture_name", ["uptrend_with_cross_df", "sideways_no_cross_df"])
    def test_vectorized_exit_equals_reference_loop(self, fixture_name, request):
        df = request.getfixturevalue(fixture_name)
        params = StrategyParams()
        out = compute_indicators(df, params)

        vectorized = build_exit_signals(out, params)
        reference = _reference_exit_loop(out, params).rename(vectorized.name).astype(vectorized.dtype)
        pd.testing.assert_series_equal(vectorized, reference)

    def test_exit_warmup_head_is_zero(self, uptrend_with_cross_df):
        params = StrategyParams()
        out = compute_indicators(uptrend_with_cross_df, params)
        vectorized = build_exit_signals(out, params)
        # adx warmup NaN and ema NaN at head → no exit signal.
        assert (vectorized.iloc[:199] == 0).all()

    def test_exit_actually_fires_somewhere(self, sideways_no_cross_df):
        """Low-ADX sideways regime must trip adx_collapse at least once."""
        params = StrategyParams()
        out = compute_indicators(sideways_no_cross_df, params)
        assert build_exit_signals(out, params).sum() >= 1


@pytest.mark.skipif(not FREQTRADE_AVAILABLE, reason="freqtrade not installed in this venv")
class TestClassDelegationMatchesBuilders:
    """When freqtrade IS importable, prove the class populate_* delegate to the
    builders with identical output. Skipped otherwise — the pure-function
    equivalence above is sufficient since populate_* now just delegate."""

    def test_populate_entry_matches_builder(self, uptrend_with_cross_df):
        from strategies.s1_trend_follow.strategy import S1TrendFollow

        params = StrategyParams()
        out = compute_indicators(uptrend_with_cross_df, params)
        strat = S1TrendFollow.__new__(S1TrendFollow)
        result = strat.populate_entry_trend(out.copy(), {})
        expected = build_entry_signals(
            out, StrategyParams(adx_entry_threshold=float(strat.adx_entry.value))
        )
        assert (result["enter_long"].values == expected.values).all()

    def test_populate_exit_matches_builder(self, uptrend_with_cross_df):
        from strategies.s1_trend_follow.strategy import S1TrendFollow

        params = StrategyParams()
        out = compute_indicators(uptrend_with_cross_df, params)
        strat = S1TrendFollow.__new__(S1TrendFollow)
        result = strat.populate_exit_trend(out.copy(), {})
        expected = build_exit_signals(
            out, StrategyParams(adx_exit_threshold=float(strat.adx_exit.value))
        )
        assert (result["exit_long"].values == expected.values).all()
