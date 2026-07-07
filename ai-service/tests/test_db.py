"""DB layer tests. Use in-memory SQLite for speed."""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

import app.db.models as models
from app.db.models import Base, StrategyStageRow
from app.db.repository import (
    STAGES,
    all_strategy_stages,
    get_strategy_stage,
    insert_reflection,
    insert_research_note,
    insert_veto_record,
    recent_high_severity_assets,
    recent_vetoes,
    upsert_strategy_stage,
)


@pytest.fixture
def session():
    """Fresh in-memory SQLite per test."""
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    s = SessionLocal()
    yield s
    s.close()
    engine.dispose()


class TestResearchNotes:
    def test_insert_and_query(self, session):
        insert_research_note(
            session,
            asset="BTC",
            event_type="regulatory",
            severity=4,
            summary="SEC delays spot BTC ETF",
            source_url="https://example.com/1",
            published_at="2026-07-06T10:00:00Z",
        )
        assets = recent_high_severity_assets(session, since_hours=24)
        assert "BTC" in assets

    def test_severity_filter_excludes_low_severity(self, session):
        insert_research_note(
            session,
            asset="ETH",
            event_type="partnership",
            severity=2,
            summary="Minor partnership",
            source_url="https://example.com/2",
            published_at="2026-07-06T10:00:00Z",
        )
        # ETH has only severity=2, so high-severity filter (>=4) excludes it
        assets = recent_high_severity_assets(session, since_hours=24)
        assert "ETH" not in assets

    def test_deduplication_of_assets(self, session):
        # Multiple severity-4 events on BTC should still result in {BTC} (distinct)
        for i in range(3):
            insert_research_note(
                session,
                asset="BTC",
                event_type="regulatory",
                severity=4,
                summary=f"Event {i}",
                source_url=f"https://example.com/{i}",
                published_at="2026-07-06T10:00:00Z",
            )
        assets = recent_high_severity_assets(session, since_hours=24)
        assert assets == {"BTC"}


class TestReflections:
    def test_insert_and_retrieve(self, session):
        insert_reflection(
            session,
            trade_id="t1",
            strategy="S1TrendFollow",
            what_worked="ADX filter worked",
            what_failed="trailing too tight",
            lesson="widen trailing in low-vol",
            confidence=0.85,
        )
        rows = list(__import__("app.db.repository", fromlist=["reflections_for_strategy"]).reflections_for_strategy(session, "S1TrendFollow"))
        assert len(rows) == 1
        assert rows[0].trade_id == "t1"


class TestVetoRecords:
    def test_insert_and_query(self, session):
        insert_veto_record(
            session,
            strategy="S1TrendFollow",
            pair="BTC/USDT",
            veto=True,
            reason="high_severity_event:BTC",
            source="rule",
        )
        rows = list(recent_vetoes(session, since_hours=24))
        assert len(rows) == 1
        assert rows[0].strategy == "S1TrendFollow"
        assert rows[0].veto is True


class TestStrategyStages:
    def test_insert_then_get(self, session):
        upsert_strategy_stage(
            session,
            strategy="S1TrendFollow",
            stage="dry_run",
            criteria_snapshot={"days_in_stage": 0},
        )
        row = get_strategy_stage(session, "S1TrendFollow")
        assert row is not None
        assert row.stage == "dry_run"

    def test_upsert_updates_existing(self, session):
        upsert_strategy_stage(session, strategy="S1", stage="dry_run")
        upsert_strategy_stage(session, strategy="S1", stage="live_small", approved_by="user")
        row = get_strategy_stage(session, "S1")
        assert row.stage == "live_small"
        assert row.approved_by == "user"

    def test_unknown_strategy_returns_none(self, session):
        assert get_strategy_stage(session, "DoesNotExist") is None

    def test_all_strategy_stages(self, session):
        upsert_strategy_stage(session, strategy="S1", stage="dry_run")
        upsert_strategy_stage(session, strategy="S2", stage="backtest")
        rows = list(all_strategy_stages(session))
        assert {r.strategy for r in rows} == {"S1", "S2"}

    def test_stages_constant_includes_all_adsmitted_stages(self):
        # ADR-005 state machine stages — must include all four
        expected = {"backtest", "dry_run", "live_small", "live_scaled"}
        assert set(STAGES) == expected


@pytest.fixture
def reset_engine_singleton():
    """Reset the module-level engine singleton so get_engine rebuilds.

    RB2.1: the concurrency test needs a real file-based SQLite DB routed
    through the production get_engine() so it exercises the WAL + busy_timeout
    hardening. We reset before AND after so we never leak a temp-file engine
    into other tests (which rely on their own in-memory engines).
    """
    prev_engine, prev_session = models._engine, models._SessionLocal
    models._engine = None
    models._SessionLocal = None
    yield
    if models._engine is not None:
        models._engine.dispose()
    models._engine = prev_engine
    models._SessionLocal = prev_session


class TestSqliteConcurrencyHardening:
    """RB2.1 — WAL + busy_timeout + check_same_thread for concurrent writers."""

    def test_wal_pragma_applied_on_file_db(self, tmp_path, reset_engine_singleton):
        db_path = tmp_path / "wal.db"
        engine = models.get_engine(f"sqlite:///{db_path}")
        with engine.connect() as conn:
            journal_mode = conn.execute(text("PRAGMA journal_mode")).scalar()
            busy_timeout = conn.execute(text("PRAGMA busy_timeout")).scalar()
        assert journal_mode.lower() == "wal"
        assert busy_timeout == 5000

    def test_concurrent_writers_no_database_locked(self, tmp_path, reset_engine_singleton):
        db_path = tmp_path / "concurrent.db"
        models.get_engine(f"sqlite:///{db_path}")

        rows_per_thread = 25
        errors: list[Exception] = []
        barrier = threading.Barrier(2)

        def write_veto_records() -> None:
            barrier.wait()
            try:
                for i in range(rows_per_thread):
                    session = models.get_session()
                    try:
                        insert_veto_record(
                            session,
                            strategy="S1TrendFollow",
                            pair="BTC/USDT",
                            veto=bool(i % 2),
                            reason=f"veto-{i}",
                            source="rule",
                        )
                    finally:
                        session.close()
            except Exception as exc:  # noqa: BLE001 - capture for assertion
                errors.append(exc)

        def write_research_notes() -> None:
            barrier.wait()
            try:
                for i in range(rows_per_thread):
                    session = models.get_session()
                    try:
                        insert_research_note(
                            session,
                            asset="BTC",
                            event_type="regulatory",
                            severity=4,
                            summary=f"event-{i}",
                            source_url=f"https://example.com/{i}",
                            published_at="2026-07-06T10:00:00Z",
                        )
                    finally:
                        session.close()
            except Exception as exc:  # noqa: BLE001 - capture for assertion
                errors.append(exc)

        threads = [
            threading.Thread(target=write_veto_records),
            threading.Thread(target=write_research_notes),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not any(isinstance(e, OperationalError) for e in errors), (
            f"database-is-locked / OperationalError raised under concurrency: {errors}"
        )
        assert not errors, f"unexpected errors: {errors}"

        # All rows from both writer threads persisted.
        verify = models.get_session()
        try:
            assert len(list(recent_vetoes(verify, since_hours=24))) == rows_per_thread
            assert recent_high_severity_assets(verify, since_hours=24) == {"BTC"}
        finally:
            verify.close()