"""S2 ranking + weekly rotation backtest (synthetic, no network)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from strategies.s2_momentum_rotation import (
    RotationParams,
    is_rebalance_timestamp,
    rank_holdings,
)
from strategies.s2_momentum_rotation.backtest import run_rotation_backtest


def _ohlcv(close: np.ndarray, start: str = "2021-01-04") -> pd.DataFrame:
    idx = pd.bdate_range(start, periods=len(close), freq="C")
    close = close.astype(float)
    return pd.DataFrame(
        {
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": np.full(len(close), 1_000_000.0),
        },
        index=idx,
    )


def test_monday_is_rebalance_and_tuesday_is_not():
    assert is_rebalance_timestamp(pd.Timestamp("2022-01-03")) is True  # Monday
    assert is_rebalance_timestamp(pd.Timestamp("2022-01-04")) is False


def test_rank_holdings_picks_highest_momentum():
    n = 40
    weak = _ohlcv(np.linspace(100, 90, n))
    strong = _ohlcv(np.linspace(100, 160, n))
    mid = _ohlcv(np.linspace(100, 120, n))
    params = RotationParams(universe_size=3, hold_top_k=2, momentum_lookback_days=30)
    held = rank_holdings(
        {"WEAK/USDT": weak, "STRONG/USDT": strong, "MID/USDT": mid},
        params,
    )
    assert held[0] == "STRONG/USDT"
    assert "WEAK/USDT" not in held
    assert len(held) == 2


def test_rotation_backtest_produces_trades_and_survives_crash():
    """2021 rally then 2022-like crash: must include the drawdown in sample."""
    rng = np.random.default_rng(0)
    n = 520  # ~2 years of weekdays
    dates_start = "2021-01-04"
    bull = np.linspace(100, 200, 260)
    crash = np.linspace(200, 80, 130)
    recover = np.linspace(80, 110, 130)
    a = np.concatenate([bull, crash, recover]) + rng.normal(0, 0.4, n)
    b = np.concatenate([bull * 0.9, crash * 1.1, recover * 1.2]) + rng.normal(0, 0.4, n)
    c = np.concatenate([bull * 1.1, crash * 0.8, recover]) + rng.normal(0, 0.4, n)
    prices = {
        "AAA/USDT": _ohlcv(a, dates_start),
        "BBB/USDT": _ohlcv(b, dates_start),
        "CCC/USDT": _ohlcv(c, dates_start),
    }
    params = RotationParams(universe_size=3, hold_top_k=2, momentum_lookback_days=30)
    report = run_rotation_backtest(prices, params, initial_capital=1000.0)
    assert report.n_trades >= 5
    assert report.equity_curve.index.min() <= pd.Timestamp("2022-01-03")
    assert report.max_drawdown_pct < 0  # crash must show up
    assert report.equity_curve.iloc[-1] > 0
