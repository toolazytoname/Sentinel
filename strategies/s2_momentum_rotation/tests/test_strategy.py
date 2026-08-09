"""Unit tests for S2 momentum rotation pure logic."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from strategies.s2_momentum_rotation import (
    RotationParams,
    compute_universe,
    equal_weights,
    rotation_plan,
    select_top_k,
    top_k_mask,
    weekly_rotation_mask,
)


class TestComputeUniverse:
    def test_excludes_stablecoins(self):
        params = RotationParams(universe_size=10)
        symbols = ["BTC/USDT", "ETH/USDT", "USDT/USDT", "USDC/USD", "SOL/USDT"]
        volumes = {s: 1_000_000.0 for s in symbols}
        out = compute_universe(symbols, volumes, params)
        # USDT and USDC pairs should be excluded even with fake-high volume
        assert "USDT/USDT" not in out
        assert "USDC/USD" not in out
        # Real assets present
        assert "BTC/USDT" in out
        assert "ETH/USDT" in out
        assert "SOL/USDT" in out

    def test_respects_universe_size(self):
        params = RotationParams(universe_size=3)
        symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "ADA/USDT"]
        # Make BTC the unambiguous volume leader
        volumes = {
            "BTC/USDT": 5000.0,
            "ETH/USDT": 3000.0,
            "SOL/USDT": 1000.0,
            "XRP/USDT": 500.0,
            "ADA/USDT": 100.0,
        }
        out = compute_universe(symbols, volumes, params)
        assert len(out) == 3
        assert out[0] == "BTC/USDT"
        assert out == ["BTC/USDT", "ETH/USDT", "SOL/USDT"]

    def test_returns_sorted_by_volume_desc(self):
        params = RotationParams(universe_size=5)
        symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
        volumes = {"BTC/USDT": 100.0, "ETH/USDT": 500.0, "SOL/USDT": 200.0}
        out = compute_universe(symbols, volumes, params)
        assert out == ["ETH/USDT", "SOL/USDT", "BTC/USDT"]


class TestSelectTopK:
    def test_picks_top_k_by_momentum(self):
        params = RotationParams(hold_top_k=2, min_momentum=-0.99)
        momentum = {"A": 0.5, "B": 0.1, "C": 0.3, "D": -0.2}
        out = select_top_k(momentum, params)
        assert out == ["A", "C"]  # 0.5 then 0.3

    def test_excludes_dead_coins_below_min(self):
        params = RotationParams(hold_top_k=3, min_momentum=-0.50)
        momentum = {"A": 0.5, "B": 0.1, "DEAD": -0.95}
        out = select_top_k(momentum, params)
        assert "DEAD" not in out
        assert out == ["A", "B"]

    def test_k_larger_than_universe_returns_what_exists(self):
        params = RotationParams(hold_top_k=5, min_momentum=-0.99)
        momentum = {"A": 0.5}
        out = select_top_k(momentum, params)
        assert out == ["A"]  # only one available


class TestEqualWeights:
    def test_full_universe_gets_equal_split(self):
        params = RotationParams(hold_top_k=3)
        weights = equal_weights(["A", "B", "C"], total_stake=300.0, params=params)
        assert weights == {"A": 100.0, "B": 100.0, "C": 100.0}

    def test_partial_universe_doesnt_idle_cash(self):
        # If only 2 holdable symbols exist but hold_top_k=3, deploy all 300
        params = RotationParams(hold_top_k=3)
        weights = equal_weights(["A", "B"], total_stake=300.0, params=params)
        assert weights == {"A": 150.0, "B": 150.0}

    def test_empty_holdings_returns_empty(self):
        params = RotationParams(hold_top_k=3)
        assert equal_weights([], total_stake=300.0, params=params) == {}


class TestRotationPlan:
    def test_end_to_end_pipeline(self):
        params = RotationParams(universe_size=5, hold_top_k=2, min_momentum=-0.99)
        symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "USDT/USD"]
        volumes = {"BTC/USDT": 1000, "ETH/USDT": 800, "SOL/USDT": 500,
                   "XRP/USDT": 300, "USDT/USD": 999999}  # stable excluded
        momentum = {"BTC/USDT": 0.20, "ETH/USDT": 0.35, "SOL/USDT": 0.10, "XRP/USDT": -0.05}
        weights = rotation_plan(symbols, volumes, momentum, total_stake=200.0, params=params)
        # Top 2 by momentum: ETH (0.35), BTC (0.20)
        assert "ETH/USDT" in weights
        assert "BTC/USDT" in weights
        assert weights["ETH/USDT"] == 100.0
        assert weights["BTC/USDT"] == 100.0
        # SOL and XRP not in top 2
        assert "SOL/USDT" not in weights
        assert "XRP/USDT" not in weights

    def test_stablecoin_high_volume_does_not_pollute_top_k(self):
        # Classic trap: USDT/USDT has astronomical volume, would otherwise dominate
        params = RotationParams(universe_size=3, hold_top_k=2, min_momentum=-0.99)
        symbols = ["BTC/USDT", "ETH/USDT", "USDT/USDT"]
        volumes = {"BTC/USDT": 100, "ETH/USDT": 50, "USDT/USDT": 999_999_999}
        momentum = {"BTC/USDT": 0.10, "ETH/USDT": 0.20, "USDT/USDT": 0.0}
        weights = rotation_plan(symbols, volumes, momentum, total_stake=200.0, params=params)
        assert "USDT/USDT" not in weights
        assert weights == {"ETH/USDT": 100.0, "BTC/USDT": 100.0}


class TestTopKMask:
    """Vectorized cross-sectional top-K selection.

    This is the backtest-faithful, lookahead-safe core: at each bar it ranks
    all pairs by *that bar's* momentum and marks the top-K. freqtrade fills the
    resulting entry signal on the NEXT candle's open, so using close(t) in the
    momentum at bar t introduces no lookahead.
    """

    def test_selects_top_k_per_row(self):
        # 2 bars, 3 pairs, K=2
        wide = pd.DataFrame(
            {
                "BTC/USDT": [0.10, 0.05],
                "ETH/USDT": [0.30, 0.02],
                "SOL/USDT": [0.20, 0.40],
            }
        )
        mask = top_k_mask(wide, hold_top_k=2, min_momentum=-0.99)
        # bar 0: ETH(0.30), SOL(0.20) selected; BTC(0.10) not
        assert mask.iloc[0].tolist() == [False, True, True]
        # bar 1: SOL(0.40), BTC(0.05) selected; ETH(0.02) not
        assert mask.iloc[1].tolist() == [True, False, True]

    def test_excludes_below_min_momentum_even_if_top_k(self):
        # Only 2 pairs, K=3, but one is a dead coin below min → excluded
        wide = pd.DataFrame({"A": [0.50], "DEAD": [-0.80]})
        mask = top_k_mask(wide, hold_top_k=3, min_momentum=-0.50)
        assert mask.iloc[0].tolist() == [True, False]  # DEAD excluded despite room

    def test_nan_momentum_never_selected(self):
        # Warmup: a pair with NaN momentum must not be picked even if K has room
        wide = pd.DataFrame({"A": [np.nan], "B": [0.10], "C": [np.nan]})
        mask = top_k_mask(wide, hold_top_k=3, min_momentum=-0.99)
        assert mask.iloc[0].tolist() == [False, True, False]

    def test_all_nan_row_selects_nothing(self):
        wide = pd.DataFrame({"A": [np.nan], "B": [np.nan]})
        mask = top_k_mask(wide, hold_top_k=2, min_momentum=-0.99)
        assert mask.iloc[0].tolist() == [False, False]

    def test_ties_are_deterministic_first_column_wins(self):
        # Two pairs tie; K=1 → the earlier column is chosen deterministically
        wide = pd.DataFrame({"A": [0.20], "B": [0.20], "C": [0.05]})
        mask = top_k_mask(wide, hold_top_k=1, min_momentum=-0.99)
        assert mask.iloc[0].tolist() == [True, False, False]

    def test_output_shape_and_dtype(self):
        wide = pd.DataFrame({"A": [0.1, 0.2], "B": [0.3, 0.4]})
        mask = top_k_mask(wide, hold_top_k=1, min_momentum=-0.99)
        assert mask.shape == wide.shape
        assert mask.dtypes.eq(bool).all()
        assert list(mask.columns) == ["A", "B"]


class TestWeeklyRotationMask:
    def test_uses_liquid_universe_before_momentum_rank(self):
        dates = pd.date_range("2026-01-05", periods=2, freq="D", tz="UTC")
        momentum = pd.DataFrame(
            {"LIQUID": [0.10, 0.10], "ILLIQUID": [0.90, 0.90]}, index=dates
        )
        quote_volume = pd.DataFrame(
            {"LIQUID": [1_000.0, 1_000.0], "ILLIQUID": [1.0, 1.0]}, index=dates
        )
        params = RotationParams(universe_size=1, hold_top_k=1, min_momentum=-0.99)

        held = weekly_rotation_mask(momentum, quote_volume, params)

        assert held.loc[dates[0]].to_dict() == {"LIQUID": True, "ILLIQUID": False}

    def test_rebalances_monday_and_holds_selection_between_weeks(self):
        dates = pd.date_range("2026-01-05", periods=9, freq="D", tz="UTC")
        momentum = pd.DataFrame(
            {
                "A": [0.30, 0.10, 0.05, 0.02, 0.01, 0.01, 0.01, 0.01, 0.01],
                "B": [0.10, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90, 1.00, 1.10],
            },
            index=dates,
        )
        quote_volume = pd.DataFrame(100.0, index=dates, columns=["A", "B"])
        params = RotationParams(universe_size=2, hold_top_k=1, min_momentum=-0.99)

        held = weekly_rotation_mask(momentum, quote_volume, params)

        assert held.loc["2026-01-05":"2026-01-11", "A"].all()
        assert not held.loc["2026-01-05":"2026-01-11", "B"].any()
        assert held.loc[pd.Timestamp("2026-01-12", tz="UTC"), "B"]

    def test_future_changes_do_not_change_past_holdings(self):
        dates = pd.date_range("2026-01-05", periods=10, freq="D", tz="UTC")
        momentum = pd.DataFrame(
            {"A": np.linspace(0.1, 0.2, 10), "B": np.linspace(0.2, 0.1, 10)},
            index=dates,
        )
        quote_volume = pd.DataFrame(
            {"A": np.linspace(100, 200, 10), "B": np.linspace(200, 100, 10)},
            index=dates,
        )
        params = RotationParams(universe_size=2, hold_top_k=1, min_momentum=-0.99)
        baseline = weekly_rotation_mask(momentum, quote_volume, params)

        changed_momentum = momentum.copy()
        changed_volume = quote_volume.copy()
        changed_momentum.loc[dates[7]:, :] = [[100.0, -100.0]] * 3
        changed_volume.loc[dates[7]:, :] = [[1.0, 1_000_000.0]] * 3
        changed = weekly_rotation_mask(changed_momentum, changed_volume, params)

        pd.testing.assert_frame_equal(baseline.loc[:dates[6]], changed.loc[:dates[6]])
