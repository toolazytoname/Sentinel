"""Per-pair S1 trade simulator using the tested entry/exit functions."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from strategies.s1_trend_follow.strategy import (
    StrategyParams,
    compute_indicators,
    entry_signal,
    exit_signal,
)


@dataclass(frozen=True)
class ClosedTrade:
    pair: str
    entry_date: object
    exit_date: object
    entry_price: float
    exit_price: float
    pnl_pct: float
    reason: str


def simulate_pair(
    df: pd.DataFrame,
    params: StrategyParams,
    pair: str = "PAIR",
) -> list[ClosedTrade]:
    """Walk one OHLCV frame; enter/exit at close. No lookahead beyond row t."""
    out = compute_indicators(df, params)
    trades: list[ClosedTrade] = []
    in_pos = False
    entry = peak = 0.0
    entry_date = None
    for ts, row in out.iterrows():
        price = float(row["close"])
        if not in_pos:
            if entry_signal(row, params):
                in_pos = True
                entry = peak = price
                entry_date = ts
            continue
        peak = max(peak, price)
        should, reason = exit_signal(row, entry, peak, params)
        if not should:
            continue
        trades.append(
            ClosedTrade(
                pair=pair,
                entry_date=entry_date,
                exit_date=ts,
                entry_price=entry,
                exit_price=price,
                pnl_pct=price / entry - 1.0,
                reason=reason,
            )
        )
        in_pos = False
    if in_pos:
        last_ts = out.index[-1]
        last_px = float(out.iloc[-1]["close"])
        trades.append(
            ClosedTrade(
                pair=pair,
                entry_date=entry_date,
                exit_date=last_ts,
                entry_price=entry,
                exit_price=last_px,
                pnl_pct=last_px / entry - 1.0,
                reason="end_of_data",
            )
        )
    return trades


def simulate_universe(
    prices: dict[str, pd.DataFrame],
    params: StrategyParams,
) -> list[ClosedTrade]:
    """Independent per-pair simulation (live max_open_trades=3 is NOT applied)."""
    all_trades: list[ClosedTrade] = []
    for pair, frame in prices.items():
        all_trades.extend(simulate_pair(frame, params, pair=pair))
    return all_trades
