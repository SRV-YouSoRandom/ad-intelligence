"""Pydantic schemas for brand-related requests and responses."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class BrandSearchRequest(BaseModel):
    identifier: str = Field(..., description="Brand name or Meta Page ID")
    identifier_type: str = Field("name", pattern="^(name|page_id)$")
    countries: list[str] = Field(default=["GB", "DE", "FR"])
    ad_active_status: str = Field("ALL", pattern="^(ALL|ACTIVE|INACTIVE)$")
    max_ads: int | None = Field(default=200, ge=1, le=10000)


class BrandSearchResponse(BaseModel):
    job_id: uuid.UUID
    status: str
    message: str


class BrandResponse(BaseModel):
    id: uuid.UUID
    page_id: str
    page_name: str
    fetched_at: datetime | None = None
    ad_count: int = 0
    created_at: datetime

    model_config = {"from_attributes": True}


class BrandListResponse(BaseModel):
    brands: list[BrandResponse]
    total: int


class BrandRecommendationResponse(BaseModel):
    brand_id: uuid.UUID
    brand_name: str
    brand_context: str  # 'commercial' | 'political'
    total_ads_analyzed: int
    static_patterns: dict[str, Any]
    video_patterns: dict[str, Any]
    hypotheses_to_test: list[dict[str, Any]]
    portfolio_summary: str