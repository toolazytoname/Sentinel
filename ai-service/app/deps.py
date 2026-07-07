"""Dependency-injection helpers for FastAPI routes.

Centralizes how routes get their session, LLM client, and extractor instances.
All routes use Depends() to pull these in — no global state in request handlers.
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Iterator

from fastapi import Header, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_engine, get_session, insert_llm_call
from app.llm import (
    LLMClient,
    OpenAICompatibleClient,
    ReflectionExtractor,
    ResearchExtractor,
    StructuredExtractor,
    VetoExtractor,
)
from app.notifier import NotifierConfig, TelegramNotifier

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _settings() -> dict:
    """Load configuration once. Env-driven (12-factor).

    Default base_url points at agnes-ai (OpenAI-compatible). Provider-agnostic —
    override via env to point at DeepSeek / OpenAI / OpenRouter.
    """
    return {
        "api_key": os.environ.get("AGNES_API_KEY") or os.environ.get("OPENAI_API_KEY", "sk-fake-for-dev"),
        "base_url": os.environ.get("LLM_BASE_URL") or os.environ.get("OPENAI_API_BASE", "https://apihub.agnes-ai.com/v1"),
        "quick_model": os.environ.get("LLM_QUICK_MODEL", "agnes-2.0-flash"),
        "deep_model": os.environ.get("LLM_DEEP_MODEL", "agnes-2.0-flash"),
        "https_proxy": os.environ.get("HTTPS_PROXY"),
        "db_url": os.environ.get("DATABASE_URL", "sqlite:///./sentinel.db"),
        "env": os.environ.get("SENTINEL_ENV", "dev"),
    }


def validate_required_secrets() -> None:
    """Fail fast in prod if no real LLM key is configured. Called at app startup.

    In dev (SENTINEL_ENV unset or 'dev') the `sk-fake-for-dev` fallback in
    `_settings()` is allowed so the service runs without a key. In prod,
    that fallback would make every LLM call 401 while /healthz stays green —
    an invisible failure. Refuse to start instead (security.md: validate
    required secrets at startup).
    """
    s = _settings()
    has_real_key = bool(
        os.environ.get("AGNES_API_KEY") or os.environ.get("OPENAI_API_KEY")
    )
    if s["env"] == "prod" and not has_real_key:
        raise RuntimeError(
            "SENTINEL_ENV=prod but no LLM API key set. "
            "Set AGNES_API_KEY or OPENAI_API_KEY. "
            "Refusing to start with a fake key."
        )


def require_api_token(
    x_sentinel_token: str | None = Header(default=None),
) -> None:
    """Optional shared-token guard for OPS-facing write endpoints (RS.3).

    When `SENTINEL_API_TOKEN` is set (production/VPS), callers of the gated
    routes (`/strategy/register`, `/strategy/check`, `/research/note`,
    `/reflection`) must send a matching `X-Sentinel-Token` header. When the
    env var is unset (dev/tests), this is a no-op so local flows keep working.

    NOTE: `/veto` and `/trade-close` are intentionally NOT gated — they are
    called by freqtrade/strategy code which cannot send custom headers and
    rely on loopback bind + firewall network isolation instead.
    """
    expected = os.environ.get("SENTINEL_API_TOKEN")
    if expected and x_sentinel_token != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing X-Sentinel-Token",
        )


def get_db() -> Iterator[Session]:
    """FastAPI dependency: yields a session, closes on exit."""
    get_engine(_settings()["db_url"])  # initialize engine once
    session = get_session()
    try:
        yield session
    finally:
        session.close()


@lru_cache(maxsize=1)
def _llm_client() -> LLMClient:
    s = _settings()
    return OpenAICompatibleClient(
        api_key=s["api_key"],
        base_url=s["base_url"],
        quick_model=s["quick_model"],
        deep_model=s["deep_model"],
        https_proxy=s["https_proxy"],
        usage_callback=_persist_llm_usage,
    )


def _persist_llm_usage(
    *,
    model: str,
    model_tier: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
) -> None:
    """Persist one LLM call's token usage (P2.2 DoD). Best-effort.

    The client already wraps this in try/except, but we stay defensive here too:
    a DB hiccup must never affect a completion result. Uses a fresh session so
    the write is independent of any request-scoped session.
    """
    get_engine(_settings()["db_url"])  # ensure engine initialized
    session = get_session()
    try:
        insert_llm_call(
            session,
            model=model,
            model_tier=model_tier,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        )
    except Exception as exc:  # noqa: BLE001 - logging must never break the call
        logger.warning("failed to persist llm_calls row (ignored): %s", exc)
    finally:
        session.close()


def get_llm_client() -> LLMClient:
    return _llm_client()


def get_structured_extractor() -> StructuredExtractor:
    return StructuredExtractor(get_llm_client())


def get_veto_extractor() -> VetoExtractor:
    return VetoExtractor(get_structured_extractor())


def get_research_extractor() -> ResearchExtractor:
    return ResearchExtractor(get_structured_extractor())


def get_reflection_extractor() -> ReflectionExtractor:
    return ReflectionExtractor(get_structured_extractor())


@lru_cache(maxsize=1)
def _notifier_config() -> NotifierConfig:
    return NotifierConfig.from_env()


@lru_cache(maxsize=1)
def _notifier() -> TelegramNotifier:
    return TelegramNotifier(_notifier_config())


def get_notifier() -> TelegramNotifier:
    """FastAPI dependency: returns the app-scoped TelegramNotifier.

    Lifespan overrides this cache so we get the same instance the
    scheduler uses (so scheduler alerts and webhook replies share state
    like the http connection pool). For tests, build one manually and
    inject via `app.dependency_overrides[get_notifier]`.
    """
    return _notifier()


def reset_caches_for_testing() -> None:
    """Clear lru_cache so test fixtures can swap env vars between tests."""
    _settings.cache_clear()
    _llm_client.cache_clear()
    _notifier_config.cache_clear()
    _notifier.cache_clear()