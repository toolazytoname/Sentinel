"""Tests for the Telegram notifier (P2.8).

Coverage:
  - NotifierConfig.from_env() handles missing token / explicit disable
  - send_message: log-only mode, enabled mode (mocked HTTP)
  - send_research_alerts / send_stage_alert / send_weekly_summary: format
    + transport dispatch
  - Pure formatters: status overview, status detail, help
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import httpx
import pytest

from app.notifier import (
    NotifierConfig,
    TELEGRAM_API_BASE,
    TelegramNotifier,
)


# --- NotifierConfig env parsing -------------------------------------------

def test_config_disabled_when_token_missing(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_ENABLED", raising=False)
    cfg = NotifierConfig.from_env()
    assert cfg.enabled is False
    assert cfg.token is None
    assert cfg.chat_id is None


def test_config_disabled_when_only_token_present(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_ENABLED", raising=False)
    cfg = NotifierConfig.from_env()
    assert cfg.enabled is False


def test_config_disabled_when_explicit_disable(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100123")
    monkeypatch.setenv("TELEGRAM_ENABLED", "false")
    cfg = NotifierConfig.from_env()
    assert cfg.enabled is False


def test_config_enabled_with_full_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100123")
    monkeypatch.delenv("TELEGRAM_ENABLED", raising=False)
    cfg = NotifierConfig.from_env()
    assert cfg.enabled is True
    assert cfg.token == "123:abc"
    assert cfg.chat_id == "-100123"


# --- Transport: log-only mode ---------------------------------------------

def test_send_message_log_only_returns_true_and_logs(caplog):
    cfg = NotifierConfig(token=None, chat_id=None, enabled=False)
    n = TelegramNotifier(cfg)
    with caplog.at_level("INFO"):
        ok = n.send_message("hello", chat_id="anywhere")
    assert ok is True
    assert any("telegram (log-only)" in r.message for r in caplog.records)


def test_send_message_log_only_drops_without_chat_id(caplog):
    """Even in log-only mode, missing a target should not crash."""
    cfg = NotifierConfig(token=None, chat_id=None, enabled=False)
    n = TelegramNotifier(cfg)
    ok = n.send_message("hello")
    assert ok is True


# --- Transport: enabled mode (mocked HTTP) -------------------------------

class _CapturingTransport:
    """httpx.MockTransport that records every POST so tests can inspect payload."""

    def __init__(self, status_code: int = 200, fail: bool = False):
        self.requests: list[httpx.Request] = []
        self.status_code = status_code
        self.fail = fail

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail:
            return httpx.Response(500, text="boom")
        return httpx.Response(self.status_code, json={"ok": True, "result": {"message_id": 1}})


def _build_enabled_notifier(*, fail: bool = False) -> tuple[TelegramNotifier, _CapturingTransport]:
    transport = _CapturingTransport(fail=fail)
    cfg = NotifierConfig(token="BOT_TOKEN_X", chat_id="-100DEFAULT", enabled=True)
    n = TelegramNotifier(cfg)
    # Inject mocked client. The notifier lazily creates one; we replace the
    # factory method to return our mocked client.
    n._client = httpx.Client(transport=httpx.MockTransport(transport))
    return n, transport


def test_send_message_posts_to_correct_endpoint():
    n, transport = _build_enabled_notifier()
    ok = n.send_message("hello world")
    assert ok is True
    assert len(transport.requests) == 1
    req = transport.requests[0]
    assert req.method == "POST"
    assert req.url == f"{TELEGRAM_API_BASE}/botBOT_TOKEN_X/sendMessage"
    body = json.loads(req.content)
    assert body["chat_id"] == "-100DEFAULT"
    assert body["text"] == "hello world"
    assert body["parse_mode"] == "Markdown"


def test_send_message_uses_overridden_chat_id():
    n, transport = _build_enabled_notifier()
    n.send_message("hi", chat_id="-100OTHER")
    body = json.loads(transport.requests[0].content)
    assert body["chat_id"] == "-100OTHER"


def test_send_message_returns_false_on_http_failure(caplog):
    n, transport = _build_enabled_notifier(fail=True)
    with caplog.at_level("ERROR"):
        ok = n.send_message("hi")
    assert ok is False
    assert any("telegram send_message failed" in r.message for r in caplog.records)


def test_send_message_enabled_no_chat_id_returns_false():
    """Edge case: enabled but default chat_id got dropped somehow."""
    cfg = NotifierConfig(token="BOT_TOKEN_X", chat_id=None, enabled=True)
    n = TelegramNotifier(cfg)
    assert n.send_message("hi") is False


# --- Domain helpers -------------------------------------------------------

def _make_research_note(
    *,
    asset: str = "BTC",
    event_type: str = "regulatory",
    severity: int = 5,
    summary: str = "Regulator rejected spot ETF",
    source_url: str = "https://example.com/etf",
):
    n = MagicMock()
    n.asset = asset
    n.event_type = event_type
    n.severity = severity
    n.summary = summary
    n.source_url = source_url
    return n


def test_send_research_alerts_dispatches_one_per_note():
    n, transport = _build_enabled_notifier()
    notes = [_make_research_note(severity=5), _make_research_note(asset="ETH", severity=4)]
    sent = n.send_research_alerts(notes)
    assert sent == 2
    assert len(transport.requests) == 2


def test_send_research_alerts_format_includes_severity_and_link():
    n, transport = _build_enabled_notifier()
    n.send_research_alerts([_make_research_note(severity=5)])
    body = json.loads(transport.requests[0].content)
    assert "(5/5)" in body["text"]
    assert "*BTC*" in body["text"]
    assert "https://example.com/etf" in body["text"]


def test_send_stage_alert_promote_includes_emoji():
    n, transport = _build_enabled_notifier()
    n.send_stage_alert(
        strategy="S1", current_stage="dry_run",
        recommendation="promote", rationale="30 days stable, 50 trades",
        next_stage="live_small",
    )
    body = json.loads(transport.requests[0].content)
    assert "🟢 PROMOTE" in body["text"]
    assert "`S1`" in body["text"]
    assert "live_small" in body["text"]


def test_send_stage_alert_demote_uses_red_emoji():
    n, transport = _build_enabled_notifier()
    n.send_stage_alert(
        strategy="S2", current_stage="live_scaled",
        recommendation="demote", rationale="drawdown exceeded threshold",
        next_stage="live_small",
    )
    body = json.loads(transport.requests[0].content)
    assert "🔴 DEMOTE" in body["text"]


def test_send_stage_alert_hold_uses_yellow():
    n, transport = _build_enabled_notifier()
    n.send_stage_alert(
        strategy="S3", current_stage="backtest",
        recommendation="hold", rationale="not enough trades yet",
    )
    body = json.loads(transport.requests[0].content)
    assert "🟡 HOLD" in body["text"]


def test_send_weekly_summary_empty_dict():
    n, transport = _build_enabled_notifier()
    n.send_weekly_summary({})
    body = json.loads(transport.requests[0].content)
    assert "No closed trades" in body["text"]


def test_send_weekly_summary_groups_by_strategy():
    n, transport = _build_enabled_notifier()
    n.send_weekly_summary({"S1": 12, "S2": 3, "S3": 7})
    body = json.loads(transport.requests[0].content)
    assert "`S1`: 12 reflections" in body["text"]
    assert "`S2`: 3 reflections" in body["text"]
    assert "`S3`: 7 reflections" in body["text"]


# --- Pure formatters ------------------------------------------------------

def test_format_help_mentions_status_and_help():
    out = TelegramNotifier.format_help()
    assert "/status" in out
    assert "/help" in out


def test_format_status_overview_empty():
    out = TelegramNotifier.format_status_overview([])
    assert "No strategies" in out


def test_format_status_overview_includes_each_strategy():
    s1 = MagicMock()
    s1.strategy = "S1"
    s1.stage = "dry_run"
    s1.entered_at = datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc)
    s1.trade_count = 42
    out = TelegramNotifier.format_status_overview([s1])
    assert "`S1`" in out
    assert "`dry_run`" in out
    assert "42 trades" in out


def test_format_status_detail_includes_recent_reflections():
    stage = MagicMock()
    stage.strategy = "S1"
    stage.stage = "dry_run"
    stage.entered_at = datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc)
    stage.trade_count = 42
    stage.max_observed_drawdown_pct = 3.2

    r1 = MagicMock()
    r1.created_at = datetime(2026, 7, 5, 12, 0, tzinfo=timezone.utc)
    r1.lesson = "widen trailing in low-vol"

    r2 = MagicMock()
    r2.created_at = datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc)
    r2.lesson = "ADX filter worked"

    out = TelegramNotifier.format_status_detail(stage_row=stage, recent_reflections=[r1, r2])
    assert "`S1`" in out
    assert "3.2%" in out
    assert "widen trailing" in out
    assert "ADX filter" in out


def test_format_status_detail_without_reflections():
    stage = MagicMock()
    stage.strategy = "S1"
    stage.stage = "backtest"
    stage.entered_at = datetime(2026, 7, 1, tzinfo=timezone.utc)
    stage.trade_count = 0
    stage.max_observed_drawdown_pct = 0.0
    out = TelegramNotifier.format_status_detail(stage_row=stage, recent_reflections=[])
    assert "Last 0 reflection" not in out  # no reflections section at all
    assert "`S1`" in out


def test_format_status_detail_truncates_long_lessons():
    stage = MagicMock()
    stage.strategy = "S1"
    stage.stage = "backtest"
    stage.entered_at = datetime(2026, 7, 1, tzinfo=timezone.utc)
    stage.trade_count = 0
    stage.max_observed_drawdown_pct = 0.0

    r = MagicMock()
    r.created_at = datetime(2026, 7, 5, tzinfo=timezone.utc)
    r.lesson = "x" * 500  # way over 120-char limit

    out = TelegramNotifier.format_status_detail(stage_row=stage, recent_reflections=[r])
    assert "..." in out
    # Ensure the giant lesson did NOT make it through verbatim
    assert "x" * 200 not in out