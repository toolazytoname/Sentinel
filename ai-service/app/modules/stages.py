"""Strategy stage tracker per ADR-005.

ADR-005 state machine:
  backtest → dry_run(≥28d) → live_small(≥56d + ≥30 trades) → live_scaled

This module:
  1. Computes whether the current stage's criteria are met
  2. Outputs an upgrade/downgrade recommendation
  3. NEVER auto-promotes (per ADR-002: human approves via telegram)

The criteria thresholds are configurable in CRITERIA so future tuning
doesn't require code changes.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy.orm import Session

from app.db import STAGES, get_strategy_stage, upsert_strategy_stage
from app.db.models import StrategyStageRow

logger = logging.getLogger(__name__)

StageName = Literal["backtest", "dry_run", "live_small", "live_scaled"]


# Criteria for moving FROM one stage to the next (ADR-005)
@dataclass(frozen=True)
class StageCriteria:
    """Days + trade count + max_drawdown_pct required at the CURRENT stage
    before promoting to the NEXT stage.
    """
    min_days_in_stage: int
    min_trade_count: int
    max_allowed_drawdown_pct: float  # observed drawdown must be <= this


# Indexed by current stage name → requirements to LEAVE that stage
CRITERIA: dict[str, StageCriteria] = {
    "backtest": StageCriteria(
        min_days_in_stage=7,  # at least a week of meaningful backtests
        min_trade_count=100,
        max_allowed_drawdown_pct=20.0,
    ),
    "dry_run": StageCriteria(
        min_days_in_stage=28,
        min_trade_count=30,
        max_allowed_drawdown_pct=15.0,
    ),
    "live_small": StageCriteria(
        min_days_in_stage=56,
        min_trade_count=30,
        max_allowed_drawdown_pct=12.0,
    ),
    # "live_scaled" is terminal — no further upgrade
}


@dataclass(frozen=True)
class StageReport:
    """Output of an upgrade check."""
    strategy: str
    current_stage: str
    days_in_stage: int
    trade_count: int
    observed_drawdown_pct: float
    recommendation: Literal["promote", "hold", "demote"]
    rationale: str
    next_stage: str | None  # if promote, where to; else None


def check_stage_upgrade(
    session: Session,
    strategy: str,
    *,
    observed_drawdown_pct: float | None = None,
    trade_count: int | None = None,
) -> StageReport | None:
    """Inspect a strategy's current stage and recommend next action.

    If the strategy has no stage row, returns None (caller should call
    register_strategy first).
    """
    row = get_strategy_stage(session, strategy)
    if row is None:
        return None

    now = datetime.now(timezone.utc)
    # SQLite doesn't preserve timezone info — treat stored timestamps as UTC
    # (consistent with models._utcnow() which always uses UTC).
    entered_at = row.entered_at
    if entered_at.tzinfo is None:
        entered_at = entered_at.replace(tzinfo=timezone.utc)
    days_in_stage = (now - entered_at).days
    actual_drawdown = (
        observed_drawdown_pct if observed_drawdown_pct is not None
        else row.max_observed_drawdown_pct
    )
    actual_trades = trade_count if trade_count is not None else row.trade_count

    # Demote if observed drawdown exceeded
    current_criteria = CRITERIA.get(row.stage)
    if current_criteria and actual_drawdown > current_criteria.max_allowed_drawdown_pct * 1.5:
        prior_stage = _prior_stage(row.stage)
        rationale = (
            f"drawdown {actual_drawdown:.1f}% exceeds 1.5x threshold "
            f"({current_criteria.max_allowed_drawdown_pct * 1.5:.1f}%) — demote to {prior_stage}"
        )
        return StageReport(
            strategy=strategy,
            current_stage=row.stage,
            days_in_stage=days_in_stage,
            trade_count=actual_trades,
            observed_drawdown_pct=actual_drawdown,
            recommendation="demote",
            rationale=rationale,
            next_stage=prior_stage,
        )

    # Check promote
    if row.stage == "live_scaled":
        return StageReport(
            strategy=strategy,
            current_stage="live_scaled",
            days_in_stage=days_in_stage,
            trade_count=actual_trades,
            observed_drawdown_pct=actual_drawdown,
            recommendation="hold",
            rationale="terminal stage — no further upgrade",
            next_stage=None,
        )

    if current_criteria is None:
        return StageReport(
            strategy=strategy,
            current_stage=row.stage,
            days_in_stage=days_in_stage,
            trade_count=actual_trades,
            observed_drawdown_pct=actual_drawdown,
            recommendation="hold",
            rationale="no criteria configured for this stage",
            next_stage=None,
        )

    if (
        days_in_stage >= current_criteria.min_days_in_stage
        and actual_trades >= current_criteria.min_trade_count
        and actual_drawdown <= current_criteria.max_allowed_drawdown_pct
    ):
        next_stage = _next_stage(row.stage)
        return StageReport(
            strategy=strategy,
            current_stage=row.stage,
            days_in_stage=days_in_stage,
            trade_count=actual_trades,
            observed_drawdown_pct=actual_drawdown,
            recommendation="promote",
            rationale=(
                f"met criteria: days={days_in_stage}>={current_criteria.min_days_in_stage}, "
                f"trades={actual_trades}>={current_criteria.min_trade_count}, "
                f"drawdown={actual_drawdown:.1f}%<={current_criteria.max_allowed_drawdown_pct:.1f}%"
            ),
            next_stage=next_stage,
        )

    # Default: hold
    return StageReport(
        strategy=strategy,
        current_stage=row.stage,
        days_in_stage=days_in_stage,
        trade_count=actual_trades,
        observed_drawdown_pct=actual_drawdown,
        recommendation="hold",
        rationale=(
            f"criteria not met: need days>={current_criteria.min_days_in_stage}, "
            f"trades>={current_criteria.min_trade_count}, "
            f"drawdown<={current_criteria.max_allowed_drawdown_pct:.1f}%"
        ),
        next_stage=None,
    )


def register_strategy(
    session: Session,
    strategy: str,
    *,
    initial_stage: StageName = "backtest",
    approved_by: str = "user",
) -> StrategyStageRow:
    """Initial registration of a strategy at its starting stage."""
    if initial_stage not in STAGES:
        raise ValueError(f"Invalid stage: {initial_stage}")
    return upsert_strategy_stage(
        session,
        strategy=strategy,
        stage=initial_stage,
        criteria_snapshot={"initial": True},
        approved_by=approved_by,
    )


def apply_recommendation(session: Session, report: StageReport, *, approved_by: str = "user") -> StrategyStageRow:
    """Apply a stage report's recommendation to the strategy's stage.

    Per ADR-002: caller MUST confirm this is human-approved (not auto).
    Pass approved_by="user" only after the user has confirmed via Telegram.
    """
    if report.recommendation == "hold":
        # No-op: just return current row
        return get_strategy_stage(session, report.strategy)  # type: ignore[return-value]
    if report.recommendation == "promote":
        return upsert_strategy_stage(
            session,
            strategy=report.strategy,
            stage=report.next_stage or "live_scaled",
            criteria_snapshot={"promoted_from": report.current_stage},
            approved_by=approved_by,
        )
    if report.recommendation == "demote":
        return upsert_strategy_stage(
            session,
            strategy=report.strategy,
            stage=report.next_stage or "backtest",
            criteria_snapshot={"demoted_from": report.current_stage},
            approved_by=approved_by,
        )
    raise ValueError(f"Unknown recommendation: {report.recommendation}")


def _next_stage(current: str) -> str:
    order = list(STAGES)
    idx = order.index(current)
    return order[min(idx + 1, len(order) - 1)]


def _prior_stage(current: str) -> str:
    order = list(STAGES)
    idx = order.index(current)
    return order[max(idx - 1, 0)]