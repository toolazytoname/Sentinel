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


def run_daily_research(ingester) -> None:
    """One ingestion pass. Logs but never raises."""
    count = ingester.run_once()
    logger.info("daily research job persisted %d notes", count)


def run_daily_stage_check(session_factory) -> None:
    """Check every registered strategy and log the recommendation.

    For now: log-only (no Telegram yet — that's P2.8). Telegram wiring
    will subscribe to a future hook this function calls.
    """
    # Local imports to keep the module importable without DB
    from app.db import all_strategy_stages
    from app.modules.stages import check_stage_upgrade

    with session_factory() as session:
        rows = all_strategy_stages(session)
        for row in rows:
            report = check_stage_upgrade(session, row.strategy)
            if report is None:
                continue
            level = "PROMOTE" if report.recommendation == "promote" else (
                "DEMOTE" if report.recommendation == "demote" else "HOLD"
            )
            logger.info(
                "stage-check %s: %s (day %d, dd=%.1f%%) — %s",
                report.strategy, report.current_stage, report.days_in_stage,
                report.observed_drawdown_pct, level,
            )


def run_weekly_rollup(session_factory) -> None:
    """Sunday roll-up of trade reflections.

    Placeholder: counts this week's reflections per strategy and logs.
    Will grow into a proper weekly report with Telegram delivery once P2.5
    trade-close webhook is wired in.
    """
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


# --- Scheduler wrapper ---

@dataclass(frozen=True)
class SchedulerConfig:
    """All schedule knobs in one place for easy env-driven config."""
    enabled: bool
    research_cron: str
    stage_cron: str
    weekly_cron: str

    @classmethod
    def from_env(cls) -> "SchedulerConfig":
        return cls(
            enabled=_env_bool("SCHEDULER_ENABLED", default=True),
            research_cron=os.environ.get("SCHEDULER_RESEARCH_CRON", "0 9 * * *"),
            stage_cron=os.environ.get("SCHEDULER_STAGE_CRON", "30 9 * * *"),
            weekly_cron=os.environ.get("SCHEDULER_WEEKLY_CRON", "0 10 * * 0"),
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
    ):
        self._config = config
        self._ingester = ingester
        self._session_factory = session_factory
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

        # Daily research — coin source → LLM → research_notes
        self._scheduler.add_job(
            lambda: safe_run("daily_research", lambda: run_daily_research(self._ingester)),
            CronTrigger.from_crontab(self._config.research_cron, timezone="UTC"),
            id="daily_research",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

        # Daily stage check
        self._scheduler.add_job(
            lambda: safe_run("daily_stage_check", lambda: run_daily_stage_check(self._session_factory)),
            CronTrigger.from_crontab(self._config.stage_cron, timezone="UTC"),
            id="daily_stage_check",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

        # Weekly roll-up
        self._scheduler.add_job(
            lambda: safe_run("weekly_rollup", lambda: run_weekly_rollup(self._session_factory)),
            CronTrigger.from_crontab(self._config.weekly_cron, timezone="UTC"),
            id="weekly_rollup",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

        self._scheduler.start()
        logger.info(
            "scheduler started: research=%s stage=%s weekly=%s",
            self._config.research_cron, self._config.stage_cron, self._config.weekly_cron,
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