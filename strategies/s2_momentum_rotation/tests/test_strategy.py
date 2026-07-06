"""Unit tests for S2 momentum rotation pure logic."""
from __future__ import annotations

import pytest

from strategies.s2_momentum_rotation import (
    RotationParams,
    compute_universe,
    equal_weights,
    rotation_plan,
    select_top_k,
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