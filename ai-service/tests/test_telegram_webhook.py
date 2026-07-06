"""Tests for the inbound Telegram webhook (P2.8).

The webhook endpoint accepts raw Telegram Update dicts and routes
commands. We override `get_notifier` with a MagicMock so we can assert
on what was sent back. Formatting is covered by test_notifier.py.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["OPENAI_API_KEY"] = "sk-test-fake"

from app.db.models import Base  # noqa: E402
from app.deps import get_db, get_notifier, reset_caches_for_testing  # noqa: E402
from app.main import app  # noqa: E402
from app.modules.stages import register_strategy as repo_register  # noqa: E402
from app.notifier import TelegramNotifier  # noqa: E402


def _build_app():
    """Test app with fresh in-memory DB + injectable notifier mock.

    LLM is irrelevant for webhook tests — the LLM client is built but
    never called (none of the webhook code paths touch it).
    """
    reset_caches_for_testing()
    engine = create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    def override_get_db():
        s = SessionLocal()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override_get_db

    notifier_mock = MagicMock(spec=TelegramNotifier)
    notifier_mock.enabled = True  # so we don't trigger log-only branch
    app.dependency_overrides[get_notifier] = lambda: notifier_mock

    return app, notifier_mock


def _teardown_app():
    app.dependency_overrides.clear()


def _make_update(text: str, chat_id: int = -1001) -> dict:
    return {
        "update_id": 1,
        "message": {
            "message_id": 100,
            "date": 0,
            "chat": {"id": chat_id, "type": "private"},
            "text": text,
        },
    }


# --- /help and /start ------------------------------------------------------

def test_help_command_replies_with_help_text():
    app_obj, notifier = _build_app()
    try:
        client = TestClient(app_obj)
        resp = client.post("/telegram/webhook", json=_make_update("/help"))
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        notifier.send_message.assert_called_once()
        text = notifier.send_message.call_args[0][0]
        assert "/status" in text
        assert "/help" in text
    finally:
        _teardown_app()


def test_start_command_aliases_to_help():
    app_obj, notifier = _build_app()
    try:
        client = TestClient(app_obj)
        resp = client.post("/telegram/webhook", json=_make_update("/start"))
        assert resp.status_code == 200
        notifier.send_message.assert_called_once()
        text = notifier.send_message.call_args[0][0]
        assert "/status" in text
    finally:
        _teardown_app()


# --- /status overview ------------------------------------------------------

def test_status_no_args_lists_strategies():
    app_obj, notifier = _build_app()
    try:
        client = TestClient(app_obj)
        # Seed two strategies via the repo (use the override dependency directly)
        gen = app_obj.dependency_overrides[get_db]()
        s = next(gen)
        try:
            repo_register(s, strategy="S1TrendFollow", initial_stage="dry_run", approved_by="test")
            repo_register(s, strategy="S2MomentumRotation", initial_stage="backtest", approved_by="test")
        finally:
            try:
                next(gen)
            except StopIteration:
                pass

        resp = client.post("/telegram/webhook", json=_make_update("/status"))
        assert resp.status_code == 200
        notifier.send_message.assert_called_once()
        text = notifier.send_message.call_args[0][0]
        assert "S1TrendFollow" in text
        assert "S2MomentumRotation" in text
        assert "dry_run" in text
        assert "backtest" in text
    finally:
        _teardown_app()


def test_status_no_args_empty_db_says_no_strategies():
    app_obj, notifier = _build_app()
    try:
        client = TestClient(app_obj)
        resp = client.post("/telegram/webhook", json=_make_update("/status"))
        assert resp.status_code == 200
        text = notifier.send_message.call_args[0][0]
        assert "No strategies" in text
    finally:
        _teardown_app()


# --- /status <name> detail -------------------------------------------------

def test_status_named_strategy_returns_detail():
    app_obj, notifier = _build_app()
    try:
        client = TestClient(app_obj)
        gen = app_obj.dependency_overrides[get_db]()
        s = next(gen)
        try:
            repo_register(s, strategy="S1TrendFollow", initial_stage="dry_run", approved_by="test")
        finally:
            try:
                next(gen)
            except StopIteration:
                pass

        resp = client.post("/telegram/webhook", json=_make_update("/status S1TrendFollow"))
        assert resp.status_code == 200
        text = notifier.send_message.call_args[0][0]
        assert "S1TrendFollow" in text
        assert "dry_run" in text
    finally:
        _teardown_app()


def test_status_unknown_strategy_returns_error_message():
    app_obj, notifier = _build_app()
    try:
        client = TestClient(app_obj)
        resp = client.post("/telegram/webhook", json=_make_update("/status DoesNotExist"))
        assert resp.status_code == 200
        text = notifier.send_message.call_args[0][0]
        assert "not registered" in text
        assert "DoesNotExist" in text
    finally:
        _teardown_app()


# --- @botname suffix handling ----------------------------------------------

def test_at_botname_suffix_is_stripped():
    """Users in groups type /status@MyBot — strip the suffix."""
    app_obj, notifier = _build_app()
    try:
        client = TestClient(app_obj)
        resp = client.post("/telegram/webhook", json=_make_update("/status@SentinelBot"))
        assert resp.status_code == 200
        notifier.send_message.assert_called_once()
    finally:
        _teardown_app()


# --- Unknown / non-command messages ---------------------------------------

def test_unknown_command_replies_with_help_pointer():
    app_obj, notifier = _build_app()
    try:
        client = TestClient(app_obj)
        resp = client.post("/telegram/webhook", json=_make_update("/nope"))
        assert resp.status_code == 200
        text = notifier.send_message.call_args[0][0]
        assert "Unknown command" in text
        assert "/help" in text
    finally:
        _teardown_app()


def test_non_command_message_is_silently_ignored():
    app_obj, notifier = _build_app()
    try:
        client = TestClient(app_obj)
        resp = client.post("/telegram/webhook", json=_make_update("hello there"))
        assert resp.status_code == 200
        notifier.send_message.assert_not_called()
    finally:
        _teardown_app()


def test_update_without_message_is_silently_ignored():
    app_obj, notifier = _build_app()
    try:
        client = TestClient(app_obj)
        resp = client.post("/telegram/webhook", json={"update_id": 99})
        assert resp.status_code == 200
        notifier.send_message.assert_not_called()
    finally:
        _teardown_app()


# --- Webhook source verification (RS.1) -----------------------------------

def test_secret_set_correct_header_is_processed(monkeypatch):
    """Secret configured + matching header → command processed normally."""
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "s3cr3t")
    app_obj, notifier = _build_app()
    try:
        client = TestClient(app_obj)
        resp = client.post(
            "/telegram/webhook",
            json=_make_update("/help"),
            headers={"X-Telegram-Bot-Api-Secret-Token": "s3cr3t"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        notifier.send_message.assert_called_once()
    finally:
        _teardown_app()


def test_secret_set_wrong_header_is_silently_dropped(monkeypatch):
    """Secret configured + wrong header → silent 200, no processing, no reply."""
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "s3cr3t")
    app_obj, notifier = _build_app()
    try:
        client = TestClient(app_obj)
        resp = client.post(
            "/telegram/webhook",
            json=_make_update("/help"),
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        notifier.send_message.assert_not_called()
    finally:
        _teardown_app()


def test_secret_set_missing_header_is_silently_dropped(monkeypatch):
    """Secret configured + no header → silent 200, no processing, no reply."""
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "s3cr3t")
    app_obj, notifier = _build_app()
    try:
        client = TestClient(app_obj)
        resp = client.post("/telegram/webhook", json=_make_update("/help"))
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        notifier.send_message.assert_not_called()
    finally:
        _teardown_app()


def test_secret_unset_dev_path_still_processes(monkeypatch):
    """No secret configured (dev) → processed as before, no header needed."""
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)
    app_obj, notifier = _build_app()
    try:
        client = TestClient(app_obj)
        resp = client.post("/telegram/webhook", json=_make_update("/help"))
        assert resp.status_code == 200
        notifier.send_message.assert_called_once()
    finally:
        _teardown_app()


# --- Reply routes to originating chat -------------------------------------

def test_reply_uses_originating_chat_id():
    app_obj, notifier = _build_app()
    try:
        client = TestClient(app_obj)
        update = _make_update("/help", chat_id=-1009999)
        resp = client.post("/telegram/webhook", json=update)
        assert resp.status_code == 200
        notifier.send_message.assert_called_once()
        kwargs = notifier.send_message.call_args.kwargs
        assert kwargs["chat_id"] == "-1009999"
    finally:
        _teardown_app()