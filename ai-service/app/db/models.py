"""SQLAlchemy ORM models for Sentinel AI service.

Schema reflects docs/system/02-design.md §2.4. Using SQLite for local
single-process use; switching to PostgreSQL only requires changing the
connection URL — the ORM models stay identical.

All timestamps are stored as ISO 8601 strings in UTC. The DB layer enforces
schema; business logic (e.g. VETO priority) lives in app/modules/*.
"""
from __future__ import annotations

import logging
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
    event,
    inspect,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

logger = logging.getLogger(__name__)


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


class LlmCallRow(Base):
    """Token-usage audit trail: one row per successful LLM chat completion.

    Fulfills design P2.2 DoD — lets cost/token consumption be audited. Written
    best-effort via a usage callback (see llm/openai_compat.py); a logging
    failure must never affect the completion result.
    """
    __tablename__ = "llm_calls"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    model_tier: Mapped[str] = mapped_column(String(16), nullable=False)  # "quick"|"deep"
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
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


# --- Schema reconciliation (lightweight, idempotent — see RB.4) ---
#
# Base.metadata.create_all() only CREATES wholly-missing tables; it never adds a
# new COLUMN to an existing table. A sentinel.db persisted from before a model
# gained a column (e.g. strategy_stages.trade_count) would raise "no such column"
# at runtime on redeploy. This reconciler diffs the live schema against the ORM
# models and ADDs any missing columns. Deliberately not full Alembic: this is a
# single-node personal system; Alembic/Postgres is a documented future upgrade.


def _column_default_sql(column) -> Optional[str]:
    """Render an SQL literal for a column's default, or None if it has none.

    A DEFAULT is required to add a NOT NULL column to a table with existing rows
    (so those rows get a valid value). Server-side defaults win; otherwise a
    scalar Python-side default is rendered. Callable/sequence defaults are
    skipped (no static literal to emit).
    """
    if column.server_default is not None:
        arg = column.server_default.arg
        return arg.text if hasattr(arg, "text") else str(arg)

    default = column.default
    if default is None or default.is_callable or default.is_sequence:
        return None
    value = default.arg
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        escaped = value.replace("'", "''")
        return f"'{escaped}'"
    return None


def _add_column_ddl(table_name: str, column, dialect) -> str:
    """Build a dialect-appropriate ``ALTER TABLE ... ADD COLUMN`` statement."""
    col_type = column.type.compile(dialect)
    default_sql = _column_default_sql(column)
    parts = [column.name, col_type]
    # Only assert NOT NULL when we can back-fill existing rows with a default;
    # otherwise SQLite (and others) reject the ALTER on a populated table.
    if not column.nullable and default_sql is not None:
        parts.append("NOT NULL")
    if default_sql is not None:
        parts.append(f"DEFAULT {default_sql}")
    return f"ALTER TABLE {table_name} ADD COLUMN {' '.join(parts)}"


def ensure_schema(engine) -> None:
    """Idempotently reconcile the live DB schema against the ORM models.

    1. ``create_all`` — creates any wholly-missing tables (engine-agnostic;
       works for sqlite and postgres alike).
    2. ``inspect`` each mapped table and diff its live columns against the model.
    3. For each column present in the model but missing in the DB, issue an
       ``ALTER TABLE ... ADD COLUMN`` (with the model's default so existing rows
       are back-filled). Each ALTER is wrapped so a race/duplicate never crashes
       startup.

    Idempotent (a second run is a no-op) and dialect-tolerant: we only ADD
    columns — never drop, rename, or change types (all SQLite supports).
    """
    Base.metadata.create_all(engine)

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    for table_name, table in Base.metadata.tables.items():
        if table_name not in existing_tables:
            # create_all should have built it; skip defensively if not.
            continue
        existing_cols = {c["name"] for c in inspector.get_columns(table_name)}
        for column in table.columns:
            if column.name in existing_cols:
                continue
            ddl = _add_column_ddl(table_name, column, engine.dialect)
            try:
                with engine.begin() as conn:
                    conn.execute(text(ddl))
                logger.warning(
                    "ensure_schema: added missing column %s.%s",
                    table_name,
                    column.name,
                )
            except Exception as exc:  # noqa: BLE001 - never crash startup on ALTER
                logger.warning(
                    "ensure_schema: could not add column %s.%s: %s",
                    table_name,
                    column.name,
                    exc,
                )


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
        is_sqlite = url.startswith("sqlite")
        # SQLite: allow cross-thread use (BackgroundScheduler writes research_notes
        # while FastAPI request threads write veto_records/reflections). No-op for
        # Postgres and other backends.
        connect_args = {"check_same_thread": False} if is_sqlite else {}
        _engine = create_engine(
            url, echo=False, future=True, connect_args=connect_args
        )
        if is_sqlite:
            # Enable WAL + a busy timeout on every new connection so concurrent
            # writers retry instead of failing with "database is locked".
            @event.listens_for(_engine, "connect")
            def _set_sqlite_pragma(dbapi_conn, _rec):  # noqa: ANN001
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA busy_timeout=5000")
                cur.close()
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
        ensure_schema(_engine)
    return _engine


def get_session() -> Session:
    """Return a new Session. Caller is responsible for closing."""
    if _SessionLocal is None:
        get_engine()
    assert _SessionLocal is not None
    return _SessionLocal()