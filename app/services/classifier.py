"""Ad classifier — two-pass: metadata heuristics then VL model fallback."""

import json

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.core.metrics import metrics

logger = get_logger(__name__)

# Classification system prompt (v1)
CLASSIFICATION_SYSTEM_PROMPT = """You are a media type classifier for digital advertisements.
Your only job is to determine whether an ad creative is a STATIC image or a VIDEO.

Rules:
- If the image shows a video player interface, play button overlay, or is clearly a video thumbnail, output VIDEO.
- If the image is a still photograph, illustration, graphic, or banner with no video indicators, output STATIC.
- Output ONLY a JSON object with a single key. No other text.

Output format:
{"type": "STATIC"} or {"type": "VIDEO"}"""


def classify_from_metadata(ad_raw: dict) -> tuple[str, str] | None:
    """
    Pass 1: Classify ad type from metadata heuristics (free, no API call).

    Returns:
        Tuple of (ad_type, method) or None if uncertain.
    """
    creatives = ad_raw.get("ad_creatives", {}).get("data", [])

    has_video = False
    has_image = False

    for c in creatives:
        if c.get("video_id") or c.get("video_sd_url") or c.get("video_hd_url"):
            has_video = True
        if c.get("image_hash") or c.get("image_url") or c.get("thumbnail_url"):
            has_image = True

    # Carousel logic: if ANY creative has video → VIDEO
    if has_video:
        return ("VIDEO", "metadata")
    if has_image:
        return ("STATIC", "metadata")

    return None  # Uncertain — fall through to Pass 2


async def classify_with_vl_model(thumbnail_url: str) -> tuple[str, str]:
    """
    Pass 2: Use Qwen VL-30B to visually classify from thumbnail.

    Args:
        thumbnail_url: URL of the ad's thumbnail image.

    Returns:
        Tuple of (ad_type, method).
    """
    try:
        payload = {
            "model": settings.CLASSIFICATION_MODEL,
            "max_tokens": 50,
            "messages": [
                {"role": "system", "content": CLASSIFICATION_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": thumbnail_url}},
                        {"type": "text", "text": "Classify this ad creative. Respond only with the JSON."},
                    ],
                },
            ],
        }

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{settings.OPENROUTER_BASE_URL}/chat/completions",
                json=payload,
                headers={
                    "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                },
            )
            response.raise_for_status()
            data = response.json()

            raw_content = data["choices"][0]["message"]["content"]
            # Strip thinking tags if present
            content = raw_content.strip()
            if "</think>" in content:
                content = content.split("</think>")[-1].strip()
            # Parse JSON
            clean = content.strip().lstrip("```json").rstrip("```").strip()
            result = json.loads(clean)
            ad_type = result.get("type", "UNKNOWN").upper()

            if ad_type in ("STATIC", "VIDEO"):
                metrics.increment("classification_vl_model")
                return (ad_type, "vl_model")
            else:
                return ("UNKNOWN", "vl_model")

    except Exception as exc:
        logger.error("classification_vl_error", error=str(exc))
        return ("UNKNOWN", "fallback")


async def classify_ad(ad_raw: dict) -> tuple[str, str]:
    """
    Full two-pass classification pipeline.

    Returns:
        Tuple of (ad_type, classification_method).
    """
    # Pass 1: Metadata
    result = classify_from_metadata(ad_raw)
    if result:
        metrics.increment("classification_metadata")
        return result

    # Pass 2: VL model fallback
    creatives = ad_raw.get("ad_creatives", {}).get("data", [])
    thumbnail_url = None
    for c in creatives:
        thumbnail_url = c.get("thumbnail_url") or c.get("image_url")
        if thumbnail_url:
            break

    if thumbnail_url:
        return await classify_with_vl_model(thumbnail_url)

    # No creatives at all
    logger.warning("classification_no_creatives", ad_archive_id=ad_raw.get("ad_archive_id"))
    return ("UNKNOWN", "fallback")
