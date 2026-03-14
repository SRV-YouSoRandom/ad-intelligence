"""Pydantic schemas for insight-related responses."""

import uuid
from datetime import datetime

from pydantic import BaseModel


class InsightFactor(BaseModel):
    trait: str
    category: str
    impact: str
    confidence: str
    evidence: str


class InsightResponse(BaseModel):
    """Returned when insight exists."""
    id: uuid.UUID
    ad_id: uuid.UUID
    summary: str
    factors: list[InsightFactor]
    model_used: str
    prompt_version: str
    analysis_mode: str  # "visual" | "text_only"
    generated_at: datetime

    model_config = {"from_attributes": True}


class InsightPendingResponse(BaseModel):
    """Returned when generation is in progress."""
    status: str = "pending"
    message: str
    job_id: uuid.UUID | None = None


class InsightNotGeneratedResponse(BaseModel):
    """Returned when insight has never been requested for this ad."""
    status: str = "not_generated"
    message: str
    ad_id: uuid.UUID
    can_generate: bool = True
    has_media: bool = False
    has_performance_data: bool = False