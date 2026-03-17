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
    start_time = time.time()
    ad_id = payload["ad_id"]

    vk = await get_valkey()
    queue = JobQueue(vk)

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
        async with async_session_factory() as db:
            result = await db.execute(select(Ad).where(Ad.id == ad_id))
            ad = result.scalar_one_or_none()

        if not ad:
            raise ValueError(f"Ad not found: {ad_id}")

        if ad.snapshot_url and not ad.media_local_path:
            logger.info("fetching_deferred_media", ad_id=ad_id)
            from app.services.media_processor import process_deferred_media
            
            media_result = await process_deferred_media(ad.snapshot_url, ad.ad_archive_id)
            
            if media_result:
                ad.media_local_path = media_result.get("media_local_path")
                ad.frame_paths = media_result.get("frame_paths")
                ad.frame_metadata = media_result.get("frame_metadata")
                
                is_video_signal = False
                if (ad.frame_paths and len(ad.frame_paths) > 0) or (ad.media_local_path and ad.media_local_path.lower().endswith((".mp4", ".mov", ".m4v", ".webm"))):
                    is_video_signal = True
                    
                ad.ad_type = "VIDEO" if is_video_signal else "STATIC"
                
                async with async_session_factory() as update_db:
                    await update_db.execute(
                        update(Ad).where(Ad.id == ad_id).values(
                            media_local_path=ad.media_local_path,
                            frame_paths=ad.frame_paths,
                            frame_metadata=ad.frame_metadata,
                            ad_type=ad.ad_type
                        )
                    )
                    await update_db.commit()

        insight_result = await generate_insight(ad)

        async with async_session_factory() as db:
            existing = await db.execute(select(Insight).where(Insight.ad_id == ad_id))
            existing_insight = existing.scalar_one_or_none()

            if existing_insight:
                existing_insight.summary = insight_result.summary
                existing_insight.factors = insight_result.factors
                existing_insight.model_used = insight_result.model_used
                existing_insight.prompt_version = insight_result.prompt_version
                existing_insight.analysis_mode = insight_result.analysis_mode
                existing_insight.ad_context = insight_result.ad_context
                existing_insight.generated_at = datetime.now(timezone.utc)
            else:
                insight = Insight(
                    ad_id=ad_id,
                    summary=insight_result.summary,
                    factors=insight_result.factors,
                    model_used=insight_result.model_used,
                    prompt_version=insight_result.prompt_version,
                    analysis_mode=insight_result.analysis_mode,
                    ad_context=insight_result.ad_context,
                )
                db.add(insight)

            await db.commit()

        elapsed_ms = (time.time() - start_time) * 1000
        metrics.record_timing("generate_insight", elapsed_ms)

        async with async_session_factory() as db:
            await db.execute(
                update(Job).where(Job.id == job_id).values(
                    status="DONE",
                    result={
                        "ad_id": str(ad_id),
                        "analysis_mode": insight_result.analysis_mode,
                        "ad_context": insight_result.ad_context,
                        "elapsed_ms": round(elapsed_ms, 1),
                    },
                    updated_at=datetime.now(timezone.utc),
                )
            )
            await db.commit()

        await queue.update_status(job_id, "DONE")
        logger.info(
            "insight_generated",
            job_id=job_id,
            ad_id=ad_id,
            mode=insight_result.analysis_mode,
            context=insight_result.ad_context,
        )

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