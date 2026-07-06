"""Dependency-injection helpers for FastAPI routes.

Centralizes how routes get their session, LLM client, and extractor instances.
All routes use Depends() to pull these in — no global state in request handlers.
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Iterator

from sqlalchemy.orm import Session

from app.db import get_engine, get_session
from app.llm import (
    LLMClient,
    OpenAICompatibleClient,
    ReflectionExtractor,
    ResearchExtractor,
    StructuredExtractor,
    VetoExtractor,
)


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
    }


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
    )


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


def reset_caches_for_testing() -> None:
    """Clear lru_cache so test fixtures can swap env vars between tests."""
    _settings.cache_clear()
    _llm_client.cache_clear()