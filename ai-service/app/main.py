"""FastAPI app entrypoint + routes.

Routes mirror docs/system/02-design.md §2.2 modules:
  POST /audit/veto        → veto module (rule + LLM fail-open)
  POST /research/note     → research ingest (manual or auto)
  POST /reflection        → reflection writer
  POST /strategy/register → register strategy at stage
  POST /strategy/check    → check upgrade criteria
  GET  /strategy/{name}/stage → current stage snapshot
  GET  /veto               → compact veto endpoint for the strategy side
  GET  /healthz           → liveness probe

All endpoints accept/return JSON with Pydantic validation. LLM-dependent
endpoints may return 503 on LLMUnavailable (caller can fall back).

Background scheduler (see app/scheduler.py) is wired via the lifespan
context manager so it starts on app startup and stops cleanly on shutdown.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, status
from sqlalchemy.orm import Session

from app import api_schemas as schemas
from app import __version__
from app.db import get_session
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
    recent_high_severity_assets,
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


# --- Lifespan: scheduler lifecycle ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start the background scheduler on startup, stop on shutdown.

    The scheduler is opt-out via SCHEDULER_ENABLED=false. In tests we leave
    it disabled (set via env in conftest). Real production runs leave it on.
    """
    from app.llm import ResearchExtractor, StructuredExtractor
    from app.modules.research import CoinGeckoEventsSource, ResearchIngester
    from app.scheduler import SchedulerConfig, SentinelScheduler

    config = SchedulerConfig.from_env()
    ingester = ResearchIngester(
        source=CoinGeckoEventsSource(),
        extractor=ResearchExtractor(StructuredExtractor(get_llm_client())),
        session_factory=get_session,
    )
    scheduler = SentinelScheduler(
        config,
        ingester=ingester,
        session_factory=get_session,
    )
    scheduler.start()
    app.state.scheduler = scheduler
    try:
        yield
    finally:
        scheduler.stop()


app = FastAPI(
    title="Sentinel AI Service",
    version=__version__,
    description="Risk audit, research, reflection, and stage tracking for Sentinel trading system.",
    lifespan=lifespan,
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


@app.get("/veto", response_model=schemas.StrategyVetoResponse)
def strategy_veto(
    strategy: str,
    pair: str,
    db: Session = Depends(get_db),
    veto_extractor: VetoExtractor = Depends(get_veto_extractor),
) -> schemas.StrategyVetoResponse:
    """Compact veto endpoint for live strategy use.

    Contract (matches `strategies.veto_gate.check_veto` and the deployed
    `S1TrendFollow.py`):
      GET /veto?strategy=S1TrendFollow&pair=BTC/USDT
        → {"decision": "PASS" | "VETO", "reason": "<short string>"}

    The strategy side treats the LLM/AI service as an additional safety net
    and defaults to PASS on any error or timeout — never blocks trading due
    to AI service failure (ADR-002 fail-open).
    """
    # Conservative defaults for signal-time veto calls. The strategy knows
    # nothing about the rest of the book; the AI service derives what it can
    # from DB state (recent high-severity events) and uses fixed safe limits.
    base_asset = pair.split("/")[0]
    high_sev_assets = recent_high_severity_assets(db, since_hours=24)
    context = MarketContext(
        recent_high_severity_events=[base_asset] if base_asset in high_sev_assets else [],
        current_total_exposure_pct=0.0,  # strategy doesn't know the book; rule 2 disabled
        max_exposure_pct=1.0,            # disabled (no real exposure info)
        upcoming_event_window_minutes=0,
    )
    signal = TradeSignal(
        strategy=strategy,
        pair=pair,
        side="long",
        stake_pct=0.05,
    )
    result = audit(signal, context, veto_extractor)
    insert_veto_record(
        db,
        strategy=strategy,
        pair=pair,
        veto=result.veto,
        reason=result.reason,
        source=result.source,
    )
    return schemas.StrategyVetoResponse(
        decision="VETO" if result.veto else "PASS",
        reason=result.reason,
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