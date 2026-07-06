"""Telegram notifier: outbound alerts + inbound command routing.

Design:
  - Outbound: scheduler jobs (research, stage check, weekly rollup) call
    `notifier.send_research_alerts(notes)`, `send_stage_alert(...)`,
    `send_weekly_summary(...)`. Each helper formats text + calls the
    low-level transport.
  - Inbound: a Telegram webhook hits POST /telegram/webhook. The route
    parses the command, fetches data from DB (via FastAPI dependency),
    asks the notifier to render the response, then `send_message`s back
    to the originating chat.
  - Graceful degradation: if TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is
    missing (or TELEGRAM_ENABLED=false), the notifier becomes a logger —
    every send becomes an `INFO` line. Tests + dev environments benefit;
    ops can verify the pipeline without bot setup.

Transport:
  - httpx.Client direct to Telegram Bot API. No SDK dependency to keep
    the dep footprint small (httpx is already used by the LLM client).

Security notes (TODO for production hardening):
  - Webhook does not verify the X-Telegram-Bot-Api-Secret-Token header.
    In production, set TELEGRAM_WEBHOOK_SECRET and validate every update.
  - No per-chat authorization: anyone who knows the webhook URL can
    trigger commands. Add an allowlist of chat_ids for /status if exposed
    beyond a private bot chat.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Mapping, Optional, Sequence

import httpx

if TYPE_CHECKING:
    from app.db.models import ReflectionRow, ResearchNoteRow, StrategyStageRow

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"


# --- Config ---------------------------------------------------------------

@dataclass(frozen=True)
class NotifierConfig:
    """All knobs in one place. `enabled=False` → log-only mode."""
    token: Optional[str]
    chat_id: Optional[str]
    enabled: bool
    timeout: float = 10.0

    @classmethod
    def from_env(cls) -> "NotifierConfig":
        token = os.environ.get("TELEGRAM_BOT_TOKEN") or None
        chat_id = os.environ.get("TELEGRAM_CHAT_ID") or None
        explicit_disable = not _env_bool("TELEGRAM_ENABLED", default=True)
        enabled = (not explicit_disable) and bool(token and chat_id)
        return cls(token=token, chat_id=chat_id, enabled=enabled)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# --- Notifier -------------------------------------------------------------

class TelegramNotifier:
    """Outbound transport + message formatting for Telegram."""

    def __init__(self, config: NotifierConfig):
        self._config = config
        self._client: Optional[httpx.Client] = None

    # Properties ----------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    @property
    def default_chat_id(self) -> Optional[str]:
        return self._config.chat_id

    # Transport -----------------------------------------------------------

    def _ensure_client(self) -> Optional[httpx.Client]:
        if not self._config.enabled:
            return None
        if self._client is None:
            self._client = httpx.Client(timeout=self._config.timeout)
        return self._client

    def send_message(self, text: str, *, chat_id: Optional[str] = None) -> bool:
        """Send a plain-text message to `chat_id` (or the default).

        Returns True on success OR log-only dispatch. Returns False only
        when the HTTP call failed — caller decides whether to retry.
        """
        target = chat_id or self._config.chat_id
        if not self._config.enabled:
            logger.info("telegram (log-only) → %s: %s", target or "<no-chat>", text[:200])
            return True
        if not target:
            logger.warning("telegram enabled but no target chat_id; dropping message")
            return False
        client = self._ensure_client()
        assert client is not None
        url = f"{TELEGRAM_API_BASE}/bot{self._config.token}/sendMessage"
        payload = {
            "chat_id": target,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }
        try:
            resp = client.post(url, json=payload)
            resp.raise_for_status()
            return True
        except Exception:
            logger.exception("telegram send_message failed (chat_id=%s)", target)
            return False

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    # --- Outbound helpers (scheduler-facing) -----------------------------

    def send_research_alerts(
        self,
        notes: "Sequence[ResearchNoteRow]",
        *,
        chat_id: Optional[str] = None,
    ) -> int:
        """One message per high-severity note. Returns count of messages dispatched."""
        sent = 0
        for n in notes:
            text = self._format_research_note(n)
            if self.send_message(text, chat_id=chat_id):
                sent += 1
        return sent

    def send_stage_alert(
        self,
        *,
        strategy: str,
        current_stage: str,
        recommendation: str,
        rationale: str,
        next_stage: Optional[str] = None,
        chat_id: Optional[str] = None,
    ) -> bool:
        text = self._format_stage_alert(
            strategy=strategy,
            current_stage=current_stage,
            recommendation=recommendation,
            rationale=rationale,
            next_stage=next_stage,
        )
        return self.send_message(text, chat_id=chat_id)

    def send_weekly_summary(
        self,
        by_strategy: "Mapping[str, int]",
        *,
        chat_id: Optional[str] = None,
    ) -> bool:
        text = self._format_weekly_summary(by_strategy)
        return self.send_message(text, chat_id=chat_id)

    # --- Formatters (pure; reusable by inbound /status command) ---------

    @staticmethod
    def _format_research_note(n: "ResearchNoteRow") -> str:
        return (
            f"🚨 *High-severity research* ({n.severity}/5)\n"
            f"*{n.asset}* — _{n.event_type}_\n"
            f"{n.summary}\n"
            f"[source]({n.source_url})"
        )

    @staticmethod
    def _format_stage_alert(
        *,
        strategy: str,
        current_stage: str,
        recommendation: str,
        rationale: str,
        next_stage: Optional[str],
    ) -> str:
        if recommendation == "promote":
            verb = "🟢 PROMOTE"
        elif recommendation == "demote":
            verb = "🔴 DEMOTE"
        else:
            verb = "🟡 HOLD"
        lines = [
            f"*{verb}* — `{strategy}`",
            f"Current stage: `{current_stage}`",
            f"Rationale: {rationale}",
        ]
        if next_stage:
            lines.append(f"Next: `{next_stage}`")
        return "\n".join(lines)

    @staticmethod
    def _format_weekly_summary(by_strategy: "Mapping[str, int]") -> str:
        if not by_strategy:
            return "📊 *Weekly Sentinel summary*\n\nNo closed trades this week."
        lines = ["📊 *Weekly Sentinel summary*", ""]
        for strat, count in sorted(by_strategy.items()):
            lines.append(f"• `{strat}`: {count} reflections")
        return "\n".join(lines)

    @staticmethod
    def format_help() -> str:
        return (
            "*Sentinel commands*\n\n"
            "/status — list all strategies + current stage\n"
            "/status `<name>` — detail one strategy (stage, last reflections)\n"
            "/help — show this help\n"
        )

    @staticmethod
    def format_status_overview(stages: "Sequence[StrategyStageRow]") -> str:
        if not stages:
            return "📋 *Strategy status*\n\nNo strategies registered yet."
        lines = ["📋 *Strategy status*", ""]
        for row in stages:
            lines.append(
                f"• `{row.strategy}` — `{row.stage}` "
                f"(entered {row.entered_at.strftime('%Y-%m-%d')}, "
                f"{row.trade_count} trades)"
            )
        return "\n".join(lines)

    @staticmethod
    def format_status_detail(
        *,
        stage_row: "StrategyStageRow",
        recent_reflections: "Sequence[ReflectionRow]",
    ) -> str:
        lines = [
            f"📋 *`{stage_row.strategy}`* — `{stage_row.stage}`",
            f"Entered: {stage_row.entered_at.strftime('%Y-%m-%d %H:%M UTC')}",
            f"Trades: {stage_row.trade_count}  |  Max DD: {stage_row.max_observed_drawdown_pct:.1f}%",
        ]
        if recent_reflections:
            lines.append("")
            lines.append(f"_Last {len(recent_reflections)} reflection(s):_")
            for r in recent_reflections[:3]:
                when = r.created_at.strftime("%m-%d")
                lesson = (r.lesson or "").replace("\n", " ")
                if len(lesson) > 120:
                    lesson = lesson[:117] + "..."
                lines.append(f"• [{when}] {lesson}")
        return "\n".join(lines)