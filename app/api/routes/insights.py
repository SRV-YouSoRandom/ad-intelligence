"""Insight API routes."""

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import valkey.asyncio as valkey_async

from app.api.dependencies import get_db, get_valkey
from app.db.models import Ad, Insight, Job
from app.schemas.insight import InsightPendingResponse, InsightResponse

router = APIRouter()


@router.get("/ads/{ad_id}/insights", response_model=InsightResponse | InsightPendingResponse)
async def get_ad_insights(
    ad_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    vk: valkey_async.Valkey = Depends(get_valkey),
):
    """
    Get insight for an ad.
    If not yet generated, returns pending status and triggers generation if not already queued.
    """
    # Check ad exists
    ad_result = await db.execute(select(Ad).where(Ad.id == ad_id))
    ad = ad_result.scalar_one_or_none()
    if not ad:
        raise HTTPException(status_code=404, detail="Ad not found")

    # Check if insight already exists
    insight_result = await db.execute(select(Insight).where(Insight.ad_id == ad_id))
    insight = insight_result.scalar_one_or_none()

    if insight:
        return InsightResponse.model_validate(insight)

    # Check if a generation job is already pending for this ad
    existing_job_result = await db.execute(
        select(Job).where(
            Job.job_type == "generate_insights",
            Job.status.in_(["PENDING", "RUNNING"]),
            Job.payload["ad_id"].astext == str(ad_id),
        )
    )
    existing_job = existing_job_result.scalar_one_or_none()

    if existing_job:
        return InsightPendingResponse(
            status="pending",
            message="Insight generation is already in progress.",
            job_id=existing_job.id,
        )

    # Create and enqueue a generation job
    job = Job(
        job_type="generate_insights",
        status="PENDING",
        payload={"ad_id": str(ad_id)},
    )
    db.add(job)
    await db.flush()

    job_payload = json.dumps({
        "job_id": str(job.id),
        "job_type": "generate_insights",
        "payload": {"ad_id": str(ad_id)},
    })
    await vk.rpush("jobs:pending", job_payload)

    return InsightPendingResponse(
        status="pending",
        message="Insight generation has been queued.",
        job_id=job.id,
    )
