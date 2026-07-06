"""Tests for the risk audit / veto module.

Critical paths covered:
  1. Rules layer: each rule individually + combined
  2. LLM veto: yes/no/skip on unavailable
  3. Top-level fail-open behavior
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from app.llm import LLMUnavailable, VetoExtractor
from app.llm.client import StructuredExtractor
from app.modules import MarketContext, TradeSignal, VetoResult, audit, check_rules
from app.schemas import VetoDecision


# --- Fixtures ---

@pytest.fixture
def clean_signal() -> TradeSignal:
    return TradeSignal(
        strategy="S1TrendFollow",
        pair="BTC/USDT",
        side="long",
        stake_pct=0.05,
    )


@pytest.fixture
def clean_context() -> MarketContext:
    return MarketContext(
        recent_high_severity_events=[],
        current_total_exposure_pct=0.10,
    )


class FakeVetoExtractor:
    """Stand-in for VetoExtractor. Programs the next decision."""

    def __init__(self, decision: VetoDecision | None = None, raise_unavailable: bool = False):
        self._decision = decision
        self._raise = raise_unavailable
        self.calls = 0

    def extract(self, prompt: str) -> VetoDecision:
        self.calls += 1
        if self._raise:
            raise LLMUnavailable("simulated outage")
        return self._decision


def _decision(veto: bool, reason: str = "looks fine", confidence: float = 0.5) -> VetoDecision:
    return VetoDecision(veto=veto, reason=reason, confidence=confidence)


# --- Rule layer tests ---

class TestCheckRules:
    def test_no_veto_when_all_rules_pass(self, clean_signal, clean_context):
        vetoed, reason = check_rules(clean_signal, clean_context)
        assert vetoed is False
        assert reason == "rules_passed"

    def test_high_severity_event_blocks_trade(self, clean_signal):
        context = MarketContext(
            recent_high_severity_events=["BTC"],  # BTC event 24h ago
            current_total_exposure_pct=0.10,
        )
        vetoed, reason = check_rules(clean_signal, context)
        assert vetoed is True
        assert "high_severity_event" in reason
        assert "BTC" in reason

    def test_high_severity_event_on_different_asset_passes(self, clean_signal):
        context = MarketContext(
            recent_high_severity_events=["ETH"],  # wrong asset
            current_total_exposure_pct=0.10,
        )
        vetoed, reason = check_rules(clean_signal, context)
        assert vetoed is False

    def test_exposure_breach_blocks_trade(self, clean_signal):
        context = MarketContext(
            recent_high_severity_events=[],
            current_total_exposure_pct=0.55,
            max_exposure_pct=0.60,
        )
        # Current 0.55 + stake 0.05 = 0.60, exactly at limit, should pass
        vetoed, _ = check_rules(clean_signal, context)
        assert vetoed is False
        # Bump stake: 0.55 + 0.06 = 0.61 > 0.60, should veto
        clean_signal = TradeSignal("S1", "BTC/USDT", "long", 0.06)
        vetoed, reason = check_rules(clean_signal, context)
        assert vetoed is True
        assert "exposure_breach" in reason

    def test_event_window_blocks_trade(self, clean_signal):
        context = MarketContext(
            recent_high_severity_events=[],
            current_total_exposure_pct=0.10,
            upcoming_event_window_minutes=15,  # FOMC in 15 min
        )
        vetoed, reason = check_rules(clean_signal, context)
        assert vetoed is True
        assert "event_window" in reason

    def test_event_window_far_away_does_not_block(self, clean_signal):
        context = MarketContext(
            recent_high_severity_events=[],
            current_total_exposure_pct=0.10,
            upcoming_event_window_minutes=120,  # 2 hours away, fine
        )
        vetoed, _ = check_rules(clean_signal, context)
        assert vetoed is False


# --- LLM layer tests ---

class TestLLMVeto:
    def test_llm_yes_veto(self, clean_signal, clean_context):
        ext = FakeVetoExtractor(_decision(veto=True, reason="concentration risk too high"))
        decision = ext.extract("any prompt")
        assert decision.veto is True

    def test_llm_no_veto(self, clean_signal, clean_context):
        ext = FakeVetoExtractor(_decision(veto=False))
        decision = ext.extract("any prompt")
        assert decision.veto is False


# --- Top-level audit tests (the critical ones) ---

class TestAudit:
    def test_rule_veto_skips_llm(self, clean_signal):
        # Rule vetoes, LLM should NOT be called
        context = MarketContext(
            recent_high_severity_events=["BTC"],
            current_total_exposure_pct=0.10,
        )
        ext = FakeVetoExtractor(_decision(veto=False))
        result = audit(clean_signal, context, ext)
        assert result.veto is True
        assert result.source == "rule"
        assert ext.calls == 0, "LLM must not be called when rules already veto"

    def test_llm_veto_propagates(self, clean_signal, clean_context):
        ext = FakeVetoExtractor(_decision(veto=True, reason="black swan risk"))
        result = audit(clean_signal, clean_context, ext)
        assert result.veto is True
        assert result.source == "llm"
        assert "black swan" in result.reason
        assert ext.calls == 1

    def test_llm_pass_propagates(self, clean_signal, clean_context):
        ext = FakeVetoExtractor(_decision(veto=False, reason="looks clean"))
        result = audit(clean_signal, clean_context, ext)
        assert result.veto is False
        assert result.source == "llm"

    def test_llm_unavailable_defaults_to_pass(self, clean_signal, clean_context):
        # CRITICAL: when LLM is down, we MUST pass (fail-open principle)
        ext = FakeVetoExtractor(raise_unavailable=True)
        result = audit(clean_signal, clean_context, ext)
        assert result.veto is False, "FAIL-OPEN VIOLATED: LLM down should default to PASS"
        assert result.source == "llm_unavailable"
        assert ext.calls == 1

    def test_rule_priority_over_llm(self, clean_signal):
        # Even if LLM says pass, rules must still veto
        context = MarketContext(
            recent_high_severity_events=["BTC"],
            current_total_exposure_pct=0.10,
        )
        ext = FakeVetoExtractor(_decision(veto=False))
        result = audit(clean_signal, context, ext)
        assert result.veto is True
        assert result.source == "rule"
        assert ext.calls == 0