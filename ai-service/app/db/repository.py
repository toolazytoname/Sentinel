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