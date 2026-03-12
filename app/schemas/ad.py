"""Pydantic schemas for ad-related responses."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel


class AdResponse(BaseModel):
    """Response body for a single ad object."""
    id: uuid.UUID
    ad_archive_id: str
    brand_id: uuid.UUID | None = None
    page_name: str | None = None
    is_active: bool
    ad_type: str | None = None
    classification_method: str | None = None
    caption: str | None = None
    link_title: str | None = None
    link_description: str | None = None
    cta_type: str | None = None
    publisher_platforms: list[str] | None = None
    start_date: date | None = None
    end_date: date | None = None
    impressions_lower: int | None = None
    impressions_upper: int | None = None
    impressions_mid: int | None = None
    reach_lower: int | None = None
    reach_upper: int | None = None
    reach_mid: int | None = None
    spend_lower: Decimal | None = None
    spend_upper: Decimal | None = None
    snapshot_url: str | None = None
    media_local_path: str | None = None
    frame_paths: list[str] | None = None
    performance_score: Decimal | None = None
    performance_label: str | None = None
    performance_percentile: Decimal | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AdDetailResponse(AdResponse):
    """Extended ad response with raw_meta_json and frame_metadata."""
    raw_meta_json: dict[str, Any] | None = None
    frame_metadata: list[dict[str, Any]] | None = None


class AdListResponse(BaseModel):
    """Paginated response for GET /api/v1/ads."""
    ads: list[AdResponse]
    total: int
    limit: int
    offset: int
