"""Background scheduler for the AI service.

Wires three periodic jobs (per docs/system/03-tasks.md Phase 2):
  1. Daily research ingestion (default 09:00 UTC) — fetch raw events from
     CoinGecko, summarize via LLM, persist research_notes rows.
  2. Daily stage check (default 09:30 UTC) — iterate every registered
     strategy, run check_stage_upgrade, log recommendations.
  3. Weekly reflection roll-up (Sunday 10:00 UTC) — summarise this week's
     trade reflections (placeholder for P2.5 weekly report; expanded later
     when the per-trade webhook lands).

Failure semantics:
  - Each job runs inside `safe_run()` so one job's failure is logged and
    isolated from siblings (no cascade, no scheduler crash).
  - Scheduler startup failure is propagated so the app refuses to start in
    a half-broken state.

Configuration (env vars, all optional):
  - SCHEDULER_ENABLED         (default: true outside pytest)
  - SCHEDULER_RESEARCH_CRON   (default: "0 9 * * *")      — daily 09:00 UTC
  - SCHEDULER_STAGE_CRON      (default: "30 9 * * *")     — daily 09:30 UTC
  - SCHEDULER_WEEKLY_CRON     (default: "0 10 * * 0")     — Sunday 10:00 UTC
  - SCHEDULER_VETO_CRON       (default: "*/15 * * * *")   — every 15 min (LLM veto precompute)

The scheduler is intentionally thin — it composes existing module
functions (ResearchIngester, check_stage_upgrade) so each job stays
small and unit-testable.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)


# --- Public job functions (testable in isolation) ---

def safe_run(name: str, fn: Callable[[], None]) -> None:
    """Wrap a job body so exceptions are logged but never propagated.

    Scheduler exceptions would otherwise silently kill the trigger; APScheduler
    has its own misfire grace, but we want a clean log line every time.
    """
    try:
        fn()
    except Exception:
        logger.exception("scheduled job %s failed", name)


def run_daily_research(ingester, notifier=None) -> None:
    """One ingestion pass, then notify Telegram for any severity>=4 notes.

    Logs but never raises. Notifier is optional — if absent or in log-only
    mode, alerts go to the application log instead.
    """
    from datetime import datetime, timedelta, timezone

    count = ingester.run_once()
    logger.info("daily research job persisted %d notes", count)

    if notifier is None:
        return

    # Query notes created in the last hour — these are the ones we just
    # ingested (daily cron runs hours apart so 1h captures the new batch
    # without flooding with yesterday's notes).
    from app.db.models import ResearchNoteRow
    from sqlalchemy import select

    cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
    with ingester._session_factory() as session:  # type: ignore[attr-defined]
        stmt = (
            select(ResearchNoteRow)
            .where(ResearchNoteRow.severity >= 4)
            .where(ResearchNoteRow.created_at >= cutoff)
            .order_by(ResearchNoteRow.created_at.desc())
        )
        notes = session.execute(stmt).scalars().all()

    sent = notifier.send_research_alerts(notes)
    if sent:
        logger.info("daily research job sent %d high-severity alerts", sent)


def run_daily_stage_check(session_factory, notifier=None) -> None:
    """Check every registered strategy and log + notify on promote/demote."""
    # Local imports to keep the module importable without DB
    from app.db import all_strategy_stages
    from app.modules.stages import check_stage_upgrade

    with session_factory() as session:
        rows = all_strategy_stages(session)
        for row in rows:
            report = check_stage_upgrade(session, row.strategy)
            if report is None:
                continue
            if report.recommendation == "promote":
                level = "PROMOTE"
            elif report.recommendation == "demote":
                level = "DEMOTE"
            else:
                level = "HOLD"
            logger.info(
                "stage-check %s: %s (day %d, dd=%.1f%%) — %s",
                report.strategy, report.current_stage, report.days_in_stage,
                report.observed_drawdown_pct, level,
            )
            # Only send actionable recommendations to Telegram
            if notifier is not None and report.recommendation in ("promote", "demote"):
                notifier.send_stage_alert(
                    strategy=report.strategy,
                    current_stage=report.current_stage,
                    recommendation=report.recommendation,
                    rationale=report.rationale,
                    next_stage=report.next_stage,
                )


def run_weekly_rollup(session_factory, notifier=None) -> None:
    """Sunday roll-up: count reflections per strategy, send Telegram summary."""
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import desc, select

    from app.db.models import ReflectionRow

    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    with session_factory() as session:
        stmt = (
            select(ReflectionRow)
            .where(ReflectionRow.created_at >= cutoff)
            .order_by(desc(ReflectionRow.created_at))
        )
        rows = session.execute(stmt).scalars().all()
    by_strategy: dict[str, int] = {}
    for r in rows:
        by_strategy[r.strategy] = by_strategy.get(r.strategy, 0) + 1
    if by_strategy:
        for strategy, count in by_strategy.items():
            logger.info("weekly rollup %s: %d reflections this week", strategy, count)
    else:
        logger.info("weekly rollup: no reflections this week")

    if notifier is not None:
        notifier.send_weekly_summary(by_strategy)


def run_llm_veto_precompute(session_factory, veto_extractor, notifier=None) -> None:
    """Async LLM devil's-advocate veto precompute (ADR-002, docs §2.2).

    Keeps GET /veto fast by moving the (slow, deep-tier) LLM veto OFF the request
    path: this job runs on a cron, evaluates the pairs the strategy has recently
    asked about, and writes any LLM veto to `veto_records` with source="llm".
    GET /veto then honors a fresh precomputed LLM veto without ever calling the
    LLM in-request.

    Fail-open per ADR-002: a per-pair LLM outage is logged and SKIPPED (we never
    write a VETO on doubt), and one pair's failure never aborts the rest.
    """
    from app.db.repository import (
        insert_veto_record,
        recent_high_severity_assets,
        recent_veto_query_pairs,
    )
    from app.llm import LLMUnavailable
    from app.modules.events import current_event_window_minutes
    from app.modules.veto import MarketContext, TradeSignal, llm_veto

    # Read the candidate set + shared rule-1 context once.
    with session_factory() as session:
        pairs = recent_veto_query_pairs(session)
        high_sev_assets = recent_high_severity_assets(session, since_hours=24)

    if not pairs:
        logger.info("llm veto precompute: no recent query pairs, nothing to do")
        return

    # Rule 3: derive the "major event window" once per job (fail-open to 0 on a
    # broken/missing calendar). Shared across all pairs for this run.
    event_window_min = current_event_window_minutes()

    written = 0
    for strategy, pair in pairs:
        try:
            # Build the same context GET /veto uses: rule-1 asset list from DB
            # state, exposure disabled (the AI service doesn't know the book).
            base_asset = pair.split("/")[0]
            context = MarketContext(
                recent_high_severity_events=(
                    [base_asset] if base_asset in high_sev_assets else []
                ),
                current_total_exposure_pct=0.0,
                max_exposure_pct=1.0,
                upcoming_event_window_minutes=event_window_min,
            )
            signal = TradeSignal(
                strategy=strategy, pair=pair, side="long", stake_pct=0.05,
            )
            decision = llm_veto(signal, context, veto_extractor)

            with session_factory() as session:
                insert_veto_record(
                    session,
                    strategy=strategy,
                    pair=pair,
                    veto=decision.veto,
                    reason=decision.reason,
                    source="llm",
                )
            written += 1
            if decision.veto:
                logger.info(
                    "llm veto precompute: VETO %s %s — %s",
                    strategy, pair, decision.reason,
                )
                if notifier is not None:
                    # Optional alert; a log line is the contract, keep it simple.
                    logger.info(
                        "llm veto precompute alert: %s %s vetoed", strategy, pair,
                    )
        except LLMUnavailable as exc:
            # Fail-open: skip this pair, do NOT write a VETO row.
            logger.warning(
                "llm veto precompute: LLM unavailable for %s %s, skipping: %s",
                strategy, pair, exc,
            )
            continue
        except Exception:  # noqa: BLE001 - one pair must not abort the rest
            logger.exception(
                "llm veto precompute: unexpected failure for %s %s", strategy, pair,
            )
            continue

    logger.info(
        "llm veto precompute: evaluated %d pairs, wrote %d records",
        len(pairs), written,
    )


# --- Scheduler wrapper ---

@dataclass(frozen=True)
class SchedulerConfig:
    """All schedule knobs in one place for easy env-driven config."""
    enabled: bool
    research_cron: str
    stage_cron: str
    weekly_cron: str
    veto_cron: str

    @classmethod
    def from_env(cls) -> "SchedulerConfig":
        return cls(
            enabled=_env_bool("SCHEDULER_ENABLED", default=True),
            research_cron=os.environ.get("SCHEDULER_RESEARCH_CRON", "0 9 * * *"),
            stage_cron=os.environ.get("SCHEDULER_STAGE_CRON", "30 9 * * *"),
            weekly_cron=os.environ.get("SCHEDULER_WEEKLY_CRON", "0 10 * * 0"),
            veto_cron=os.environ.get("SCHEDULER_VETO_CRON", "*/15 * * * *"),
        )


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


class SentinelScheduler:
    """Wraps APScheduler with Sentinel's three jobs and a clean lifecycle."""

    def __init__(
        self,
        config: SchedulerConfig,
        *,
        ingester,
        session_factory,
        notifier=None,
        veto_extractor=None,
    ):
        self._config = config
        self._ingester = ingester
        self._session_factory = session_factory
        self._notifier = notifier
        self._veto_extractor = veto_extractor
        self._scheduler: BackgroundScheduler | None = None

    def start(self) -> None:
        """Register jobs and start the background thread. Idempotent."""
        if not self._config.enabled:
            logger.info("scheduler disabled by config (SCHEDULER_ENABLED=false)")
            return
        if self._scheduler is not None:
            logger.warning("scheduler already started; ignoring second start()")
            return

        self._scheduler = BackgroundScheduler(daemon=True, timezone="UTC")

        # Daily research — coin source → LLM → research_notes → Telegram alerts
        self._scheduler.add_job(
            lambda: safe_run(
                "daily_research",
                lambda: run_daily_research(self._ingester, self._notifier),
            ),
            CronTrigger.from_crontab(self._config.research_cron, timezone="UTC"),
            id="daily_research",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

        # Daily stage check
        self._scheduler.add_job(
            lambda: safe_run(
                "daily_stage_check",
                lambda: run_daily_stage_check(self._session_factory, self._notifier),
            ),
            CronTrigger.from_crontab(self._config.stage_cron, timezone="UTC"),
            id="daily_stage_check",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

        # Weekly roll-up
        self._scheduler.add_job(
            lambda: safe_run(
                "weekly_rollup",
                lambda: run_weekly_rollup(self._session_factory, self._notifier),
            ),
            CronTrigger.from_crontab(self._config.weekly_cron, timezone="UTC"),
            id="weekly_rollup",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

        # Async LLM veto precompute — only when an extractor is wired (opt-in;
        # keeps existing scheduler tests, which pass no extractor, unchanged).
        if self._veto_extractor is not None:
            self._scheduler.add_job(
                lambda: safe_run(
                    "llm_veto_precompute",
                    lambda: run_llm_veto_precompute(
                        self._session_factory,
                        self._veto_extractor,
                        self._notifier,
                    ),
                ),
                CronTrigger.from_crontab(self._config.veto_cron, timezone="UTC"),
                id="llm_veto_precompute",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )

        self._scheduler.start()
        logger.info(
            "scheduler started: research=%s stage=%s weekly=%s veto=%s",
            self._config.research_cron, self._config.stage_cron,
            self._config.weekly_cron,
            self._config.veto_cron if self._veto_extractor is not None else "disabled",
        )

    def stop(self) -> None:
        """Stop the background thread. Safe to call even if never started."""
        if self._scheduler is None:
            return
        try:
            self._scheduler.shutdown(wait=False)
        except Exception:
            logger.exception("error during scheduler shutdown")
        finally:
            self._scheduler = None
            logger.info("scheduler stopped")

    # Test/inspection helpers -------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self._scheduler is not None and self._scheduler.running

    def job_ids(self) -> list[str]:
        if self._scheduler is None:
            return []
        return [job.id for job in self._scheduler.get_jobs()]