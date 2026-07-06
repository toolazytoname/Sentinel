"""Risk audit / veto module.

Per ADR-002: this is the ONLY place where LLM can affect execution, and only
via VETO (block). LLM cannot approve or generate trades.

Per ADR-002 fail-open principle: if this service is unreachable, the
strategy MUST proceed with the trade. LLM is an additional safety net, not
a gate. The strategy-side timeout config determines max wait.

Three layers, in priority order:
  1. Rule-based checks (deterministic, no LLM)
     - Recent high-severity negative events on this asset
     - Position concentration limits
     - Scheduled event windows (e.g. FOMC)
  2. LLM devil's-advocate review (deep model)
     - Given context, argue against the trade
     - Returns {veto, reason, confidence}
  3. Default: PASS

If any rule fires, we VETO and skip the LLM call (faster + cheaper).
If all rules pass, we ask LLM. If LLM fails, we PASS (fail-open).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.llm import LLMUnavailable, VetoExtractor
from app.schemas import VetoDecision

logger = logging.getLogger(__name__)


# --- Rule layer (no LLM) ---

@dataclass(frozen=True)
class TradeSignal:
    """What the strategy wants to do. Minimal info needed for audit."""
    strategy: str
    pair: str
    side: str  # "long" | "short" (future)
    stake_pct: float  # fraction of total equity (0.0 - 1.0)
    proposed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class MarketContext:
    """State around the proposed trade. All fields are independent checks."""
    recent_high_severity_events: list[str]  # asset symbols with severity≥4 events in last 24h
    current_total_exposure_pct: float  # sum of all open positions as % of equity
    max_exposure_pct: float = 0.60       # hard limit (ADR-001, user can tune)
    upcoming_event_window_minutes: int = 0  # 0 = no upcoming event, else minutes until


def check_rules(signal: TradeSignal, context: MarketContext) -> tuple[bool, str]:
    """Return (vetoed, reason). vetoed=True blocks the trade.

    Pure deterministic logic; no LLM, no I/O. Always returns.
    """
    # Rule 1: High-severity negative news on this asset within 24h
    base_asset = signal.pair.split("/")[0]
    if base_asset in context.recent_high_severity_events:
        return True, f"high_severity_event:{base_asset}"

    # Rule 2: Position concentration
    # Use round() to avoid IEEE-754 floating-point drift (e.g. 0.55+0.05 = 0.6000000000000001)
    # 4 decimal places is far smaller than any meaningful stake_pct difference.
    new_total = round(context.current_total_exposure_pct + signal.stake_pct, 4)
    if new_total > context.max_exposure_pct:
        return True, f"exposure_breach:{new_total:.0%}>{context.max_exposure_pct:.0%}"

    # Rule 3: Upcoming event window (e.g. FOMC, CPI) within 30 minutes
    if 0 < context.upcoming_event_window_minutes < 30:
        return True, f"event_window:{context.upcoming_event_window_minutes}min"

    return False, "rules_passed"


# --- LLM layer ---

def llm_veto(signal: TradeSignal, context: MarketContext, veto_extractor: VetoExtractor) -> VetoDecision:
    """Ask LLM for devil's-advocate review. May raise LLMUnavailable (caller must handle)."""
    prompt = _build_veto_prompt(signal, context)
    return veto_extractor.extract(prompt)


def _build_veto_prompt(signal: TradeSignal, context: MarketContext) -> str:
    """Build the prompt for the LLM. Kept deterministic for testability."""
    return (
        f"You are reviewing a proposed trade for risk. "
        f"Rule-based checks have already passed; your job is to look for "
        f"second-order risks the rules may have missed.\n\n"
        f"Strategy: {signal.strategy}\n"
        f"Pair: {signal.pair}\n"
        f"Side: {signal.side}\n"
        f"Stake: {signal.stake_pct:.1%} of equity\n"
        f"Current total exposure: {context.current_total_exposure_pct:.1%}\n"
        f"Max allowed: {context.max_exposure_pct:.1%}\n"
        f"Recent high-severity events (24h): {context.recent_high_severity_events}\n"
        f"Upcoming event window: {context.upcoming_event_window_minutes} minutes\n\n"
        f"Argue AGAINST this trade. Output your decision as JSON."
    )


# --- Top-level orchestrator ---

@dataclass(frozen=True)
class VetoResult:
    """The final answer returned to the strategy (via GET /veto endpoint)."""
    veto: bool
    reason: str
    source: str  # "rule" | "llm" | "default_pass" | "llm_unavailable"


def audit(
    signal: TradeSignal,
    context: MarketContext,
    veto_extractor: VetoExtractor,
) -> VetoResult:
    """The full audit pipeline. Rules first, LLM if rules pass.

    Fail-open: if LLM is unavailable, we pass (veto=False) with a clear
    reason so the caller knows LLM didn't actually review.
    """
    # Layer 1: rules
    rules_vetoed, rules_reason = check_rules(signal, context)
    if rules_vetoed:
        logger.info("Veto by rule: %s", rules_reason)
        return VetoResult(veto=True, reason=rules_reason, source="rule")

    # Layer 2: LLM devil's advocate (fail-open)
    try:
        decision = llm_veto(signal, context, veto_extractor)
        if decision.veto:
            logger.info("Veto by LLM (confidence %.2f): %s", decision.confidence, decision.reason)
            return VetoResult(veto=True, reason=decision.reason, source="llm")
        return VetoResult(veto=False, reason=f"llm_pass:{decision.reason}", source="llm")
    except LLMUnavailable as e:
        logger.warning("LLM unavailable, defaulting to PASS: %s", e)
        return VetoResult(
            veto=False,
            reason="llm_unavailable_default_pass",
            source="llm_unavailable",
        )