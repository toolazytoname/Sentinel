"""S2: Cross-sectional momentum rotation.

Logic
-----
Weekly rebalance:
  1. Universe = top-N symbols by trading volume (proxy for "large enough to trade")
  2. Filter out stablecoins and quote currencies
  3. Compute 30-day momentum score for each symbol
  4. Rank: hold top-K (default 3)
  5. Equal-weight: every selected pair receives the same configured stake

This strategy is LONG-ONLY and LONG-TERM — it doesn't care about absolute
direction, only relative strength. Works in bull markets; tends to lag in
sharp reversals (mitigated by S1's hard stop at the freqtrade strategy
level).

Pure logic functions below are tested without freqtrade dependency. The
freqtrade IStrategy adapter wires them to the protocol.
"""
from __future__ import annotations

from typing import NamedTuple, Sequence

import pandas as pd

from strategies.indicators import is_stablecoin, momentum_score


class RotationParams(NamedTuple):
    universe_size: int = 10         # top-N candidates by liquidity
    hold_top_k: int = 3             # number to actually hold
    momentum_lookback_days: int = 30
    liquidity_lookback_days: int = 30
    rebalance_weekday: int = 0       # Monday, UTC
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


def top_k_mask(
    momentum_wide: pd.DataFrame,
    hold_top_k: int,
    min_momentum: float,
) -> pd.DataFrame:
    """Vectorized cross-sectional top-K selection over time.

    Args:
        momentum_wide: DataFrame indexed by time, one column per pair, values
            are that pair's momentum score at each bar (NaN during warmup or
            when the pair isn't listed yet).
        hold_top_k: how many pairs to hold at each bar.
        min_momentum: pairs at or below this momentum are ineligible (dead
            coins), even if that leaves fewer than K holdings.

    Returns:
        A boolean DataFrame, same shape/columns as the input, True where the
        pair is among the top-K by momentum at that bar.

    Lookahead safety: the decision at bar t uses only bar-t momentum
    (close(t)/close(t-N)); freqtrade fills the resulting entry on bar t+1's
    open, so no future information leaks into the signal.
    """
    # Ineligible cells (NaN or <= min_momentum) become NaN so rank() skips them.
    eligible = momentum_wide.where(momentum_wide > min_momentum)
    # Rank per row, highest momentum = rank 1. method="first" breaks ties by
    # column order → deterministic, reproducible selection.
    ranks = eligible.rank(axis=1, ascending=False, method="first")
    mask = ranks <= hold_top_k
    return mask.fillna(False).astype(bool)


def weekly_rotation_mask(
    momentum_wide: pd.DataFrame,
    quote_volume_wide: pd.DataFrame,
    params: RotationParams,
) -> pd.DataFrame:
    """Select liquid momentum leaders weekly and hold until next rebalance.

    Both input frames must contain point-in-time rolling values.  At each
    Monday UTC close, the function first keeps the top-N pairs by trailing
    quote volume, then keeps the top-K of those by trailing momentum.  The
    resulting decision is forward-filled until the following Monday.
    """
    if not isinstance(momentum_wide.index, pd.DatetimeIndex):
        raise ValueError("momentum_wide must use a DatetimeIndex")
    if not 0 <= params.rebalance_weekday <= 6:
        raise ValueError("rebalance_weekday must be between 0 and 6")

    quote_volume = quote_volume_wide.reindex(
        index=momentum_wide.index,
        columns=momentum_wide.columns,
    )
    liquidity_rank = quote_volume.rank(axis=1, ascending=False, method="first")
    liquid_universe = liquidity_rank <= params.universe_size
    eligible_momentum = momentum_wide.where(liquid_universe)
    selected = top_k_mask(
        eligible_momentum,
        params.hold_top_k,
        params.min_momentum,
    )

    rebalance_rows = momentum_wide.index.dayofweek == params.rebalance_weekday
    weekly_decisions = selected.astype("boolean")
    weekly_decisions.loc[~rebalance_rows, :] = pd.NA
    return weekly_decisions.ffill().fillna(False).astype(bool)


# ---- freqtrade adapter ----

try:
    from freqtrade.strategy import IntParameter

    from strategies.base import StrategyBase

    class S2MomentumRotation(StrategyBase):
        """freqtrade-compatible cross-sectional momentum rotation.

        Wires the pure ranking functions above into freqtrade's per-pair
        populate_* model. At each bar it ranks the whole (stablecoin-free)
        whitelist by momentum and holds the top-K; a pair enters when it is in
        the top-K and exits when it drops out.

        Inherits confirm_trade_entry → AI veto from StrategyBase.
        """
        timeframe = "1d"
        can_short = False
        startup_candle_count = 60  # need 30d momentum + warmup
        stoploss = -0.10           # exchange hard floor; rotation exits handle the rest
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

        # Hyperopt ranges — narrow to prevent overfit
        universe_size = IntParameter(5, 20, default=10, space="buy")
        hold_top_k = IntParameter(2, 5, default=3, space="buy")
        momentum_lookback = IntParameter(14, 60, default=30, space="buy")

        def _params(self) -> "RotationParams":
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
            """Build point-in-time momentum and liquidity frames from raw OHLCV."""
            params = self._params()
            configured_pairs = self.config.get("rotation_pairs")
            if configured_pairs:
                whitelist = list(configured_pairs)
            else:
                try:
                    whitelist = list(self.dp.current_whitelist())
                except Exception:  # pragma: no cover - dp unavailable outside runtime
                    whitelist = [current_pair]

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
                momentum_frames[pair] = momentum_score(
                    raw,
                    params.momentum_lookback_days,
                )
                quote_volume = raw["close"] * raw["volume"]
                volume_frames[pair] = quote_volume.rolling(
                    params.liquidity_lookback_days,
                    min_periods=params.liquidity_lookback_days,
                ).sum()

            if not momentum_frames:
                empty_index = pd.DatetimeIndex(pd.to_datetime(current_df["date"], utc=True))
                return pd.DataFrame(index=empty_index), pd.DataFrame(index=empty_index)
            return (
                pd.DataFrame(momentum_frames).sort_index(),
                pd.DataFrame(volume_frames).sort_index(),
            )

        def populate_indicators(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
            params = self._params()
            pair = metadata["pair"]
            dataframe["momentum"] = momentum_score(dataframe, params.momentum_lookback_days)

            momentum_wide, volume_wide = self._cross_section_data(pair, dataframe)
            if pair in momentum_wide.columns and not momentum_wide.empty:
                held = weekly_rotation_mask(momentum_wide, volume_wide, params)
                by_date = pd.Series(held[pair].values, index=held.index)
                dates = pd.to_datetime(dataframe["date"], utc=True)
                dataframe["in_top_k"] = (
                    dates.map(by_date).fillna(False).astype(bool)
                )
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

except ImportError:
    S2MomentumRotation = None  # type: ignore[assignment]
