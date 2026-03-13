"""Task: Fetch all ads for a brand — full pipeline (fetch → classify → download → score)."""

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
    1. Fetch ads from Meta API (up to max_ads if set)
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
    max_ads: int | None = payload.get("max_ads", 200)  # Default 200 if missing from older jobs

    async with async_session_factory() as db:
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
        page_id = identifier  # Always treat identifier as page_id

        fetcher = MetaFetcher(vk)

        # Upsert brand record with identifier as placeholder name
        async with async_session_factory() as db:
            result = await db.execute(select(Brand).where(Brand.page_id == page_id))
            brand = result.scalar_one_or_none()

            if not brand:
                brand = Brand(
                    page_id=page_id,
                    page_name=identifier,
                )
                db.add(brand)
                await db.flush()

            brand_id = brand.id
            await db.commit()

        # Fetch and process ads in batches, respecting max_ads
        ad_batch = []
        total_fetched = 0       # raw count of ads received from Meta API
        total_processed = 0     # count of ads successfully upserted
        brand_page_name = None  # will be extracted from first ad
        limit_reached = False

        async for ad_raw in fetcher.fetch_all_ads_for_brand(page_id, countries, ad_active_status):
            # Enforce max_ads limit on the raw fetch count
            if max_ads is not None and total_fetched >= max_ads:
                limit_reached = True
                logger.info(
                    "max_ads_limit_reached",
                    max_ads=max_ads,
                    total_fetched=total_fetched,
                    brand_id=str(brand_id),
                )
                break

            # Extract real page_name from first ad and update brand record
            if brand_page_name is None:
                brand_page_name = ad_raw.get("page_name")
                if brand_page_name and brand_page_name != identifier:
                    async with async_session_factory() as db:
                        await db.execute(
                            update(Brand).where(Brand.id == brand_id).values(
                                page_name=brand_page_name,
                            )
                        )
                        await db.commit()
                    logger.info("brand_name_updated", page_name=brand_page_name)

            ad_batch.append(ad_raw)
            total_fetched += 1

            if len(ad_batch) >= BATCH_SIZE:
                total_processed += await _process_batch(ad_batch, brand_id)
                ad_batch.clear()

        # Process any remaining ads in the last partial batch
        if ad_batch:
            total_processed += await _process_batch(ad_batch, brand_id)

        logger.info(
            "fetch_pipeline_complete",
            total_fetched=total_fetched,
            total_processed=total_processed,
            limit_reached=limit_reached,
            brand_id=str(brand_id),
        )

        # Update brand record with final count
        async with async_session_factory() as db:
            await db.execute(
                update(Brand).where(Brand.id == brand_id).values(
                    ad_count=total_processed,
                    fetched_at=datetime.now(timezone.utc),
                )
            )
            await db.commit()

        # Re-score ALL ads for this brand (percentiles shift when new ads arrive)
        scored = []
        async with async_session_factory() as db:
            result = await db.execute(select(Ad).where(Ad.brand_id == brand_id))
            all_ads = result.scalars().all()

            logger.info("scoring_ads", total_ads=len(all_ads), brand_id=str(brand_id))
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

        # Enqueue insight generation for ads that:
        # - have a performance label (scored)
        # - have media downloaded (media_local_path set)
        async with async_session_factory() as db:
            result = await db.execute(
                select(Ad).where(
                    Ad.brand_id == brand_id,
                    Ad.performance_label.isnot(None),
                    Ad.media_local_path.isnot(None),
                )
            )
            scored_ads = result.scalars().all()
            logger.info("enqueuing_insights", count=len(scored_ads))

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

        # Mark job DONE
        elapsed_ms = (time.time() - start_time) * 1000
        metrics.record_timing("fetch_brand_total", elapsed_ms)

        async with async_session_factory() as db:
            await db.execute(
                update(Job).where(Job.id == job_id).values(
                    status="DONE",
                    result={
                        "total_ads": total_processed,
                        "scored_ads": len(scored),
                        "limit_reached": limit_reached,
                        "max_ads": max_ads,
                        "elapsed_ms": round(elapsed_ms, 1),
                    },
                    updated_at=datetime.now(timezone.utc),
                )
            )
            await db.commit()

        await queue.update_status(job_id, "DONE")
        logger.info("fetch_brand_complete", job_id=job_id, total=total_processed)

    except Exception as exc:
        logger.error("fetch_brand_failed", job_id=job_id, error=str(exc), exc_info=True)

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


async def _process_batch(ad_batch: list[dict], brand_id) -> int:
    """Process a batch of raw ads: classify, download media, and upsert to DB."""
    processed = 0

    for ad_raw in ad_batch:
        try:
            # FIX: Fall back to 'id' if 'ad_archive_id' is missing.
            # The Ads Library API sometimes returns only 'id'.
            ad_archive_id = ad_raw.get("ad_archive_id") or ad_raw.get("id", "")
            if not ad_archive_id:
                logger.warning("ad_missing_id", ad_raw_keys=list(ad_raw.keys()))
                continue

            logger.info("processing_ad", ad_archive_id=ad_archive_id)

            # Classify (metadata heuristics first, VL model fallback)
            ad_type, classification_method = await classify_ad(ad_raw)

            # Parse impression/reach/spend ranges
            impressions = ad_raw.get("impressions") or {}
            reach = ad_raw.get("reach") or {}
            spend = ad_raw.get("spend") or {}

            # Media: The Ads Library API does NOT return direct video/image URLs
            # in the main response. ad_snapshot_url is the only reliable media reference.
            # We use it as the image URL for static ads (it's a preview page, not a direct image).
            # For actual downloadable media, use the snapshot URL directly as image.
            snapshot_url = ad_raw.get("ad_snapshot_url")

            # Download media
            media_local_path = None
            frame_paths = None
            frame_metadata = None

            if snapshot_url:
                # Try downloading the snapshot as an image (works for static ads).
                # For video ads the snapshot is a thumbnail, which is still useful.
                media_local_path = await download_image(snapshot_url, ad_archive_id)

            # Parse dates
            start_date = _parse_date(ad_raw.get("ad_delivery_start_time"))
            end_date = _parse_date(ad_raw.get("ad_delivery_stop_time"))

            # Build caption from bodies (deduplicate)
            bodies = ad_raw.get("ad_creative_bodies") or []
            seen = set()
            unique_bodies = []
            for b in bodies:
                if b and b not in seen:
                    seen.add(b)
                    unique_bodies.append(b)
            caption = unique_bodies[0] if unique_bodies else None

            link_titles = ad_raw.get("ad_creative_link_titles") or []
            link_descs = ad_raw.get("ad_creative_link_descriptions") or []

            # Upsert ad record
            async with async_session_factory() as db:
                stmt = pg_insert(Ad).values(
                    ad_archive_id=ad_archive_id,
                    brand_id=brand_id,
                    page_name=ad_raw.get("page_name"),
                    is_active=bool(ad_raw.get("is_active", False)),
                    ad_type=ad_type,
                    classification_method=classification_method,
                    caption=caption,
                    link_title=link_titles[0] if link_titles else None,
                    link_description=link_descs[0] if link_descs else None,
                    cta_type=None,  # Not available in flat Ads Library response
                    publisher_platforms=ad_raw.get("publisher_platforms"),
                    start_date=start_date,
                    end_date=end_date,
                    impressions_lower=_parse_range_value(impressions, "lower_bound"),
                    impressions_upper=_parse_range_value(impressions, "upper_bound"),
                    reach_lower=_parse_range_value(reach, "lower_bound"),
                    reach_upper=_parse_range_value(reach, "upper_bound"),
                    spend_lower=_parse_range_value(spend, "lower_bound"),
                    spend_upper=_parse_range_value(spend, "upper_bound"),
                    snapshot_url=snapshot_url,
                    media_local_path=media_local_path,
                    frame_paths=frame_paths,
                    frame_metadata=frame_metadata,
                    raw_meta_json=ad_raw,
                ).on_conflict_do_update(
                    index_elements=["ad_archive_id"],
                    set_={
                        "is_active": bool(ad_raw.get("is_active", False)),
                        "impressions_lower": _parse_range_value(impressions, "lower_bound"),
                        "impressions_upper": _parse_range_value(impressions, "upper_bound"),
                        "reach_lower": _parse_range_value(reach, "lower_bound"),
                        "reach_upper": _parse_range_value(reach, "upper_bound"),
                        "spend_lower": _parse_range_value(spend, "lower_bound"),
                        "spend_upper": _parse_range_value(spend, "upper_bound"),
                        "snapshot_url": snapshot_url,
                        "media_local_path": media_local_path,
                        "raw_meta_json": ad_raw,
                        "updated_at": datetime.now(timezone.utc),
                    },
                )
                await db.execute(stmt)
                await db.commit()

            processed += 1
            metrics.increment("ads_processed")
            logger.info("ad_upserted", ad_archive_id=ad_archive_id, ad_type=ad_type)

        except Exception as exc:
            ad_id_for_log = ad_raw.get("ad_archive_id") or ad_raw.get("id", "unknown")
            logger.error("ad_processing_error", ad_archive_id=ad_id_for_log, error=str(exc), exc_info=True)
            continue

    return processed


def _parse_date(value: str | None):
    """Parse a date string from Meta API into a Python date object."""
    if not value:
        return None
    try:
        from datetime import date
        # Meta returns ISO format: "2024-01-15T00:00:00+0000" or "2024-01-15"
        if "T" in value:
            return datetime.fromisoformat(value.replace("+0000", "+00:00")).date()
        return date.fromisoformat(value)
    except Exception:
        return None


def _parse_range_value(range_dict: dict | str | None, key: str) -> int | None:
    """Parse a range value from Meta's impression/reach/spend data."""
    if not range_dict or isinstance(range_dict, str):
        return None
    val = range_dict.get(key)
    if val is not None:
        try:
            return int(val)
        except (ValueError, TypeError):
            return None
    return None