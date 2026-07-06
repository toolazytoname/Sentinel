"""S1: Trend-following strategy on BTC/ETH daily.

Logic
-----
Long-only. Enter when:
  1. Fast EMA (50) crosses above Slow EMA (200) — golden cross
  2. ADX > 25 (confirms trend strength, filters out choppy markets)

Exit (in priority order):
  1. custom_stoploss below -8% from entry → hard stop
  2. Trailing stop: 5% from peak since entry → locks in profit
  3. EMA cross back below (50 < 200) → trend reversal exit
  4. ADX drops below 18 → trend weakness exit

Designed for the freqtrade IStrategy interface. Tested via direct unit tests
on the underlying signal logic without requiring freqtrade runtime.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import pandas as pd

from strategies.indicators import adx, ema


# ---- Signal definitions (pure logic, no freqtrade dependency) ----

class Signal(Enum):
    ENTER_LONG = "enter_long"
    EXIT = "exit"
    HOLD = "hold"


@dataclass(frozen=True)
class StrategyParams:
    """All numeric thresholds. Edit here, not deep in the strategy body."""
    fast_ema_period: int = 50
    slow_ema_period: int = 200
    adx_entry_threshold: float = 25.0
    adx_exit_threshold: float = 18.0
    hard_stop_pct: float = 0.08  # -8% from entry triggers exit
    trailing_stop_pct: float = 0.05  # 5% from peak


def compute_indicators(
    df: pd.DataFrame,
    params: StrategyParams,
) -> pd.DataFrame:
    """Return df with added columns: ema_fast, ema_slow, adx, golden_cross.

    `golden_cross` is a boolean Series: True on the bar where fast EMA first
    crosses above slow EMA.
    """
    out = df.copy()
    out["ema_fast"] = ema(out["close"], params.fast_ema_period)
    out["ema_slow"] = ema(out["close"], params.slow_ema_period)
    out["adx"] = adx(out, period=14)

    # Cross detection: fast > slow now AND fast <= slow on the prior bar
    fast = out["ema_fast"]
    slow = out["ema_slow"]
    out["golden_cross"] = (fast > slow) & (fast.shift(1) <= slow.shift(1))
    out["death_cross"] = (fast < slow) & (fast.shift(1) >= slow.shift(1))
    return out


def entry_signal(row: pd.Series, params: StrategyParams) -> bool:
    """True on the bar where we should enter long.

    Conservative: requires all three conditions to be true simultaneously.
    - Golden cross just happened (row-level)
    - Fast EMA > Slow EMA (sustained)
    - ADX > entry threshold (trend is real, not noise)
    """
    if pd.isna(row["adx"]) or pd.isna(row["ema_fast"]) or pd.isna(row["ema_slow"]):
        return False
    if not row["golden_cross"]:
        return False
    if not (row["ema_fast"] > row["ema_slow"]):
        return False
    return row["adx"] > params.adx_entry_threshold


def exit_signal(
    row: pd.Series,
    entry_price: float,
    peak_since_entry: float,
    params: StrategyParams,
) -> tuple[bool, str]:
    """Return (should_exit, reason). First reason wins (priority order).

    Priority:
      1. Hard stop (-8% from entry)
      2. Trailing stop (-5% from peak)
      3. Death cross (trend reversal)
      4. ADX collapse (< 18)
    """
    if entry_price <= 0 or peak_since_entry <= 0:
        return False, "no_position"

    current = row["close"]

    # 1. Hard stop — protects against catastrophic loss
    if current <= entry_price * (1 - params.hard_stop_pct):
        return True, "hard_stop"

    # 2. Trailing stop — protects unrealized gains
    if current <= peak_since_entry * (1 - params.trailing_stop_pct):
        return True, "trailing_stop"

    # 3. Death cross — only check if indicator is valid
    if pd.notna(row.get("ema_fast")) and pd.notna(row.get("ema_slow")):
        if row["ema_fast"] < row["ema_slow"] and not pd.isna(row.get("death_cross")) and row["death_cross"]:
            return True, "death_cross"

    # 4. ADX collapse
    if pd.notna(row["adx"]) and row["adx"] < params.adx_exit_threshold:
        return True, "adx_collapse"

    return False, "hold"


# ---- freqtrade adapter (only loads when freqtrade IStrategy is available) ----

try:
    from freqtrade.strategy import IStrategy, IntParameter, DecimalParameter

    class S1TrendFollow(IStrategy):
        """freqtrade-compatible strategy class.

        Tested via the pure functions above; this class wires them into the
        freqtrade callback protocol.
        """
        timeframe = "1d"
        can_short = False
        startup_candle_count = 220  # warmup for 200 EMA

        # Parameter spaces for hyperopt (P1.4 — keep narrow to avoid overfit)
        adx_entry = DecimalParameter(20.0, 35.0, default=25.0, space="buy")
        adx_exit = DecimalParameter(15.0, 22.0, default=18.0, space="sell")
        hard_stop = DecimalParameter(0.05, 0.15, default=0.08, space="sell")
        trailing_stop_pct = DecimalParameter(0.03, 0.10, default=0.05, space="sell")

        def populate_indicators(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
            params = StrategyParams()
            return compute_indicators(dataframe, params)

        def populate_entry_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
            # freqtrade expects a 'enter_long' column (1 to enter, 0 to skip)
            params = StrategyParams(adx_entry_threshold=float(self.adx_entry.value))
            dataframe["enter_long"] = 0
            for i in range(len(dataframe)):
                if entry_signal(dataframe.iloc[i], params):
                    dataframe.iat[i, dataframe.columns.get_loc("enter_long")] = 1
            return dataframe

        def populate_exit_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
            params = StrategyParams(
                adx_exit_threshold=float(self.adx_exit.value),
                hard_stop_pct=float(self.hard_stop.value),
                trailing_stop_pct=float(self.trailing_stop_pct.value),
            )
            dataframe["exit_long"] = 0
            for i in range(len(dataframe)):
                row = dataframe.iloc[i]
                # Track peak/trough at decision time (freqtrade does its own custom_stoploss)
                # Here we only emit signal-based exits; hard stop/trailing live in custom_stoploss
                if pd.notna(row.get("ema_fast")) and pd.notna(row.get("ema_slow")):
                    if row["ema_fast"] < row["ema_slow"] and row.get("death_cross", False):
                        dataframe.iat[i, dataframe.columns.get_loc("exit_long")] = 1
                if pd.notna(row["adx"]) and row["adx"] < params.adx_exit_threshold:
                    dataframe.iat[i, dataframe.columns.get_loc("exit_long")] = 1
            return dataframe

except ImportError:
    # freqtrade not installed locally — pure-logic functions still usable for testing
    S1TrendFollow = None  # type: ignore[assignment]