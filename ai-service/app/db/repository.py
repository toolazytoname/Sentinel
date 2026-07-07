"""Repository layer: thin CRUD helpers around ORM rows.

Repositories return ORM rows or domain objects, never raw SQL. This makes
testing trivial (use in-memory SQLite) and lets us swap engines.
"""
from __future__ import annotations

from datetime import datetime
from typing import Sequence

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.db.models import (
    LlmCallRow,
    ReflectionRow,
    ResearchNoteRow,
    StrategyStageRow,
    VetoRecordRow,
)


# --- Research notes ---

def insert_research_note(
    session: Session,
    *,
    asset: str,
    event_type: str,
    severity: int,
    summary: str,
    source_url: str,
    published_at: str,
) -> ResearchNoteRow:
    row = ResearchNoteRow(
        asset=asset,
        event_type=event_type,
        severity=severity,
        summary=summary,
        source_url=source_url,
        published_at=published_at,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def recent_high_severity_assets(session: Session, since_hours: int = 24) -> set[str]:
    """Return the set of assets with severity>=4 events in the last N hours.

    Used by the veto module's rule layer (Rule 1).
    """
    from datetime import timedelta, timezone
    cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    stmt = (
        select(ResearchNoteRow.asset)
        .where(ResearchNoteRow.severity >= 4)
        .where(ResearchNoteRow.created_at >= cutoff)
        .distinct()
    )
    return {row[0] for row in session.execute(stmt).all()}


# --- Reflections ---

def insert_reflection(
    session: Session,
    *,
    trade_id: str,
    strategy: str,
    what_worked: str,
    what_failed: str,
    lesson: str,
    confidence: float,
) -> ReflectionRow:
    row = ReflectionRow(
        trade_id=trade_id,
        strategy=strategy,
        what_worked=what_worked,
        what_failed=what_failed,
        lesson=lesson,
        confidence=confidence,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def reflections_for_strategy(session: Session, strategy: str, limit: int = 20) -> Sequence[ReflectionRow]:
    stmt = (
        select(ReflectionRow)
        .where(ReflectionRow.strategy == strategy)
        .order_by(desc(ReflectionRow.created_at))
        .limit(limit)
    )
    return session.execute(stmt).scalars().all()


def get_reflection_by_trade_id(
    session: Session, trade_id: str, strategy: str | None = None,
) -> ReflectionRow | None:
    """Lookup one reflection by its (strategy, trade_id) pair.

    Used by P2.5 webhook idempotency — when freqtrade replays an
    EXIT_FILL, we don't want to generate a second reflection for the
    same trade. `strategy` is part of the key because two strategies
    could theoretically reuse the same trade_id across separate
    freqtrade instances (each has its own SQLite DB).
    """
    stmt = select(ReflectionRow).where(ReflectionRow.trade_id == trade_id)
    if strategy is not None:
        stmt = stmt.where(ReflectionRow.strategy == strategy)
    return session.execute(stmt).scalar_one_or_none()


# --- Veto records ---

def insert_veto_record(
    session: Session,
    *,
    strategy: str,
    pair: str,
    veto: bool,
    reason: str,
    source: str,
    resolved_by: str | None = None,
) -> VetoRecordRow:
    row = VetoRecordRow(
        strategy=strategy,
        pair=pair,
        veto=veto,
        reason=reason,
        source=source,
        resolved_by=resolved_by,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def recent_vetoes(session: Session, since_hours: int = 24) -> Sequence[VetoRecordRow]:
    from datetime import timedelta, timezone
    cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    stmt = (
        select(VetoRecordRow)
        .where(VetoRecordRow.created_at >= cutoff)
        .order_by(desc(VetoRecordRow.created_at))
    )
    return session.execute(stmt).scalars().all()


def recent_veto_query_pairs(
    session: Session, since_hours: int = 24,
) -> list[tuple[str, str]]:
    """Distinct (strategy, pair) tuples the strategy has queried within the window.

    Every GET /veto call persists a veto_records row, so this is the set of
    (strategy, pair) combinations actually seen recently — the candidate set the
    async LLM-veto precompute job should evaluate (rather than the full universe
    of pairs, most of which are never traded).
    """
    from datetime import timedelta, timezone
    cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    stmt = (
        select(VetoRecordRow.strategy, VetoRecordRow.pair)
        .where(VetoRecordRow.created_at >= cutoff)
        .distinct()
    )
    return [(row[0], row[1]) for row in session.execute(stmt).all()]


def latest_llm_veto(
    session: Session, strategy: str, pair: str, within_minutes: int,
) -> VetoRecordRow | None:
    """Most recent `source="llm"` veto_records row for (strategy, pair), if fresh.

    Returns the newest LLM-sourced row whose `created_at` is within
    `within_minutes` of now, else None. Used by GET /veto to honor an
    asynchronously precomputed LLM veto without making an in-request LLM call.
    """
    from datetime import timedelta, timezone
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=within_minutes)
    stmt = (
        select(VetoRecordRow)
        .where(VetoRecordRow.strategy == strategy)
        .where(VetoRecordRow.pair == pair)
        .where(VetoRecordRow.source == "llm")
        .where(VetoRecordRow.created_at >= cutoff)
        .order_by(desc(VetoRecordRow.created_at))
        .limit(1)
    )
    return session.execute(stmt).scalars().first()


# --- LLM call token usage (P2.2 DoD) ---

def insert_llm_call(
    session: Session,
    *,
    model: str,
    model_tier: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
) -> LlmCallRow:
    row = LlmCallRow(
        model=model,
        model_tier=model_tier,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


# --- Strategy stages (ADR-005) ---

STAGES = ("backtest", "dry_run", "live_small", "live_scaled")


def get_strategy_stage(session: Session, strategy: str) -> StrategyStageRow | None:
    return session.get(StrategyStageRow, strategy) or session.execute(
        select(StrategyStageRow).where(StrategyStageRow.strategy == strategy)
    ).scalar_one_or_none()


def upsert_strategy_stage(
    session: Session,
    *,
    strategy: str,
    stage: str,
    criteria_snapshot: dict | None = None,
    approved_by: str = "system",
) -> StrategyStageRow:
    """Insert or update the stage for a strategy."""
    row = get_strategy_stage(session, strategy)
    if row is None:
        row = StrategyStageRow(
            strategy=strategy,
            stage=stage,
            criteria_snapshot=criteria_snapshot or {},
            approved_by=approved_by,
        )
        session.add(row)
    else:
        row.stage = stage
        if criteria_snapshot is not None:
            row.criteria_snapshot = criteria_snapshot
        row.approved_by = approved_by
        from datetime import timezone
        row.entered_at = datetime.now(timezone.utc)
    session.commit()
    session.refresh(row)
    return row


def all_strategy_stages(session: Session) -> Sequence[StrategyStageRow]:
    stmt = select(StrategyStageRow).order_by(StrategyStageRow.strategy)
    return session.execute(stmt).scalars().all()