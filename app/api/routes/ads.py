"""Ad API routes — listing and detail."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select, desc, asc
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db
from app.db.models import Ad
from app.schemas.ad import AdDetailResponse, AdListResponse, AdResponse

router = APIRouter()

# Allowed sort columns
SORT_COLUMNS = {
    "impressions_mid": Ad.impressions_mid,
    "reach_mid": Ad.reach_mid,
    "start_date": Ad.start_date,
    "performance_score": Ad.performance_score,
    "created_at": Ad.created_at,
}


@router.get("/ads", response_model=AdListResponse)
async def list_ads(
    brand_id: uuid.UUID | None = Query(None),
    status: str = Query("ALL", pattern="^(ACTIVE|INACTIVE|ALL)$"),
    type: str = Query("ALL", pattern="^(STATIC|VIDEO|ALL)$"),
    has_insights: bool | None = Query(None),
    sort_by: str = Query("created_at"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """List ads with filtering, sorting, and pagination."""
    query = select(Ad)

    # Filters
    if brand_id:
        query = query.where(Ad.brand_id == brand_id)
    if status == "ACTIVE":
        query = query.where(Ad.is_active.is_(True))
    elif status == "INACTIVE":
        query = query.where(Ad.is_active.is_(False))
    if type != "ALL":
        query = query.where(Ad.ad_type == type)
    if has_insights is True:
        from app.db.models import Insight
        query = query.where(Ad.insight.has())
    elif has_insights is False:
        from app.db.models import Insight
        query = query.where(~Ad.insight.has())

    # Count total before pagination
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Sorting
    sort_col = SORT_COLUMNS.get(sort_by, Ad.created_at)
    if order == "desc":
        query = query.order_by(desc(sort_col))
    else:
        query = query.order_by(asc(sort_col))

    # Pagination
    query = query.limit(limit).offset(offset)

    result = await db.execute(query)
    ads = result.scalars().all()

    return AdListResponse(
        ads=[AdResponse.model_validate(a) for a in ads],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/ads/{ad_id}", response_model=AdDetailResponse)
async def get_ad(
    ad_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get full ad detail including raw_meta_json and frame_metadata."""
    result = await db.execute(select(Ad).where(Ad.id == ad_id))
    ad = result.scalar_one_or_none()
    if not ad:
        raise HTTPException(status_code=404, detail="Ad not found")
    return AdDetailResponse.model_validate(ad)
