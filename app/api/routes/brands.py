"""Brand API routes — search, listing, and cached recommendations."""

import hashlib
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import valkey.asyncio as valkey_async

from app.api.dependencies import get_db, get_valkey
from app.db.models import Ad, Brand, BrandRecommendation, Insight, Job
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


def _compute_insights_fingerprint(insight_ids: list[str]) -> str:
    """
    SHA-256 of sorted insight IDs.
    If any new insight is added or deleted, this changes → cache is stale.
    """
    sorted_ids = sorted(insight_ids)
    return hashlib.sha256(",".join(sorted_ids).encode()).hexdigest()


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
    result = await db.execute(select(Brand).order_by(Brand.created_at.desc()))
    brands = result.scalars().all()
    return BrandListResponse(
        brands=[BrandResponse.model_validate(b) for b in brands],
        total=len(brands),
    )


@router.get("/brands/{brand_id}/recommendations", response_model=BrandRecommendationResponse)
async def get_brand_recommendations(
    brand_id: uuid.UUID,
    force_refresh: bool = Query(
        default=False,
        description="Force regeneration even if a cached report exists. "
                    "Only use when you know new insights have been added.",
    ),
    db: AsyncSession = Depends(get_db),
):
    """
    Get (or generate) a brand-level creative strategy report.

    CACHING BEHAVIOUR:
    - First call: generates from AI, stores in DB, returns result.
    - Subsequent calls: returns cached result instantly. No AI call.
    - Cache invalidates automatically when new insights are added
      (detected via SHA-256 fingerprint of all insight IDs).
    - force_refresh=true: bypasses cache and regenerates. Use sparingly.

    Requires at least 3 ads with generated insights.
    """
    brand_result = await db.execute(select(Brand).where(Brand.id == brand_id))
    brand = brand_result.scalar_one_or_none()
    if not brand:
        raise HTTPException(status_code=404, detail="Brand not found")

    # Fetch all ads with their insights for this brand
    ads_result = await db.execute(select(Ad).where(Ad.brand_id == brand_id))
    all_ads = ads_result.scalars().all()

    ads_with_insights = []
    for ad in all_ads:
        insight_result = await db.execute(select(Insight).where(Insight.ad_id == ad.id))
        insight = insight_result.scalar_one_or_none()
        if insight:
            ads_with_insights.append((ad, insight))

    if len(ads_with_insights) < 3:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Need at least 3 ads with generated insights. "
                f"Currently have {len(ads_with_insights)}. Generate insights for more ads first."
            ),
        )

    # Compute fingerprint of current insight set
    current_fingerprint = _compute_insights_fingerprint(
        [str(insight.id) for _, insight in ads_with_insights]
    )

    # Check cache
    cached_result = await db.execute(
        select(BrandRecommendation).where(BrandRecommendation.brand_id == brand_id)
    )
    cached = cached_result.scalar_one_or_none()

    if cached and not force_refresh and cached.insights_fingerprint == current_fingerprint:
        # Cache hit — return immediately, zero AI calls
        return BrandRecommendationResponse(
            brand_id=brand_id,
            brand_name=brand.page_name,
            brand_context=cached.brand_context,
            total_ads_analyzed=cached.total_ads_analyzed,
            static_patterns=cached.result.get("static_patterns", {}),
            video_patterns=cached.result.get("video_patterns", {}),
            hypotheses_to_test=cached.result.get("hypotheses_to_test", []),
            portfolio_summary=cached.result.get("portfolio_summary", ""),
            cached=True,
            generated_at=cached.generated_at.isoformat(),
        )

    # Cache miss or stale — generate fresh report
    political_count = sum(
        1 for ad, _ in ads_with_insights if _detect_ad_context(ad) == "political"
    )
    brand_context = "political" if political_count > len(ads_with_insights) / 2 else "commercial"

    strong, average, weak = [], [], []
    for ad, insight in ads_with_insights:
        entry = {
            "ad_type": ad.ad_type,
            "ad_context": _detect_ad_context(ad),
            "performance_label": ad.performance_label,
            "summary": insight.summary,
            "analysis_mode": insight.analysis_mode,
            # Only pass top 3 factors to keep token usage bounded
            "factors": insight.factors[:3] if insight.factors else [],
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

    # Upsert into cache
    if cached:
        cached.brand_context = brand_context
        cached.total_ads_analyzed = len(ads_with_insights)
        cached.insights_fingerprint = current_fingerprint
        cached.result = recommendations
        from datetime import datetime, timezone
        cached.generated_at = datetime.now(timezone.utc)
    else:
        new_cache = BrandRecommendation(
            brand_id=brand_id,
            brand_context=brand_context,
            total_ads_analyzed=len(ads_with_insights),
            insights_fingerprint=current_fingerprint,
            result=recommendations,
        )
        db.add(new_cache)

    await db.commit()

    # Fetch the saved record to get generated_at
    saved = await db.execute(
        select(BrandRecommendation).where(BrandRecommendation.brand_id == brand_id)
    )
    saved_rec = saved.scalar_one()

    return BrandRecommendationResponse(
        brand_id=brand_id,
        brand_name=brand.page_name,
        brand_context=brand_context,
        total_ads_analyzed=len(ads_with_insights),
        static_patterns=recommendations.get("static_patterns", {}),
        video_patterns=recommendations.get("video_patterns", {}),
        hypotheses_to_test=recommendations.get("hypotheses_to_test", []),
        portfolio_summary=recommendations.get("portfolio_summary", ""),
        cached=False,
        generated_at=saved_rec.generated_at.isoformat(),
    )