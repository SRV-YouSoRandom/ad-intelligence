"""
Task: Fetch all ads for a brand — fetch → classify → score.

KEY CHANGE: Now stores disclaimer, bylines, beneficiary_payers,
estimated_audience_size, demographic_distribution, delivery_by_region,
languages, currency from the API response. These are all real fields
the Meta Ads Library API returns — we were previously ignoring them.
"""

import time
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.api.dependencies import get_valkey
from app.core.logging import get_logger
from app.core.metrics import metrics
from app.db.models import Ad, Brand, Job
from app.db.session import async_session_factory
from app.services.classifier import classify_ad
from app.services.media_processor import fetch_media_from_snapshot
from app.services.meta_fetcher import MetaFetcher
from app.services.performance_scorer import score_brand_ads
from app.worker.queue import JobQueue

logger = get_logger(__name__)

BATCH_SIZE = 50


async def run_fetch_brand_ads(job_id: str, payload: dict) -> None:
    start_time = time.time()
    identifier = payload["identifier"]
    countries = payload["countries"]
    ad_active_status = payload.get("ad_active_status", "ALL")
    max_ads: int | None = payload.get("max_ads", 200)

    async with async_session_factory() as db:
        await db.execute(
            update(Job).where(Job.id == job_id).values(
                status="RUNNING", updated_at=datetime.now(timezone.utc)
            )
        )
        await db.commit()

    vk = await get_valkey()
    queue = JobQueue(vk)
    await queue.update_status(job_id, "RUNNING")

    try:
        page_id = identifier
        fetcher = MetaFetcher(vk)

        async with async_session_factory() as db:
            result = await db.execute(select(Brand).where(Brand.page_id == page_id))
            brand = result.scalar_one_or_none()
            if not brand:
                brand = Brand(page_id=page_id, page_name=identifier)
                db.add(brand)
                await db.flush()
            brand_id = brand.id
            await db.commit()

        ad_batch = []
        total_fetched = 0
        total_processed = 0
        brand_page_name = None
        limit_reached = False
        political_count = 0

        async for ad_raw in fetcher.fetch_all_ads_for_brand(page_id, countries, ad_active_status):
            if max_ads is not None and total_fetched >= max_ads:
                limit_reached = True
                break

            if brand_page_name is None:
                brand_page_name = ad_raw.get("page_name")
                if brand_page_name and brand_page_name != identifier:
                    async with async_session_factory() as db:
                        await db.execute(
                            update(Brand).where(Brand.id == brand_id).values(page_name=brand_page_name)
                        )
                        await db.commit()

            # Count political ads for logging
            if ad_raw.get("disclaimer") or ad_raw.get("bylines") or ad_raw.get("beneficiary_payers"):
                political_count += 1

            ad_batch.append(ad_raw)
            total_fetched += 1

            if len(ad_batch) >= BATCH_SIZE:
                total_processed += await _process_batch(ad_batch, brand_id)
                ad_batch.clear()

        if ad_batch:
            total_processed += await _process_batch(ad_batch, brand_id)

        logger.info(
            "fetch_pipeline_complete",
            total_fetched=total_fetched,
            total_processed=total_processed,
            political_ads=political_count,
            commercial_ads=total_fetched - political_count,
            limit_reached=limit_reached,
        )

        async with async_session_factory() as db:
            await db.execute(
                update(Brand).where(Brand.id == brand_id).values(
                    ad_count=total_processed,
                    fetched_at=datetime.now(timezone.utc),
                )
            )
            await db.commit()

        # Score all ads
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

        elapsed_ms = (time.time() - start_time) * 1000
        metrics.record_timing("fetch_brand_total", elapsed_ms)

        async with async_session_factory() as db:
            await db.execute(
                update(Job).where(Job.id == job_id).values(
                    status="DONE",
                    result={
                        "total_ads": total_processed,
                        "political_ads": political_count,
                        "commercial_ads": total_fetched - political_count,
                        "scored_ads": len(scored),
                        "limit_reached": limit_reached,
                        "max_ads": max_ads,
                        "elapsed_ms": round(elapsed_ms, 1),
                        "note": "Insights not auto-generated. Use POST /ads/{id}/insights/generate per ad.",
                    },
                    updated_at=datetime.now(timezone.utc),
                )
            )
            await db.commit()

        await queue.update_status(job_id, "DONE")

    except Exception as exc:
        logger.error("fetch_brand_failed", job_id=job_id, error=str(exc), exc_info=True)
        async with async_session_factory() as db:
            await db.execute(
                update(Job).where(Job.id == job_id).values(
                    status="FAILED", error=str(exc), updated_at=datetime.now(timezone.utc)
                )
            )
            await db.commit()
        await queue.update_status(job_id, "FAILED")
        raise


async def _process_batch(ad_batch: list[dict], brand_id) -> int:
    processed = 0

    for ad_raw in ad_batch:
        try:
            ad_archive_id = ad_raw.get("ad_archive_id") or ad_raw.get("id", "")
            if not ad_archive_id:
                continue

            impressions = ad_raw.get("impressions") or {}
            reach = ad_raw.get("reach") or {}
            spend = ad_raw.get("spend") or {}
            snapshot_url = ad_raw.get("ad_snapshot_url")

            # Estimated audience size (separate from impressions)
            est_audience = ad_raw.get("estimated_audience_size") or {}

            media_local_path = None
            frame_paths = None
            frame_metadata = None
            is_video_signal = False

            media_local_path = None
            frame_paths = None
            frame_metadata = None
            is_video_signal = False

            # Media fetching is DEFERRED to generate_insights phase to avoid rate limits
            # and Playwright overhead during bulk ingestion


            ad_type, classification_method = await classify_ad(ad_raw, media_local_path, is_video_signal)

            logger.info(
                "ad_media_identified",
                ad_id=ad_archive_id,
                ad_type=ad_type,
                classification_method=classification_method,
                has_media_path=bool(media_local_path),
                is_video_signal=is_video_signal
            )

            start_date = _parse_date(ad_raw.get("ad_delivery_start_time"))
            end_date = _parse_date(ad_raw.get("ad_delivery_stop_time"))

            bodies = ad_raw.get("ad_creative_bodies") or []
            seen, unique_bodies = set(), []
            for b in bodies:
                if b and b not in seen:
                    seen.add(b)
                    unique_bodies.append(b)
            caption = unique_bodies[0] if unique_bodies else None

            link_titles = ad_raw.get("ad_creative_link_titles") or []
            link_descs = ad_raw.get("ad_creative_link_descriptions") or []

            # Political ad specific fields
            disclaimer = ad_raw.get("disclaimer") or None
            bylines = ad_raw.get("bylines") or None
            beneficiary_payers = ad_raw.get("beneficiary_payers") or None
            demographic_distribution = ad_raw.get("demographic_distribution") or None
            delivery_by_region = ad_raw.get("delivery_by_region") or None
            languages = ad_raw.get("languages") or None
            currency = ad_raw.get("currency") or None

            # Calculate midpoints for performance scoring
            imp_lower = _parse_range_value(impressions, "lower_bound")
            imp_upper = _parse_range_value(impressions, "upper_bound")
            if imp_lower is not None and imp_upper is None:
                imp_upper = imp_lower
                
            reach_lower = _parse_range_value(reach, "lower_bound")
            reach_upper = _parse_range_value(reach, "upper_bound")
            if reach_lower is not None and reach_upper is None:
                reach_upper = reach_lower

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
                    cta_type=None,
                    publisher_platforms=ad_raw.get("publisher_platforms"),
                    start_date=start_date,
                    end_date=end_date,
                    impressions_lower=imp_lower,
                    impressions_upper=imp_upper,
                    reach_lower=reach_lower,
                    reach_upper=reach_upper,
                    spend_lower=_parse_range_value(spend, "lower_bound"),
                    spend_upper=_parse_range_value(spend, "upper_bound"),
                    estimated_audience_lower=_parse_range_value(est_audience, "lower_bound"),
                    estimated_audience_upper=_parse_range_value(est_audience, "upper_bound"),
                    snapshot_url=snapshot_url,
                    media_local_path=media_local_path,
                    frame_paths=frame_paths,
                    frame_metadata=frame_metadata,
                    # New political/demographic fields
                    disclaimer=disclaimer,
                    bylines=bylines,
                    beneficiary_payers=beneficiary_payers,
                    demographic_distribution=demographic_distribution,
                    delivery_by_region=delivery_by_region,
                    languages=languages,
                    currency=currency,
                    raw_meta_json=ad_raw,
                ).on_conflict_do_update(
                    index_elements=["ad_archive_id"],
                    set_={
                        "is_active": bool(ad_raw.get("is_active", False)),
                        "impressions_lower": imp_lower,
                        "impressions_upper": imp_upper,
                        "reach_lower": reach_lower,
                        "reach_upper": reach_upper,
                        "spend_lower": _parse_range_value(spend, "lower_bound"),
                        "spend_upper": _parse_range_value(spend, "upper_bound"),
                        "disclaimer": disclaimer,
                        "bylines": bylines,
                        "beneficiary_payers": beneficiary_payers,
                        "demographic_distribution": demographic_distribution,
                        "delivery_by_region": delivery_by_region,
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

        except Exception as exc:
            ad_id_for_log = ad_raw.get("ad_archive_id") or ad_raw.get("id", "unknown")
            logger.error("ad_processing_error", ad_archive_id=ad_id_for_log, error=str(exc), exc_info=True)
            continue

    return processed


def _parse_date(value: str | None):
    if not value:
        return None
    try:
        from datetime import date
        if "T" in value:
            return datetime.fromisoformat(value.replace("+0000", "+00:00")).date()
        return date.fromisoformat(value)
    except Exception:
        return None


def _parse_range_value(range_dict, key: str) -> int | None:
    if not range_dict or isinstance(range_dict, str):
        return None
    val = range_dict.get(key)
    if val is not None:
        try:
            return int(val)
        except (ValueError, TypeError):
            return None
    return None