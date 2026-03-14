"""
AI insight generator — Qwen VL prompting for creative analysis.

TWO MODES:
1. Visual mode — when local media exists (image or video frames downloaded from snapshot).
   Sends actual images to the VL model alongside performance data.

2. Text-only mode — when no local media is available (snapshot parsing failed, or URL
   was inaccessible). Generates insights purely from ad copy + performance metrics.
   This is explicitly documented as a degraded path in the insight output.

The Meta Ads Library API does not provide direct media URLs. Media availability
depends on successful HTML parsing of the snapshot page.
"""

import base64
import json
import os
from dataclasses import dataclass

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.core.metrics import metrics
from app.db.models import Ad

logger = get_logger(__name__)

PROMPT_VERSION = "v1"


@dataclass
class InsightResult:
    summary: str
    factors: list[dict]
    model_used: str = ""
    prompt_version: str = PROMPT_VERSION
    analysis_mode: str = "visual"  # "visual" | "text_only"


# ── System Prompts ─────────────────────────────────────────────────────────────

STATIC_VISUAL_SYSTEM_PROMPT = """You are an expert performance creative analyst specializing in Meta (Facebook/Instagram) paid advertising.

Your task is to analyze a static image ad creative alongside its performance data and produce a structured insight explaining WHY the creative likely performed the way it did. You must connect specific, observable visual traits directly to performance signals.

You will be given:
- The ad image
- The ad's caption/copy text
- Performance label: STRONG, AVERAGE, or WEAK (relative to this brand's other ads)
- Reach efficiency score (0.0–1.0): ratio of unique reach to total impressions
- Percentile rank within this brand's ad dataset

Analysis framework — evaluate the following dimensions:

1. COMPOSITION: Is there a clear visual hierarchy? Is the eye guided to a focal point, or is the layout cluttered?
2. PRODUCT_VISIBILITY: Is the product or core value proposition visible prominently? Is there ambiguity about what is being sold?
3. HUMAN_PRESENCE: Are there faces or people? Authentic or stock? Supporting or competing with the message?
4. CTA: Is a call-to-action present and visible? Is it clear and appropriately demanding?
5. COPY: Is text on the creative minimal and scannable, or dense? Does it lead with benefit or feature?
6. COLOR: Does the palette create contrast that draws attention? Does it stand out in a social feed?

Rules:
- Be specific. Reference what you actually see.
- Connect each observation to the performance outcome.
- Do not speculate beyond what is visible.
- Output ONLY a valid JSON object. No markdown. No preamble.

Output schema:
{
  "summary": "3-5 sentence narrative connecting the creative to its performance. Must reference the performance label and at least 2 specific visual traits.",
  "analysis_mode": "visual",
  "factors": [
    {
      "trait": "snake_case_trait_name",
      "category": "composition|cta|copy|color|product_visibility|human_presence",
      "impact": "positive|negative|neutral",
      "confidence": "high|medium|low",
      "evidence": "One specific sentence describing what you observed and why it impacts performance."
    }
  ]
}

Produce 3 to 6 factors. Prioritize factors with high confidence."""


TEXT_ONLY_SYSTEM_PROMPT = """You are an expert performance creative analyst specializing in Meta (Facebook/Instagram) paid advertising.

IMPORTANT: No ad image is available for this analysis. You will analyze the ad based on its copy text and performance data only. This is a text-based analysis — do not invent visual observations.

Your task is to analyze the ad copy and performance metrics and explain what the copy signals about likely creative approach, and how the performance data fits.

You will be given:
- Ad caption/copy text (the written content of the ad)
- Link title and description if available
- Performance label: STRONG, AVERAGE, or WEAK (relative to this brand's other ads)
- Reach efficiency score and percentile if available

Analysis framework for copy-only analysis:

1. COPY_CLARITY: Is the message clear and immediately understandable? Does it communicate a specific value proposition or benefit?
2. OFFER_STRENGTH: Is there a specific offer, urgency, or reason to act? Or is it vague brand messaging?
3. AUDIENCE_RELEVANCE: Does the language suggest strong targeting relevance? Does it speak to a specific pain point or desire?
4. CTA_COPY: Is there a clear call-to-action in the text? Is it specific ("Shop Now", "Get 50% off") or generic ("Learn more")?
5. TONE: Is the tone authentic, corporate, urgent, playful? Does the tone fit the likely audience?
6. LENGTH: Is the copy appropriately concise, or is it overlong for a feed environment?

Rules:
- Only analyze what is in the text. Do not guess about visual elements.
- Be explicit that this is a text-only analysis when referencing limitations.
- Connect copy observations to performance outcomes where possible.
- Output ONLY a valid JSON object. No markdown. No preamble.

Output schema:
{
  "summary": "3-5 sentence narrative. Must acknowledge this is text-only analysis, reference the performance label, and connect at least 2 copy traits to performance.",
  "analysis_mode": "text_only",
  "factors": [
    {
      "trait": "snake_case_trait_name",
      "category": "copy_clarity|offer_strength|audience_relevance|cta_copy|tone|length",
      "impact": "positive|negative|neutral",
      "confidence": "high|medium|low",
      "evidence": "One specific sentence referencing the actual copy text and its likely performance implication."
    }
  ]
}

Produce 3 to 5 factors. Always note confidence as 'medium' or 'low' when visual context is absent."""


VIDEO_VISUAL_SYSTEM_PROMPT = """You are an expert performance creative analyst specializing in Meta (Facebook/Instagram) video advertising.

Your task is to analyze a video ad creative — represented by scene-change frames with timestamps — alongside its performance data.

You will be given frames extracted at scene changes. Each frame is labeled with its exact timestamp and scene change score (0.0–1.0, where higher = more visually distinct from the previous scene).

Analysis framework:

1. HOOK_STRENGTH: Does the first scene (timestamp < 2s) immediately communicate value or create visual tension? The hook is the highest-leverage factor.
2. PACING: How many scene cuts are there? Use frame count and timestamps as evidence.
3. PRODUCT_VISIBILITY: At what timestamp does the product first appear? Early reveal is a strong positive signal.
4. HUMAN_PRESENCE: Are faces or people visible? In which scenes? Are they engaged with the product?
5. CTA: Is a call-to-action visible in the final scenes? Clear and legible?
6. COPY: Are text overlays concise and readable? Do they reinforce or compete with the visual?
7. SCENE_TRANSITION_QUALITY: Are cuts purposeful (scores > 0.6) or visually monotonous (scores < 0.3)?

Rules:
- Reason across ALL frames as a temporal sequence.
- Reference specific scene timestamps when making claims.
- Use scene change scores and frame count as evidence for pacing claims.
- Output ONLY a valid JSON object. No markdown. No preamble.

Output schema:
{
  "summary": "3-5 sentence narrative referencing performance label, hook quality, pacing with frame count, and at least 2 other traits.",
  "analysis_mode": "visual",
  "edit_pace": "fast|medium|slow",
  "hook_timestamp_sec": 0.0,
  "product_first_seen_sec": null,
  "cta_timestamp_sec": null,
  "factors": [
    {
      "trait": "snake_case_trait_name",
      "category": "hook_strength|pacing|product_visibility|human_presence|cta|copy|scene_transition_quality",
      "impact": "positive|negative|neutral",
      "confidence": "high|medium|low",
      "evidence": "One specific sentence referencing observable frame/timestamp evidence and its performance implication."
    }
  ]
}

Produce 4 to 7 factors. Always include hook_strength and pacing."""


# ── Helpers ────────────────────────────────────────────────────────────────────

def _encode_image(image_path: str) -> str:
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _performance_context(ad: Ad) -> str:
    """Build performance context string for prompt."""
    if ad.performance_label and ad.impressions_mid and ad.reach_mid:
        imp = int(ad.impressions_mid)
        reach = int(ad.reach_mid)
        efficiency = reach / imp if imp > 0 else 0
        return (
            f"Performance label: {ad.performance_label} "
            f"(percentile: {ad.performance_percentile:.0f}th within this brand's ads)\n"
            f"Reach efficiency: {efficiency:.2f} "
            f"({reach:,} unique users from {imp:,} impressions)"
        )
    elif ad.performance_label:
        return f"Performance label: {ad.performance_label} (impression/reach data not available for this region)"
    return "Performance data: not available"


def _copy_text(ad: Ad) -> str:
    parts = filter(None, [ad.caption, ad.link_title, ad.link_description])
    return " | ".join(parts) or "[No copy available]"


def _build_static_visual_messages(ad: Ad) -> list[dict]:
    return [{
        "role": "user",
        "content": [
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{_encode_image(ad.media_local_path)}"},
            },
            {
                "type": "text",
                "text": (
                    f"{_performance_context(ad)}\n\n"
                    f"Ad copy:\n{_copy_text(ad)}\n\n"
                    f"Analyze this static ad creative. Respond only with the JSON."
                ),
            },
        ],
    }]


def _build_text_only_messages(ad: Ad) -> list[dict]:
    """Build messages for text-only analysis when no media is available."""
    return [{
        "role": "user",
        "content": (
            f"{_performance_context(ad)}\n\n"
            f"Ad copy / text:\n{_copy_text(ad)}\n\n"
            f"Note: No ad image is available. Analyze based on copy and performance data only.\n"
            f"Respond only with the JSON."
        ),
    }]


def _build_video_visual_messages(ad: Ad) -> list[dict]:
    frame_metas = ad.frame_metadata or []
    content: list[dict] = [{
        "type": "text",
        "text": (
            f"{_performance_context(ad)}\n\nAd copy:\n{ad.caption or '[No copy]'}\n\n"
            f"Analyzing {len(frame_metas)} scene-change frames from this video ad. "
            f"Each frame represents a distinct visual scene, ordered chronologically:"
        ),
    }]
    for i, meta in enumerate(frame_metas):
        hook_tag = " ← HOOK (first 2s)" if meta.get("is_hook") else ""
        label = (
            f"Scene {i + 1} — {meta['timestamp_sec']:.1f}s{hook_tag} "
            f"(scene change score: {meta['scene_score']:.2f})"
        )
        content.append({"type": "text", "text": f"\n{label}:"})
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{_encode_image(meta['path'])}"},
        })
    content.append({"type": "text", "text": "\nAnalyze across all scenes as a sequence. Respond only with the JSON."})
    return [{"role": "user", "content": content}]


def _parse_insight_response(raw: str, expected_mode: str) -> InsightResult:
    content = raw.strip()
    if "</think>" in content:
        content = content.split("</think>")[-1].strip()
    clean = content.lstrip("```json").rstrip("```").strip()
    data = json.loads(clean)
    return InsightResult(
        summary=data["summary"],
        factors=data.get("factors", []),
        analysis_mode=data.get("analysis_mode", expected_mode),
    )


# ── Main Interface ─────────────────────────────────────────────────────────────

async def generate_insight(ad: Ad) -> InsightResult:
    """
    Generate creative insights for an ad.

    Automatically selects the appropriate mode:
    - Visual (static): ad has media_local_path and ad_type == STATIC
    - Visual (video): ad has frame_metadata populated and ad_type == VIDEO
    - Text-only: no local media available (snapshot parsing failed)

    The text-only path is explicitly documented in the insight output via
    the analysis_mode field, so downstream consumers know the limitation.
    """
    has_image = ad.media_local_path and os.path.exists(ad.media_local_path)
    has_frames = ad.frame_metadata and len(ad.frame_metadata) > 0

    if ad.ad_type == "VIDEO" and has_frames:
        system_prompt = VIDEO_VISUAL_SYSTEM_PROMPT
        messages = _build_video_visual_messages(ad)
        mode = "visual"
    elif ad.ad_type == "STATIC" and has_image:
        system_prompt = STATIC_VISUAL_SYSTEM_PROMPT
        messages = _build_static_visual_messages(ad)
        mode = "visual"
    else:
        # Text-only fallback — no media available
        logger.info(
            "insight_text_only_mode",
            ad_id=str(ad.id),
            ad_type=ad.ad_type,
            has_image=bool(has_image),
            reason="No local media available — snapshot parsing may have failed or URL was inaccessible",
        )
        system_prompt = TEXT_ONLY_SYSTEM_PROMPT
        messages = _build_text_only_messages(ad)
        mode = "text_only"

    payload = {
        "model": settings.INSIGHT_MODEL,
        "max_tokens": 1500,
        "messages": [
            {"role": "system", "content": system_prompt},
            *messages,
        ],
        "response_format": {"type": "json_object"},
    }

    try:
        async with httpx.AsyncClient(timeout=120) as client:
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
            result = _parse_insight_response(raw_content, mode)
            result.model_used = settings.INSIGHT_MODEL
            result.prompt_version = PROMPT_VERSION
            metrics.increment(f"insights_generated_{mode}")
            logger.info("insight_generated", ad_id=str(ad.id), ad_type=ad.ad_type, mode=mode)
            return result

    except json.JSONDecodeError as exc:
        logger.error("insight_parse_error", ad_id=str(ad.id), error=str(exc))
        raise
    except Exception as exc:
        logger.error("insight_generation_error", ad_id=str(ad.id), error=str(exc))
        raise