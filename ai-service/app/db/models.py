"""SQLAlchemy ORM models for Sentinel AI service.

Schema reflects docs/system/02-design.md §2.4. Using SQLite for local
single-process use; switching to PostgreSQL only requires changing the
connection URL — the ORM models stay identical.

All timestamps are stored as ISO 8601 strings in UTC. The DB layer enforces
schema; business logic (e.g. VETO priority) lives in app/modules/*.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class ResearchNoteRow(Base):
    """Structured facts extracted from news/market sources (see schemas/llm_outputs.py ResearchNote)."""
    __tablename__ = "research_notes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    asset: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[int] = mapped_column(Integer, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    published_at: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)


class ReflectionRow(Base):
    """Post-trade reflection output (see schemas/llm_outputs.py TradeReflection)."""
    __tablename__ = "reflections"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    trade_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    strategy: Mapped[str] = mapped_column(String(64), nullable=False)
    what_worked: Mapped[str] = mapped_column(Text, nullable=False)
    what_failed: Mapped[str] = mapped_column(Text, nullable=False)
    lesson: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)


class VetoRecordRow(Base):
    """Audit trail of every veto decision (rule, LLM, or default_pass)."""
    __tablename__ = "veto_records"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    strategy: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    pair: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    signal_time: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    veto: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)  # "rule" | "llm" | "llm_unavailable"
    resolved_by: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)


class StrategyStageRow(Base):
    """Tracks which stage of the ADR-005 state machine each strategy is in.

    Stages: backtest → dry_run → live_small → live_scaled.
    Criteria_snapshot stores the numeric thresholds at entry (for audit).
    """
    __tablename__ = "strategy_stages"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    strategy: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    entered_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    criteria_snapshot: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    approved_by: Mapped[str] = mapped_column(String(64), default="system", nullable=False)
    # Rolling counters used by stage-up criteria checks (ADR-005)
    trade_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_observed_drawdown_pct: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)


# --- Engine + session factory ---

_engine = None
_SessionLocal: sessionmaker | None = None


def get_engine(url: str = "sqlite:///./sentinel.db"):
    """Module-level singleton engine. SQLite default; override for PostgreSQL.

    Production should override via env var, e.g.:
      DATABASE_URL=postgresql://user:pass@host:5432/sentinel
    """
    global _engine, _SessionLocal
    if _engine is None:
        _engine = create_engine(url, echo=False, future=True)
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
        Base.metadata.create_all(_engine)
    return _engine


def get_session() -> Session:
    """Return a new Session. Caller is responsible for closing."""
    if _SessionLocal is None:
        get_engine()
    assert _SessionLocal is not None
    return _SessionLocal()