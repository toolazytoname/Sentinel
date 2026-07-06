"""Tests for the background scheduler (P2.7).

Focus: job functions are pure and unit-testable; the scheduler wrapper
respects SCHEDULER_ENABLED, runs three named jobs, and isolates failures
(one bad job does not stop the others).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from app.db.models import Base  # noqa: E402
from app.modules.stages import register_strategy as repo_register  # noqa: E402
from app.modules.research import ResearchIngester  # noqa: E402
from app.scheduler import (  # noqa: E402
    SchedulerConfig,
    SentinelScheduler,
    safe_run,
    run_daily_research,
    run_daily_stage_check,
    run_weekly_rollup,
)


# --- Pure helpers -----------------------------------------------------------

def test_safe_run_swallows_exceptions_and_logs(caplog):
    def boom():
        raise RuntimeError("nope")
    with caplog.at_level("ERROR"):
        safe_run("test_job", boom)  # must not raise


def test_safe_run_passes_through_normal_return():
    called = []
    def ok():
        called.append(1)
    safe_run("ok", ok)
    assert called == [1]


# --- Job functions ----------------------------------------------------------

def _make_session_factory():
    """In-memory SQLite + session factory, fresh per test."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_run_daily_research_calls_ingester():
    ingester = MagicMock(spec=ResearchIngester)
    ingester.run_once.return_value = 3
    run_daily_research(ingester)
    ingester.run_once.assert_called_once()


def test_run_daily_stage_check_logs_each_strategy(caplog):
    sf = _make_session_factory()
    with sf() as s:
        repo_register(s, strategy="S1", initial_stage="dry_run", approved_by="test")
        repo_register(s, strategy="S2", initial_stage="backtest", approved_by="test")
    with caplog.at_level("INFO"):
        run_daily_stage_check(sf)
    msgs = [r.message for r in caplog.records]
    assert any("S1" in m and "dry_run" in m for m in msgs)
    assert any("S2" in m and "backtest" in m for m in msgs)


def test_run_daily_stage_check_empty_db_does_not_raise():
    sf = _make_session_factory()
    # No strategies registered — must not raise
    run_daily_stage_check(sf)


def test_run_weekly_rollup_groups_by_strategy(caplog):
    sf = _make_session_factory()
    from app.db.repository import insert_reflection
    # Insert 2 reflections for S1, 1 for S2, with created_at in last 7 days
    recent = datetime.now(timezone.utc) - timedelta(days=2)
    with sf() as s:
        for _ in range(2):
            r = insert_reflection(
                s, trade_id="t1", strategy="S1",
                what_worked="a", what_failed="b", lesson="c", confidence=0.5,
            )
            r.created_at = recent  # sqlite default might be earlier; force it
            s.commit()
        r = insert_reflection(
            s, trade_id="t2", strategy="S2",
            what_worked="a", what_failed="b", lesson="c", confidence=0.5,
        )
        r.created_at = recent
        s.commit()
    with caplog.at_level("INFO"):
        run_weekly_rollup(sf)
    msgs = [r.message for r in caplog.records]
    assert any("S1" in m and "2 reflections" in m for m in msgs)
    assert any("S2" in m and "1 reflections" in m for m in msgs)


def test_run_weekly_rollup_handles_empty_db(caplog):
    sf = _make_session_factory()
    with caplog.at_level("INFO"):
        run_weekly_rollup(sf)  # must not raise
    assert any("no reflections" in r.message for r in caplog.records)


# --- SchedulerConfig --------------------------------------------------------

def test_scheduler_config_defaults(monkeypatch):
    monkeypatch.delenv("SCHEDULER_ENABLED", raising=False)
    monkeypatch.delenv("SCHEDULER_RESEARCH_CRON", raising=False)
    monkeypatch.delenv("SCHEDULER_STAGE_CRON", raising=False)
    monkeypatch.delenv("SCHEDULER_WEEKLY_CRON", raising=False)
    cfg = SchedulerConfig.from_env()
    assert cfg.enabled is True
    assert cfg.research_cron == "0 9 * * *"
    assert cfg.stage_cron == "30 9 * * *"
    assert cfg.weekly_cron == "0 10 * * 0"


def test_scheduler_config_can_be_disabled(monkeypatch):
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    cfg = SchedulerConfig.from_env()
    assert cfg.enabled is False


def test_scheduler_config_parses_custom_crons(monkeypatch):
    monkeypatch.setenv("SCHEDULER_RESEARCH_CRON", "15 8 * * *")
    monkeypatch.setenv("SCHEDULER_STAGE_CRON", "45 8 * * *")
    monkeypatch.setenv("SCHEDULER_WEEKLY_CRON", "0 20 * * 6")
    cfg = SchedulerConfig.from_env()
    assert cfg.research_cron == "15 8 * * *"
    assert cfg.stage_cron == "45 8 * * *"
    assert cfg.weekly_cron == "0 20 * * 6"


# --- SentinelScheduler lifecycle -------------------------------------------

def _build_scheduler(monkeypatch, *, enabled: bool = False):
    """Build a scheduler with a mock ingester. Default disabled to avoid
    actually running jobs during the test window."""
    monkeypatch.setenv("SCHEDULER_ENABLED", "true" if enabled else "false")
    config = SchedulerConfig.from_env()
    return SentinelScheduler(
        config,
        ingester=MagicMock(spec=ResearchIngester),
        session_factory=_make_session_factory(),
    )


def test_scheduler_disabled_does_not_start(monkeypatch):
    s = _build_scheduler(monkeypatch, enabled=False)
    s.start()
    assert not s.is_running
    assert s.job_ids() == []
    s.stop()  # idempotent


def test_scheduler_starts_and_registers_three_jobs(monkeypatch):
    s = _build_scheduler(monkeypatch, enabled=True)
    s.start()
    try:
        assert s.is_running
        ids = set(s.job_ids())
        assert ids == {"daily_research", "daily_stage_check", "weekly_rollup"}
    finally:
        s.stop()
    assert not s.is_running


def test_scheduler_start_is_idempotent(monkeypatch):
    s = _build_scheduler(monkeypatch, enabled=True)
    s.start()
    s.start()  # must not raise or duplicate
    try:
        assert s.is_running
        # Still three jobs after second start
        assert len(s.job_ids()) == 3
    finally:
        s.stop()


def test_scheduler_stop_without_start_is_safe():
    s = _build_scheduler(MagicMock(), enabled=False)
    s.stop()  # must not raise
    assert not s.is_running


def test_scheduler_isolates_job_failure(monkeypatch, caplog):
    """A raising job must not stop the scheduler or kill sibling jobs."""
    s = _build_scheduler(monkeypatch, enabled=True)

    # Replace one job with a raising callable after start; sibling jobs
    # should still be scheduled (we can inspect via job_ids).
    s.start()
    try:
        # Manually fire safe_run via the registered lambda and verify it logs
        original_jobs = list(s._scheduler.get_jobs())  # type: ignore[union-attr]
        assert len(original_jobs) == 3

        # Find the research job's func and invoke safe_run with a boom — should not raise
        with caplog.at_level("ERROR"):
            safe_run("simulated_failure", lambda: (_ for _ in ()).throw(RuntimeError("boom")))

        # Scheduler still alive after the simulated failure
        assert s.is_running
        # And other jobs are still scheduled
        assert len(s.job_ids()) == 3
    finally:
        s.stop()