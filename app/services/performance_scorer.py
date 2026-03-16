"""
Performance scorer — percentile-based scoring within a brand's ad dataset.

Scoring is relative within a brand (not cross-brand absolute) because
impression ranges vary enormously by brand size and campaign objective.

Data quality notes for Meta Ads Library API:
- impressions/reach: range estimates, only for EU-delivered ads
- ad_delivery_stop_time: frequently missing → daily_impressions often null
- spend: rarely populated via Ads Library API

The scorer handles missing data explicitly and surfaces data quality
metadata so the UI can communicate uncertainty to users.
"""

from dataclasses import dataclass
from typing import Optional

from app.core.logging import get_logger
from app.db.models import Ad

logger = get_logger(__name__)

WEIGHTS = {
    "reach_efficiency": 0.50,   # bumped from 0.40 — most reliable signal
    "impressions_mid": 0.35,
    "daily_impressions": 0.15,  # reduced from 0.25 — often null
}


@dataclass
class ScoringMetrics:
    impressions_mid: int
    reach_mid: Optional[int]
    reach_efficiency: Optional[float]
    duration_days: Optional[int]
    daily_impressions: Optional[float]
    data_quality: str  # 'full' | 'partial' | 'range_only'


def compute_raw_metrics(ad: Ad) -> Optional[ScoringMetrics]:
    """
    Compute raw performance metrics for a single ad.
    Returns None if the ad has no impression/reach data (unscoreable).
    """
    if ad.impressions_mid is None:
        return None

    imp = int(ad.impressions_mid)
    
    if imp == 0:
        return None

    reach_mid = int(ad.reach_mid) if ad.reach_mid is not None else None
    reach_efficiency = min(reach_mid / imp, 1.0) if reach_mid is not None else None

    duration_days: Optional[int] = None
    daily_impressions: Optional[float] = None

    if ad.start_date and ad.end_date:
        duration_days = max((ad.end_date - ad.start_date).days, 1)
        daily_impressions = imp / duration_days

    # Determine data quality level
    imp_range = (ad.impressions_upper or 0) - (ad.impressions_lower or 0)
    if imp_range == 0:
        data_quality = "full"  # exact count (rare from Ads Library)
    elif imp_range <= imp * 0.5:
        data_quality = "partial"  # tight range
    else:
        data_quality = "range_only"  # wide range — lower confidence

    return ScoringMetrics(
        impressions_mid=imp,
        reach_mid=reach_mid,
        reach_efficiency=reach_efficiency,
        duration_days=duration_days,
        daily_impressions=daily_impressions,
        data_quality=data_quality,
    )


def _normalize(values: list[float]) -> list[float]:
    """Min-max normalize a list of values to [0.0, 1.0]."""
    mn, mx = min(values), max(values)
    if mx == mn:
        return [0.5] * len(values)
    return [(v - mn) / (mx - mn) for v in values]


def score_brand_ads(ads: list[Ad]) -> list[tuple[Ad, float, str, float]]:
    """
    Score all scoreable ads for a brand using percentile-based ranking.

    Composite score = weighted sum of normalized metrics:
      - reach_efficiency (50%): ratio of unique reach to impressions
        → measures how broadly the ad reached new people vs re-showing
      - impressions_mid (35%): raw delivery volume
      - daily_impressions (15%): velocity — only counted when dates available

    When daily_impressions is unavailable (stop_time missing), its weight
    is redistributed proportionally to the other two metrics rather than
    silently using 0.5. This is explicitly logged.

    Labels: top 33% → STRONG, middle 34% → AVERAGE, bottom 33% → WEAK

    Returns: list of (ad, composite_score, label, percentile)
    """
    scoreable = []
    no_dates_count = 0

    for ad in ads:
        m = compute_raw_metrics(ad)
        if m is not None:
            scoreable.append((ad, m))
            if m.daily_impressions is None:
                no_dates_count += 1

    if not scoreable:
        logger.info("no_scoreable_ads", total=len(ads))
        return []

    if len(scoreable) == 1:
        ad, _ = scoreable[0]
        return [(ad, 0.5, "AVERAGE", 50.0)]

    di_available = sum(1 for _, m in scoreable if m.daily_impressions is not None)
    use_daily = di_available >= len(scoreable) * 0.5  # only use if >50% have date data
    
    re_available = sum(1 for _, m in scoreable if m.reach_efficiency is not None)
    use_reach = re_available >= len(scoreable) * 0.5

    if no_dates_count > 0:
        logger.info(
            "scorer_missing_dates",
            missing=no_dates_count,
            total=len(scoreable),
            using_daily_impressions=use_daily,
            using_reach_efficiency=use_reach,
        )

    # Redistribute weights proportionally based on available data
    base_w = {
        "reach_efficiency": WEIGHTS["reach_efficiency"] if use_reach else 0.0,
        "impressions_mid": WEIGHTS["impressions_mid"],
        "daily_impressions": WEIGHTS["daily_impressions"] if use_daily else 0.0,
    }
    
    total_active_w = sum(base_w.values())
    w_re = base_w["reach_efficiency"] / total_active_w if total_active_w > 0 else 0
    w_imp = base_w["impressions_mid"] / total_active_w if total_active_w > 0 else 0
    w_di = base_w["daily_impressions"] / total_active_w if total_active_w > 0 else 0

    # Normalize each metric across the brand dataset
    if use_reach:
        re_vals = _normalize([m.reach_efficiency if m.reach_efficiency is not None else 0.0 for _, m in scoreable])
    else:
        re_vals = [0.0] * len(scoreable)
        
    imp_vals = _normalize([m.impressions_mid for _, m in scoreable])

    if use_daily:
        di_raw = [m.daily_impressions if m.daily_impressions is not None else 0.0 for _, m in scoreable]
        di_vals = _normalize(di_raw)
    else:
        di_vals = [0.0] * len(scoreable)

    results = []
    for i, (ad, _) in enumerate(scoreable):
        composite = w_re * re_vals[i] + w_imp * imp_vals[i] + w_di * di_vals[i]
        results.append((ad, round(composite, 4)))

    # Sort descending, assign labels
    results.sort(key=lambda x: x[1], reverse=True)
    total = len(results)

    labeled = []
    for rank, (ad, score) in enumerate(results):
        percentile = ((total - rank) / total) * 100
        if percentile >= 67:
            label = "STRONG"
        elif percentile >= 34:
            label = "AVERAGE"
        else:
            label = "WEAK"
        labeled.append((ad, score, label, round(percentile, 2)))

    logger.info(
        "brand_ads_scored",
        total_ads=len(ads),
        scoreable=len(scoreable),
        strong=sum(1 for _, _, l, _ in labeled if l == "STRONG"),
        average=sum(1 for _, _, l, _ in labeled if l == "AVERAGE"),
        weak=sum(1 for _, _, l, _ in labeled if l == "WEAK"),
        used_daily_impressions=use_daily,
    )

    return labeled