"""Shared indicator computations for Sentinel strategies.

Designed to be freqtrade-independent so unit tests can run in plain Python.
All functions are pure: take a pandas DataFrame with OHLCV columns, return
a pandas Series or DataFrame of the same length. No side effects.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma(series: pd.Series, period: int) -> pd.Series:
    """Simple moving average. Returns NaN for the first `period-1` rows."""
    if period < 1:
        raise ValueError(f"sma period must be >= 1, got {period}")
    return series.rolling(window=period, min_periods=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential moving average (adjust=False for backward compatibility)."""
    if period < 1:
        raise ValueError(f"ema period must be >= 1, got {period}")
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def adx(
    df: pd.DataFrame,
    period: int = 14,
    *,
    high_col: str = "high",
    low_col: str = "low",
    close_col: str = "close",
) -> pd.Series:
    """Average Directional Index. Measures trend strength (NOT direction).

    Returns a Series with values in [0, 100]. ADX > 25 typically means a
    trending market; ADX < 20 means sideways / choppy.
    """
    if period < 1:
        raise ValueError(f"adx period must be >= 1, got {period}")

    high = df[high_col]
    low = df[low_col]
    close = df[close_col]

    # +DM and -DM
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    # True Range
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    # Wilder's smoothing
    atr = tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(
        alpha=1 / period, adjust=False, min_periods=period
    ).mean() / atr
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(
        alpha=1 / period, adjust=False, min_periods=period
    ).mean() / atr

    # ADX: average of DX over period
    di_sum = plus_di + minus_di
    di_diff = (plus_di - minus_di).abs()
    dx = 100 * di_diff / di_sum.replace(0, np.nan)
    return dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def momentum_score(df: pd.DataFrame, lookback_days: int) -> pd.Series:
    """Return-on-N: (close / close.shift(N)) - 1. Used by S2 ranking."""
    if lookback_days < 1:
        raise ValueError(f"lookback_days must be >= 1, got {lookback_days}")
    return df["close"].pct_change(periods=lookback_days)


def is_stablecoin(symbol: str) -> bool:
    """Heuristic: match common stablecoin quote/base currencies and tokens.

    Conservative: anything USDT/USDC/BUSD/DAI/TUSD/USDP/USDD or pegged
    variants. Used by S2 to exclude stablecoins from rotation universe.
    """
    stablecoins = {
        "USDT", "USDC", "BUSD", "DAI", "TUSD", "USDP", "USDD", "FDUSD",
        "PYUSD", "GUSD", "SUSD", "EUR", "GBP", "AUD", "USD",
    }
    base = symbol.upper().split("/")[0] if "/" in symbol else symbol.upper()
    return base in stablecoins