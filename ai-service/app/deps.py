"""Dependency-injection helpers for FastAPI routes.

Centralizes how routes get their session, LLM client, and extractor instances.
All routes use Depends() to pull these in — no global state in request handlers.
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Iterator, Optional

from fastapi import Header, HTTPException, status
from pydantic_settings import BaseSettings, SettingsConfigDict
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


class Settings(BaseSettings):
    """Typed env-driven configuration (RC2.1).

    Defaults match the previous hand-rolled `_settings()` dict exactly so
    existing tests/dev setups keep working. Field names map case-insensitively
    to env vars (e.g. `llm_quick_model` ↔ `LLM_QUICK_MODEL`).

    `api_key` / `base_url` are derived from the raw key/base fields with the
    same AGNES→OPENAI / LLM_BASE_URL→OPENAI_API_BASE fallback chains used
    before. We expose them as @property so they aren't accidentally read as
    their own (unset) env vars by pydantic.
    """

    model_config = SettingsConfigDict(
        env_file=None,  # rely on process env, not a dotenv file
        case_sensitive=False,
        extra="ignore",
    )

    # LLM API keys (raw — the `api_key` property resolves the fallback chain)
    agnes_api_key: Optional[str] = None
    openai_api_key: Optional[str] = None

    # LLM endpoint / models
    llm_base_url: Optional[str] = None
    openai_api_base: str = "https://apihub.agnes-ai.com/v1"
    llm_quick_model: str = "agnes-2.0-flash"
    llm_deep_model: str = "agnes-2.0-flash"

    # Network / DB
    https_proxy: Optional[str] = None
    database_url: str = "sqlite:///./sentinel.db"

    # App
    sentinel_env: str = "dev"

    @property
    def api_key(self) -> str:
        return self.agnes_api_key or self.openai_api_key or "sk-fake-for-dev"

    @property
    def base_url(self) -> str:
        return self.llm_base_url or self.openai_api_base


@lru_cache(maxsize=1)
def _settings() -> Settings:
    """Load configuration once. Env-driven (12-factor).

    Provider-agnostic — override via env to point at DeepSeek / OpenAI /
    OpenRouter. Kept as an `@lru_cache` so `reset_caches_for_testing()` keeps
    working (existing tests assume `_settings.cache_clear()` exists).
    """
    return Settings()


def validate_required_secrets() -> None:
    """Fail fast in prod if no real LLM key is configured. Called at app startup.

    In dev (SENTINEL_ENV unset or 'dev') the `sk-fake-for-dev` fallback in
    `Settings.api_key` is allowed so the service runs without a key. In prod,
    that fallback would make every LLM call 401 while /healthz stays green —
    an invisible failure. Refuse to start instead (security.md: validate
    required secrets at startup).
    """
    s = _settings()
    has_real_key = bool(s.agnes_api_key or s.openai_api_key)
    if s.sentinel_env == "prod" and not has_real_key:
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
    get_engine(_settings().database_url)  # initialize engine once
    session = get_session()
    try:
        yield session
    finally:
        session.close()


@lru_cache(maxsize=1)
def _llm_client() -> LLMClient:
    s = _settings()
    return OpenAICompatibleClient(
        api_key=s.api_key,
        base_url=s.base_url,
        quick_model=s.llm_quick_model,
        deep_model=s.llm_deep_model,
        https_proxy=s.https_proxy,
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
    get_engine(_settings().database_url)  # ensure engine initialized
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