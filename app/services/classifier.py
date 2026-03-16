"""
Ad classifier — three-pass pipeline:
  Pass 1: publisher_platforms + creative field heuristics (free, reliable)
  Pass 2: snapshot URL pattern matching (free, fragile)
  Pass 3: Qwen VL model fallback (paid, accurate)

Political ad context: political ads from parties like BJP often have
video content embedded in static-looking snapshot pages. The VL model
is explicitly prompted to handle this ambiguity.
"""

import json

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.core.metrics import metrics

logger = get_logger(__name__)

# Platforms that strongly indicate video creative
VIDEO_PLATFORMS = {"instagram_reels", "facebook_reels", "facebook_stories", "instagram_stories"}
# Platforms that strongly indicate static creative
STATIC_PLATFORMS = {"facebook_feed", "instagram_feed", "facebook_marketplace"}

CLASSIFICATION_SYSTEM_PROMPT = """You are a media type classifier for digital advertisements, including both commercial brand ads and political party ads.

Your only job is to determine whether an ad creative is a STATIC image or a VIDEO.

Rules:
- If the image shows a video player interface, play button overlay, timeline scrubber, or is clearly a video thumbnail with duration text, output VIDEO.
- If the image is a still photograph, illustration, graphic, banner, or poster with no video indicators, output STATIC.
- Political party ads (BJP, Congress, etc.) often use poster-style graphics with text overlays — these are STATIC unless a video player is visible.
- Output ONLY a JSON object with a single key. No other text.

Output format:
{"type": "STATIC"} or {"type": "VIDEO"}"""


def _detect_ad_context(ad_raw: dict) -> str:
    """
    Detect whether this is a political/social ad or a commercial brand ad.
    Used to provide context for downstream analysis.
    Returns: 'political' | 'commercial'
    """
    disclaimer = ad_raw.get("disclaimer", "") or ""
    page_name = ad_raw.get("page_name", "") or ""

    # Meta Ads Library marks political/social issue ads with a disclaimer
    if disclaimer:
        return "political"

    # Common political party indicators
    political_keywords = [
        "party", "bjp", "congress", "election", "vote", "manifesto",
        "campaign", "political", "neta", "sarkar", "modi", "rahul",
    ]
    combined = (page_name + disclaimer).lower()
    if any(kw in combined for kw in political_keywords):
        return "political"

    return "commercial"


def classify_from_metadata(ad_raw: dict) -> tuple[str, str] | None:
    """
    Pass 1 & 2: Classify from metadata signals, strongest first.

    Signal priority (descending reliability):
    1. publisher_platforms — most reliable signal
    2. Video-specific flat fields presence
    3. Snapshot URL pattern
    4. Caption/body text presence (weak signal for STATIC)

    Returns: (ad_type, method) or None if uncertain
    """
    platforms = set(ad_raw.get("publisher_platforms") or [])

    # --- Pass 1: publisher_platforms ---
    video_overlap = platforms & VIDEO_PLATFORMS
    static_overlap = platforms & STATIC_PLATFORMS

    if video_overlap and not static_overlap:
        logger.debug("classify_video_by_platforms", platforms=list(video_overlap))
        return ("VIDEO", "metadata_platforms")

    if static_overlap and not video_overlap:
        # Still need to check other signals — static platforms can also run video
        # but if exclusively on feed with no story/reels, very likely static
        bodies = ad_raw.get("ad_creative_bodies") or []
        link_titles = ad_raw.get("ad_creative_link_titles") or []
        if bodies or link_titles:
            logger.debug("classify_static_by_platforms_and_text")
            return ("STATIC", "metadata_platforms_text")

    # Mixed platforms (both feed and stories/reels) — inconclusive from platforms alone

    # --- Pass 2: snapshot URL pattern ---
    snapshot_url = ad_raw.get("ad_snapshot_url", "") or ""
    if "video" in snapshot_url.lower():
        return ("VIDEO", "metadata_url_hint")

    # --- Weak STATIC signal: has substantial copy text ---
    bodies = ad_raw.get("ad_creative_bodies") or []
    link_titles = ad_raw.get("ad_creative_link_titles") or []
    link_descs = ad_raw.get("ad_creative_link_descriptions") or []

    # Political ads with no link titles but rich body copy are typically poster/static
    if bodies and not link_titles and not link_descs:
        context = _detect_ad_context(ad_raw)
        if context == "political":
            return ("STATIC", "metadata_political_poster_heuristic")

    # Commercial ads with link titles = almost always static
    if link_titles:
        return ("STATIC", "metadata_text_hint")

    return None  # Fall through to VL model


import base64

async def classify_with_vl_model(media_local_path: str) -> tuple[str, str]:
    """
    Pass 3: Use Qwen VL-30B to visually classify from the downloaded media file.
    Falls back to UNKNOWN on any failure.
    """
    if not media_local_path or not os.path.exists(media_local_path):
        logger.warning("classification_vl_missing_media", path=media_local_path)
        return ("UNKNOWN", "fallback_no_media")

    try:
        with open(media_local_path, "rb") as image_file:
            encoded_string = base64.b64encode(image_file.read()).decode('utf-8')
        
        # OpenRouter wants a base64 data URI for uploaded images
        mime_type = "image/jpeg"
        if media_local_path.lower().endswith(".png"):
            mime_type = "image/png"
        elif media_local_path.lower().endswith(".webp"):
            mime_type = "image/webp"
            
        data_uri = f"data:{mime_type};base64,{encoded_string}"

        payload = {
            "model": settings.CLASSIFICATION_MODEL,
            "max_tokens": 50,
            "messages": [
                {"role": "system", "content": CLASSIFICATION_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_uri}},
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
            return ("UNKNOWN", "vl_model")

    except Exception as exc:
        logger.error("classification_vl_error", error=str(exc))
        return ("UNKNOWN", "fallback")


async def classify_ad(ad_raw: dict, media_local_path: str | None = None) -> tuple[str, str]:
    """
    Full three-pass classification pipeline.
    Returns: (ad_type, classification_method)
    """
    # High-priority signal: Media Processor explicitly found a video file
    if media_local_path and media_local_path.lower().endswith((".mp4", ".mov", ".m4v")):
        return ("VIDEO", "media_processor_file_signal")

    result = classify_from_metadata(ad_raw)
    if result:
        metrics.increment("classification_metadata")
        return result

    if media_local_path:
        return await classify_with_vl_model(media_local_path)

    logger.warning("classification_no_signal", ad_id=ad_raw.get("ad_archive_id") or ad_raw.get("id"))
    return ("UNKNOWN", "fallback")