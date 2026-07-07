"""Integration tests for the FastAPI HTTP API.

Uses TestClient + in-memory SQLite + a mocked LLM client so we exercise
the full HTTP → service → DB stack without network or API keys.
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["OPENAI_API_KEY"] = "sk-test-fake"

from app.db.models import Base  # noqa: E402
from app.deps import get_db, reset_caches_for_testing  # noqa: E402
from app.main import app  # noqa: E402
from app.schemas import VetoDecision  # noqa: E402
from tests.conftest import bind_module_engine, reset_module_engine  # noqa: E402


# --- Test doubles ---

def _ok_chat_response(content: str) -> dict:
    return {
        "id": "fake",
        "object": "chat.completion",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
        ],
    }


class FakeLLMRouter:
    def __init__(self, response_for_veto: VetoDecision, raise_unavailable: bool = False):
        self.veto_decision = response_for_veto
        self.raise_unavailable = raise_unavailable
        self.calls: list[str] = []

    def build_client(self):
        def responder(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            prompt = body["messages"][0]["content"]
            self.calls.append(prompt)
            if self.raise_unavailable:
                return httpx.Response(503, text="simulated outage")
            # Inspect prompt to choose response shape
            low = prompt.lower()
            if "devil" in low or "risk" in low or "vetting" in low:
                content = json.dumps({
                    "veto": self.veto_decision.veto,
                    "reason": self.veto_decision.reason,
                    "confidence": self.veto_decision.confidence,
                })
            else:
                content = json.dumps({
                    "trade_id": "t1",
                    "what_worked": "ADX filter worked correctly",
                    "what_failed": "trailing stop was too tight",
                    "lesson": "widen trailing in low-vol",
                    "confidence": 0.7,
                })
            return httpx.Response(200, json=_ok_chat_response(content))

        return httpx.MockTransport(responder)


@contextmanager
def _build_app_with_fake_llm(router: FakeLLMRouter):
    """Rebuild the app with the fake LLM client injected. Used as context manager.

    Uses StaticPool so every connection sees the same in-memory SQLite database.
    Otherwise each session gets its own private :memory: DB and DDL is invisible.
    """
    reset_caches_for_testing()
    test_engine = create_engine(
        os.environ["DATABASE_URL"],
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.drop_all(test_engine)
    Base.metadata.create_all(test_engine)

    # RT.1: share one engine between get_session (used by ReflectionWriter)
    # and the get_db override so the writer and the test read one database.
    session_local = bind_module_engine(test_engine)

    def override_get_db():
        s = session_local()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override_get_db

    from app.llm.openai_compat import OpenAICompatibleClient
    transport = router.build_client()
    original_make_client = OpenAICompatibleClient._make_client
    original_make_async = OpenAICompatibleClient._make_async_client
    OpenAICompatibleClient._make_client = lambda self: httpx.Client(transport=transport)
    OpenAICompatibleClient._make_async_client = lambda self: httpx.AsyncClient(transport=transport)

    from app.deps import _llm_client
    _llm_client.cache_clear()

    yield app

    OpenAICompatibleClient._make_client = original_make_client
    OpenAICompatibleClient._make_async_client = original_make_async
    app.dependency_overrides.clear()
    reset_module_engine()


def _veto_payload(context_overrides: dict | None = None) -> dict:
    """Build the full /audit/veto request body (signal + market context)."""
    context = {
        "recent_high_severity_events": [],
        "current_total_exposure_pct": 0.10,
        "max_exposure_pct": 0.60,
        "upcoming_event_window_minutes": 0,
    }
    if context_overrides:
        context.update(context_overrides)
    return {
        "strategy": "S1",
        "pair": "BTC/USDT",
        "side": "long",
        "stake_pct": 0.05,
        "context": context,
    }


def test_healthz():
    with _build_app_with_fake_llm(FakeLLMRouter(VetoDecision(veto=False, reason="no risk detected", confidence=0.5))):
        client = TestClient(app)
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "version": "0.1.0"}


def test_veto_rule_blocks_without_calling_llm():
    """Rule vetoes (recent high-severity event) → LLM must NOT be called."""
    router = FakeLLMRouter(VetoDecision(veto=False, reason="no risk detected", confidence=0.5))
    with _build_app_with_fake_llm(router):
        client = TestClient(app)
        resp = client.post(
            "/audit/veto",
            json=_veto_payload({"recent_high_severity_events": ["BTC"]}),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["veto"] is True
        assert "high_severity_event" in body["reason"]
        assert body["source"] == "rule"
        # Critical: rules short-circuit LLM
        assert len(router.calls) == 0, "Rule veto must not invoke LLM"


def test_veto_llm_passes():
    router = FakeLLMRouter(VetoDecision(veto=False, reason="looks fine to me", confidence=0.6))
    with _build_app_with_fake_llm(router):
        client = TestClient(app)
        resp = client.post("/audit/veto", json=_veto_payload())
        assert resp.status_code == 200
        body = resp.json()
        assert body["veto"] is False
        assert body["source"] == "llm"


def test_veto_llm_blocks():
    router = FakeLLMRouter(VetoDecision(veto=True, reason="concentration risk too high", confidence=0.8))
    with _build_app_with_fake_llm(router):
        client = TestClient(app)
        resp = client.post("/audit/veto", json=_veto_payload())
        body = resp.json()
        assert body["veto"] is True
        assert body["source"] == "llm"
        assert "concentration" in body["reason"]


def test_veto_fail_open_when_llm_down():
    """ADR-002 fail-open: LLM down → 200 with source=llm_unavailable, NOT 5xx."""
    router = FakeLLMRouter(
        VetoDecision(veto=False, reason="no risk", confidence=0.5),
        raise_unavailable=True,
    )
    with _build_app_with_fake_llm(router):
        client = TestClient(app)
        resp = client.post("/audit/veto", json=_veto_payload())
        assert resp.status_code == 200, "FAIL-OPEN VIOLATED"
        body = resp.json()
        assert body["veto"] is False
        assert body["source"] == "llm_unavailable"


# --- Strategy-facing GET /veto ---

def test_get_veto_pass_when_no_events():
    """Strategy-side endpoint: returns {decision: PASS} via the rule layer only.

    RB.1: GET /veto runs ONLY the deterministic rules — it must NOT call the
    LLM (the strategy calls this on a tight timeout; a synchronous deep-tier
    LLM call would always time out). Prove the LLM extractor is never invoked.
    """
    router = FakeLLMRouter(VetoDecision(veto=False, reason="looks fine", confidence=0.5))
    with _build_app_with_fake_llm(router):
        client = TestClient(app)
        resp = client.get("/veto", params={"strategy": "S1TrendFollow", "pair": "BTC/USDT"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["decision"] == "PASS"
        assert isinstance(body["reason"], str)
        # RB.1 red-line: the hot-path endpoint must never hit the LLM.
        assert len(router.calls) == 0


def test_get_veto_blocks_on_recent_high_severity_event():
    """Rule layer: recent severity>=4 research note on the asset → VETO."""
    router = FakeLLMRouter(VetoDecision(veto=False, reason="all clear", confidence=0.5))
    with _build_app_with_fake_llm(router) as app_ctx:
        client = TestClient(app_ctx)
        # Seed a high-severity event for ETH
        client.post(
            "/research/note",
            json={
                "asset": "ETH",
                "event_type": "regulatory",
                "severity": 5,
                "summary": "ETH ETF rejected by regulator",
                "source_url": "https://example.com/eth",
                "published_at": "2026-07-06T08:00:00Z",
            },
        )
        # Query for ETH/USDT → should VETO via rule layer
        resp = client.get("/veto", params={"strategy": "S1", "pair": "ETH/USDT"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["decision"] == "VETO"
        assert "high_severity_event" in body["reason"]
        # Rule must short-circuit — LLM must not have been called
        assert len(router.calls) == 0


def test_get_veto_does_not_block_unrelated_asset():
    """A high-severity event for BTC must NOT block an ETH entry."""
    router = FakeLLMRouter(VetoDecision(veto=False, reason="eth looks fine", confidence=0.6))
    with _build_app_with_fake_llm(router):
        client = TestClient(app)
        client.post(
            "/research/note",
            json={
                "asset": "BTC",
                "event_type": "regulatory",
                "severity": 5,
                "summary": "BTC news",
                "source_url": "https://example.com/btc",
                "published_at": "2026-07-06T08:00:00Z",
            },
        )
        resp = client.get("/veto", params={"strategy": "S1", "pair": "ETH/USDT"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["decision"] == "PASS"


def test_get_veto_ignores_llm_outage_and_uses_rules_only():
    """RB.1: LLM is out of the hot path entirely.

    Even if the LLM would be unavailable, GET /veto never calls it — it runs
    rules only, returns PASS via the rule layer, and never touches the LLM.
    """
    router = FakeLLMRouter(
        VetoDecision(veto=False, reason="no risk", confidence=0.5),
        raise_unavailable=True,
    )
    with _build_app_with_fake_llm(router):
        client = TestClient(app)
        resp = client.get("/veto", params={"strategy": "S1", "pair": "BTC/USDT"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["decision"] == "PASS"
        assert body["reason"] == "rules_passed"
        # LLM must never be reached, so its outage is irrelevant here.
        assert len(router.calls) == 0


def test_get_veto_ignores_llm_veto_on_hot_path():
    """RB.1 contract change: an LLM veto must NOT affect GET /veto.

    The LLM would veto, but GET /veto is rules-only now, so rules-pass →
    decision=PASS and the LLM is never called. (LLM-veto capability lives on
    POST /audit/veto, which is unchanged.)
    """
    router = FakeLLMRouter(
        VetoDecision(veto=True, reason="concentration risk too high", confidence=0.8)
    )
    with _build_app_with_fake_llm(router):
        client = TestClient(app)
        resp = client.get("/veto", params={"strategy": "S1", "pair": "BTC/USDT"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["decision"] == "PASS"
        assert body["reason"] == "rules_passed"
        assert len(router.calls) == 0


def test_research_note_submit_and_persist():
    with _build_app_with_fake_llm(FakeLLMRouter(VetoDecision(veto=False, reason="no risk", confidence=0.5))):
        client = TestClient(app)
        resp = client.post(
            "/research/note",
            json={
                "asset": "BTC",
                "event_type": "regulatory",
                "severity": 4,
                "summary": "SEC delays spot BTC ETF approval by 30 days",
                "source_url": "https://example.com/etf",
                "published_at": "2026-07-06T08:00:00Z",
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["asset"] == "BTC"
        assert body["severity"] == 4
        assert body["id"] > 0


def test_research_note_rejects_invalid_severity():
    with _build_app_with_fake_llm(FakeLLMRouter(VetoDecision(veto=False, reason="no risk", confidence=0.5))):
        client = TestClient(app)
        resp = client.post(
            "/research/note",
            json={
                "asset": "BTC",
                "event_type": "regulatory",
                "severity": 10,
                "summary": "Test summary text here",
                "source_url": "https://example.com/1",
                "published_at": "2026-07-06T08:00:00Z",
            },
        )
        assert resp.status_code == 422


def test_strategy_register_and_check_round_trip():
    with _build_app_with_fake_llm(FakeLLMRouter(VetoDecision(veto=False, reason="no risk", confidence=0.5))):
        client = TestClient(app)
        resp = client.post("/strategy/register", json={"strategy": "S1TrendFollow", "initial_stage": "dry_run"})
        assert resp.status_code == 201

        resp = client.post(
            "/strategy/check",
            json={"strategy": "S1TrendFollow", "observed_drawdown_pct": 5.0, "trade_count": 10},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["current_stage"] == "dry_run"
        assert body["recommendation"] == "hold"


def test_strategy_get_404_for_unknown():
    with _build_app_with_fake_llm(FakeLLMRouter(VetoDecision(veto=False, reason="no risk", confidence=0.5))):
        client = TestClient(app)
        resp = client.get("/strategy/DoesNotExist/stage")
        assert resp.status_code == 404


def _reflection_payload() -> dict:
    """Build a full POST /reflection request body for a closed trade."""
    return {
        "trade_id": "t1",
        "strategy": "S1TrendFollow",
        "pair": "BTC/USDT",
        "side": "long",
        "entry_price": 100.0,
        "exit_price": 110.0,
        "profit_pct": 0.10,
        "hold_duration_hours": 6.0,
        "signal_snapshot": {"enter_tag": "adx"},
        "closed_at": "2026-07-06T08:00:00Z",
    }


def test_reflection_submit_returns_real_primary_key():
    """POST /reflection persists and returns the real DB id (not hardcoded 0)."""
    router = FakeLLMRouter(VetoDecision(veto=False, reason="no risk", confidence=0.5))
    with _build_app_with_fake_llm(router):
        client = TestClient(app)
        resp = client.post("/reflection", json=_reflection_payload())
        assert resp.status_code == 201
        body = resp.json()
        assert body["trade_id"] == "t1"
        assert body["id"] > 0, "reflection id must be the real DB primary key"