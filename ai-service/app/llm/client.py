"""LLM client abstraction layer.

Design:
- Two model tiers: quick (cheap/fast for routine tasks) and deep (smarter
  for complex analysis). Mirrors TradingAgents' deep_think_llm/quick_think_llm.
- All completions return structured Pydantic models, never raw strings.
- Validation failures are retried ONCE with a corrective system prompt;
  second failure raises a structured error so the caller can fall back.
- Real OpenAI-compatible API integration is gated behind HTTPX; tests
  use a mock client to avoid requiring API keys.
"""
from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import Type, TypeVar

from pydantic import BaseModel, ValidationError

from app.schemas import ResearchNote, TradeReflection, VetoDecision

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class LLMUnavailable(RuntimeError):
    """Raised when LLM call fails and retry doesn't help. Caller should
    fall back to safe defaults (e.g. don't VETO without LLM confirmation)."""


class LLMClient(ABC):
    """Abstract interface. Tests provide a fake; production uses HTTPXOpenAIClient."""

    @abstractmethod
    def complete(
        self,
        prompt: str,
        *,
        model_tier: str = "quick",
        max_retries: int = 1,
    ) -> str:
        """Return the raw completion text. Implementations must surface
        their own errors via LLMUnavailable on persistent failure."""
        raise NotImplementedError


class StructuredExtractor:
    """Wraps an LLMClient and parses its output into a Pydantic model."""

    def __init__(self, client: LLMClient):
        self._client = client

    def extract(
        self,
        prompt: str,
        schema: Type[T],
        *,
        model_tier: str = "quick",
    ) -> T:
        """Get a completion and validate against schema.

        On JSON parse failure or schema validation failure, retry once with
        a corrective prompt that reminds the model of the schema. If the
        second attempt also fails, raise LLMUnavailable.
        """
        last_error: Exception | None = None
        for attempt in range(2):  # initial + 1 retry
            suffix = "" if attempt == 0 else _corrective_prompt(schema)
            text = self._client.complete(prompt + suffix, model_tier=model_tier)
            try:
                return _parse_and_validate(text, schema)
            except (json.JSONDecodeError, ValidationError) as e:
                last_error = e
                logger.warning(
                    "LLM output validation failed (attempt %d/2): %s",
                    attempt + 1,
                    e,
                )
        raise LLMUnavailable(
            f"Could not extract valid {schema.__name__} after retry: {last_error}"
        )


def _corrective_prompt(schema: Type[BaseModel]) -> str:
    """Build a corrective suffix that re-states the expected JSON schema."""
    return (
        "\n\nIMPORTANT: Your previous response was not valid. "
        f"Reply with ONLY a single JSON object matching this schema:\n"
        f"{json.dumps(schema.model_json_schema(), indent=2)}"
    )


def _parse_and_validate(text: str, schema: Type[T]) -> T:
    """Parse JSON from text and validate against schema.

    Robust to: leading/trailing whitespace, ```json code fences, leading prose.
    """
    text = text.strip()
    # Strip common code fences
    if text.startswith("```"):
        # Remove opening fence (with optional language tag)
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1 :]
        if text.endswith("```"):
            text = text[:-3]
    # Try to find JSON object boundaries if there's surrounding prose
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start : end + 1]
    parsed = json.loads(text)
    return schema.model_validate(parsed)


# --- Convenience: per-schema extractors ---

class ResearchExtractor:
    def __init__(self, extractor: StructuredExtractor):
        self._ext = extractor

    def extract(self, prompt: str) -> ResearchNote:
        return self._ext.extract(prompt, ResearchNote, model_tier="quick")


class VetoExtractor:
    def __init__(self, extractor: StructuredExtractor):
        self._ext = extractor

    def extract(self, prompt: str) -> VetoDecision:
        return self._ext.extract(prompt, VetoDecision, model_tier="deep")


class ReflectionExtractor:
    def __init__(self, extractor: StructuredExtractor):
        self._ext = extractor

    def extract(self, prompt: str) -> TradeReflection:
        return self._ext.extract(prompt, TradeReflection, model_tier="deep")