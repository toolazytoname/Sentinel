"""S1: Trend-following strategy for freqtrade dry-run / live.

Self-contained — no external package imports so it works inside the freqtrade
Docker image without mounting the strategies/ package. Shared AI-veto logic
lives in `base.py` (same directory).

Logic
-----
Long-only. Enter when:
  1. Fast EMA (50) crosses above Slow EMA (200) — golden cross
  2. ADX > 25 (trend strength confirmed, filters chop)

Exit (priority order):
  1. Hard stop: price ≤ entry × (1 - 0.08) → -8%
  2. Trailing stop: price ≤ peak × (1 - 0.05) → 5% from peak
  3. Death cross: fast EMA drops back below slow EMA
  4. ADX collapse: ADX < 18

Veto (confirm_trade_entry):
  Inherited from base.StrategyBase. Calls AI service GET /veto?strategy=&pair=
  before each entry. On any error / timeout, defaults to PASS (fail-open).
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from freqtrade.strategy import DecimalParameter

from base import StrategyBase

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────
FAST_EMA = 50
SLOW_EMA = 200
ADX_PERIOD = 14
ADX_ENTRY = 25.0
ADX_EXIT = 18.0
HARD_STOP_PCT = 0.08
TRAILING_STOP_PCT = 0.05


# ── Pure indicator helpers ─────────────────────────────────────────────────

def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def _adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    up = high.diff()
    dn = -low.diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    alpha = 1 / period
    atr = tr.ewm(alpha=alpha, adjust=False, min_periods=period).mean()
    pdi = (
        100
        * pd.Series(plus_dm, index=df.index).ewm(alpha=alpha, adjust=False, min_periods=period).mean()
        / atr
    )
    mdi = (
        100
        * pd.Series(minus_dm, index=df.index).ewm(alpha=alpha, adjust=False, min_periods=period).mean()
        / atr
    )
    di_sum = pdi + mdi
    dx = 100 * (pdi - mdi).abs() / di_sum.replace(0, np.nan)
    return dx.ewm(alpha=alpha, adjust=False, min_periods=period).mean()


# ── Freqtrade strategy ─────────────────────────────────────────────────────

class S1TrendFollow(StrategyBase):
    """Trend-following strategy. Timeframe: 1d.

    Inherits confirm_trade_entry (AI veto) from StrategyBase.
    """

    timeframe = "1d"
    can_short = False
    startup_candle_count = 220  # warmup for 200-period EMA

    # Hyperopt parameter spaces (P1.4 — narrow to reduce overfit risk)
    adx_entry = DecimalParameter(20.0, 35.0, default=ADX_ENTRY, space="buy")
    adx_exit = DecimalParameter(15.0, 22.0, default=ADX_EXIT, space="sell")
    hard_stop = DecimalParameter(0.05, 0.15, default=HARD_STOP_PCT, space="sell")
    trailing_stop_p = DecimalParameter(0.03, 0.10, default=TRAILING_STOP_PCT, space="sell")

    stoploss = -0.10  # freqtrade hard floor (exchange stoploss); custom_stoploss tightens
    stoploss_on_exchange = True
    trailing_stop = False  # handled by custom_stoploss below

    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    minimal_roi = {"0": 100}  # rely on exit signals, not fixed ROI

    order_types = {
        "entry": "limit",
        "exit": "limit",
        "stoploss": "market",
        "stoploss_on_exchange": True,
    }
    order_time_in_force = {"entry": "GTC", "exit": "GTC"}

    # ── Indicators ────────────────────────────────────────────────────────

    def populate_indicators(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        dataframe["ema_fast"] = _ema(dataframe["close"], FAST_EMA)
        dataframe["ema_slow"] = _ema(dataframe["close"], SLOW_EMA)
        dataframe["adx"] = _adx(dataframe, ADX_PERIOD)

        fast, slow = dataframe["ema_fast"], dataframe["ema_slow"]
        dataframe["golden_cross"] = (fast > slow) & (fast.shift(1) <= slow.shift(1))
        dataframe["death_cross"] = (fast < slow) & (fast.shift(1) >= slow.shift(1))
        return dataframe

    # ── Entry ─────────────────────────────────────────────────────────────

    def populate_entry_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        threshold = float(self.adx_entry.value)
        dataframe["enter_long"] = (
            dataframe["golden_cross"]
            & (dataframe["ema_fast"] > dataframe["ema_slow"])
            & (dataframe["adx"] > threshold)
            & dataframe["adx"].notna()
        ).astype(int)
        return dataframe

    # ── Exit ──────────────────────────────────────────────────────────────

    def populate_exit_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        adx_threshold = float(self.adx_exit.value)
        # Signal-based exits; hard/trailing stop handled by custom_stoploss
        dataframe["exit_long"] = (
            dataframe["death_cross"] | (dataframe["adx"] < adx_threshold)
        ).astype(int)
        return dataframe

    def custom_stoploss(self, *args, **kwargs) -> float:
        """Tighten stop as trade moves in our favour.

        Returns the stoploss as a fraction of current_rate (negative = stop
        below current price). freqtrade will use the most conservative
        (tightest) of this and the static stoploss.
        """
        hard = -float(self.hard_stop.value)
        current_profit = kwargs.get("current_profit")
        if current_profit is not None and current_profit > 0:
            # Once in profit, trail at trailing_stop_p from current_rate
            trail = -float(self.trailing_stop_p.value)
            return max(hard, trail)
        return hard