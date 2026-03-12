"""Task: Generate AI insights for a single ad."""

import time
from datetime import datetime, timezone

from sqlalchemy import select, update

from app.api.dependencies import get_valkey
from app.core.logging import get_logger
from app.core.metrics import metrics
from app.db.models import Ad, Insight, Job
from app.db.session import async_session_factory
from app.services.insight_generator import generate_insight
from app.worker.queue import JobQueue

logger = get_logger(__name__)


async def run_generate_insights(job_id: str, payload: dict) -> None:
    """
    Generate creative insights for a single ad.

    1. Load ad from DB
    2. Call insight_generator.generate_insight()
    3. Upsert into insights table
    4. Update job status
    """
    start_time = time.time()
    ad_id = payload["ad_id"]

    vk = await get_valkey()
    queue = JobQueue(vk)

    # Update job status to RUNNING
    async with async_session_factory() as db:
        await db.execute(
            update(Job).where(Job.id == job_id).values(
                status="RUNNING",
                updated_at=datetime.now(timezone.utc),
            )
        )
        await db.commit()

    await queue.update_status(job_id, "RUNNING")

    try:
        # Load ad
        async with async_session_factory() as db:
            result = await db.execute(select(Ad).where(Ad.id == ad_id))
            ad = result.scalar_one_or_none()

        if not ad:
            raise ValueError(f"Ad not found: {ad_id}")

        if not ad.media_local_path and not ad.frame_paths:
            raise ValueError(f"Ad {ad_id} has no media available for analysis")

        # Generate insight
        insight_result = await generate_insight(ad)

        # Upsert insight
        async with async_session_factory() as db:
            # Check if insight already exists
            existing = await db.execute(select(Insight).where(Insight.ad_id == ad_id))
            existing_insight = existing.scalar_one_or_none()

            if existing_insight:
                existing_insight.summary = insight_result.summary
                existing_insight.factors = insight_result.factors
                existing_insight.model_used = insight_result.model_used
                existing_insight.prompt_version = insight_result.prompt_version
                existing_insight.generated_at = datetime.now(timezone.utc)
            else:
                insight = Insight(
                    ad_id=ad_id,
                    summary=insight_result.summary,
                    factors=insight_result.factors,
                    model_used=insight_result.model_used,
                    prompt_version=insight_result.prompt_version,
                )
                db.add(insight)

            await db.commit()

        # Mark job as DONE
        elapsed_ms = (time.time() - start_time) * 1000
        metrics.record_timing("generate_insight", elapsed_ms)

        async with async_session_factory() as db:
            await db.execute(
                update(Job).where(Job.id == job_id).values(
                    status="DONE",
                    result={
                        "ad_id": str(ad_id),
                        "elapsed_ms": round(elapsed_ms, 1),
                    },
                    updated_at=datetime.now(timezone.utc),
                )
            )
            await db.commit()

        await queue.update_status(job_id, "DONE")
        logger.info("insight_generated", job_id=job_id, ad_id=ad_id)

    except Exception as exc:
        logger.error("insight_generation_failed", job_id=job_id, ad_id=ad_id, error=str(exc))

        async with async_session_factory() as db:
            await db.execute(
                update(Job).where(Job.id == job_id).values(
                    status="FAILED",
                    error=str(exc),
                    updated_at=datetime.now(timezone.utc),
                )
            )
            await db.commit()

        await queue.update_status(job_id, "FAILED")
        raise
