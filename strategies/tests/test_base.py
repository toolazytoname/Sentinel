"""Tests for the StrategyBase confirm_trade_entry contract (P1.7).

Three paths required by DoD:
  1. AI service returns VETO → entry blocked (returns False)
  2. AI service returns PASS → entry allowed (returns True)
  3. AI service timeout / unreachable → fail-open (returns True)
"""
from __future__ import annotations

import io
import json
import unittest.mock as mock

import pytest

from strategies.base import StrategyBase, _PureStrategyBase
from strategies.veto_gate import DEFAULT_AI_SERVICE_URL


def _mock_response(body: dict, status: int = 200):
    raw = json.dumps(body).encode()
    resp = mock.MagicMock()
    resp.read.return_value = raw
    resp.__enter__ = lambda s: s
    resp.__exit__ = mock.MagicMock(return_value=False)
    return resp


# --- Base class contract ---

def test_pure_strategy_base_fails_open_on_urlerror():
    """confirm_trade_entry → returns True on URL errors."""
    base = _PureStrategyBase()
    with mock.patch(
        "strategies.veto_gate.urllib.request.urlopen",
        side_effect=IOError("connection refused"),
    ):
        result = base.confirm_trade_entry(
            pair="BTC/USDT",
            order_type="limit",
            amount=0.1,
            rate=50000,
            time_in_force="GTC",
            current_time=None,
            entry_tag=None,
            side="long",
        )
    assert result is True


def test_pure_strategy_base_respects_veto():
    """VETO from AI service → entry blocked."""
    base = _PureStrategyBase()
    resp = _mock_response({"decision": "VETO", "reason": "bear market"})
    with mock.patch("strategies.veto_gate.urllib.request.urlopen", return_value=resp):
        result = base.confirm_trade_entry(
            pair="BTC/USDT",
            order_type="limit",
            amount=0.1,
            rate=50000,
            time_in_force="GTC",
            current_time=None,
            entry_tag=None,
            side="long",
        )
    assert result is False


def test_pure_strategy_base_respects_pass():
    """PASS from AI service → entry allowed."""
    base = _PureStrategyBase()
    resp = _mock_response({"decision": "PASS", "reason": ""})
    with mock.patch("strategies.veto_gate.urllib.request.urlopen", return_value=resp):
        result = base.confirm_trade_entry(
            pair="BTC/USDT",
            order_type="limit",
            amount=0.1,
            rate=50000,
            time_in_force="GTC",
            current_time=None,
            entry_tag=None,
            side="long",
        )
    assert result is True


def test_default_ai_service_url_matches_veto_gate():
    """Single source of truth for the URL constant."""
    assert StrategyBase.ai_service_url == DEFAULT_AI_SERVICE_URL


def test_default_custom_stoploss_returns_static_stoploss():
    """Base custom_stoploss is a pass-through; subclasses tighten."""
    base = _PureStrategyBase()
    base.stoploss = -0.10
    assert base.custom_stoploss() == -0.10


def test_required_class_attrs_documented():
    """REQUIRED_CLASS_ATTRS lists the minimum contract every strategy must satisfy."""
    from strategies.base import REQUIRED_CLASS_ATTRS
    for attr in REQUIRED_CLASS_ATTRS:
        assert hasattr(_PureStrategyBase, attr), (
            f"StrategyBase must define {attr} (in REQUIRED_CLASS_ATTRS)"
        )