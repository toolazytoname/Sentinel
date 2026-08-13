"""S1 simulator: pullback entries produce trades after a golden-cross regime."""
from __future__ import annotations

import numpy as np
import pandas as pd

from strategies.s1_trend_follow.backtest import simulate_pair
from strategies.s1_trend_follow.strategy import StrategyParams, compute_indicators, entry_signal


def _ohlcv(close: np.ndarray) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": np.ones(len(close)) * 1000,
        }
    )


def test_simulator_records_exit_reason():
    down = np.linspace(200, 100, 200)
    up = np.linspace(100, 250, 300)
    df = _ohlcv(np.concatenate([down, up]))
    trades = simulate_pair(df, StrategyParams(), pair="BTC/USDT")
    assert len(trades) >= 1
    assert trades[0].pair == "BTC/USDT"
    assert trades[0].reason in {
        "hard_stop",
        "trailing_stop",
        "death_cross",
        "adx_collapse",
        "end_of_data",
    }


def test_established_uptrend_without_new_golden_cross_can_still_enter():
    """The 2024–2026 failure mode: already in uptrend, no new golden cross."""
    rng = np.random.default_rng(3)
    up = np.linspace(100, 220, 280) + rng.normal(0, 0.2, 280)
    # Force a dip below the fast EMA then reclaim — pullback path.
    close = np.concatenate([up, np.array([up[-1] * 0.92, up[-1] * 0.90, up[-1] * 0.97])])
    df = _ohlcv(close)
    params = StrategyParams()
    out = compute_indicators(df, params)
    tail = out.iloc[-1]
    # Either the last bar or some post-warmup bar that is NOT a golden cross enters.
    non_cross_entries = []
    for _, row in out.iloc[220:].iterrows():
        if entry_signal(row, params) and not bool(row["golden_cross"]):
            non_cross_entries.append(True)
    assert non_cross_entries, "pullback path must allow entries without a new golden cross"
    assert bool(tail["pullback_reclaim"]) or len(non_cross_entries) >= 1
