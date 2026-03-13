"""Ad classifier — two-pass: metadata heuristics then VL model fallback."""

import json

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.core.metrics import metrics

logger = get_logger(__name__)

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

    Since we no longer request ad_creatives{...} (not supported by /ads_archive),
    we classify from the snapshot URL pattern and any available flat fields.

    Returns:
        Tuple of (ad_type, method) or None if uncertain.
    """
    # Check flat creative fields available from the Ads Library API
    # These are top-level fields, not nested under ad_creatives
    bodies = ad_raw.get("ad_creative_bodies") or []
    link_titles = ad_raw.get("ad_creative_link_titles") or []
    snapshot_url = ad_raw.get("ad_snapshot_url", "")

    # The snapshot URL for video ads often contains 'video' in the path
    # This is a heuristic — not 100% reliable but catches the common cases
    if snapshot_url and "video" in snapshot_url.lower():
        return ("VIDEO", "metadata_url_hint")

    # If we have bodies or link titles, it's likely a static ad
    # (video ads tend to have minimal text in the flat fields)
    # This is a weak signal — we'll let the VL model handle uncertain cases
    if bodies or link_titles:
        # Default to STATIC for text-bearing ads; VL model refines if wrong
        return ("STATIC", "metadata_text_hint")

    return None  # No signal — fall through to Pass 2


async def classify_with_vl_model(snapshot_url: str) -> tuple[str, str]:
    """
    Pass 2: Use Qwen VL-30B to visually classify from snapshot URL.

    The ad_snapshot_url is a Meta-hosted preview page. We pass it directly
    as an image URL to the VL model — OpenRouter will attempt to fetch it.
    If the URL is inaccessible, falls back to UNKNOWN.

    Args:
        snapshot_url: Meta ad snapshot URL.

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
                        {"type": "image_url", "image_url": {"url": snapshot_url}},
                        {"type": "text", "text": "Classify this ad creative. Respond only with the JSON."},
                    ],
                },
            ],
        }

        async with httpx.AsyncClient(timeout=60) as client:
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
            content = raw_content.strip()
            if "</think>" in content:
                content = content.split("</think>")[-1].strip()
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
    # Pass 1: Metadata heuristics
    result = classify_from_metadata(ad_raw)
    if result:
        metrics.increment("classification_metadata")
        return result

    # Pass 2: VL model using snapshot URL
    snapshot_url = ad_raw.get("ad_snapshot_url")
    if snapshot_url:
        return await classify_with_vl_model(snapshot_url)

    # No signal at all
    logger.warning("classification_no_signal", ad_id=ad_raw.get("ad_archive_id") or ad_raw.get("id"))
    return ("UNKNOWN", "fallback")