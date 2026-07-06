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


# --- Health ---

class HealthResponse(BaseModel):
    status: Literal["ok"]
    version: str = "0.1.0"