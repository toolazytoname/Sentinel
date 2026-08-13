#!/usr/bin/env python3
"""Run S1 + S2 research backtests on Yahoo daily data and print a markdown report.

Not used for live orders. Binance public API is blocked in some clouds (HTTP 451);
Yahoo USD daily is a close proxy for USDT spot research.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from strategies.data_yahoo import fetch_universe
from strategies.s1_trend_follow.backtest import simulate_universe
from strategies.s1_trend_follow.strategy import StrategyParams
from strategies.s2_momentum_rotation.backtest import run_rotation_backtest
from strategies.s2_momentum_rotation.strategy import RotationParams


def _in_window(trades, start, end) -> int:
    n = 0
    for t in trades:
        d = t.entry_date
        if start <= str(d)[:10] <= end:
            n += 1
    return n


def main() -> int:
    prices = fetch_universe("2020-06-01")
    if len(prices) < 8:
        print(f"download failed: only {len(prices)} pairs", file=sys.stderr)
        return 1

    s1_trades = simulate_universe(prices, StrategyParams())
    s1_2022 = [t for t in s1_trades if str(t.entry_date)[:4] == "2022" or str(t.exit_date)[:4] == "2022"]
    s1_holdout = [t for t in s1_trades if str(t.entry_date)[:10] >= "2024-01-01"]

    s2 = run_rotation_backtest(
        prices,
        RotationParams(universe_size=10, hold_top_k=3, momentum_lookback_days=30),
        initial_capital=1000.0,
    )
    s2_2022 = any(str(t.entry_date)[:4] == "2022" or str(t.exit_date)[:4] == "2022" for t in s2.trades)

    print("# S1 / S2 research backtest (Yahoo daily USD proxy)")
    print()
    print(f"- pairs downloaded: {len(prices)} / 15")
    print(f"- coverage: {min(f.index.min() for f in prices.values()).date()} → {max(f.index.max() for f in prices.values()).date()}")
    print()
    print("## S1 trend-follow (golden cross OR EMA50 pullback reclaim, ADX>25)")
    print(f"- closed trades (unconstrained per pair): **{len(s1_trades)}**")
    print(f"- trades overlapping 2022: **{len(s1_2022)}**")
    print(f"- entries in 2024-01-01+: **{len(s1_holdout)}**")
    if s1_trades:
        wins = sum(1 for t in s1_trades if t.pnl_pct > 0)
        print(f"- win rate: {wins / len(s1_trades):.1%}")
        print(f"- mean pnl: {sum(t.pnl_pct for t in s1_trades) / len(s1_trades):.2%}")
    print()
    print("## S2 weekly momentum rotation (top 3 of volume-10, 10% stop)")
    print(f"- closed trades: **{s2.n_trades}**")
    print(f"- total return: **{s2.total_return_pct:.1%}**")
    print(f"- max drawdown: **{s2.max_drawdown_pct:.1%}**")
    print(f"- 2022 in sample: **{s2_2022}**")
    print()
    print("Notes: S1 sim is per-pair independent (live `max_open_trades=3` will skip extras).")
    print("S2 ranking uses T-1 closes (no current-bar lookahead). Not freqtrade hyperopt.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
