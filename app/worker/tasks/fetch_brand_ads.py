"""Task: Fetch all ads for a brand — full pipeline (fetch → classify → download → score)."""

import json
import time
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.api.dependencies import get_valkey
from app.core.config import settings
from app.core.logging import get_logger
from app.core.metrics import metrics
from app.db.models import Ad, Brand, Job
from app.db.session import async_session_factory
from app.services.classifier import classify_ad
from app.services.media_processor import download_and_extract_frames, download_image
from app.services.meta_fetcher import MetaFetcher
from app.services.performance_scorer import score_brand_ads
from app.worker.queue import JobQueue

logger = get_logger(__name__)

BATCH_SIZE = 50


async def run_fetch_brand_ads(job_id: str, payload: dict) -> None:
    """
    Full brand ad fetch pipeline:
    1. Fetch all ads from Meta API
    2. Classify each ad (static/video)
    3. Download media (images / video frames)
    4. Score inactive ads with performance data
    5. Enqueue insight generation for scored ads
    """
    start_time = time.time()
    identifier = payload["identifier"]
    identifier_type = payload["identifier_type"]
    countries = payload["countries"]
    ad_active_status = payload.get("ad_active_status", "ALL")

    async with async_session_factory() as db:
        # Update job status to RUNNING
        await db.execute(
            update(Job).where(Job.id == job_id).values(
                status="RUNNING",
                updated_at=datetime.now(timezone.utc),
            )
        )
        await db.commit()

    vk = await get_valkey()
    queue = JobQueue(vk)
    await queue.update_status(job_id, "RUNNING")

    try:
        # Determine page_id
        page_id = identifier if identifier_type == "page_id" else identifier

        # Initialize the Meta fetcher
        fetcher = MetaFetcher(vk)

        # Upsert brand record
        async with async_session_factory() as db:
            result = await db.execute(select(Brand).where(Brand.page_id == page_id))
            brand = result.scalar_one_or_none()

            if not brand:
                brand = Brand(
                    page_id=page_id,
                    page_name=identifier,  # Will be updated from first ad's page_name
                )
                db.add(brand)
                await db.flush()

            brand_id = brand.id
            await db.commit()

        # Fetch and process ads in batches
        ad_batch = []
        total_processed = 0

        async for ad_raw in fetcher.fetch_all_ads_for_brand(page_id, countries, ad_active_status):
            ad_batch.append(ad_raw)

            if len(ad_batch) >= BATCH_SIZE:
                total_processed += await _process_batch(ad_batch, brand_id, page_id)
                ad_batch.clear()

        # Process remaining ads
        if ad_batch:
            total_processed += await _process_batch(ad_batch, brand_id, page_id)

        # Update brand record
        async with async_session_factory() as db:
            await db.execute(
                update(Brand).where(Brand.id == brand_id).values(
                    ad_count=total_processed,
                    fetched_at=datetime.now(timezone.utc),
                )
            )
            await db.commit()

        # Re-score all ads for this brand
        async with async_session_factory() as db:
            result = await db.execute(select(Ad).where(Ad.brand_id == brand_id))
            all_ads = result.scalars().all()

            scored = score_brand_ads(all_ads)

            for ad, score, label, percentile in scored:
                await db.execute(
                    update(Ad).where(Ad.id == ad.id).values(
                        performance_score=score,
                        performance_label=label,
                        performance_percentile=percentile,
                    )
                )
            await db.commit()

        # Enqueue insight generation for inactive ads with performance labels
        async with async_session_factory() as db:
            result = await db.execute(
                select(Ad).where(
                    Ad.brand_id == brand_id,
                    Ad.performance_label.isnot(None),
                    Ad.media_local_path.isnot(None),
                )
            )
            scored_ads = result.scalars().all()

            for ad in scored_ads:
                insight_job = Job(
                    job_type="generate_insights",
                    status="PENDING",
                    payload={"ad_id": str(ad.id)},
                )
                db.add(insight_job)
                await db.flush()
                await queue.enqueue(
                    job_id=str(insight_job.id),
                    job_type="generate_insights",
                    payload={"ad_id": str(ad.id)},
                )

            await db.commit()

        # Mark job as DONE
        elapsed_ms = (time.time() - start_time) * 1000
        metrics.record_timing("fetch_brand_total", elapsed_ms)

        async with async_session_factory() as db:
            await db.execute(
                update(Job).where(Job.id == job_id).values(
                    status="DONE",
                    result={
                        "total_ads": total_processed,
                        "scored_ads": len(scored),
                        "elapsed_ms": round(elapsed_ms, 1),
                    },
                    updated_at=datetime.now(timezone.utc),
                )
            )
            await db.commit()

        await queue.update_status(job_id, "DONE")
        logger.info("fetch_brand_complete", job_id=job_id, total=total_processed)

    except Exception as exc:
        logger.error("fetch_brand_failed", job_id=job_id, error=str(exc))

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


async def _process_batch(ad_batch: list[dict], brand_id, page_id: str) -> int:
    """Process a batch of raw ads: classify, download media, and upsert to DB."""
    processed = 0

    for ad_raw in ad_batch:
        try:
            ad_archive_id = ad_raw.get("ad_archive_id", "")
            if not ad_archive_id:
                continue

            # Classify
            ad_type, classification_method = await classify_ad(ad_raw)

            # Extract metadata
            creatives = ad_raw.get("ad_creatives", {}).get("data", [])
            first_creative = creatives[0] if creatives else {}

            # Parse impression/reach/spend ranges
            impressions = ad_raw.get("impressions", {})
            reach = ad_raw.get("reach", {})
            spend = ad_raw.get("spend", {})

            # Determine media URLs
            image_url = None
            video_url = None
            for c in creatives:
                if not video_url:
                    video_url = c.get("video_hd_url") or c.get("video_sd_url")
                if not image_url:
                    image_url = c.get("image_url") or c.get("thumbnail_url")

            # Download media
            media_local_path = None
            frame_paths = None
            frame_metadata = None

            if ad_type == "VIDEO" and video_url:
                result = await download_and_extract_frames(video_url, ad_archive_id)
                if result:
                    frame_paths, frame_metadata = result
                    media_local_path = frame_paths[0] if frame_paths else None
            elif image_url:
                media_local_path = await download_image(image_url, ad_archive_id)

            # Parse dates
            start_date = ad_raw.get("ad_delivery_start_time")
            end_date = ad_raw.get("ad_delivery_stop_time")

            # Build caption from bodies
            bodies = ad_raw.get("ad_creative_bodies", [])
            caption = bodies[0] if bodies else first_creative.get("body")

            # Link info
            link_titles = ad_raw.get("ad_creative_link_titles", [])
            link_descs = ad_raw.get("ad_creative_link_descriptions", [])
            link_captions = ad_raw.get("ad_creative_link_captions", [])

            # Upsert ad record
            async with async_session_factory() as db:
                stmt = pg_insert(Ad).values(
                    ad_archive_id=ad_archive_id,
                    brand_id=brand_id,
                    page_name=ad_raw.get("page_name"),
                    is_active=ad_raw.get("is_active", False),
                    ad_type=ad_type,
                    classification_method=classification_method,
                    caption=caption,
                    link_title=link_titles[0] if link_titles else first_creative.get("title"),
                    link_description=link_descs[0] if link_descs else first_creative.get("description"),
                    cta_type=first_creative.get("call_to_action_type"),
                    publisher_platforms=ad_raw.get("publisher_platforms"),
                    start_date=start_date,
                    end_date=end_date,
                    impressions_lower=_parse_range_value(impressions, "lower_bound"),
                    impressions_upper=_parse_range_value(impressions, "upper_bound"),
                    reach_lower=_parse_range_value(reach, "lower_bound"),
                    reach_upper=_parse_range_value(reach, "upper_bound"),
                    spend_lower=_parse_range_value(spend, "lower_bound"),
                    spend_upper=_parse_range_value(spend, "upper_bound"),
                    snapshot_url=ad_raw.get("ad_snapshot_url"),
                    media_local_path=media_local_path,
                    frame_paths=frame_paths,
                    frame_metadata=frame_metadata,
                    raw_meta_json=ad_raw,
                ).on_conflict_do_update(
                    index_elements=["ad_archive_id"],
                    set_={
                        "is_active": ad_raw.get("is_active", False),
                        "impressions_lower": _parse_range_value(impressions, "lower_bound"),
                        "impressions_upper": _parse_range_value(impressions, "upper_bound"),
                        "reach_lower": _parse_range_value(reach, "lower_bound"),
                        "reach_upper": _parse_range_value(reach, "upper_bound"),
                        "spend_lower": _parse_range_value(spend, "lower_bound"),
                        "spend_upper": _parse_range_value(spend, "upper_bound"),
                        "raw_meta_json": ad_raw,
                        "updated_at": datetime.now(timezone.utc),
                    },
                )
                await db.execute(stmt)
                await db.commit()

            processed += 1
            metrics.increment("ads_processed")

        except Exception as exc:
            logger.error("ad_processing_error", ad_archive_id=ad_raw.get("ad_archive_id"), error=str(exc))
            continue

    return processed


def _parse_range_value(range_dict: dict | str, key: str) -> int | None:
    """Parse a range value from Meta's impression/reach/spend data."""
    if isinstance(range_dict, str):
        return None
    val = range_dict.get(key)
    if val is not None:
        try:
            return int(val)
        except (ValueError, TypeError):
            return None
    return None
