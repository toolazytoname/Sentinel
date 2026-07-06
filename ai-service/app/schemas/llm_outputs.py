"""LLM output schemas. Pydantic models ensure strict validation.

Per ADR-002: LLM outputs are always validated before use. If validation
fails, the call is retried with a corrective prompt; if still failing,
the caller receives a structured error and falls back to safe defaults
(typically: do not act).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class ResearchNote(BaseModel):
    """A single structured fact extracted from unstructured news/market content."""
    asset: str = Field(..., min_length=1, max_length=32)
    event_type: Literal[
        "regulatory", "technical", "partnership", "macro", "security", "other"
    ]
    severity: int = Field(..., ge=1, le=5)
    summary: str = Field(..., min_length=10, max_length=500)
    source_url: str = Field(..., pattern=r"^https?://.+")
    published_at: str  # ISO8601 string; validation deferred to caller

    @field_validator("source_url")
    @classmethod
    def url_must_be_well_formed(cls, v: str) -> str:
        # The pattern check above is the primary guard; this adds length limit
        if len(v) > 2048:
            raise ValueError("source_url exceeds 2048 chars")
        return v


class VetoDecision(BaseModel):
    """LLM-as-devil's-advocate output. Per ADR-002: LLM can only VETO."""
    veto: bool
    reason: str = Field(..., min_length=5, max_length=200)
    confidence: float = Field(..., ge=0.0, le=1.0)


class TradeReflection(BaseModel):
    """Post-trade reflection output. Used to enrich future decisions."""
    trade_id: str = Field(..., min_length=1)
    what_worked: str = Field(..., min_length=10, max_length=1000)
    what_failed: str = Field(..., min_length=10, max_length=1000)
    lesson: str = Field(..., min_length=10, max_length=500)
    confidence: float = Field(..., ge=0.0, le=1.0)