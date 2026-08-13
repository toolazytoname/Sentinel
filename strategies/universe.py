"""Frozen USDT spot universe for S1/S2 (P1.1).

Snapshot date: 2026-08-13. This is a static list so backtests are
reproducible — not a live CoinGecko scrape.

Excludes:
  - stables (USDT/USDC/DAI/…)
  - BNB (existing config blacklist: exchange token concentration)

Fourteen of fifteen names traded through the 2022 bear; NEAR listed
2022-04 so its 2022 sample is partial. S2 still ranks whatever pairs
have enough lookback on each rebalance date.
"""
from __future__ import annotations

USDT_SPOT_UNIVERSE: tuple[str, ...] = (
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "XRP/USDT",
    "ADA/USDT",
    "DOGE/USDT",
    "AVAX/USDT",
    "DOT/USDT",
    "LINK/USDT",
    "BCH/USDT",
    "LTC/USDT",
    "UNI/USDT",
    "ATOM/USDT",
    "FIL/USDT",
    "NEAR/USDT",
)

# Yahoo Finance uses hyphenated USD pairs; prices track USDT spot closely
# enough for a research backtest (we are not placing orders).
YAHOO_SYMBOLS: dict[str, str] = {pair: pair.split("/")[0] + "-USD" for pair in USDT_SPOT_UNIVERSE}

UNIVERSE_SIZE = len(USDT_SPOT_UNIVERSE)
MAX_OPEN_TRADES_CAP = 3  # iron rule 4 / design §2.1


def yahoo_symbol(pair: str) -> str:
    """Return the Yahoo chart symbol for a USDT pair."""
    if pair not in YAHOO_SYMBOLS:
        raise KeyError(f"pair {pair!r} is not in USDT_SPOT_UNIVERSE")
    return YAHOO_SYMBOLS[pair]
