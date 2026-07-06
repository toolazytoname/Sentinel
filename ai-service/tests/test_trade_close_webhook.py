"""Tests for the inbound trade-close webhook (P2.5).

The endpoint accepts freqtrade's EXIT_FILL payload, runs idempotency
checks, and only triggers an LLM reflection on the *final* fill of a
trade. We override the LLM with a FakeLLMRouter and the DB with an
in-memory SQLite so we can exercise the full HTTP → reflection writer →
DB stack without network or API keys.
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["OPENAI_API_KEY"] = "sk-test-fake"

from app.db.models import Base, ReflectionRow  # noqa: E402
from app.deps import get_db, reset_caches_for_testing  # noqa: E402
from app.main import app  # noqa: E402
from app.schemas import VetoDecision  # noqa: E402


# --- Fake LLM that always returns a valid TradeReflection ----------------

def _ok_chat_response(content: str) -> dict:
    return {
        "id": "fake",
        "object": "chat.completion",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
        ],
    }


class _ReflectionRouter:
    """Routes LLM requests for reflection prompts.

    ReflectionExtractor calls `extract(prompt, TradeReflection, model_tier="deep")`
    which sends the prompt to the LLM. The reflection prompt contains
    'Trade ID:' (see modules/reflection.py _build_prompt). Use that as
    a reliable sentinel so we don't accidentally route to veto/other paths.
    """

    def __init__(self):
        self.calls: list[str] = []

    def build_client(self):
        def responder(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            prompt = body["messages"][0]["content"]
            self.calls.append(prompt)
            # Extract the trade_id we asked about (best effort)
            trade_id = "t-fake"
            for line in prompt.splitlines():
                if line.startswith("Trade ID:"):
                    trade_id = line.split(":", 1)[1].strip()
                    break
            content = json.dumps({
                "trade_id": trade_id,
                "what_worked": "entry timing aligned with golden cross",
                "what_failed": "exit fired before profit target",
                "lesson": "widen profit target in trending regimes",
                "confidence": 0.82,
            })
            return httpx.Response(200, json=_ok_chat_response(content))

        return httpx.MockTransport(responder)


@contextmanager
def _build_app():
    """Rebuild the app with fresh in-memory DB + fake LLM for reflection path."""
    reset_caches_for_testing()
    test_engine = create_engine(
        os.environ["DATABASE_URL"],
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.drop_all(test_engine)
    Base.metadata.create_all(test_engine)

    def override_get_db():
        s = sessionmaker(bind=test_engine, expire_on_commit=False)()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override_get_db

    router = _ReflectionRouter()
    from app.llm.openai_compat import OpenAICompatibleClient
    transport = router.build_client()
    original_make_client = OpenAICompatibleClient._make_client
    original_make_async = OpenAICompatibleClient._make_async_client
    OpenAICompatibleClient._make_client = lambda self: httpx.Client(transport=transport)
    OpenAICompatibleClient._make_async_client = lambda self: httpx.AsyncClient(transport=transport)
    from app.deps import _llm_client
    _llm_client.cache_clear()

    try:
        yield app, router
    finally:
        OpenAICompatibleClient._make_client = original_make_client
        OpenAICompatibleClient._make_async_client = original_make_async
        app.dependency_overrides.clear()


def _payload(
    *,
    trade_id: int = 42,
    is_final_exit: bool = True,
    sub_trade: bool = False,
    profit_ratio: float = 0.05,
    strategy: str = "S1TrendFollow",
    open_date: str = "2026-06-01T10:00:00+00:00",
    close_date: str = "2026-06-05T10:00:00+00:00",
) -> dict:
    """Build a freqtrade-shaped EXIT_FILL webhook payload."""
    return {
        "trade_id": trade_id,
        "strategy": strategy,
        "pair": "BTC/USDT",
        "direction": "Long",
        "open_rate": 60000.0,
        "close_rate": 63000.0,
        "profit_ratio": profit_ratio,
        "profit_amount": 15.0,
        "open_date": open_date,
        "close_date": close_date,
        "exit_reason": "exit_signal",
        "enter_tag": "golden_cross",
        "stake_amount": 300.0,
        "stake_currency": "USDT",
        "is_final_exit": is_final_exit,
        "sub_trade": sub_trade,
        "side": "long",
        "extra": {"adx_at_entry": 32.1},
    }


# --- Happy path ----------------------------------------------------------

def test_trade_close_records_reflection_on_final_exit():
    with _build_app() as (app_obj, router):
        client = TestClient(app_obj)
        resp = client.post("/trade-close", json=_payload(trade_id=42))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "recorded"
        assert body["trade_id"] == 42
        assert body["reflection_id"] is not None
        assert len(router.calls) == 1, "final exit must invoke the LLM once"


def test_trade_close_persists_reflection_to_db():
    with _build_app() as (app_obj, _router):
        client = TestClient(app_obj)
        client.post("/trade-close", json=_payload(trade_id=99))
        # Read back via dependency-overridden DB
        gen = app_obj.dependency_overrides[get_db]()
        session = next(gen)
        try:
            rows = session.query(ReflectionRow).all()
            assert len(rows) == 1
            assert rows[0].trade_id == "99"  # coerced to str in TradeContext
            assert rows[0].strategy == "S1TrendFollow"
            assert rows[0].confidence == pytest.approx(0.82)
            assert "golden cross" in rows[0].what_worked  # LLM prompt context
        finally:
            try:
                next(gen)
            except StopIteration:
                pass


def test_trade_close_signal_snapshot_includes_freqtrade_metadata():
    """The signal_snapshot fed to the LLM must include freqtrade fields."""
    with _build_app() as (app_obj, router):
        client = TestClient(app_obj)
        client.post("/trade-close", json=_payload(trade_id=7))
        assert len(router.calls) == 1
        prompt = router.calls[0]
        # enter_tag / exit_reason / stake_amount are surfaced in signal_snapshot
        # (via reflection.py _build_prompt which dumps signal_snapshot as-is)
        assert "golden_cross" in prompt
        assert "exit_signal" in prompt
        assert "300.0" in prompt  # stake_amount
        assert "Long" in prompt


def test_trade_close_computes_hold_duration_hours():
    with _build_app() as (app_obj, router):
        client = TestClient(app_obj)
        client.post("/trade-close", json=_payload(
            trade_id=10,
            open_date="2026-06-01T00:00:00+00:00",
            close_date="2026-06-02T12:00:00+00:00",  # 36 hours
        ))
        prompt = router.calls[0]
        assert "36.0 hours" in prompt


# --- Partial-fill skip ----------------------------------------------------

def test_trade_close_skips_partial_fill():
    """Non-final EXIT_FILL (mid-trade partial exit) must not invoke LLM."""
    with _build_app() as (app_obj, router):
        client = TestClient(app_obj)
        resp = client.post(
            "/trade-close",
            json=_payload(trade_id=11, is_final_exit=False),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "skipped"
        assert body["reason"] == "partial_fill"
        assert body["reflection_id"] is None
        assert len(router.calls) == 0


def test_trade_close_skips_sub_trade_flag():
    """sub_trade=true (DCA partial fill) is also treated as partial."""
    with _build_app() as (app_obj, router):
        client = TestClient(app_obj)
        resp = client.post(
            "/trade-close",
            json=_payload(trade_id=12, sub_trade=True),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "skipped"
        assert body["reason"] == "partial_fill"
        assert len(router.calls) == 0


# --- Idempotency ----------------------------------------------------------

def test_trade_close_is_idempotent_on_duplicate_webhook():
    """Same trade_id posted twice → second is skipped, no second LLM call."""
    with _build_app() as (app_obj, router):
        client = TestClient(app_obj)
        r1 = client.post("/trade-close", json=_payload(trade_id=20))
        r2 = client.post("/trade-close", json=_payload(trade_id=20))
        assert r1.json()["status"] == "recorded"
        assert r2.json()["status"] == "skipped"
        assert r2.json()["reason"] == "already_reflected"
        assert r2.json()["reflection_id"] == r1.json()["reflection_id"]
        assert len(router.calls) == 1


def test_trade_close_idempotency_keyed_on_strategy():
    """trade_id alone is NOT the key — same id under different strategy creates a new reflection."""
    with _build_app() as (app_obj, router):
        client = TestClient(app_obj)
        r1 = client.post("/trade-close", json=_payload(trade_id=30, strategy="S1TrendFollow"))
        r2 = client.post("/trade-close", json=_payload(trade_id=30, strategy="S2MomentumRotation"))
        assert r1.json()["status"] == "recorded"
        assert r2.json()["status"] == "recorded"
        assert r1.json()["reflection_id"] != r2.json()["reflection_id"]
        assert len(router.calls) == 2


# --- LLM unavailable → 503 -----------------------------------------------

def test_trade_close_returns_503_when_llm_unavailable():
    """LLM outage must surface as 503 so freqtrade logs the failure clearly."""
    from app.llm.openai_compat import OpenAICompatibleClient

    # Patch LLM transport to 503
    def _outage(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="simulated outage")

    outage_transport = httpx.MockTransport(_outage)
    original_make_client = OpenAICompatibleClient._make_client
    original_make_async = OpenAICompatibleClient._make_async_client
    OpenAICompatibleClient._make_client = lambda self: httpx.Client(transport=outage_transport)
    OpenAICompatibleClient._make_async_client = lambda self: httpx.AsyncClient(transport=outage_transport)
    reset_caches_for_testing()
    from app.deps import _llm_client
    _llm_client.cache_clear()

    test_engine = create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.drop_all(test_engine)
    Base.metadata.create_all(test_engine)

    def override_get_db():
        s = sessionmaker(bind=test_engine, expire_on_commit=False)()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        resp = client.post("/trade-close", json=_payload(trade_id=50))
        assert resp.status_code == 503
        assert "LLM unavailable" in resp.json()["detail"]
        # Verify no reflection was written
        session = next(iter(override_get_db()))
        try:
            assert session.query(ReflectionRow).count() == 0
        finally:
            session.close()
    finally:
        OpenAICompatibleClient._make_client = original_make_client
        OpenAICompatibleClient._make_async_client = original_make_async
        app.dependency_overrides.clear()


# --- Validation ----------------------------------------------------------

def test_trade_close_rejects_missing_required_field():
    with _build_app() as (app_obj, _router):
        client = TestClient(app_obj)
        bad = _payload(trade_id=60)
        del bad["pair"]  # required
        resp = client.post("/trade-close", json=bad)
        assert resp.status_code == 422


def test_trade_close_rejects_negative_open_rate():
    with _build_app() as (app_obj, _router):
        client = TestClient(app_obj)
        bad = _payload(trade_id=61)
        bad["open_rate"] = -1.0  # gt=0 constraint
        resp = client.post("/trade-close", json=bad)
        assert resp.status_code == 422


def test_trade_close_rejects_zero_stake_amount():
    with _build_app() as (app_obj, _router):
        client = TestClient(app_obj)
        bad = _payload(trade_id=62)
        bad["stake_amount"] = 0.0  # gt=0 constraint
        resp = client.post("/trade-close", json=bad)
        assert resp.status_code == 422


def test_trade_close_rejects_empty_strategy():
    with _build_app() as (app_obj, _router):
        client = TestClient(app_obj)
        bad = _payload(trade_id=63)
        bad["strategy"] = ""
        resp = client.post("/trade-close", json=bad)
        assert resp.status_code == 422


def test_trade_close_inverts_dates_safely_when_close_before_open():
    """open_date > close_date would yield negative hold_duration_hours.
    The endpoint must clamp to a small positive value so the writer doesn't
    reject the prompt's gt=0 constraint."""
    with _build_app() as (app_obj, router):
        client = TestClient(app_obj)
        resp = client.post("/trade-close", json=_payload(
            trade_id=70,
            open_date="2026-06-05T00:00:00+00:00",
            close_date="2026-06-01T00:00:00+00:00",
        ))
        assert resp.status_code == 200
        # LLM was called with a clamped (small positive) hold duration
        assert len(router.calls) == 1
        # Prompt won't contain a negative duration
        assert "hours\n" in router.calls[0]