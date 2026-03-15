"""Pydantic schemas for ad-related responses."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, computed_field


class AdResponse(BaseModel):
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
    languages: list[str] | None = None
    start_date: date | None = None
    end_date: date | None = None
    currency: str | None = None

    # Performance metrics
    impressions_lower: int | None = None
    impressions_upper: int | None = None
    impressions_mid: int | None = None
    reach_lower: int | None = None
    reach_upper: int | None = None
    reach_mid: int | None = None
    spend_lower: Decimal | None = None
    spend_upper: Decimal | None = None
    estimated_audience_lower: int | None = None
    estimated_audience_upper: int | None = None

    # Political ad fields — disclaimer presence = political/issue ad
    disclaimer: str | None = None
    bylines: str | None = None
    beneficiary_payers: Any | None = None

    # Media
    snapshot_url: str | None = None
    media_local_path: str | None = None
    frame_paths: list[str] | None = None

    # Scoring
    performance_score: Decimal | None = None
    performance_label: str | None = None
    performance_percentile: Decimal | None = None

    created_at: datetime
    updated_at: datetime

    @computed_field
    @property
    def ad_context(self) -> str:
        """Derived field: 'political' if disclaimer present, else 'commercial'."""
        return "political" if self.disclaimer or self.bylines else "commercial"

    model_config = {"from_attributes": True}


class AdDetailResponse(AdResponse):
    """Extended ad response with raw data and breakdowns."""
    raw_meta_json: dict[str, Any] | None = None
    frame_metadata: list[dict[str, Any]] | None = None
    demographic_distribution: Any | None = None
    delivery_by_region: Any | None = None


class AdListResponse(BaseModel):
    ads: list[AdResponse]
    total: int
    limit: int
    offset: int