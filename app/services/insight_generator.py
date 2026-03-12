"""AI insight generator — Qwen VL prompting for creative analysis."""

import base64
import json
from dataclasses import dataclass

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.core.metrics import metrics
from app.db.models import Ad

logger = get_logger(__name__)

# Current prompt version
PROMPT_VERSION = "v1"


@dataclass
class InsightResult:
    """Result from the insight generation model."""
    summary: str
    factors: list[dict]
    model_used: str = ""
    prompt_version: str = PROMPT_VERSION


# ── System Prompts ─────────────────────────────────────────────────────────────

STATIC_SYSTEM_PROMPT = """You are an expert performance creative analyst specializing in Meta (Facebook/Instagram) paid advertising.

Your task is to analyze a static image ad creative alongside its performance data and produce a structured insight explaining WHY the creative likely performed the way it did. You must connect specific, observable visual traits directly to performance signals.

You will be given:
- The ad image
- The ad's caption/copy text
- Performance label: STRONG, AVERAGE, or WEAK (relative to this brand's other ads)
- Reach efficiency score (0.0–1.0): ratio of unique reach to total impressions
- Percentile rank within this brand's ad dataset

Analysis framework — evaluate the following dimensions and determine their impact:

1. COMPOSITION: Is there a clear visual hierarchy? Is the eye guided to a focal point, or is the layout cluttered? Does the layout follow F-pattern or Z-pattern scanning behavior?
2. PRODUCT_VISIBILITY: Is the product or core value proposition visible prominently and early? Is there ambiguity about what is being sold?
3. HUMAN_PRESENCE: Are there faces or people? Are they authentic or stock-photo quality? Are they positioned to support the message or compete with it?
4. CTA: Is a call-to-action present and visible? Is it clear, specific, and appropriately demanding for the likely audience temperature (soft CTA for cold audiences, hard CTA for warm)?
5. COPY: Is text on the creative minimal and scannable, or dense and hard to read? Does it lead with benefit or feature? Is there urgency or an offer?
6. COLOR: Does the color palette create contrast that draws attention to key elements? Does it stand out in a social feed context?

Rules:
- Be specific. Do not use generic praise or criticism. Reference what you actually see.
- Connect each observation to the performance outcome. Use language like "which likely contributed to..." or "this may have limited reach because..."
- Do not speculate beyond what is visible. If an element is unclear, say so.
- Output ONLY a valid JSON object. No markdown. No preamble.

Output schema:
{
  "summary": "3-5 sentence narrative connecting the creative to its performance. Must reference the performance label and at least 2 specific visual traits.",
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

Produce 3 to 6 factors. Prioritize factors with high confidence. Do not fabricate factors you cannot substantiate from the image."""


VIDEO_SYSTEM_PROMPT = """You are an expert performance creative analyst specializing in Meta (Facebook/Instagram) video advertising.

Your task is to analyze a video ad creative — represented by a sequence of key frames with timestamps — alongside its performance data, and produce a structured insight explaining WHY the creative likely performed the way it did. Connect specific, observable traits from the video frames to the performance signal.

You will be given frames extracted at scene changes — each frame represents a distinct visual cut or transition, not a fixed time interval. The number of frames reflects the actual editing pace of the ad: many frames = fast-cut ad, few frames = slow/single-scene ad. Each frame is labeled with its exact timestamp and scene change score (0.0–1.0, where higher = more visually distinct from the previous scene).

Analysis framework — evaluate the following dimensions:

1. HOOK_STRENGTH: Does the first scene (timestamp < 2s) immediately communicate value, show the product, or create visual tension? The hook is the highest-leverage factor. A weak or ambiguous opening is the primary driver of scroll-past behavior.
2. PACING: How many scene cuts are there? Fast-cut ads (6+ distinct scenes) signal high energy and tend to retain attention in feed environments. Single-scene or slow ads must compensate with strong copy or emotional pull. Use the frame count and timestamps as evidence.
3. PRODUCT_VISIBILITY: At what timestamp does the product first appear clearly? Early reveal (within the first 2-3 scenes) is a strong positive signal. Delayed reveal risks drop-off before the product is seen. Reference the specific scene timestamp.
4. HUMAN_PRESENCE: Are faces or people visible? In which scenes? Are they engaged with the product or looking toward camera? Human faces in the hook scene are especially high-value for scroll-stopping.
5. CTA: Is a call-to-action visible in the final scenes? Is the offer or next step clear and legible? Reference the scene timestamp where the CTA appears.
6. COPY: Are text overlays present? Are they concise and readable against the background? Do they reinforce the visual narrative or compete with it?
7. SCENE_TRANSITION_QUALITY: Based on scene change scores — are cuts purposeful and high-contrast (scores > 0.6), suggesting deliberate editing? Or are scores low across all frames (< 0.3), suggesting a single slow scene with minimal visual dynamism?

Rules:
- Reason across ALL frames as a temporal sequence, not as isolated images.
- Reference specific scene timestamps when making claims (e.g., "by scene 3 at 4.2s, the product is clearly featured...").
- Use the scene change score and frame count as evidence for pacing claims — this is quantitative data, use it.
- Connect observations to performance outcomes using directional language.
- Output ONLY a valid JSON object. No markdown. No preamble.

Output schema:
{
  "summary": "3-5 sentence narrative. Must reference the performance label, hook quality, pacing (with frame count as evidence), and at least 2 other traits.",
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

Produce 4 to 7 factors. Always include hook_strength and pacing. Prioritize factors with high confidence."""


# ── Helpers ────────────────────────────────────────────────────────────────────

def _encode_image(image_path: str) -> str:
    """Encode an image file to base64 string."""
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _build_static_messages(ad: Ad) -> list[dict]:
    """Build the message payload for static ad insight generation."""
    if ad.performance_label and ad.impressions_mid and ad.reach_mid:
        reach_efficiency = int(ad.reach_mid) / int(ad.impressions_mid) if int(ad.impressions_mid) > 0 else 0
        performance_context = (
            f"Performance label: {ad.performance_label} "
            f"(percentile rank: {ad.performance_percentile:.0f}th out of this brand's ads)\n"
            f"Reach efficiency: {reach_efficiency:.2f} "
            f"({int(ad.reach_mid):,} unique users reached from {int(ad.impressions_mid):,} impressions)"
        )
    else:
        performance_context = "Performance data: not available"

    copy_text = " | ".join(filter(None, [ad.caption, ad.link_title, ad.link_description, ad.cta_type]))

    return [
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{_encode_image(ad.media_local_path)}"},
                },
                {
                    "type": "text",
                    "text": (
                        f"{performance_context}\n\n"
                        f"Ad copy / text:\n{copy_text or '[No copy available]'}\n\n"
                        f"Analyze this static ad creative. Respond only with the JSON."
                    ),
                },
            ],
        }
    ]


def _build_video_messages(ad: Ad) -> list[dict]:
    """Build the message payload for video ad insight generation."""
    if ad.performance_label and ad.impressions_mid and ad.reach_mid:
        reach_efficiency = int(ad.reach_mid) / int(ad.impressions_mid) if int(ad.impressions_mid) > 0 else 0
        performance_context = (
            f"Performance label: {ad.performance_label} "
            f"(percentile rank: {ad.performance_percentile:.0f}th)\n"
            f"Reach efficiency: {reach_efficiency:.2f}"
        )
    else:
        performance_context = "Performance data: not available"

    frame_metas = ad.frame_metadata or []

    content: list[dict] = [
        {
            "type": "text",
            "text": (
                f"{performance_context}\n\nAd copy:\n{ad.caption or '[No copy]'}\n\n"
                f"Analyzing {len(frame_metas)} scene-change frames from this video ad. "
                f"Each frame represents a distinct visual scene or cut, ordered chronologically:"
            ),
        }
    ]

    for i, meta in enumerate(frame_metas):
        hook_tag = " ← HOOK (first 2s)" if meta.get("is_hook") else ""
        label = (
            f"Scene {i + 1} — {meta['timestamp_sec']:.1f}s into video{hook_tag} "
            f"(scene change score: {meta['scene_score']:.2f})"
        )
        content.append({"type": "text", "text": f"\n{label}:"})
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{_encode_image(meta['path'])}"},
        })

    content.append({
        "type": "text",
        "text": "\nAnalyze this video ad creative across all scenes as a sequence. Respond only with the JSON.",
    })

    return [{"role": "user", "content": content}]


def _parse_insight_response(raw: str) -> InsightResult:
    """Parse the raw model response into an InsightResult."""
    content = raw.strip()
    # Strip thinking tags if present
    if "</think>" in content:
        content = content.split("</think>")[-1].strip()
    clean = content.lstrip("```json").rstrip("```").strip()
    data = json.loads(clean)
    return InsightResult(
        summary=data["summary"],
        factors=data.get("factors", []),
    )


# ── Main Interface ─────────────────────────────────────────────────────────────

async def generate_insight(ad: Ad) -> InsightResult:
    """
    Generate creative insights for an ad using Qwen VL-235B.

    Args:
        ad: The Ad model instance (must have media_local_path or frame_metadata populated)

    Returns:
        InsightResult with summary and structured factors
    """
    if ad.ad_type == "STATIC":
        system_prompt = STATIC_SYSTEM_PROMPT
        messages = _build_static_messages(ad)
    else:
        system_prompt = VIDEO_SYSTEM_PROMPT
        messages = _build_video_messages(ad)

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
            result = _parse_insight_response(raw_content)
            result.model_used = settings.INSIGHT_MODEL
            result.prompt_version = PROMPT_VERSION

            metrics.increment("insights_generated")
            logger.info("insight_generated", ad_id=str(ad.id), ad_type=ad.ad_type)
            return result

    except json.JSONDecodeError as exc:
        logger.error("insight_parse_error", ad_id=str(ad.id), error=str(exc))
        raise
    except Exception as exc:
        logger.error("insight_generation_error", ad_id=str(ad.id), error=str(exc))
        raise
