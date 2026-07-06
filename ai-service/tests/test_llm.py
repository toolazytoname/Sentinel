"""Tests for LLM extraction. No real LLM calls — all mocked."""
from __future__ import annotations

import json

import pytest

from app.llm import (
    LLMClient,
    LLMUnavailable,
    ReflectionExtractor,
    ResearchExtractor,
    StructuredExtractor,
    VetoExtractor,
)
from app.schemas import ResearchNote, TradeReflection, VetoDecision


class FakeLLMClient(LLMClient):
    """Test double. Programmable sequence of responses."""

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls: list[str] = []

    def complete(self, prompt, *, model_tier="quick", max_retries=1):
        self.calls.append(prompt)
        if not self._responses:
            raise LLMUnavailable("FakeLLMClient out of responses")
        return self._responses.pop(0)


def _valid_research_json() -> str:
    return json.dumps(
        {
            "asset": "BTC",
            "event_type": "regulatory",
            "severity": 4,
            "summary": "SEC delays spot BTC ETF approval by 30 days",
            "source_url": "https://example.com/news/123",
            "published_at": "2026-07-06T08:00:00Z",
        }
    )


def _valid_veto_json(veto: bool = False) -> str:
    return json.dumps(
        {
            "veto": veto,
            "reason": "Asset has unresolved regulatory uncertainty",
            "confidence": 0.65,
        }
    )


def _valid_reflection_json() -> str:
    return json.dumps(
        {
            "trade_id": "trade-abc-123",
            "what_worked": "ADX filter correctly avoided choppy market entry",
            "what_failed": "Trailing stop was too tight, exited on minor pullback",
            "lesson": "Widen trailing stop to 7% in low-vol regimes",
            "confidence": 0.80,
        }
    )


class TestStructuredExtractor:
    def test_parses_clean_json(self):
        client = FakeLLMClient([_valid_research_json()])
        ext = StructuredExtractor(client)
        result = ext.extract("any prompt", ResearchNote)
        assert isinstance(result, ResearchNote)
        assert result.asset == "BTC"
        assert result.severity == 4

    def test_strips_code_fences(self):
        wrapped = "```json\n" + _valid_research_json() + "\n```"
        client = FakeLLMClient([wrapped])
        ext = StructuredExtractor(client)
        result = ext.extract("any prompt", ResearchNote)
        assert result.asset == "BTC"

    def test_extracts_json_from_surrounding_prose(self):
        prose_then_json = "Here is the answer:\n\n" + _valid_research_json() + "\n\nLet me know if you need more."
        client = FakeLLMClient([prose_then_json])
        ext = StructuredExtractor(client)
        result = ext.extract("any prompt", ResearchNote)
        assert result.asset == "BTC"

    def test_retries_on_invalid_json(self):
        # First call: garbage. Second call: valid. Should succeed on retry.
        client = FakeLLMClient(["not json at all", _valid_research_json()])
        ext = StructuredExtractor(client)
        result = ext.extract("any prompt", ResearchNote)
        assert result.asset == "BTC"
        assert len(client.calls) == 2
        # The second prompt should contain corrective guidance
        assert "schema" in client.calls[1].lower() or "JSON" in client.calls[1]

    def test_retries_on_schema_violation(self):
        # First: missing required field "summary". Second: valid.
        bad = json.dumps({"asset": "BTC", "severity": 3, "source_url": "https://x.com"})
        client = FakeLLMClient([bad, _valid_research_json()])
        ext = StructuredExtractor(client)
        result = ext.extract("any prompt", ResearchNote)
        assert result.asset == "BTC"

    def test_raises_unavailable_after_persistent_failure(self):
        client = FakeLLMClient(["garbage 1", "garbage 2"])
        ext = StructuredExtractor(client)
        with pytest.raises(LLMUnavailable):
            ext.extract("any prompt", ResearchNote)

    def test_validates_severity_bounds(self):
        # severity=10 violates the ge=1, le=5 constraint in the Pydantic schema
        bad = (
            '{"asset": "BTC", "event_type": "regulatory", "severity": 10, '
            '"summary": "Test summary text here", "source_url": "https://x.com", '
            '"published_at": "2026-07-06"}'
        )
        client = FakeLLMClient([bad])
        ext = StructuredExtractor(client)
        with pytest.raises(LLMUnavailable):
            ext.extract("any prompt", ResearchNote)


class TestSpecializedExtractors:
    def test_research_extractor_returns_research_note(self):
        client = FakeLLMClient([_valid_research_json()])
        ext = ResearchExtractor(StructuredExtractor(client))
        note = ext.extract("summarize this article")
        assert isinstance(note, ResearchNote)

    def test_veto_extractor_returns_veto_decision(self):
        client = FakeLLMClient([_valid_veto_json(veto=True)])
        ext = VetoExtractor(StructuredExtractor(client))
        decision = ext.extract("is this trade too risky?")
        assert isinstance(decision, VetoDecision)
        assert decision.veto is True

    def test_reflection_extractor_returns_reflection(self):
        client = FakeLLMClient([_valid_reflection_json()])
        ext = ReflectionExtractor(StructuredExtractor(client))
        ref = ext.extract("reflect on this trade")
        assert isinstance(ref, TradeReflection)
        assert ref.trade_id == "trade-abc-123"