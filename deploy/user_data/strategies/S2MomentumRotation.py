"""S2: point-in-time cross-sectional momentum rotation for Freqtrade.

The strategy ranks the configured whitelist by trailing 30-day quote volume,
then ranks the liquid top-N by trailing 30-day momentum.  It holds the top-K
and only changes the selection on Monday UTC.  All cross-pair inputs are raw
OHLCV; analyzed data is deliberately avoided to prevent pair-order coupling.
"""
from __future__ import annotations

from typing import NamedTuple

import pandas as pd
from freqtrade.strategy import IntParameter

from base import StrategyBase


class RotationParams(NamedTuple):
    universe_size: int = 10
    hold_top_k: int = 3
    momentum_lookback_days: int = 30
    liquidity_lookback_days: int = 30
    rebalance_weekday: int = 0
    min_momentum: float = -0.50


def is_stablecoin(symbol: str) -> bool:
    """Return whether the pair's base asset is a common stablecoin."""
    stablecoins = {
        "USDT", "USDC", "BUSD", "DAI", "TUSD", "USDP", "USDD", "FDUSD",
        "PYUSD", "GUSD", "SUSD", "EUR", "GBP", "AUD", "USD",
    }
    base = symbol.upper().split("/")[0] if "/" in symbol else symbol.upper()
    return base in stablecoins


def momentum_score(dataframe: pd.DataFrame, lookback_days: int) -> pd.Series:
    """Calculate trailing return without future values."""
    return dataframe["close"].pct_change(periods=lookback_days)


def top_k_mask(
    momentum_wide: pd.DataFrame,
    hold_top_k: int,
    min_momentum: float,
) -> pd.DataFrame:
    """Return point-in-time cross-sectional top-K membership."""
    eligible = momentum_wide.where(momentum_wide > min_momentum)
    ranks = eligible.rank(axis=1, ascending=False, method="first")
    return (ranks <= hold_top_k).fillna(False).astype(bool)


def weekly_rotation_mask(
    momentum_wide: pd.DataFrame,
    quote_volume_wide: pd.DataFrame,
    params: RotationParams,
) -> pd.DataFrame:
    """Choose liquid momentum leaders weekly and hold until next Monday."""
    if not isinstance(momentum_wide.index, pd.DatetimeIndex):
        raise ValueError("momentum_wide must use a DatetimeIndex")
    quote_volume = quote_volume_wide.reindex(
        index=momentum_wide.index,
        columns=momentum_wide.columns,
    )
    liquidity_rank = quote_volume.rank(axis=1, ascending=False, method="first")
    liquid_universe = liquidity_rank <= params.universe_size
    selected = top_k_mask(
        momentum_wide.where(liquid_universe),
        params.hold_top_k,
        params.min_momentum,
    )
    rebalance_rows = momentum_wide.index.dayofweek == params.rebalance_weekday
    weekly_decisions = selected.astype("boolean")
    weekly_decisions.loc[~rebalance_rows, :] = pd.NA
    return weekly_decisions.ffill().fillna(False).astype(bool)


class S2MomentumRotation(StrategyBase):
    """Long-only weekly momentum rotation with Sentinel risk controls."""

    timeframe = "1d"
    can_short = False
    startup_candle_count = 60
    stoploss = -0.10
    stoploss_on_exchange = True
    minimal_roi = {"0": 100}
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    order_types = {
        "entry": "limit",
        "exit": "limit",
        "stoploss": "market",
        "stoploss_on_exchange": True,
    }
    order_time_in_force = {"entry": "GTC", "exit": "GTC"}

    universe_size = IntParameter(5, 20, default=10, space="buy")
    hold_top_k = IntParameter(2, 5, default=3, space="buy")
    momentum_lookback = IntParameter(14, 60, default=30, space="buy")

    def _params(self) -> RotationParams:
        return RotationParams(
            universe_size=int(self.universe_size.value),
            hold_top_k=int(self.hold_top_k.value),
            momentum_lookback_days=int(self.momentum_lookback.value),
        )

    @property
    def protections(self):
        """Engine-level circuit breakers required by Sentinel."""
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
            {"method": "CooldownPeriod", "stop_duration_candles": 2},
        ]

    def _cross_section_data(
        self,
        current_pair: str,
        current_df: pd.DataFrame,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Build aligned rolling scores from raw OHLCV for every pair."""
        params = self._params()
        configured_pairs = self.config.get("rotation_pairs")
        whitelist = (
            list(configured_pairs)
            if configured_pairs
            else list(self.dp.current_whitelist())
        )
        momentum_frames: dict[str, pd.Series] = {}
        volume_frames: dict[str, pd.Series] = {}
        for pair in whitelist:
            if is_stablecoin(pair):
                continue
            pdf = current_df if pair == current_pair else self.dp.get_pair_dataframe(
                pair=pair,
                timeframe=self.timeframe,
            )
            if pdf is None or pdf.empty or not {"date", "close", "volume"} <= set(pdf):
                continue
            raw = pdf[["date", "close", "volume"]].drop_duplicates("date").copy()
            raw["date"] = pd.to_datetime(raw["date"], utc=True)
            raw = raw.set_index("date").sort_index()
            momentum_frames[pair] = momentum_score(raw, params.momentum_lookback_days)
            quote_volume = raw["close"] * raw["volume"]
            volume_frames[pair] = quote_volume.rolling(
                params.liquidity_lookback_days,
                min_periods=params.liquidity_lookback_days,
            ).sum()
        return pd.DataFrame(momentum_frames).sort_index(), pd.DataFrame(volume_frames).sort_index()

    def populate_indicators(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        params = self._params()
        pair = metadata["pair"]
        dataframe["momentum"] = momentum_score(dataframe, params.momentum_lookback_days)
        momentum_wide, volume_wide = self._cross_section_data(pair, dataframe)
        if pair in momentum_wide.columns and not momentum_wide.empty:
            held = weekly_rotation_mask(momentum_wide, volume_wide, params)
            by_date = pd.Series(held[pair].values, index=held.index)
            dates = pd.to_datetime(dataframe["date"], utc=True)
            dataframe["in_top_k"] = dates.map(by_date).fillna(False).astype(bool)
        else:
            dataframe["in_top_k"] = False
        dates = pd.to_datetime(dataframe["date"], utc=True)
        dataframe["is_rebalance"] = dates.dt.dayofweek.eq(params.rebalance_weekday)
        return dataframe

    def populate_entry_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        eligible = (
            dataframe["is_rebalance"]
            & dataframe["in_top_k"]
            & dataframe["momentum"].notna()
            & dataframe["volume"].gt(0)
        )
        dataframe["enter_long"] = eligible.astype(int)
        dataframe.loc[eligible, "enter_tag"] = "weekly_rotation"
        return dataframe

    def populate_exit_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        rotate_out = dataframe["is_rebalance"] & ~dataframe["in_top_k"].fillna(False)
        dataframe["exit_long"] = rotate_out.astype(int)
        return dataframe
