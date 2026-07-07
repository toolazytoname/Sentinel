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
from typing import Any, Callable

import httpx

from app.llm.client import LLMClient, LLMUnavailable

logger = logging.getLogger(__name__)

# Callback invoked (best-effort) after each successful completion with token
# usage. Signature: (model, model_tier, prompt_tokens, completion_tokens,
# total_tokens) -> None. Wired to a DB write in deps; the client never depends
# on the DB directly.
UsageCallback = Callable[..., None]


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
        usage_callback: UsageCallback | None = None,
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
        self._usage_callback = usage_callback

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

    def _log_usage(self, body: dict, model: str, model_tier: str) -> None:
        """Best-effort: forward token usage to the callback. Never raises.

        Logging must NEVER break or slow-fail the LLM call, so any callback
        failure is swallowed with a warning. If the response carries no `usage`
        block, we skip logging entirely.
        """
        if self._usage_callback is None:
            return
        usage = _extract_usage(body)
        if usage is None:
            return
        try:
            self._usage_callback(
                model=model,
                model_tier=model_tier,
                prompt_tokens=usage["prompt_tokens"],
                completion_tokens=usage["completion_tokens"],
                total_tokens=usage["total_tokens"],
            )
        except Exception as exc:  # noqa: BLE001 - logging must never break the call
            logger.warning("usage_callback failed (ignored): %s", exc)

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
                    body = resp.json()
                    self._log_usage(body, payload["model"], model_tier)
                    return _extract_text(body)
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
                    body = resp.json()
                    self._log_usage(body, payload["model"], model_tier)
                    return _extract_text(body)
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


def _extract_usage(body: dict) -> dict[str, int] | None:
    """Return normalized token counts from a response's `usage` block.

    Returns None when `usage` is absent (older/minimal providers) so callers
    skip logging rather than crash. Missing individual keys default to 0.
    """
    usage = body.get("usage")
    if not isinstance(usage, dict):
        return None
    return {
        "prompt_tokens": usage.get("prompt_tokens", 0) or 0,
        "completion_tokens": usage.get("completion_tokens", 0) or 0,
        "total_tokens": usage.get("total_tokens", 0) or 0,
    }