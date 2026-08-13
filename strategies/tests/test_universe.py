"""P1.1 universe contract: 15 USDT pairs, no stables, reproducible."""
from __future__ import annotations

import pytest

from strategies.indicators import is_stablecoin
from strategies.universe import (
    MAX_OPEN_TRADES_CAP,
    UNIVERSE_SIZE,
    USDT_SPOT_UNIVERSE,
    yahoo_symbol,
)


def test_universe_has_exactly_fifteen_pairs():
    assert UNIVERSE_SIZE == 15
    assert len(USDT_SPOT_UNIVERSE) == 15
    assert len(set(USDT_SPOT_UNIVERSE)) == 15


def test_universe_excludes_stables_and_bnb():
    for pair in USDT_SPOT_UNIVERSE:
        assert not is_stablecoin(pair), pair
        assert not pair.upper().startswith("BNB/")
        assert pair.endswith("/USDT")


def test_core_majors_are_present():
    for pair in ("BTC/USDT", "ETH/USDT", "SOL/USDT"):
        assert pair in USDT_SPOT_UNIVERSE


def test_yahoo_symbol_maps_usdt_to_usd():
    assert yahoo_symbol("BTC/USDT") == "BTC-USD"
    assert yahoo_symbol("ETH/USDT") == "ETH-USD"


def test_yahoo_symbol_rejects_unknown_pair():
    with pytest.raises(KeyError):
        yahoo_symbol("FAKE/USDT")


def test_open_trades_cap_is_iron_rule():
    assert MAX_OPEN_TRADES_CAP == 3
