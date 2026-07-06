"""DB layer tests. Use in-memory SQLite for speed."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

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