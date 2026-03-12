"""Brand API routes — search and listing."""

import json
import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
import valkey.asyncio as valkey_async

from app.api.dependencies import get_db, get_valkey
from app.db.models import Brand, Job
from app.schemas.brand import (
    BrandListResponse,
    BrandResponse,
    BrandSearchRequest,
    BrandSearchResponse,
)

router = APIRouter()


@router.post("/brands/search", response_model=BrandSearchResponse)
async def search_brand(
    request: BrandSearchRequest,
    db: AsyncSession = Depends(get_db),
    vk: valkey_async.Valkey = Depends(get_valkey),
):
    """Trigger a background job to fetch all ads for a brand."""
    # Create the job record
    job = Job(
        job_type="fetch_brand_ads",
        status="PENDING",
        payload={
            "identifier": request.identifier,
            "identifier_type": request.identifier_type,
            "countries": request.countries,
            "ad_active_status": request.ad_active_status,
        },
    )
    db.add(job)
    await db.flush()

    # Enqueue the job in Valkey
    job_payload = json.dumps({
        "job_id": str(job.id),
        "job_type": "fetch_brand_ads",
        "payload": {
            "identifier": request.identifier,
            "identifier_type": request.identifier_type,
            "countries": request.countries,
            "ad_active_status": request.ad_active_status,
        },
    })
    await vk.rpush("jobs:pending", job_payload)

    # Set initial status in Valkey for fast polling
    await vk.hset(f"jobs:status:{job.id}", mapping={
        "status": "PENDING",
        "updated_at": str(job.created_at),
    })

    return BrandSearchResponse(
        job_id=job.id,
        status="PENDING",
        message="Brand ad fetch queued. Poll /jobs/{job_id}/status for progress.",
    )


@router.get("/brands", response_model=BrandListResponse)
async def list_brands(
    db: AsyncSession = Depends(get_db),
):
    """List all brands that have been fetched, with ad counts."""
    result = await db.execute(
        select(Brand).order_by(Brand.created_at.desc())
    )
    brands = result.scalars().all()
    return BrandListResponse(
        brands=[BrandResponse.model_validate(b) for b in brands],
        total=len(brands),
    )
