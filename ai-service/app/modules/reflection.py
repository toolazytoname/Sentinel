"""Reflection module: post-trade LLM-driven reflection.

Flow (docs/system/02-design.md §2.2 Reflection Module):
  1. freqtrade sends a webhook when a trade closes (out of scope for this PR —
     the API just exposes POST /reflection to be called manually or by an
     external cron that polls freqtrade's closed_trade endpoint)
  2. We build a prompt containing the trade context + recent research notes
  3. LLM returns a TradeReflection (what worked / what failed / lesson)
  4. We persist + (optionally) inject into next analysis as context
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.db import insert_reflection
from app.llm import LLMUnavailable, ReflectionExtractor
from app.schemas import TradeReflection

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TradeContext:
    """Minimal context for a closed trade, used to build the reflection prompt."""
    trade_id: str
    strategy: str
    pair: str
    side: str
    entry_price: float
    exit_price: float
    profit_pct: float
    hold_duration_hours: float
    signal_snapshot: dict[str, Any]  # what the strategy saw at entry
    closed_at: datetime


class ReflectionWriter:
    """Builds prompts, calls LLM, persists TradeReflection rows."""

    def __init__(
        self,
        extractor: ReflectionExtractor,
        session_factory,
    ):
        self._extractor = extractor
        self._session_factory = session_factory

    def record(self, ctx: TradeContext) -> TradeReflection:
        """Generate and persist a reflection for one closed trade."""
        prompt = _build_prompt(ctx)
        try:
            reflection = self._extractor.extract(prompt)
        except LLMUnavailable as e:
            logger.warning("LLM unavailable for reflection on trade %s: %s", ctx.trade_id, e)
            # Re-raise — the caller (HTTP handler) decides whether to 503 or queue
            raise
        self._persist(ctx.strategy, reflection)
        return reflection

    def _persist(self, strategy: str, reflection: TradeReflection) -> None:
        with self._session_factory() as session:
            insert_reflection(
                session,
                trade_id=reflection.trade_id,
                strategy=strategy,
                what_worked=reflection.what_worked,
                what_failed=reflection.what_failed,
                lesson=reflection.lesson,
                confidence=reflection.confidence,
            )


def _build_prompt(ctx: TradeContext) -> str:
    """Build a structured prompt that puts the LLM in the role of a trading post-mortem analyst."""
    direction = "win" if ctx.profit_pct > 0 else "loss"
    return (
        f"You are reviewing a closed trade for a disciplined crypto trading system.\n\n"
        f"Trade ID: {ctx.trade_id}\n"
        f"Strategy: {ctx.strategy}\n"
        f"Pair: {ctx.pair} ({ctx.side})\n"
        f"Entry: {ctx.entry_price:.4f} → Exit: {ctx.exit_price:.4f} "
        f"({ctx.profit_pct:+.2%}, {direction})\n"
        f"Hold duration: {ctx.hold_duration_hours:.1f} hours\n"
        f"Closed at: {ctx.closed_at.isoformat()}\n"
        f"Signal at entry: {ctx.signal_snapshot}\n\n"
        f"Write a structured post-mortem:\n"
        f"- what_worked: what aspects of the strategy logic executed as intended\n"
        f"- what_failed: what aspects didn't (entry timing, exit timing, sizing, etc.)\n"
        f"- lesson: ONE actionable rule the system should follow next time\n\n"
        f"Output a single JSON object matching the schema."
    )