"""Pydantic request/response models for the HTTP API."""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


# --- Veto ---

class MarketContextPayload(BaseModel):
    recent_high_severity_events: list[str] = Field(default_factory=list)
    current_total_exposure_pct: float = 0.0
    max_exposure_pct: float = 0.60
    upcoming_event_window_minutes: int = 0


class VetoRequest(BaseModel):
    strategy: str
    pair: str
    side: Literal["long", "short"] = "long"
    stake_pct: float = Field(..., gt=0.0, le=1.0)
    context: MarketContextPayload = Field(default_factory=MarketContextPayload)


class VetoResponse(BaseModel):
    veto: bool
    reason: str
    source: Literal["rule", "llm", "llm_unavailable"]


class StrategyVetoResponse(BaseModel):
    """Compact response shape for the strategy-side GET /veto endpoint.

    Mirrors the contract that `strategies.veto_gate.check_veto` (and the
    deployed `S1TrendFollow.py`) expects: a `decision` field with PASS/VETO,
    plus an optional `reason` for logging. The AI service performs the audit
    internally and translates the detailed result into this shape.
    """
    decision: Literal["PASS", "VETO"]
    reason: str = ""


# --- Research ---

class ResearchRequest(BaseModel):
    """Manual research note submission (skipping the LLM extraction)."""
    asset: str = Field(..., min_length=1, max_length=32)
    event_type: Literal["regulatory", "technical", "partnership", "macro", "security", "other"]
    severity: int = Field(..., ge=1, le=5)
    summary: str = Field(..., min_length=10, max_length=500)
    source_url: str = Field(..., pattern=r"^https?://.+")
    published_at: str


class ResearchResponse(BaseModel):
    id: int
    asset: str
    event_type: str
    severity: int
    summary: str
    source_url: str
    published_at: str
    created_at: datetime


# --- Reflection ---

class ReflectionRequest(BaseModel):
    trade_id: str = Field(..., min_length=1)
    strategy: str
    pair: str
    side: Literal["long", "short"] = "long"
    entry_price: float = Field(..., gt=0)
    exit_price: float = Field(..., gt=0)
    profit_pct: float
    hold_duration_hours: float = Field(..., gt=0)
    signal_snapshot: dict = Field(default_factory=dict)
    closed_at: datetime


class ReflectionResponse(BaseModel):
    id: int
    trade_id: str
    strategy: str
    what_worked: str
    what_failed: str
    lesson: str
    confidence: float
    created_at: datetime


# --- Stages ---

class StageRegisterRequest(BaseModel):
    strategy: str = Field(..., min_length=1)
    initial_stage: Literal["backtest", "dry_run", "live_small", "live_scaled"] = "backtest"
    approved_by: str = "user"


class StageCheckRequest(BaseModel):
    strategy: str
    observed_drawdown_pct: Optional[float] = None
    trade_count: Optional[int] = None


class StageReportResponse(BaseModel):
    strategy: str
    current_stage: str
    days_in_stage: int
    trade_count: int
    observed_drawdown_pct: float
    recommendation: Literal["promote", "hold", "demote"]
    rationale: str
    next_stage: Optional[str]


# --- Trade close webhook (P2.5) ---

class TradeCloseRequest(BaseModel):
    """Payload posted by freqtrade's webhook when a trade (or sub-fill) closes.

    Field names mirror freqtrade's RPCExitMsg (`freqtrade/freqtradebot.py`
    `_notify_exit`) so the freqtrade side can forward the dict as-is via
    its recursive_format webhook template. `strategy` is NOT supplied by
    freqtrade — it's injected per-strategy in the freqtrade config (because
    freqtrade doesn't know its own strategy name in the webhook context).

    Only `is_final_exit=true` triggers a reflection — partial exits during
    a single trade's life produce multiple EXIT_FILL events that we
    intentionally discard (see docs/system/06-trade-close-webhook.md).
    """
    trade_id: int = Field(..., description="freqtrade Trade.id (DB primary key)")
    strategy: str = Field(..., min_length=1, max_length=64)
    pair: str = Field(..., min_length=1, max_length=32)
    side: Literal["long", "short"] = "long"
    direction: str = "Long"  # informational only — 'Long' / 'Short'
    open_rate: float = Field(..., gt=0)
    close_rate: float = Field(..., gt=0)
    profit_ratio: float
    profit_amount: float
    open_date: datetime
    close_date: datetime
    exit_reason: str = "unknown"
    enter_tag: str | None = None
    stake_amount: float = Field(..., gt=0)
    stake_currency: str = "USDT"
    is_final_exit: bool = True
    sub_trade: bool = False
    # Free-form metadata freqtrade doesn't know about — strategy-side can add
    extra: dict = Field(default_factory=dict)


class TradeCloseResponse(BaseModel):
    """Acknowledgement returned to freqtrade.

    `skipped=True` covers two cases:
      - duplicate webhook for an already-reflected trade_id (idempotency)
      - non-final EXIT_FILL (partial fill mid-trade)
    """
    status: Literal["recorded", "skipped"]
    trade_id: int
    reason: str = ""
    reflection_id: int | None = None


# --- Health ---

class HealthResponse(BaseModel):
    status: Literal["ok"]
    version: str = "0.1.0"