"""FastAPI app entrypoint + routes.

Routes mirror docs/system/02-design.md §2.2 modules:
  POST /audit/veto        → veto module (rule + LLM fail-open)
  POST /research/note     → research ingest (manual or auto)
  POST /reflection        → reflection writer
  POST /strategy/register → register strategy at stage
  POST /strategy/check    → check upgrade criteria
  GET  /strategy/{name}/stage → current stage snapshot
  GET  /veto               → compact veto endpoint for the strategy side
  POST /telegram/webhook   → inbound Telegram commands (/status, /help)
  GET  /healthz            → liveness probe

All endpoints accept/return JSON with Pydantic validation. LLM-dependent
endpoints may return 503 on LLMUnavailable (caller can fall back).

Background scheduler (see app/scheduler.py) and Telegram notifier
(see app/notifier.py) are wired via the lifespan context manager so
they start on app startup and stop cleanly on shutdown.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

import os

from fastapi import Depends, FastAPI, Header, HTTPException, status
from sqlalchemy.orm import Session

from app import api_schemas as schemas
from app import __version__
from app.db import get_session
from app.deps import (
    get_db,
    get_llm_client,
    get_notifier,
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
from app.modules.veto import MarketContext, TradeSignal, audit, check_rules
from app.notifier import TelegramNotifier

logger = logging.getLogger(__name__)


# --- Lifespan: scheduler lifecycle ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start the background scheduler + Telegram notifier on startup, stop on shutdown.

    The scheduler is opt-out via SCHEDULER_ENABLED=false. The notifier
    always exists — if TELEGRAM_BOT_TOKEN/CHAT_ID are missing it falls
    back to log-only mode (every send becomes an INFO log line).
    """
    from app.llm import ResearchExtractor, StructuredExtractor
    from app.modules.research import CoinGeckoEventsSource, ResearchIngester
    from app.notifier import NotifierConfig, TelegramNotifier
    from app.scheduler import SchedulerConfig, SentinelScheduler

    from app.deps import validate_required_secrets

    # Fail fast: refuse to start a misconfigured prod deploy (no real LLM key)
    # rather than running blind on the dev fake-key fallback.
    validate_required_secrets()

    config = SchedulerConfig.from_env()
    notifier = TelegramNotifier(NotifierConfig.from_env())
    app.state.notifier = notifier

    ingester = ResearchIngester(
        source=CoinGeckoEventsSource(),
        extractor=ResearchExtractor(StructuredExtractor(get_llm_client())),
        session_factory=get_session,
    )
    scheduler = SentinelScheduler(
        config,
        ingester=ingester,
        session_factory=get_session,
        notifier=notifier,
    )
    scheduler.start()
    app.state.scheduler = scheduler
    try:
        yield
    finally:
        scheduler.stop()
        notifier.close()


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
) -> schemas.StrategyVetoResponse:
    """Compact veto endpoint for live strategy use.

    Contract (matches `strategies.veto_gate.check_veto` and the deployed
    `S1TrendFollow.py`):
      GET /veto?strategy=S1TrendFollow&pair=BTC/USDT
        → {"decision": "PASS" | "VETO", "reason": "<short string>"}

    Runs ONLY the deterministic rule layer (fast, <200ms) — it does NOT call
    the LLM. The strategy side calls this with a tight (~3s) timeout and
    fails-open on any error/timeout, so a synchronous deep-tier LLM call here
    would always time out and be dead weight. The full rule+LLM audit lives
    on POST /audit/veto (internal / future async use).

    The strategy side treats the AI service as an additional safety net and
    defaults to PASS on any error or timeout — never blocks trading due to
    AI service failure (ADR-002 fail-open).
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
    vetoed, reason = check_rules(signal, context)
    source = "rule" if vetoed else "rules_passed"
    insert_veto_record(
        db,
        strategy=strategy,
        pair=pair,
        veto=vetoed,
        reason=reason,
        source=source,
    )
    return schemas.StrategyVetoResponse(
        decision="VETO" if vetoed else "PASS",
        reason=reason,
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
    writer = ReflectionWriter(extractor, get_session)  # fresh-session factory (RB.3)
    try:
        reflection = writer.record(ctx)
    except LLMUnavailable as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"LLM unavailable: {e}",
        )

    from app.db.repository import get_reflection_by_trade_id
    row = get_reflection_by_trade_id(db, ctx.trade_id, body.strategy)
    return schemas.ReflectionResponse(
        id=row.id if row else 0,
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


# --- Trade close webhook (P2.5) ---

@app.post(
    "/trade-close",
    response_model=schemas.TradeCloseResponse,
    status_code=status.HTTP_200_OK,
)
def trade_close(
    body: schemas.TradeCloseRequest,
    db: Session = Depends(get_db),
    extractor: ReflectionExtractor = Depends(get_reflection_extractor),
):
    """Receive freqtrade's webhook when a trade exits.

    freqtrade emits one EXIT_FILL per fill, so a multi-fill exit produces
    several webhooks. Only the final fill (`is_final_exit=true`) triggers
    a reflection; partial fills return `skipped=true` so freqtrade sees a
    clean 200 in its logs but we don't burn LLM calls on them.

    Idempotency: if a reflection for this trade_id already exists (e.g.
    freqtrade retried after a network blip, or someone re-ran a backtest
    against a replayed trades DB), we short-circuit and return
    `skipped=true` with reason "already_reflected".

    LLM failures return 503 — freqtrade logs the error, but its own
    trading flow is unaffected (webhook delivery is best-effort in
    freqtrade).
    """
    from app.db.repository import get_reflection_by_trade_id

    # 1. Idempotency: skip if we already have a reflection for this trade_id
    existing = get_reflection_by_trade_id(db, str(body.trade_id), body.strategy)
    if existing is not None:
        return schemas.TradeCloseResponse(
            status="skipped",
            trade_id=body.trade_id,
            reason="already_reflected",
            reflection_id=existing.id,
        )

    # 2. Skip partial fills — only the final fill triggers reflection
    if not body.is_final_exit or body.sub_trade:
        return schemas.TradeCloseResponse(
            status="skipped",
            trade_id=body.trade_id,
            reason="partial_fill",
        )

    # 3. Build TradeContext for the reflection writer
    hold_hours = max(
        0.001,
        (body.close_date - body.open_date).total_seconds() / 3600.0,
    )
    signal_snapshot = {
        "enter_tag": body.enter_tag,
        "exit_reason": body.exit_reason,
        "stake_amount": body.stake_amount,
        "stake_currency": body.stake_currency,
        "profit_amount": body.profit_amount,
        "direction": body.direction,
        **body.extra,
    }
    ctx = TradeContext(
        trade_id=str(body.trade_id),
        strategy=body.strategy,
        pair=body.pair,
        side=body.side,
        entry_price=body.open_rate,
        exit_price=body.close_rate,
        profit_pct=body.profit_ratio,
        hold_duration_hours=hold_hours,
        signal_snapshot=signal_snapshot,
        closed_at=body.close_date,
    )

    # 4. Run reflection writer (LLM + persist). 503 propagates on LLM failure.
    writer = ReflectionWriter(extractor, get_session)  # fresh-session factory (RB.3)
    try:
        writer.record(ctx)
    except LLMUnavailable as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"LLM unavailable: {e}",
        )

    # 5. Re-query the persisted row to surface its DB id to the caller.
    row = get_reflection_by_trade_id(db, str(body.trade_id), body.strategy)
    return schemas.TradeCloseResponse(
        status="recorded",
        trade_id=body.trade_id,
        reflection_id=row.id if row else None,
    )


# --- Telegram webhook ---

@app.post("/telegram/webhook")
def telegram_webhook(
    update: dict,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
    notifier: TelegramNotifier = Depends(get_notifier),
) -> dict:
    """Inbound Telegram Update → route command → reply.

    Always returns 200 (Telegram retries on non-2xx). Non-command
    messages and updates without text are ignored.

    Source verification: when TELEGRAM_WEBHOOK_SECRET is set (production),
    Telegram sends header `X-Telegram-Bot-Api-Secret-Token: <secret>` on
    every callback (configured via setWebhook's `secret_token`). We drop
    any update whose header doesn't match — silently, with a 200, so a
    probe learns nothing and Telegram doesn't retry. When the secret is
    unset (local/dev) we log a warning and proceed unverified.

    Supported commands:
      /status           → list all strategies + current stage
      /status <name>    → detail one strategy (stage + last reflections)
      /help, /start     → command list
    """
    expected = os.environ.get("TELEGRAM_WEBHOOK_SECRET")
    if expected:
        if x_telegram_bot_api_secret_token != expected:
            logger.warning(
                "telegram webhook: secret token mismatch — dropping update (silent 200)"
            )
            return {"ok": True}
    else:
        logger.warning(
            "TELEGRAM_WEBHOOK_SECRET not set — webhook source not verified (dev only)"
        )

    msg = update.get("message") or {}
    text = (msg.get("text") or "").strip()
    chat_id_raw = msg.get("chat", {}).get("id")
    chat_id = str(chat_id_raw) if chat_id_raw is not None else None

    if not text or chat_id is None or not text.startswith("/"):
        return {"ok": True}

    cmd, _, args = text.partition(" ")
    cmd = cmd.split("@")[0]  # strip @botname suffix if present

    response_text: str | None = None

    if cmd in ("/help", "/start"):
        response_text = TelegramNotifier.format_help()
    elif cmd == "/status":
        target = args.strip() or None
        if target is None:
            from app.db.repository import all_strategy_stages
            stages = all_strategy_stages(db)
            response_text = TelegramNotifier.format_status_overview(stages)
        else:
            from app.db.repository import (
                get_strategy_stage,
                reflections_for_strategy,
            )
            row = get_strategy_stage(db, target)
            if row is None:
                response_text = f"❌ Strategy `{target}` is not registered."
            else:
                recent = reflections_for_strategy(db, target, limit=3)
                response_text = TelegramNotifier.format_status_detail(
                    stage_row=row, recent_reflections=recent,
                )
    else:
        response_text = f"Unknown command `{cmd}`. Try /help."

    if response_text is not None:
        notifier.send_message(response_text, chat_id=chat_id)

    return {"ok": True}