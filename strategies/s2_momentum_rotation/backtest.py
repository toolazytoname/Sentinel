"""Point-in-time weekly rotation backtest (official S2 research result).

Ranking on date T uses only closes through T-1 (no current-bar lookahead).
Fills at T's close. Intraday 10% stop uses that day's low.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from strategies.s2_momentum_rotation.strategy import (
    RotationParams,
    is_rebalance_timestamp,
    rank_holdings,
)

STOPLOSS_PCT = 0.10


@dataclass(frozen=True)
class ClosedTrade:
    pair: str
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    entry_price: float
    exit_price: float
    pnl_pct: float
    reason: str


@dataclass(frozen=True)
class BacktestReport:
    trades: tuple[ClosedTrade, ...]
    equity_curve: pd.Series
    max_drawdown_pct: float
    total_return_pct: float
    n_trades: int


def _asof(frame: pd.DataFrame, asof: pd.Timestamp) -> pd.DataFrame:
    return frame.loc[frame.index <= asof]


def _union_index(prices: dict[str, pd.DataFrame]) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex([])
    for frame in prices.values():
        idx = idx.union(frame.index)
    return pd.DatetimeIndex(idx).sort_values().unique()


def _snapshots_asof(
    prices: dict[str, pd.DataFrame],
    asof: pd.Timestamp,
) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for pair, frame in prices.items():
        sliced = _asof(frame, asof)
        if not sliced.empty:
            out[pair] = sliced
    return out


def _close_on(frame: pd.DataFrame, dt: pd.Timestamp) -> float | None:
    if dt not in frame.index:
        return None
    return float(frame.loc[dt, "close"])


def _low_on(frame: pd.DataFrame, dt: pd.Timestamp) -> float | None:
    if dt not in frame.index:
        return None
    col = "low" if "low" in frame.columns else "close"
    return float(frame.loc[dt, col])


def _max_drawdown_pct(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    peak = equity.cummax()
    dd = (equity / peak) - 1.0
    return float(dd.min())


def _close_trade(
    pair: str,
    pos: dict,
    exit_date: pd.Timestamp,
    exit_price: float,
    reason: str,
) -> ClosedTrade:
    entry = float(pos["entry_price"])
    pnl = exit_price / entry - 1.0
    return ClosedTrade(
        pair=pair,
        entry_date=pos["entry_date"],
        exit_date=exit_date,
        entry_price=entry,
        exit_price=exit_price,
        pnl_pct=pnl,
        reason=reason,
    )


def _apply_stops(
    positions: dict[str, dict],
    prices: dict[str, pd.DataFrame],
    dt: pd.Timestamp,
    stoploss_pct: float,
) -> tuple[dict[str, dict], list[ClosedTrade], float]:
    """Hit stop at the stop price; proceeds return to cash."""
    remaining: dict[str, dict] = {}
    closed: list[ClosedTrade] = []
    cash_in = 0.0
    stop_level = 1.0 - stoploss_pct
    for pair, pos in positions.items():
        frame = prices.get(pair)
        if frame is None:
            remaining[pair] = pos
            continue
        low = _low_on(frame, dt)
        if low is None:
            remaining[pair] = pos
            continue
        stop_px = float(pos["entry_price"]) * stop_level
        if low <= stop_px:
            closed.append(_close_trade(pair, pos, dt, stop_px, "stoploss"))
            cash_in += stop_px * float(pos["qty"])
        else:
            remaining[pair] = pos
    return remaining, closed, cash_in


def _mark_to_market(
    cash: float,
    positions: dict[str, dict],
    prices: dict[str, pd.DataFrame],
    dt: pd.Timestamp,
) -> float:
    total = cash
    for pair, pos in positions.items():
        frame = prices[pair]
        px = _close_on(frame, dt)
        if px is None:
            px = float(pos["entry_price"])
        total += px * float(pos["qty"])
    return total


def _rebalance(
    cash: float,
    positions: dict[str, dict],
    prices: dict[str, pd.DataFrame],
    dt: pd.Timestamp,
    target: list[str],
) -> tuple[float, dict[str, dict], list[ClosedTrade]]:
    """Liquidate names not in target; deploy equal weight into missing targets."""
    closed: list[ClosedTrade] = []
    kept: dict[str, dict] = {}
    for pair, pos in positions.items():
        if pair in target:
            kept[pair] = pos
            continue
        frame = prices[pair]
        px = _close_on(frame, dt) or float(pos["entry_price"])
        closed.append(_close_trade(pair, pos, dt, px, "rebalance"))
        cash += px * float(pos["qty"])
    missing = [p for p in target if p not in kept]
    if not missing:
        return cash, kept, closed
    marked = cash
    for pair, pos in kept.items():
        px = _close_on(prices[pair], dt) or float(pos["entry_price"])
        marked += px * float(pos["qty"])
    n = len(target)
    slot = marked / n if n else 0.0
    cash, kept = _open_missing(cash, kept, prices, dt, missing, slot)
    return cash, kept, closed


def _open_missing(
    cash: float,
    kept: dict[str, dict],
    prices: dict[str, pd.DataFrame],
    dt: pd.Timestamp,
    missing: list[str],
    slot: float,
) -> tuple[float, dict[str, dict]]:
    for pair in missing:
        px = _close_on(prices[pair], dt)
        if px is None or px <= 0 or slot <= 0:
            continue
        qty = slot / px
        if qty * px > cash:
            qty = cash / px if px else 0.0
        if qty <= 0:
            continue
        cash -= qty * px
        kept[pair] = {
            "pair": pair,
            "entry_date": dt,
            "entry_price": px,
            "qty": qty,
        }
    return cash, kept


def run_rotation_backtest(
    prices: dict[str, pd.DataFrame],
    params: RotationParams,
    *,
    stoploss_pct: float = STOPLOSS_PCT,
    initial_capital: float = 1000.0,
) -> BacktestReport:
    """Walk daily. Rank on T-1. Rebalance Mondays. 10% stop between weeks."""
    calendar = _union_index(prices)
    cash = float(initial_capital)
    positions: dict[str, dict] = {}
    trades: list[ClosedTrade] = []
    equity_pts: list[tuple[pd.Timestamp, float]] = []
    lookback = params.momentum_lookback_days

    for i, dt in enumerate(calendar):
        if i < lookback + 1:
            equity_pts.append((dt, cash))
            continue
        positions, stopped, proceeds = _apply_stops(positions, prices, dt, stoploss_pct)
        cash += proceeds
        trades.extend(stopped)
        if is_rebalance_timestamp(dt):
            asof = calendar[i - 1]
            snaps = _snapshots_asof(prices, asof)
            target = rank_holdings(snaps, params)
            cash, positions, rotated = _rebalance(cash, positions, prices, dt, target)
            trades.extend(rotated)
        equity_pts.append((dt, _mark_to_market(cash, positions, prices, dt)))

    equity = pd.Series({t: v for t, v in equity_pts}).sort_index()
    start = float(initial_capital)
    end = float(equity.iloc[-1]) if not equity.empty else start
    return BacktestReport(
        trades=tuple(trades),
        equity_curve=equity,
        max_drawdown_pct=_max_drawdown_pct(equity),
        total_return_pct=(end / start - 1.0) if start else 0.0,
        n_trades=len(trades),
    )
