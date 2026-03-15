"""Brand API routes — search, listing, and recommendations."""

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import valkey.asyncio as valkey_async

from app.api.dependencies import get_db, get_valkey
from app.db.models import Ad, Brand, Insight, Job
from app.schemas.brand import (
    BrandListResponse,
    BrandRecommendationResponse,
    BrandResponse,
    BrandSearchRequest,
    BrandSearchResponse,
)
from app.services.insight_generator import _detect_ad_context
from app.services.recommendation import generate_brand_recommendations

router = APIRouter()


@router.post("/brands/search", response_model=BrandSearchResponse)
async def search_brand(
    request: BrandSearchRequest,
    db: AsyncSession = Depends(get_db),
    vk: valkey_async.Valkey = Depends(get_valkey),
):
    """Trigger a background job to fetch all ads for a brand."""
    job = Job(
        job_type="fetch_brand_ads",
        status="PENDING",
        payload={
            "identifier": request.identifier,
            "identifier_type": request.identifier_type,
            "countries": request.countries,
            "ad_active_status": request.ad_active_status,
            "max_ads": request.max_ads,
        },
    )
    db.add(job)
    await db.flush()

    job_payload = json.dumps({
        "job_id": str(job.id),
        "job_type": "fetch_brand_ads",
        "payload": {
            "identifier": request.identifier,
            "identifier_type": request.identifier_type,
            "countries": request.countries,
            "ad_active_status": request.ad_active_status,
            "max_ads": request.max_ads,
        },
    })
    await vk.rpush("jobs:pending", job_payload)
    await vk.hset(f"jobs:status:{job.id}", mapping={
        "status": "PENDING",
        "updated_at": str(job.created_at),
    })

    limit_msg = f" (capped at {request.max_ads} ads)" if request.max_ads else ""
    return BrandSearchResponse(
        job_id=job.id,
        status="PENDING",
        message=f"Brand ad fetch queued{limit_msg}. Poll /jobs/{{job_id}}/status for progress.",
    )


@router.get("/brands", response_model=BrandListResponse)
async def list_brands(db: AsyncSession = Depends(get_db)):
    """List all brands that have been fetched, with ad counts."""
    result = await db.execute(select(Brand).order_by(Brand.created_at.desc()))
    brands = result.scalars().all()
    return BrandListResponse(
        brands=[BrandResponse.model_validate(b) for b in brands],
        total=len(brands),
    )


@router.get("/brands/{brand_id}/recommendations", response_model=BrandRecommendationResponse)
async def get_brand_recommendations(
    brand_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """
    Generate brand-level creative recommendations by synthesizing all
    existing insights for a brand's ad portfolio.

    Requires at least 3 ads with generated insights.
    Results are computed fresh each call (not cached) so they reflect
    the latest set of insights.
    """
    # Fetch brand
    brand_result = await db.execute(select(Brand).where(Brand.id == brand_id))
    brand = brand_result.scalar_one_or_none()
    if not brand:
        raise HTTPException(status_code=404, detail="Brand not found")

    # Fetch all ads with their insights
    ads_result = await db.execute(
        select(Ad).where(Ad.brand_id == brand_id)
    )
    all_ads = ads_result.scalars().all()

    # Build insights summary — only ads with generated insights
    ads_with_insights = []
    for ad in all_ads:
        insight_result = await db.execute(
            select(Insight).where(Insight.ad_id == ad.id)
        )
        insight = insight_result.scalar_one_or_none()
        if insight:
            ads_with_insights.append((ad, insight))

    if len(ads_with_insights) < 3:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Need at least 3 ads with generated insights to produce recommendations. "
                f"Currently have {len(ads_with_insights)}. Generate insights for more ads first."
            ),
        )

    # Detect brand context from the majority of ads
    political_count = sum(
        1 for ad, _ in ads_with_insights
        if _detect_ad_context(ad) == "political"
    )
    brand_context = "political" if political_count > len(ads_with_insights) / 2 else "commercial"

    # Group by performance label
    strong, average, weak = [], [], []
    for ad, insight in ads_with_insights:
        entry = {
            "ad_type": ad.ad_type,
            "ad_context": _detect_ad_context(ad),
            "performance_label": ad.performance_label,
            "summary": insight.summary,
            "analysis_mode": insight.analysis_mode,
            "factors": insight.factors,
        }
        label = ad.performance_label or "AVERAGE"
        if label == "STRONG":
            strong.append(entry)
        elif label == "WEAK":
            weak.append(entry)
        else:
            average.append(entry)

    insights_summary = {
        "total": len(ads_with_insights),
        "brand_context": brand_context,
        "strong": strong,
        "average": average,
        "weak": weak,
    }

    recommendations = await generate_brand_recommendations(brand, insights_summary)

    return BrandRecommendationResponse(
        brand_id=brand_id,
        brand_name=brand.page_name,
        brand_context=brand_context,
        total_ads_analyzed=len(ads_with_insights),
        static_patterns=recommendations.get("static_patterns", {}),
        video_patterns=recommendations.get("video_patterns", {}),
        hypotheses_to_test=recommendations.get("hypotheses_to_test", []),
        portfolio_summary=recommendations.get("portfolio_summary", ""),
    )