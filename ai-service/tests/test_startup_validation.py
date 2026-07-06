"""Tests for fail-fast startup secret validation (RS.2).

In production (SENTINEL_ENV=prod) the service must refuse to start when no
real LLM API key is configured, rather than silently falling back to the
`sk-fake-for-dev` key (which would make every LLM call 401 while /healthz
stays green). In dev the fake-key fallback is still allowed.

Env isolation: monkeypatch auto-restores env vars, but the lru_cache on
`_settings` must be cleared before and after each case so a cached value
never leaks into another test. The autouse fixture handles that.
"""
from __future__ import annotations

import pytest

from app.deps import reset_caches_for_testing, validate_required_secrets


@pytest.fixture(autouse=True)
def _clean_settings_cache():
    """Clear settings cache before and after each test for isolation."""
    reset_caches_for_testing()
    yield
    reset_caches_for_testing()


def test_prod_without_any_key_raises(monkeypatch):
    # Arrange: prod env, no LLM key of any kind
    monkeypatch.setenv("SENTINEL_ENV", "prod")
    monkeypatch.delenv("AGNES_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    reset_caches_for_testing()

    # Act / Assert
    with pytest.raises(RuntimeError, match="no LLM API key"):
        validate_required_secrets()


def test_prod_with_agnes_key_does_not_raise(monkeypatch):
    monkeypatch.setenv("SENTINEL_ENV", "prod")
    monkeypatch.setenv("AGNES_API_KEY", "sk-real-agnes")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    reset_caches_for_testing()

    validate_required_secrets()  # must not raise


def test_prod_with_openai_key_does_not_raise(monkeypatch):
    monkeypatch.setenv("SENTINEL_ENV", "prod")
    monkeypatch.delenv("AGNES_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-real-openai")
    reset_caches_for_testing()

    validate_required_secrets()  # must not raise


def test_dev_default_without_key_does_not_raise(monkeypatch):
    # SENTINEL_ENV unset → defaults to dev → fake-key fallback allowed
    monkeypatch.delenv("SENTINEL_ENV", raising=False)
    monkeypatch.delenv("AGNES_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    reset_caches_for_testing()

    validate_required_secrets()  # must not raise


def test_explicit_dev_without_key_does_not_raise(monkeypatch):
    monkeypatch.setenv("SENTINEL_ENV", "dev")
    monkeypatch.delenv("AGNES_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    reset_caches_for_testing()

    validate_required_secrets()  # must not raise
