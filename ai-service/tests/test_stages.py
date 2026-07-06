"""Tests for the stage tracker (ADR-005 state machine)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base
from app.db.repository import get_strategy_stage
from app.modules.stages import (
    CRITERIA,
    StageReport,
    apply_recommendation,
    check_stage_upgrade,
    register_strategy,
)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    s = SessionLocal()
    yield s
    s.close()
    engine.dispose()


def _backdate_entered_at(session, strategy: str, days_ago: int):
    """Manipulate the entered_at field to simulate N days in stage."""
    row = get_strategy_stage(session, strategy)
    row.entered_at = datetime.now(timezone.utc) - timedelta(days=days_ago)
    session.commit()


class TestRegisterStrategy:
    def test_creates_initial_record(self, session):
        register_strategy(session, "S1TrendFollow", initial_stage="backtest")
        row = get_strategy_stage(session, "S1TrendFollow")
        assert row is not None
        assert row.stage == "backtest"
        assert row.approved_by == "user"

    def test_invalid_stage_raises(self, session):
        with pytest.raises(ValueError, match="Invalid stage"):
            register_strategy(session, "S1", initial_stage="nonexistent")  # type: ignore[arg-type]


class TestCheckStageUpgrade:
    def test_unknown_strategy_returns_none(self, session):
        assert check_stage_upgrade(session, "Unknown") is None

    def test_dry_run_with_insufficient_days_recommends_hold(self, session):
        register_strategy(session, "S1", initial_stage="dry_run")
        # Just registered — 0 days
        report = check_stage_upgrade(
            session, "S1", observed_drawdown_pct=5.0, trade_count=30
        )
        assert report is not None
        assert report.recommendation == "hold"
        assert report.next_stage is None

    def test_dry_run_with_all_criteria_met_recommends_promote(self, session):
        register_strategy(session, "S1", initial_stage="dry_run")
        _backdate_entered_at(session, "S1", days_ago=29)  # just over 28-day threshold
        report = check_stage_upgrade(
            session, "S1", observed_drawdown_pct=10.0, trade_count=35
        )
        assert report is not None
        assert report.recommendation == "promote"
        assert report.next_stage == "live_small"

    def test_dry_run_with_high_drawdown_recommends_hold(self, session):
        register_strategy(session, "S1", initial_stage="dry_run")
        _backdate_entered_at(session, "S1", days_ago=29)
        # Drawdown 16% > 15% threshold
        report = check_stage_upgrade(
            session, "S1", observed_drawdown_pct=16.0, trade_count=35
        )
        assert report is not None
        assert report.recommendation == "hold"

    def test_extreme_drawdown_triggers_demote(self, session):
        register_strategy(session, "S1", initial_stage="dry_run")
        # Drawdown > 1.5x threshold (15% * 1.5 = 22.5%) → demote
        report = check_stage_upgrade(
            session, "S1", observed_drawdown_pct=25.0, trade_count=35
        )
        assert report is not None
        assert report.recommendation == "demote"
        assert report.next_stage == "backtest"

    def test_live_scaled_is_terminal(self, session):
        register_strategy(session, "S1", initial_stage="live_scaled")
        report = check_stage_upgrade(
            session, "S1", observed_drawdown_pct=5.0, trade_count=200
        )
        assert report is not None
        assert report.recommendation == "hold"
        assert "terminal" in report.rationale

    def test_criteria_dict_includes_all_adsmitted_stages(self):
        # ADR-005: backtest→dry_run→live_small→live_scaled
        # CRITERIA covers the stages that CAN be promoted FROM (live_scaled excluded)
        assert set(CRITERIA.keys()) == {"backtest", "dry_run", "live_small"}


class TestApplyRecommendation:
    def test_promote_advances_stage(self, session):
        register_strategy(session, "S1", initial_stage="dry_run")
        _backdate_entered_at(session, "S1", days_ago=29)
        report = check_stage_upgrade(
            session, "S1", observed_drawdown_pct=10.0, trade_count=35
        )
        assert report is not None
        apply_recommendation(session, report, approved_by="human")
        row = get_strategy_stage(session, "S1")
        assert row.stage == "live_small"
        assert row.approved_by == "human"

    def test_demote_goes_back_one_stage(self, session):
        register_strategy(session, "S1", initial_stage="live_small")
        report = check_stage_upgrade(
            session, "S1", observed_drawdown_pct=20.0, trade_count=50
        )
        assert report is not None
        assert report.recommendation == "demote"
        apply_recommendation(session, report, approved_by="human")
        row = get_strategy_stage(session, "S1")
        assert row.stage == "dry_run"

    def test_hold_is_no_op(self, session):
        register_strategy(session, "S1", initial_stage="dry_run")
        before = get_strategy_stage(session, "S1").entered_at
        report = check_stage_upgrade(
            session, "S1", observed_drawdown_pct=5.0, trade_count=10
        )
        assert report is not None
        apply_recommendation(session, report)
        after = get_strategy_stage(session, "S1")
        assert after.stage == "dry_run"
        assert after.entered_at == before