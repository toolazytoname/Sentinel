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


# ---- freqtrade adapter ----

try:
    from freqtrade.strategy import IntParameter, DecimalParameter

    from strategies.base import StrategyBase

    class S2MomentumRotation(StrategyBase):
        """freqtrade-compatible rotation strategy. Wires pure functions above.

        Inherits confirm_trade_entry → AI veto from StrategyBase.
        """
        timeframe = "1d"
        can_short = False
        startup_candle_count = 60  # need 30d momentum + warmup
        stoploss = -0.10

        # Hyperopt ranges — narrow to prevent overfit
        universe_size = IntParameter(5, 20, default=10, space="buy")
        hold_top_k = IntParameter(2, 5, default=3, space="buy")
        momentum_lookback = IntParameter(14, 60, default=30, space="buy")

        def populate_indicators(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
            params = RotationParams(momentum_lookback_days=int(self.momentum_lookback.value))
            dataframe["momentum"] = momentum_score(dataframe, params.momentum_lookback_days)
            return dataframe

except ImportError:
    S2MomentumRotation = None  # type: ignore[assignment]