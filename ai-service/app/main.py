"""FastAPI app entrypoint + routes.

Routes mirror docs/system/02-design.md §2.2 modules:
  POST /audit/veto        → veto module (rule + LLM fail-open)
  POST /research/note     → research ingest (manual or auto)
  POST /reflection        → reflection writer
  POST /strategy/register → register strategy at stage
  POST /strategy/check    → check upgrade criteria
  GET  /strategy/{name}/stage → current stage snapshot
  GET  /healthz           → liveness probe

All endpoints accept/return JSON with Pydantic validation. LLM-dependent
endpoints may return 503 on LLMUnavailable (caller can fall back).
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, status
from sqlalchemy.orm import Session

from app import api_schemas as schemas
from app import __version__
from app.deps import (
    get_db,
    get_llm_client,
    get_reflection_extractor,
    get_research_extractor,
    get_veto_extractor,
)
from app.db.repository import (
    get_strategy_stage,
    insert_reflection,
    insert_research_note,
    insert_veto_record,
)
from app.llm import LLMClient, LLMUnavailable
from app.llm import ReflectionExtractor, ResearchExtractor, VetoExtractor
from app.modules.reflection import ReflectionWriter, TradeContext
from app.modules.stages import (
    apply_recommendation,
    check_stage_upgrade,
    register_strategy,
)
from app.modules.veto import MarketContext, TradeSignal, audit

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Sentinel AI Service",
    version=__version__,
    description="Risk audit, research, reflection, and stage tracking for Sentinel trading system.",
)


# --- Health ---

@app.get("/healthz", response_model=schemas.HealthResponse)
def healthz() -> schemas.HealthResponse:
    return schemas.HealthResponse(status="ok")


# --- Veto ---

@app.post("/audit/veto", response_model=schemas.VetoResponse)
def audit_veto(
    body: schemas.VetoRequest,
    db: Session = Depends(get_db),
    veto_extractor: VetoExtractor = Depends(get_veto_extractor),
) -> schemas.VetoResponse:
    """Returns {veto, reason, source}. Never blocks on LLM — fail-open per ADR-002."""
    # Build domain objects
    signal = TradeSignal(
        strategy=body.strategy,
        pair=body.pair,
        side=body.side,
        stake_pct=body.stake_pct,
    )
    market_ctx = MarketContext(
        recent_high_severity_events=body.context.recent_high_severity_events,
        current_total_exposure_pct=body.context.current_total_exposure_pct,
        max_exposure_pct=body.context.max_exposure_pct,
        upcoming_event_window_minutes=body.context.upcoming_event_window_minutes,
    )
    result = audit(signal, market_ctx, veto_extractor)

    # Persist for audit trail
    insert_veto_record(
        db,
        strategy=body.strategy,
        pair=body.pair,
        veto=result.veto,
        reason=result.reason,
        source=result.source,
    )
    return schemas.VetoResponse(
        veto=result.veto, reason=result.reason, source=result.source
    )


# --- Research ---

@app.post(
    "/research/note",
    response_model=schemas.ResearchResponse,
    status_code=status.HTTP_201_CREATED,
)
def submit_research_note(
    body: schemas.ResearchRequest,
    db: Session = Depends(get_db),
) -> schemas.ResearchResponse:
    """Direct submission. Auto-ingest from CoinGecko is a scheduled job (out of band)."""
    row = insert_research_note(
        db,
        asset=body.asset,
        event_type=body.event_type,
        severity=body.severity,
        summary=body.summary,
        source_url=body.source_url,
        published_at=body.published_at,
    )
    return schemas.ResearchResponse(
        id=row.id,
        asset=row.asset,
        event_type=row.event_type,
        severity=row.severity,
        summary=row.summary,
        source_url=row.source_url,
        published_at=row.published_at,
        created_at=row.created_at,
    )


# --- Reflection ---

@app.post(
    "/reflection",
    response_model=schemas.ReflectionResponse,
    status_code=status.HTTP_201_CREATED,
)
def submit_reflection(
    body: schemas.ReflectionRequest,
    db: Session = Depends(get_db),
    extractor: ReflectionExtractor = Depends(get_reflection_extractor),
):
    """Generate an LLM reflection for a closed trade and persist it.

    Returns 503 if LLM is unavailable (caller can retry or queue).
    """
    ctx = TradeContext(
        trade_id=body.trade_id,
        strategy=body.strategy,
        pair=body.pair,
        side=body.side,
        entry_price=body.entry_price,
        exit_price=body.exit_price,
        profit_pct=body.profit_pct,
        hold_duration_hours=body.hold_duration_hours,
        signal_snapshot=body.signal_snapshot,
        closed_at=body.closed_at,
    )
    writer = ReflectionWriter(extractor, lambda: db)  # session factory for the writer
    try:
        reflection = writer.record(ctx)
    except LLMUnavailable as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"LLM unavailable: {e}",
        )

    return schemas.ReflectionResponse(
        id=0,  # writer inserted but didn't return id; could refactor
        trade_id=reflection.trade_id,
        strategy=body.strategy,
        what_worked=reflection.what_worked,
        what_failed=reflection.what_failed,
        lesson=reflection.lesson,
        confidence=reflection.confidence,
        created_at=datetime.utcnow(),
    )


# --- Stages ---

@app.post("/strategy/register", status_code=status.HTTP_201_CREATED)
def strategy_register(body: schemas.StageRegisterRequest, db: Session = Depends(get_db)):
    register_strategy(
        db,
        body.strategy,
        initial_stage=body.initial_stage,
        approved_by=body.approved_by,
    )
    return {"status": "registered", "strategy": body.strategy, "stage": body.initial_stage}


@app.post("/strategy/check", response_model=schemas.StageReportResponse)
def strategy_check(body: schemas.StageCheckRequest, db: Session = Depends(get_db)):
    report = check_stage_upgrade(
        db,
        body.strategy,
        observed_drawdown_pct=body.observed_drawdown_pct,
        trade_count=body.trade_count,
    )
    if report is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"strategy '{body.strategy}' not registered",
        )
    return schemas.StageReportResponse(
        strategy=report.strategy,
        current_stage=report.current_stage,
        days_in_stage=report.days_in_stage,
        trade_count=report.trade_count,
        observed_drawdown_pct=report.observed_drawdown_pct,
        recommendation=report.recommendation,
        rationale=report.rationale,
        next_stage=report.next_stage,
    )


@app.get("/strategy/{name}/stage", response_model=schemas.StageReportResponse)
def strategy_get(name: str, db: Session = Depends(get_db)):
    report = check_stage_upgrade(db, name)
    if report is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"strategy '{name}' not registered",
        )
    return schemas.StageReportResponse(
        strategy=report.strategy,
        current_stage=report.current_stage,
        days_in_stage=report.days_in_stage,
        trade_count=report.trade_count,
        observed_drawdown_pct=report.observed_drawdown_pct,
        recommendation=report.recommendation,
        rationale=report.rationale,
        next_stage=report.next_stage,
    )