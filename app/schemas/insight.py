"""Pydantic schemas for insight-related responses."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class InsightFactor(BaseModel):
    """A single insight factor extracted by the AI model."""
    trait: str
    category: str
    impact: str
    confidence: str
    evidence: str


class InsightResponse(BaseModel):
    """Response body for GET /api/v1/ads/{ad_id}/insights."""
    id: uuid.UUID
    ad_id: uuid.UUID
    summary: str
    factors: list[InsightFactor]
    model_used: str
    prompt_version: str
    generated_at: datetime

    model_config = {"from_attributes": True}


class InsightPendingResponse(BaseModel):
    """Response when insight generation is pending."""
    status: str = "pending"
    message: str = "Insight generation has been queued."
    job_id: uuid.UUID | None = None
