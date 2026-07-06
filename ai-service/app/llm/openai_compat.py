"""OpenAI-compatible sync + async LLM client.

Works with any provider that exposes the OpenAI /v1/chat/completions shape:
- OpenAI (api.openai.com)
- DeepSeek (api.deepseek.com)
- Together AI
- Local Ollama (with openai-compat mode)
- Any LiteLLM-proxied endpoint

Supports two model tiers (quick/deep) per docs/system/01-architecture.md §007.

Two completion methods:
  - complete(...)  — sync, used by StructuredExtractor (testable, FastAPI blocking routes OK)
  - acomplete(...) — async, used by FastAPI async route handlers
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx

from app.llm.client import LLMClient, LLMUnavailable

logger = logging.getLogger(__name__)


class OpenAICompatibleClient(LLMClient):
    """Sync + async client for OpenAI-compatible chat completion endpoints."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        quick_model: str = "gpt-4o-mini",
        deep_model: str = "gpt-4o",
        timeout: float = 30.0,
        https_proxy: str | None = None,
    ):
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._quick_model = quick_model
        self._deep_model = deep_model
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self._timeout = timeout
        self._proxy = https_proxy

    def _make_client(self) -> httpx.Client:
        """Return a fresh sync httpx.Client. Caller closes."""
        return httpx.Client(
            timeout=self._timeout,
            headers=self._headers,
            proxy=self._proxy,
        )

    def _make_async_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self._timeout,
            headers=self._headers,
            proxy=self._proxy,
        )

    def complete(
        self,
        prompt: str,
        *,
        model_tier: str = "quick",
        max_retries: int = 1,
    ) -> str:
        """Sync completion. Used by StructuredExtractor."""
        return self._call_with_retry(prompt, model_tier, max_retries, async_mode=False)

    async def acomplete(
        self,
        prompt: str,
        *,
        model_tier: str = "quick",
        max_retries: int = 1,
    ) -> str:
        """Async completion."""
        return await self._acall_with_retry(prompt, model_tier, max_retries)

    # --- internal ---

    def _build_payload(self, prompt: str, model_tier: str) -> tuple[str, dict[str, Any]]:
        model = self._quick_model if model_tier == "quick" else self._deep_model
        url = f"{self._base_url}/chat/completions"
        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
        }
        return url, payload

    def _call_with_retry(
        self, prompt: str, model_tier: str, max_retries: int, async_mode: bool = False
    ) -> str:
        url, payload = self._build_payload(prompt, model_tier)
        attempts = max_retries + 1
        last_error: Exception | None = None

        for attempt in range(attempts):
            try:
                with self._make_client() as client:
                    resp = client.post(url, json=payload)
                    if _is_transient(resp.status_code):
                        last_error = httpx.HTTPStatusError(
                            f"{resp.status_code}", request=resp.request, response=resp
                        )
                        logger.warning("Transient LLM error (attempt %d/%d)", attempt + 1, attempts)
                        continue  # retry
                    # 4xx other than 429 → bail out, don't retry
                    if 400 <= resp.status_code < 500:
                        raise LLMUnavailable(
                            f"Non-retryable client error {resp.status_code}: {resp.text[:200]}"
                        )
                    resp.raise_for_status()  # any other non-2xx
                    return _extract_text(resp.json())
            except (httpx.HTTPError, json.JSONDecodeError, KeyError) as e:
                last_error = e
                logger.warning("LLM call error (attempt %d/%d): %s", attempt + 1, attempts, e)
        raise LLMUnavailable(f"OpenAI-compatible LLM failed after {attempts} attempts: {last_error}")

    async def _acall_with_retry(
        self, prompt: str, model_tier: str, max_retries: int
    ) -> str:
        url, payload = self._build_payload(prompt, model_tier)
        attempts = max_retries + 1
        last_error: Exception | None = None

        async with self._make_async_client() as client:
            for attempt in range(attempts):
                try:
                    resp = await client.post(url, json=payload)
                    if _is_transient(resp.status_code):
                        last_error = httpx.HTTPStatusError(
                            f"{resp.status_code}", request=resp.request, response=resp
                        )
                        logger.warning("Transient LLM error (attempt %d/%d)", attempt + 1, attempts)
                        continue
                    if 400 <= resp.status_code < 500:
                        raise LLMUnavailable(
                            f"Non-retryable client error {resp.status_code}: {resp.text[:200]}"
                        )
                    resp.raise_for_status()
                    return _extract_text(resp.json())
                except (httpx.HTTPError, json.JSONDecodeError, KeyError) as e:
                    last_error = e
                    logger.warning("LLM call error (attempt %d/%d): %s", attempt + 1, attempts, e)
        raise LLMUnavailable(f"OpenAI-compatible LLM failed after {attempts} attempts: {last_error}")


def _is_transient(status_code: int) -> bool:
    """Retry on rate limits (429) and server errors (5xx)."""
    return status_code == 429 or 500 <= status_code < 600


def _extract_text(body: dict) -> str:
    """Pull the assistant message content from a chat completion response."""
    try:
        return body["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        raise LLMUnavailable(f"Malformed completion response: {e}") from e