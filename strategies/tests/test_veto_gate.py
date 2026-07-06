"""Tests for the veto gate HTTP logic (P1.7).

Three paths required by DoD:
  1. AI service returns VETO → entry blocked (returns False)
  2. AI service returns PASS → entry allowed (returns True)
  3. AI service timeout / unreachable → fail-open (returns True)
"""
from __future__ import annotations

import io
import json
import unittest.mock as mock
import urllib.error

import pytest

from strategies.veto_gate import check_veto


def _mock_response(body: dict, status: int = 200):
    """Return a context manager that yields a mock urllib response."""
    raw = json.dumps(body).encode()
    resp = mock.MagicMock()
    resp.read.return_value = raw
    resp.__enter__ = lambda s: s
    resp.__exit__ = mock.MagicMock(return_value=False)
    return resp


class TestCheckVeto:
    def test_veto_response_blocks_entry(self):
        resp = _mock_response({"decision": "VETO", "reason": "bear market risk"})
        with mock.patch("strategies.veto_gate.urllib.request.urlopen", return_value=resp):
            result = check_veto("S1TrendFollow", "BTC/USDT")
        assert result is False

    def test_pass_response_allows_entry(self):
        resp = _mock_response({"decision": "PASS"})
        with mock.patch("strategies.veto_gate.urllib.request.urlopen", return_value=resp):
            result = check_veto("S1TrendFollow", "BTC/USDT")
        assert result is True

    def test_missing_decision_field_defaults_to_pass(self):
        resp = _mock_response({})  # no decision key
        with mock.patch("strategies.veto_gate.urllib.request.urlopen", return_value=resp):
            result = check_veto("S1TrendFollow", "ETH/USDT")
        assert result is True

    def test_timeout_defaults_to_pass(self):
        import socket
        with mock.patch(
            "strategies.veto_gate.urllib.request.urlopen",
            side_effect=TimeoutError("timed out"),
        ):
            result = check_veto("S1TrendFollow", "BTC/USDT")
        assert result is True

    def test_connection_error_defaults_to_pass(self):
        with mock.patch(
            "strategies.veto_gate.urllib.request.urlopen",
            side_effect=urllib.error.URLError("connection refused"),
        ):
            result = check_veto("S1TrendFollow", "BTC/USDT")
        assert result is True

    def test_malformed_json_defaults_to_pass(self):
        resp = mock.MagicMock()
        resp.read.return_value = b"not json {"
        resp.__enter__ = lambda s: s
        resp.__exit__ = mock.MagicMock(return_value=False)
        with mock.patch("strategies.veto_gate.urllib.request.urlopen", return_value=resp):
            result = check_veto("S1TrendFollow", "BTC/USDT")
        assert result is True

    def test_url_built_correctly(self):
        resp = _mock_response({"decision": "PASS"})
        with mock.patch(
            "strategies.veto_gate.urllib.request.urlopen", return_value=resp
        ) as m:
            check_veto("S1TrendFollow", "BTC/USDT", ai_service_url="http://ai:8000")
        call_url = m.call_args[0][0]
        assert "strategy=S1TrendFollow" in call_url
        assert "pair=BTC/USDT" in call_url
        assert call_url.startswith("http://ai:8000")
