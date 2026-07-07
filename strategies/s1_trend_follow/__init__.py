"""Make the s1_trend_follow package importable."""
from .strategy import (
    StrategyParams,
    build_entry_signals,
    build_exit_signals,
    compute_indicators,
    entry_signal,
    exit_signal,
)

__all__ = [
    "StrategyParams",
    "build_entry_signals",
    "build_exit_signals",
    "compute_indicators",
    "entry_signal",
    "exit_signal",
]
