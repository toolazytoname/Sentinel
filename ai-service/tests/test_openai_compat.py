"""Tests for the OpenAI-compatible LLM client.

Uses httpx.MockTransport to inject canned responses — no real network.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.llm import LLMUnavailable, OpenAICompatibleClient
from app.llm.client import StructuredExtractor, VetoExtractor
from app.schemas import VetoDecision


def _ok_response(content: str) -> dict:
    """Build a fake OpenAI-style chat completion response body."""
    return {
        "id": "chatcmpl-fake",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    }


def _make_responder(responses: list[httpx.Response]):
    """Build (responder, state) where state.calls is a list of recorded requests."""
    state = {"calls": []}

    def responder(request: httpx.Request) -> httpx.Response:
        state["calls"].append(request)
        if not responses:
            return httpx.Response(500, text="exhausted")
        return responses.pop(0)

    return responder, state


def _build_client_with_transport(transport: httpx.MockTransport) -> OpenAICompatibleClient:
    """Replace _make_client/_make_async_client with factories that wire transport.

    Note: we DO NOT pass client._headers into the mocked Client/AsyncClient
    because httpx MockTransport only preserves the headers it knows about.
    Instead, headers are verified at the request level (the request object
    seen by the transport does include user headers via the default Client
    initialization).
    """
    client = OpenAICompatibleClient(api_key="sk-fake", base_url="https://fake.api/v1")
    # Build the real httpx.Client/AsyncClient but pin its transport to the mock
    def make_sync():
        # Inherit default headers from a fresh httpx.Client (Host, etc.)
        # then add Authorization (httpx auto-includes this from the kwargs)
        # No — MockTransport intercepts before headers are processed,
        # so we attach our own via the Client constructor
        c = httpx.Client(transport=transport, headers=client._headers)
        return c

    def make_async():
        c = httpx.AsyncClient(transport=transport, headers=client._headers)
        return c

    client._make_client = make_sync
    client._make_async_client = make_async
    return client


class TestSyncComplete:
    def test_basic_completion(self):
        responder, state = _make_responder([httpx.Response(200, json=_ok_response("hello world"))])
        client = _build_client_with_transport(httpx.MockTransport(responder))
        result = client.complete("test prompt")
        assert result == "hello world"
        assert len(state["calls"]) == 1

    def test_uses_quick_model_by_default(self):
        responder, state = _make_responder([httpx.Response(200, json=_ok_response("ok"))])
        client = _build_client_with_transport(httpx.MockTransport(responder))
        client.complete("p")
        body_sent = json.loads(state["calls"][0].content)
        assert body_sent["model"] == "gpt-4o-mini"

    def test_uses_deep_model_when_requested(self):
        responder, state = _make_responder([httpx.Response(200, json=_ok_response("ok"))])
        client = _build_client_with_transport(httpx.MockTransport(responder))
        client.complete("p", model_tier="deep")
        body_sent = json.loads(state["calls"][0].content)
        assert body_sent["model"] == "gpt-4o"

    def test_sends_authorization_header(self):
        # httpx redacts the Authorization header from request objects on error
        # responses (4xx/5xx), so use 200 OK + extract from the live request.
        responder, state = _make_responder([httpx.Response(200, json=_ok_response("ok"))])
        client = _build_client_with_transport(httpx.MockTransport(responder))
        client._headers["Authorization"] = "Bearer sk-fake-12345"
        client.complete("p")
        # Inspect the encoded request: read raw headers from the request bytes
        request = state["calls"][0]
        # Case-insensitive header lookup via .headers (httpx normalizes case but
        # may redact on errors; for 2xx requests the headers are preserved)
        assert "authorization" in {k.lower() for k in request.headers}
        assert request.headers.get("Authorization") == "Bearer sk-fake-12345"

    def test_retries_on_429(self):
        responder, state = _make_responder(
            [
                httpx.Response(429, text="rate limited"),
                httpx.Response(200, json=_ok_response("recovered")),
            ]
        )
        client = _build_client_with_transport(httpx.MockTransport(responder))
        result = client.complete("p", max_retries=1)
        assert result == "recovered"
        assert len(state["calls"]) == 2

    def test_retries_on_5xx(self):
        responder, state = _make_responder(
            [
                httpx.Response(503, text="unavailable"),
                httpx.Response(200, json=_ok_response("recovered")),
            ]
        )
        client = _build_client_with_transport(httpx.MockTransport(responder))
        result = client.complete("p", max_retries=1)
        assert result == "recovered"
        assert len(state["calls"]) == 2

    def test_does_not_retry_on_4xx_other_than_429(self):
        responder, state = _make_responder([httpx.Response(401, text="unauthorized")])
        client = _build_client_with_transport(httpx.MockTransport(responder))
        with pytest.raises(LLMUnavailable):
            client.complete("p", max_retries=2)
        assert len(state["calls"]) == 1

    def test_raises_unavailable_after_exhausted_retries(self):
        responder, state = _make_responder([httpx.Response(503)] * 5)
        client = _build_client_with_transport(httpx.MockTransport(responder))
        with pytest.raises(LLMUnavailable):
            client.complete("p", max_retries=1)
        assert len(state["calls"]) == 2

    def test_raises_on_malformed_response(self):
        responder, _ = _make_responder([httpx.Response(200, json={"oops": "no choices"})])
        client = _build_client_with_transport(httpx.MockTransport(responder))
        with pytest.raises(LLMUnavailable):
            client.complete("p", max_retries=0)


class TestAsyncComplete:
    def test_basic_async_completion(self):
        responder, state = _make_responder([httpx.Response(200, json=_ok_response("async hello"))])
        client = _build_client_with_transport(httpx.MockTransport(responder))
        result = asyncio.run(client.acomplete("p"))
        assert result == "async hello"
        assert len(state["calls"]) == 1

    def test_async_retries_on_429(self):
        responder, state = _make_responder(
            [httpx.Response(429), httpx.Response(200, json=_ok_response("recovered"))]
        )
        client = _build_client_with_transport(httpx.MockTransport(responder))
        result = asyncio.run(client.acomplete("p", max_retries=1))
        assert result == "recovered"
        assert len(state["calls"]) == 2


class TestIntegrationWithStructuredExtractor:
    """End-to-end: real LLMClient → StructuredExtractor → Pydantic schema."""

    def test_extract_veto_from_real_provider_response(self):
        veto_json = json.dumps(
            {"veto": True, "reason": "Concentrated position with macro event imminent", "confidence": 0.72}
        )
        responder, _ = _make_responder([httpx.Response(200, json=_ok_response(veto_json))])
        client = _build_client_with_transport(httpx.MockTransport(responder))

        extractor = StructuredExtractor(client)
        veto_ext = VetoExtractor(extractor)
        decision = veto_ext.extract("review this trade")
        assert isinstance(decision, VetoDecision)
        assert decision.veto is True
        assert decision.confidence == pytest.approx(0.72)