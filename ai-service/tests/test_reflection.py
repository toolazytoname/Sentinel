"""Tests for the reflection module."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base
from app.llm import LLMUnavailable, ReflectionExtractor
from app.modules.reflection import ReflectionWriter, TradeContext
from app.schemas import TradeReflection


class FakeReflectionExtractor:
    def __init__(self, reflection: TradeReflection | None = None, raise_unavailable: bool = False):
        self._reflection = reflection
        self._raise = raise_unavailable
        self.calls = 0

    def extract(self, prompt: str) -> TradeReflection:
        self.calls += 1
        if self._raise:
            raise LLMUnavailable("simulated")
        assert self._reflection is not None
        return self._reflection


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    yield SessionLocal
    engine.dispose()


def _make_ctx(profit_pct: float = 0.05) -> TradeContext:
    return TradeContext(
        trade_id="t-123",
        strategy="S1TrendFollow",
        pair="BTC/USDT",
        side="long",
        entry_price=100.0,
        exit_price=100.0 * (1 + profit_pct),
        profit_pct=profit_pct,
        hold_duration_hours=48.5,
        signal_snapshot={"adx": 32.5, "ema_cross": True},
        closed_at=datetime(2026, 7, 6, tzinfo=timezone.utc),
    )


def _make_reflection(trade_id: str = "t-123") -> TradeReflection:
    return TradeReflection(
        trade_id=trade_id,
        what_worked="ADX filter confirmed trend strength",
        what_failed="Trailing stop was too tight for the volatility",
        lesson="Widen trailing stop to 7% in similar volatility regimes",
        confidence=0.78,
    )


class TestReflectionWriter:
    def test_records_reflection_on_success(self, session_factory):
        ext = FakeReflectionExtractor(_make_reflection())
        writer = ReflectionWriter(ext, session_factory)
        result = writer.record(_make_ctx())
        assert result.trade_id == "t-123"
        assert ext.calls == 1

    def test_persists_to_db(self, session_factory):
        ext = FakeReflectionExtractor(_make_reflection())
        writer = ReflectionWriter(ext, session_factory)
        writer.record(_make_ctx())
        with session_factory() as s:
            from app.db.repository import reflections_for_strategy
            rows = list(reflections_for_strategy(s, "S1TrendFollow"))
            assert len(rows) == 1
            assert rows[0].trade_id == "t-123"
            assert rows[0].confidence == 0.78

    def test_llm_unavailable_propagates(self, session_factory):
        ext = FakeReflectionExtractor(raise_unavailable=True)
        writer = ReflectionWriter(ext, session_factory)
        with pytest.raises(LLMUnavailable):
            writer.record(_make_ctx())

    def test_prompt_contains_trade_context(self, session_factory):
        ext = FakeReflectionExtractor(_make_reflection())
        writer = ReflectionWriter(ext, session_factory)
        # Patch to capture the prompt
        captured: list[str] = []

        def capturing_extract(prompt):
            captured.append(prompt)
            return _make_reflection()

        ext.extract = capturing_extract  # type: ignore[method-assign]
        ctx = _make_ctx(profit_pct=0.08)
        writer.record(ctx)
        assert "BTC/USDT" in captured[0]
        assert "+8.00%" in captured[0]
        assert "S1TrendFollow" in captured[0]
        assert "win" in captured[0]  # positive profit → "win" label

    def test_loss_label_in_prompt(self, session_factory):
        ext = FakeReflectionExtractor(_make_reflection())
        writer = ReflectionWriter(ext, session_factory)
        captured: list[str] = []

        def capturing_extract(prompt):
            captured.append(prompt)
            return _make_reflection()

        ext.extract = capturing_extract  # type: ignore[method-assign]
        writer.record(_make_ctx(profit_pct=-0.04))
        assert "loss" in captured[0]
        assert "-4.00%" in captured[0]