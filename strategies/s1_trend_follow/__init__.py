"""Make the s1_trend_follow package importable."""
from .strategy import StrategyParams, compute_indicators, entry_signal, exit_signal

__all__ = ["StrategyParams", "compute_indicators", "entry_signal", "exit_signal"]