"""Performance scorer — percentile-based scoring within a brand's ad dataset."""

from app.core.logging import get_logger
from app.db.models import Ad

logger = get_logger(__name__)

# Scoring weights
WEIGHTS = {
    "reach_efficiency": 0.40,
    "impressions_mid": 0.35,
    "daily_impressions": 0.25,
}


def compute_raw_metrics(ad: Ad) -> dict | None:
    """
    Compute raw performance metrics for a single ad.

    Returns:
        Dict of metric values or None if ad is not scoreable.
    """
    if ad.impressions_mid is None or ad.reach_mid is None:
        return None

    imp = int(ad.impressions_mid)
    reach = int(ad.reach_mid)

    reach_efficiency = reach / imp if imp > 0 else 0.0

    duration_days = None
    daily_impressions = None

    if ad.start_date and ad.end_date:
        duration_days = (ad.end_date - ad.start_date).days or 1
        daily_impressions = imp / duration_days

    return {
        "impressions_mid": imp,
        "reach_mid": reach,
        "reach_efficiency": reach_efficiency,
        "duration_days": duration_days,
        "daily_impressions": daily_impressions,
    }


def _normalize(values: list[float]) -> list[float]:
    """Min-max normalize a list of values to 0.0–1.0 range."""
    mn, mx = min(values), max(values)
    if mx == mn:
        return [0.5] * len(values)
    return [(v - mn) / (mx - mn) for v in values]


def score_brand_ads(ads: list[Ad]) -> list[tuple[Ad, float, str, float]]:
    """
    Score all scoreable ads for a brand using percentile-based ranking.

    The composite score is a weighted sum of normalized metrics:
    - reach_efficiency (40%): ratio of unique reach to impressions
    - impressions_mid (35%): raw delivery volume
    - daily_impressions (25%): daily delivery velocity

    Args:
        ads: List of Ad models for a single brand

    Returns:
        List of tuples: (ad, score, label, percentile)
        Labels: top 33% → STRONG, middle 34% → AVERAGE, bottom 33% → WEAK
    """
    scoreable = []
    for ad in ads:
        metrics = compute_raw_metrics(ad)
        if metrics is not None:
            scoreable.append((ad, metrics))

    if not scoreable:
        return []

    if len(scoreable) == 1:
        ad, metrics = scoreable[0]
        return [(ad, 0.5, "AVERAGE", 50.0)]

    # Normalize each metric across the brand dataset
    re_vals = _normalize([m["reach_efficiency"] for _, m in scoreable])
    imp_vals = _normalize([m["impressions_mid"] for _, m in scoreable])

    # Check if daily_impressions is available for all ads
    di_available = all(m["daily_impressions"] is not None for _, m in scoreable)
    if di_available:
        di_vals = _normalize([m["daily_impressions"] for _, m in scoreable])
    else:
        di_vals = [0.5] * len(scoreable)

    # Compute composite scores
    results = []
    for i, (ad, metrics_dict) in enumerate(scoreable):
        composite = (
            WEIGHTS["reach_efficiency"] * re_vals[i]
            + WEIGHTS["impressions_mid"] * imp_vals[i]
            + WEIGHTS["daily_impressions"] * di_vals[i]
        )
        results.append((ad, composite, metrics_dict))

    # Sort descending by composite score
    results.sort(key=lambda x: x[1], reverse=True)
    total = len(results)

    labeled = []
    for rank, (ad, score, _metrics_dict) in enumerate(results):
        percentile = ((total - rank) / total) * 100
        if percentile >= 67:
            label = "STRONG"
        elif percentile >= 34:
            label = "AVERAGE"
        else:
            label = "WEAK"
        labeled.append((ad, round(score, 4), label, round(percentile, 2)))

    logger.info(
        "brand_ads_scored",
        total_ads=len(ads),
        scoreable=len(scoreable),
        strong=sum(1 for _, _, l, _ in labeled if l == "STRONG"),
        average=sum(1 for _, _, l, _ in labeled if l == "AVERAGE"),
        weak=sum(1 for _, _, l, _ in labeled if l == "WEAK"),
    )

    return labeled
