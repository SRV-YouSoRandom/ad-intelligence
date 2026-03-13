"""Pydantic schemas for brand-related requests and responses."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class BrandSearchRequest(BaseModel):
    """Request body for POST /api/v1/brands/search."""
    identifier: str = Field(..., description="Brand name or Meta Page ID")
    identifier_type: str = Field("name", pattern="^(name|page_id)$", description="'name' or 'page_id'")
    countries: list[str] = Field(default=["US"], description="ad_reached_countries, e.g. ['US', 'GB']")
    ad_active_status: str = Field("ALL", pattern="^(ALL|ACTIVE|INACTIVE)$")
    max_ads: int | None = Field(
        default=200,
        ge=1,
        le=10000,
        description="Maximum number of ads to fetch and process. Defaults to 200. Set to None for no limit.",
    )


class BrandSearchResponse(BaseModel):
    """Response body for POST /api/v1/brands/search."""
    job_id: uuid.UUID
    status: str
    message: str


class BrandResponse(BaseModel):
    """Response body for a brand object."""
    id: uuid.UUID
    page_id: str
    page_name: str
    fetched_at: datetime | None = None
    ad_count: int = 0
    created_at: datetime

    model_config = {"from_attributes": True}


class BrandListResponse(BaseModel):
    """Response body for GET /api/v1/brands."""
    brands: list[BrandResponse]
    total: int