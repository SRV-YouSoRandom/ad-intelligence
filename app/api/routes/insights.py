"""
Insight API routes.

Flow (manual, user-triggered only):
  GET    /ads/{ad_id}/insights          -> existing insight | pending | not_generated
  POST   /ads/{ad_id}/insights/generate -> trigger generation, returns job_id (202)
  DELETE /ads/{ad_id}/insights          -> delete insight to allow regeneration
"""

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import valkey.asyncio as valkey_async

from app.api.dependencies import get_db, get_valkey
from app.db.models import Ad, Insight, Job
from app.schemas.insight import InsightNotGeneratedResponse, InsightPendingResponse, InsightResponse

router = APIRouter()


@router.get("/ads/{ad_id}/insights")
async def get_ad_insights(
    ad_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """
    Get insight status for an ad.
    Returns existing insight, pending status, or not_generated status.
    Does NOT auto-trigger generation — use POST /generate for that.
    """
    ad_result = await db.execute(select(Ad).where(Ad.id == ad_id))
    ad = ad_result.scalar_one_or_none()
    if not ad:
        raise HTTPException(status_code=404, detail="Ad not found")

    # Return existing insight if present
    insight_result = await db.execute(select(Insight).where(Insight.ad_id == ad_id))
    insight = insight_result.scalar_one_or_none()
    if insight:
        return InsightResponse.model_validate(insight)

    # Check if generation is currently in progress
    running_job_result = await db.execute(
        select(Job).where(
            Job.job_type == "generate_insights",
            Job.status.in_(["PENDING", "RUNNING"]),
            Job.payload["ad_id"].astext == str(ad_id),
        )
    )
    running_job = running_job_result.scalar_one_or_none()
    if running_job:
        return InsightPendingResponse(
            status="pending",
            message="Insight generation is in progress.",
            job_id=running_job.id,
        )

    # Not yet requested
    return InsightNotGeneratedResponse(
        status="not_generated",
        message="Insight has not been requested for this ad. POST to /ads/{ad_id}/insights/generate to trigger.",
        ad_id=ad_id,
        can_generate=True,
        has_media=ad.media_local_path is not None,
        has_performance_data=ad.performance_label is not None,
    )


@router.post("/ads/{ad_id}/insights/generate", status_code=202)
async def generate_ad_insights(
    ad_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    vk: valkey_async.Valkey = Depends(get_valkey),
):
    """
    Trigger insight generation for a specific ad.
    Always user-initiated — never called automatically by the system.
    Returns 202 Accepted with a job_id to poll for completion.
    """
    ad_result = await db.execute(select(Ad).where(Ad.id == ad_id))
    ad = ad_result.scalar_one_or_none()
    if not ad:
        raise HTTPException(status_code=404, detail="Ad not found")

    # Block if insight already exists
    insight_result = await db.execute(select(Insight).where(Insight.ad_id == ad_id))
    if insight_result.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail="Insight already exists. DELETE the existing insight first to regenerate.",
        )

    # Block if already in progress
    running_job_result = await db.execute(
        select(Job).where(
            Job.job_type == "generate_insights",
            Job.status.in_(["PENDING", "RUNNING"]),
            Job.payload["ad_id"].astext == str(ad_id),
        )
    )
    if running_job_result.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Insight generation is already in progress for this ad.")

    # Create and enqueue job
    job = Job(
        job_type="generate_insights",
        status="PENDING",
        payload={"ad_id": str(ad_id)},
    )
    db.add(job)
    await db.flush()

    await vk.rpush("jobs:pending", json.dumps({
        "job_id": str(job.id),
        "job_type": "generate_insights",
        "payload": {"ad_id": str(ad_id)},
    }))

    mode_note = "visual analysis" if ad.media_local_path else "text-only analysis (no media available)"

    return InsightPendingResponse(
        status="pending",
        message=f"Insight generation queued ({mode_note}). Poll /jobs/{job.id}/status for progress.",
        job_id=job.id,
    )


@router.delete("/ads/{ad_id}/insights", status_code=204)
async def delete_ad_insights(
    ad_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Delete existing insight to allow regeneration."""
    insight_result = await db.execute(select(Insight).where(Insight.ad_id == ad_id))
    insight = insight_result.scalar_one_or_none()
    if not insight:
        raise HTTPException(status_code=404, detail="No insight found for this ad.")
    await db.delete(insight)
    await db.commit()