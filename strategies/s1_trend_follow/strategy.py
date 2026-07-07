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


# ---- Vectorized signal builders (pure, freqtrade-independent, testable) ----
#
# These are the vectorized equivalents of applying entry_signal / the
# populate_exit_trend loop to each row. They live at module level so they can
# be imported and equivalence-tested WITHOUT freqtrade installed. The
# freqtrade adapter's populate_* methods simply delegate to these.
#
# Vectorization uses only current/prior bars (golden_cross / death_cross are
# built from .shift(1) inside compute_indicators) — no future leakage.

def build_entry_signals(df: pd.DataFrame, params: StrategyParams) -> pd.Series:
    """Vectorized equivalent of applying `entry_signal` to every row.

    Returns an int Series (1 = enter long, 0 = skip) aligned to ``df.index``.
    NaN indicators (warmup) yield 0, matching entry_signal's ``pd.isna`` guards.
    """
    # Guard: all three indicators must be present (mirrors the pd.isna check).
    valid = df["adx"].notna() & df["ema_fast"].notna() & df["ema_slow"].notna()
    # golden_cross is built from boolean ops in compute_indicators (no NaN),
    # but fillna(False) keeps this robust if a NaN ever appears.
    golden = df["golden_cross"].fillna(False).astype(bool)
    # NaN comparisons evaluate to False, so warmup rows are already excluded;
    # `valid` makes the intent explicit and pins us to the loop's behavior.
    ema_above = df["ema_fast"] > df["ema_slow"]
    adx_ok = df["adx"] > params.adx_entry_threshold
    signal = valid & golden & ema_above & adx_ok
    return signal.astype(int)


def build_exit_signals(df: pd.DataFrame, params: StrategyParams) -> pd.Series:
    """Vectorized equivalent of the CURRENT `populate_exit_trend` loop.

    Emits ONLY the signal-based exits that populate_exit_trend emits:
      - death cross: ema_fast < ema_slow AND death_cross flag set
      - adx collapse: adx < adx_exit_threshold
    Hard stop / trailing stop are intentionally NOT here (they live in
    custom_stoploss), matching the loop exactly.

    Returns an int Series (1 = exit, 0 = hold) aligned to ``df.index``.
    """
    ema_valid = df["ema_fast"].notna() & df["ema_slow"].notna()
    ema_below = df["ema_fast"] < df["ema_slow"]
    death = df["death_cross"].fillna(False).astype(bool) if "death_cross" in df else False
    death_exit = ema_valid & ema_below & death

    adx_collapse = df["adx"].notna() & (df["adx"] < params.adx_exit_threshold)

    signal = death_exit | adx_collapse
    return signal.astype(int)


# ---- freqtrade adapter (only loads when freqtrade IStrategy is available) ----

try:
    from freqtrade.strategy import DecimalParameter

    from strategies.base import StrategyBase

    class S1TrendFollow(StrategyBase):
        """freqtrade-compatible strategy class.

        Tested via the pure functions above; this class wires them into the
        freqtrade callback protocol. Inherits confirm_trade_entry → AI veto
        from StrategyBase.
        """
        timeframe = "1d"
        can_short = False
        startup_candle_count = 220  # warmup for 200 EMA
        stoploss = -0.10  # freqtrade hard floor (custom_stoploss tightens this)

        # Parameter spaces for hyperopt (P1.4 — keep narrow to avoid overfit)
        adx_entry = DecimalParameter(20.0, 35.0, default=25.0, space="buy")
        adx_exit = DecimalParameter(15.0, 22.0, default=18.0, space="sell")
        hard_stop = DecimalParameter(0.05, 0.15, default=0.08, space="sell")
        trailing_stop_pct = DecimalParameter(0.03, 0.10, default=0.05, space="sell")

        def populate_indicators(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
            params = StrategyParams()
            return compute_indicators(dataframe, params)

        def populate_entry_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
            # freqtrade expects a 'enter_long' column (1 to enter, 0 to skip).
            # Delegates to the vectorized builder (equivalence-tested vs entry_signal).
            params = StrategyParams(adx_entry_threshold=float(self.adx_entry.value))
            dataframe["enter_long"] = build_entry_signals(dataframe, params)
            return dataframe

        def populate_exit_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
            # Signal-based exits only (death_cross + adx_collapse); hard/trailing
            # stops live in custom_stoploss. Delegates to the vectorized builder.
            params = StrategyParams(
                adx_exit_threshold=float(self.adx_exit.value),
                hard_stop_pct=float(self.hard_stop.value),
                trailing_stop_pct=float(self.trailing_stop_pct.value),
            )
            dataframe["exit_long"] = build_exit_signals(dataframe, params)
            return dataframe

        def custom_stoploss(self, *args, **kwargs) -> float:
            """Tighten stop as trade moves in our favour.

            In profit → trail by trailing_stop_pct. Always at least hard_stop.
            """
            hard = -float(self.hard_stop.value)
            # freqtrade passes current_profit as kwarg
            current_profit = kwargs.get("current_profit")
            if current_profit is not None and current_profit > 0:
                trail = -float(self.trailing_stop_pct.value)
                return max(hard, trail)
            return hard

except ImportError:
    # freqtrade not installed locally — pure-logic functions still usable for testing
    S1TrendFollow = None  # type: ignore[assignment]