"""S2: Cross-sectional momentum rotation for freqtrade dry-run / live.

Self-contained — no external package imports so it works inside the freqtrade
Docker image. Shared AI-veto logic lives in `base.py` (same directory).

Logic
-----
Weekly (Monday UTC) rebalance:
  1. Universe = whitelist minus stables, top-N by trailing volume
  2. Rank by 30-day close-to-close momentum
  3. Hold top-K (default 3), equal weight
  4. 10% stoploss between rebalances

Official historical backtest is the pandas walker in
`strategies/s2_momentum_rotation/backtest.py` (point-in-time). This adapter
ranks in bot_loop_start for live/dry-run only.

Veto: confirm_trade_entry from StrategyBase (fail-open).
"""
from __future__ import annotations

import logging

import pandas as pd
from freqtrade.strategy import IntParameter

from base import StrategyBase

logger = logging.getLogger(__name__)

STABLES = {
    "USDT", "USDC", "BUSD", "DAI", "TUSD", "USDP", "USDD", "FDUSD",
    "PYUSD", "GUSD", "SUSD", "EUR", "GBP", "AUD", "USD",
}
UNIVERSE_SIZE = 10
HOLD_TOP_K = 3
LOOKBACK = 30
MIN_MOMENTUM = -0.50


def _is_stable(symbol: str) -> bool:
    base = symbol.upper().split("/")[0] if "/" in symbol else symbol.upper()
    return base in STABLES


def _is_monday(ts) -> bool:
    t = pd.Timestamp(ts)
    if t.tzinfo is not None:
        t = t.tz_convert("UTC").tz_localize(None)
    return int(t.dayofweek) == 0


def _select_top_k(momentum: dict[str, float], k: int) -> list[str]:
    alive = {s: m for s, m in momentum.items() if m > MIN_MOMENTUM}
    ranked = sorted(alive.items(), key=lambda kv: kv[1], reverse=True)
    return [s for s, _ in ranked[:k]]


class S2MomentumRotation(StrategyBase):
    """Weekly momentum rotation. Timeframe: 1d."""

    timeframe = "1d"
    can_short = False
    startup_candle_count = 60
    stoploss = -0.10
    stoploss_on_exchange = True
    use_exit_signal = True
    use_custom_stoploss = False
    process_only_new_candles = True
    minimal_roi = {"0": 100}
    trailing_stop = False
    exit_profit_only = False
    ignore_roi_if_entry_signal = False
    order_types = {
        "entry": "limit",
        "exit": "limit",
        "stoploss": "market",
        "stoploss_on_exchange": True,
    }
    order_time_in_force = {"entry": "GTC", "exit": "GTC"}

    def bot_loop_start(self, current_time, **kwargs) -> None:
        first = getattr(self, "_hold_set", None) is None
        if not first and not _is_monday(current_time):
            return
        self._hold_set = frozenset(self._rank_from_dp())

    def populate_indicators(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        lookback = int(self.momentum_lookback.value)
        dataframe["momentum"] = dataframe["close"].pct_change(periods=lookback)
        return dataframe

    def populate_entry_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        dataframe["enter_long"] = 0
        hold = getattr(self, "_hold_set", frozenset())
        if metadata.get("pair", "") in hold and len(dataframe) > 0:
            dataframe.loc[dataframe.index[-1], "enter_long"] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        dataframe["exit_long"] = 0
        hold = getattr(self, "_hold_set", frozenset())
        if metadata.get("pair", "") not in hold and len(dataframe) > 0:
            dataframe.loc[dataframe.index[-1], "exit_long"] = 1
        return dataframe

    @property
    def protections(self):
        return [
            {
                "method": "MaxDrawdown",
                "lookback_period": 1440,
                "stop_duration": 1440,
                "trade_limit": 1,
                "max_allowed_drawdown": 0.10,
            },
            {
                "method": "StoplossGuard",
                "lookback_period": 240,
                "stop_duration": 720,
                "trade_limit": 3,
            },
            {
                "method": "CooldownPeriod",
                "stop_duration_candles": 2,
            },
        ]

    def _rank_from_dp(self) -> list[str]:
        dp = getattr(self, "dp", None)
        if dp is None:
            return []
        lookback = int(self.momentum_lookback.value)
        k = int(self.hold_top_k.value)
        n = int(self.universe_size.value)
        volumes: dict[str, float] = {}
        momentum: dict[str, float] = {}
        pairs = [p for p in dp.current_whitelist() if not _is_stable(p)]
        for pair in pairs:
            frame, _ = dp.get_analyzed_dataframe(pair, self.timeframe)
            if frame is None or len(frame) < lookback + 1:
                continue
            past = float(frame["close"].iloc[-1 - lookback])
            if past == 0:
                continue
            momentum[pair] = float(frame["close"].iloc[-1]) / past - 1.0
            volumes[pair] = float(frame["volume"].iloc[-lookback:].sum())
        ranked_vol = sorted(pairs, key=lambda s: volumes.get(s, 0.0), reverse=True)
        universe = ranked_vol[:n]
        uni_mom = {s: momentum.get(s, 0.0) for s in universe}
        return _select_top_k(uni_mom, k)
