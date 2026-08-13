"""S2: Cross-sectional momentum rotation.

Logic
-----
Weekly rebalance:
  1. Universe = top-N symbols by trading volume (proxy for "large enough to trade")
  2. Filter out stablecoins and quote currencies
  3. Compute 30-day momentum score for each symbol
  4. Rank: hold top-K (default 3)
  5. Equal-weight: each holding is stake_amount / K

This strategy is LONG-ONLY and LONG-TERM — it doesn't care about absolute
direction, only relative strength. Works in bull markets; tends to lag in
sharp reversals (mitigated by S1's hard stop at the freqtrade strategy
level).

Pure logic functions below are tested without freqtrade dependency. The
freqtrade IStrategy adapter wires them to the protocol.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import pandas as pd

from strategies.indicators import is_stablecoin, momentum_score


@dataclass(frozen=True)
class RotationParams:
    universe_size: int = 10         # top-N candidates by liquidity
    hold_top_k: int = 3             # number to actually hold
    momentum_lookback_days: int = 30
    min_momentum: float = -0.50     # filter anything below -50% (likely dead/delisted)


def compute_universe(
    symbols: Sequence[str],
    volumes_30d: dict[str, float],
    params: RotationParams,
) -> list[str]:
    """Take the top-N symbols by 30d volume, excluding stablecoins.

    Args:
        symbols: all candidate trading pairs available on the exchange
        volumes_30d: dict mapping each symbol → 30-day volume in quote ccy
        params: rotation parameters

    Returns:
        Ordered list (highest volume first) of stablecoin-free symbols,
        length <= params.universe_size.
    """
    # Filter out stablecoins first (they have fake-high volume)
    non_stable = [s for s in symbols if not is_stablecoin(s)]

    # Sort by volume desc, take top N
    ranked = sorted(
        non_stable,
        key=lambda s: volumes_30d.get(s, 0.0),
        reverse=True,
    )
    return ranked[: params.universe_size]


def select_top_k(
    momentum_by_symbol: dict[str, float],
    params: RotationParams,
) -> list[str]:
    """From the momentum dictionary, pick the top-K with momentum > min_momentum.

    Args:
        momentum_by_symbol: {symbol: momentum_score} — typically computed
            by strategies.indicators.momentum_score(close_df, lookback_days)
        params: rotation parameters

    Returns:
        List of top-K symbols ordered by momentum descending, length <= K.
        Symbols below min_momentum are excluded.
    """
    # Filter dead coins
    alive = {s: m for s, m in momentum_by_symbol.items() if m > params.min_momentum}

    # Sort by momentum desc
    ranked = sorted(alive.items(), key=lambda kv: kv[1], reverse=True)
    return [s for s, _ in ranked[: params.hold_top_k]]


def equal_weights(
    holdings: Sequence[str],
    total_stake: float,
    params: RotationParams,
) -> dict[str, float]:
    """Equal-weight allocation across holdings.

    Args:
        holdings: list of selected symbols
        total_stake: total capital to deploy
        params: rotation parameters (hold_top_k used as denominator)

    Returns:
        dict mapping symbol → stake amount. If holdings < hold_top_k (because
        universe is small), divide total_stake by holdings.count instead so
        we actually deploy the capital.
    """
    if not holdings:
        return {}
    # If we got fewer holdings than hold_top_k, divide total stake among
    # the available ones — don't leave cash idle.
    n = len(holdings) if len(holdings) > 0 else params.hold_top_k
    per_symbol = total_stake / n
    return {s: per_symbol for s in holdings}


def rotation_plan(
    symbols: Sequence[str],
    volumes_30d: dict[str, float],
    momentum_by_symbol: dict[str, float],
    total_stake: float,
    params: RotationParams,
) -> dict[str, float]:
    """Full end-to-end rotation: universe → top-K → weights.

    This is the canonical function called by the freqtrade strategy each
    rebalance bar.
    """
    universe = compute_universe(symbols, volumes_30d, params)
    # Only consider momentum for symbols in the universe
    universe_momentum = {s: momentum_by_symbol.get(s, 0.0) for s in universe}
    top_k = select_top_k(universe_momentum, params)
    return equal_weights(top_k, total_stake, params)


def is_rebalance_timestamp(ts) -> bool:
    """Weekly rebalance = Monday UTC. Naive timestamps treated as UTC."""
    t = pd.Timestamp(ts)
    if t.tzinfo is not None:
        t = t.tz_convert("UTC").tz_localize(None)
    return int(t.dayofweek) == 0


def snapshot_momentum_and_volume(
    df: pd.DataFrame,
    lookback: int,
) -> tuple[float, float] | None:
    """Point-in-time momentum + volume using only rows already in ``df``."""
    if df is None or len(df) < lookback + 1:
        return None
    close = df["close"]
    last = float(close.iloc[-1])
    past = float(close.iloc[-1 - lookback])
    if past == 0:
        return None
    mom = last / past - 1.0
    vol = float(df["volume"].iloc[-lookback:].sum()) if "volume" in df.columns else 0.0
    return mom, vol


def rank_holdings(
    snapshots: dict[str, pd.DataFrame],
    params: RotationParams,
) -> list[str]:
    """Cross-sectional top-K from a dict of as-of OHLCV frames."""
    volumes: dict[str, float] = {}
    momentum: dict[str, float] = {}
    for pair, frame in snapshots.items():
        scored = snapshot_momentum_and_volume(frame, params.momentum_lookback_days)
        if scored is None:
            continue
        momentum[pair], volumes[pair] = scored
    if not momentum:
        return []
    plan = rotation_plan(list(snapshots), volumes, momentum, total_stake=1.0, params=params)
    return list(plan.keys())


# ---- freqtrade adapter ----

try:
    from freqtrade.strategy import IntParameter

    from strategies.base import StrategyBase

    class S2MomentumRotation(StrategyBase):
        """freqtrade adapter for live/dry-run.

        Cross-sectional rank is computed in ``bot_loop_start`` from the
        dataprovider (current candle only). Historical freqtrade backtesting
        of this adapter is NOT the official S2 result — use
        ``strategies.s2_momentum_rotation.backtest.run_rotation_backtest``.
        """

        timeframe = "1d"
        can_short = False
        startup_candle_count = 60
        stoploss = -0.10
        use_exit_signal = True
        use_custom_stoploss = False
        process_only_new_candles = True
        minimal_roi = {"0": 100}
        stoploss_on_exchange = True

        universe_size = IntParameter(5, 20, default=10, space="buy")
        hold_top_k = IntParameter(2, 5, default=3, space="buy")
        momentum_lookback = IntParameter(14, 60, default=30, space="buy")

        def bot_loop_start(self, current_time, **kwargs) -> None:
            self._maybe_refresh_holdings(current_time)

        def populate_indicators(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
            lookback = int(self.momentum_lookback.value)
            dataframe["momentum"] = momentum_score(dataframe, lookback)
            return dataframe

        def populate_entry_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
            dataframe["enter_long"] = 0
            hold = getattr(self, "_hold_set", frozenset())
            pair = metadata.get("pair", "")
            if pair in hold and len(dataframe) > 0:
                dataframe.loc[dataframe.index[-1], "enter_long"] = 1
            return dataframe

        def populate_exit_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
            dataframe["exit_long"] = 0
            hold = getattr(self, "_hold_set", frozenset())
            pair = metadata.get("pair", "")
            if pair not in hold and len(dataframe) > 0:
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

        def _rotation_params(self) -> RotationParams:
            return RotationParams(
                universe_size=int(self.universe_size.value),
                hold_top_k=int(self.hold_top_k.value),
                momentum_lookback_days=int(self.momentum_lookback.value),
            )

        def _maybe_refresh_holdings(self, current_time) -> None:
            first = getattr(self, "_hold_set", None) is None
            if not first and not is_rebalance_timestamp(current_time):
                return
            self._hold_set = frozenset(self._rank_from_dataprovider())

        def _rank_from_dataprovider(self) -> list[str]:
            dp = getattr(self, "dp", None)
            if dp is None:
                return []
            snapshots: dict[str, pd.DataFrame] = {}
            for pair in dp.current_whitelist():
                frame, _ = dp.get_analyzed_dataframe(pair, self.timeframe)
                if frame is None or frame.empty:
                    continue
                snapshots[pair] = frame
            return rank_holdings(snapshots, self._rotation_params())

except ImportError:
    S2MomentumRotation = None  # type: ignore[assignment]